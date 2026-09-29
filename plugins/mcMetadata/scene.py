import os
from collections import Counter
import utils.logger as log
from performer import process_performer
from utils.files import authenticated_url, download_image, find_sidecars, rename_file, replace_file_ext
from utils.nfo import _is_plex, _count_videos, artwork_filenames, artwork_templates, build_nfo_xml, is_folder_level, _render
from utils.paths import is_inside
from utils.replacer import get_new_path
from utils.run_flow import is_dry_run
from utils.self_updates import consume, mark
from utils.videos import is_video
from conditions import should_process, build_scene_filter, format_bulk_summary

SKIP_SAMPLE_LIMIT = 10
# process_scene's skip reason for a scene without files (counted in the bulk histogram)
NO_FILES = "no_files"

BATCH_SIZE = 100
IMPOSSIBLE_PATH = "$%^&@"
# stashapi's Studio fragment only has parent_studio { id }, so each level is fetched
STUDIO_FRAGMENT = "id name parent_studio { id }"
MAX_STUDIO_DEPTH = 10


def process_all_scenes(stash, settings, api_key):
    """Process scenes in bulk, gated by the unified processing conditions.

    build_scene_filter narrows the server-side fetch where Stash can express a gate
    exactly (organized, StashID); should_process is the authoritative gate applied to
    every fetched scene. Scenes the gate rejects are tallied by reason for the dry-run
    histogram instead of being silently dropped (fixes #127 for null-StashID scenes).

    Args:
        stash: StashInterface instance
        settings: Plugin settings dict
        api_key: Stash API key for image URLs

    Returns:
        dict: {"scanned", "processed", "errors", "skipped": {reason: count}}
    """
    scene_filter = build_scene_filter(settings)
    count = stash.find_scenes(
        f=scene_filter,
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.info(f"Found {count} candidate scenes to evaluate")

    summary = {"scanned": 0, "processed": 0, "errors": 0, "skipped": {}}
    skipped = Counter()
    samples = []

    if count == 0:
        log.info("No scenes to process")
        return summary

    processed = 0
    errors = 0
    scanned = 0
    last_id = 0
    page_num = 0

    def note_skip(scene, reason):
        skipped[reason] += 1
        if len(samples) < SKIP_SAMPLE_LIMIT:
            samples.append((scene.get("id", "?"), reason))

    # Cursor paging on id: the run itself can change which scenes match (e.g. marking
    # scenes organized under organized_condition=skip), so offset paging would skip
    # unprocessed scenes. count is for progress display only.
    while True:
        page_num += 1
        page_filter = dict(scene_filter)
        if last_id:
            page_filter["id"] = {"value": last_id, "modifier": "GREATER_THAN"}

        progress = min(scanned / count, 1.0)
        log.info(f"Evaluating scenes after id {last_id} (batch {page_num}, {progress:.0%} of ~{count})")

        scenes = stash.find_scenes(
            f=page_filter,
            filter={"per_page": BATCH_SIZE, "sort": "id", "direction": "ASC"},
        )
        if not scenes:
            break

        max_id = max(int(sc["id"]) for sc in scenes)
        if max_id <= last_id:
            log.error(f"Paging did not advance past scene id {last_id}; stopping to avoid an infinite loop")
            break
        last_id = max_id

        for scene in scenes:
            scanned += 1
            ok, reason = should_process(scene, settings)
            if not ok:
                note_skip(scene, reason)
                log.debug(f"Scene {scene.get('id', '?')} skipped by processing conditions: {reason}")
                continue
            try:
                skip_reason = process_scene(scene, stash, settings, api_key)
            except Exception as err:
                errors += 1
                log.error(f"Error processing scene {scene.get('id', 'unknown')}: {err}")
                continue
            if isinstance(skip_reason, str):
                note_skip(scene, skip_reason)
            else:
                processed += 1

        if len(scenes) < BATCH_SIZE:
            break

    summary["scanned"] = scanned
    summary["processed"] = processed
    summary["errors"] = errors
    summary["skipped"] = dict(skipped)
    summary["samples"] = samples

    for line in format_bulk_summary(summary, dry_run=is_dry_run(settings)):
        log.info(line)
    return summary


def process_scene(scene, stash, settings, api_key):
    """Process a single scene: rename video, generate NFO, copy performer images.

    A dry run takes the same steps on the paths a live run would produce, so the NFO,
    poster and sidecars are reported at their post-move paths.

    Args:
        scene: Scene dict from Stash API
        stash: StashInterface instance
        settings: Plugin settings dict
        api_key: Stash API key for image URLs

    Returns:
        None once processed, or a skip reason (NO_FILES) for a scene that can't be
    """
    scene_id = scene.get("id", "unknown")
    log.debug(f"Processing Scene ID: {scene_id}")

    if not scene.get("files"):
        log.warning(f"Scene {scene_id} has no files; skipping it")
        return NO_FILES

    scene = __hydrate_scene(scene, stash)

    # rename/move video files if settings configured for that; returns the primary
    # video's path after the run (in a dry run, where it would be, with the moves the
    # dry run skipped recorded in pending)
    pending = _PendingMoves()
    target_video_path = __rename_videos(scene, stash, settings, pending)

    folder = os.path.dirname(target_video_path)
    video_count = pending.video_count(folder) if pending.videos else None
    names = artwork_filenames(settings, target_video_path, video_count=video_count)

    # overwrite the nfo at file location (use renamed path if applicable)
    if names["nfo"]:
        __write_nfo(scene, os.path.join(folder, names["nfo"]), settings, target_video_path, api_key,
                    exists=pending.exists)

    # copy any performer images to people directory
    for performer in scene["performers"] or []:
        try:
            process_performer(performer, settings, api_key)
        except Exception as err:
            log.error(f"Error processing performer image for {performer.get('name', 'unknown')}: {err}")

    # download any missing artwork images from stash into path (poster, then backdrop)
    for key in ("poster", "backdrop"):
        if not names[key]:
            continue
        image_path = os.path.join(folder, names[key])
        if not pending.exists(image_path):
            screenshot_url = authenticated_url(scene["paths"]["screenshot"], settings, api_key)
            download_image(screenshot_url, image_path, settings)
    return None


class _PendingMoves:
    """Moves a dry run only logged, so later steps see the folders as a live run leaves them.

    Stays empty in a live run: its moves really happened, so the disk is the truth.
    """

    def __init__(self):
        self.arrived = set()
        self.left = set()
        self.videos = []  # (source, destination) of each video move

    def add(self, source, dest, video=False):
        self.left.add(source)
        self.left.discard(dest)
        self.arrived.discard(source)
        self.arrived.add(dest)
        if video:
            self.videos.append((source, dest))

    def exists(self, path):
        """Whether path would exist after the run."""
        if path in self.arrived:
            return True
        if path in self.left:
            return False
        return os.path.exists(path)

    def video_count(self, folder):
        """Videos folder would hold after the run."""
        count = _count_videos(folder)
        for source, dest in self.videos:
            count += (os.path.dirname(dest) == folder) - (os.path.dirname(source) == folder)
        return count

    def other_videos(self, video_path):
        """Videos besides video_path its folder holds once the moves so far are done."""
        folder = os.path.dirname(video_path)
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        paths = {os.path.join(folder, n) for n in names if is_video(n) and os.path.isfile(os.path.join(folder, n))}
        for source, dest in self.videos:
            paths.discard(source)
            if os.path.dirname(dest) == folder:
                paths.add(dest)
        paths.discard(video_path)
        return len(paths)


def __hydrate_scene(scene, stash):
    fragmented_performers = scene["performers"] or []
    performers = []
    for fragmented_performer in fragmented_performers:
        performer = stash.find_performer(
            fragmented_performer["id"], False, "id name gender image_path"
        )
        if not performer:
            log.debug(
                f"Scene {scene.get('id', '?')}: performer {fragmented_performer['id']} no longer exists; ignoring it"
            )
            continue
        performers.append(performer)
    scene["performers"] = sorted(
        performers,
        key=lambda performer: f"{str(performer.get('gender', 'UNKNOWN'))}_{performer['name']}",
    )

    if scene["studio"]:
        scene["studio"] = __fetch_studio_chain(stash, scene["studio"]["id"])

    return scene


def __fetch_studio_chain(stash, studio_id):
    """Fetch a studio and its parents by following parent_studio.id, one find_studio per level.

    Stops after MAX_STUDIO_DEPTH levels or at a studio already seen (a loop), so a
    bad hierarchy can't hang the plugin. Every returned level has a name.
    """
    root = stash.find_studio(studio_id, STUDIO_FRAGMENT)
    node = root
    seen = {str(studio_id)}
    depth = 1
    while node:
        parent_id = (node.get("parent_studio") or {}).get("id")
        if not parent_id:
            node["parent_studio"] = None
            break
        if str(parent_id) in seen or depth >= MAX_STUDIO_DEPTH:
            problem = (
                f"loops back to studio {parent_id}"
                if str(parent_id) in seen
                else f"is deeper than {MAX_STUDIO_DEPTH} levels"
            )
            log.warning(
                f"Studio hierarchy of studio {studio_id} {problem}; "
                f"ignoring parents above studio {node.get('id')}"
            )
            node["parent_studio"] = None
            break
        seen.add(str(parent_id))
        node["parent_studio"] = stash.find_studio(parent_id, STUDIO_FRAGMENT)
        node = node["parent_studio"]
        depth += 1
    return root


def __rename_videos(scene, stash, settings, pending=None):
    """Rename/move all video files for a scene according to template settings.

    Handles scenes with multiple files. If the template includes file-level variables
    like $Resolution or $Quality, each file may get a unique name. Otherwise, files
    after the first get a suffix like (2), (3), etc.

    Multi-file modes:
    - "all" (default): Process all files, use resolution/suffix to differentiate
    - "primary_only": Only process the first/primary file
    - "skip": Skip scenes that have multiple files entirely

    A dry run makes the same decisions and records the moves it skips in pending.

    Args:
        scene: Hydrated scene dict
        stash: StashInterface instance for GraphQL mutations
        settings: Plugin settings dict
        pending: _PendingMoves that collects a dry run's skipped moves

    Returns:
        str: The primary video's path after the run (renamed or original; in a dry run,
        where it would be) for NFO generation
    """
    files = scene.get("files", [])
    if not files:
        log.warning(f"Scene {scene['id']} has no files")
        return None
    if pending is None:
        pending = _PendingMoves()
    dry_run = is_dry_run(settings)

    if settings["enable_renamer"] is not True:
        log.debug("Skipping renaming because it's disabled in settings")
        return files[0]["path"]

    # Determine how to handle multiple files
    multi_file_mode = settings.get("renamer_multi_file_mode", "all")
    # Options: "all" (default), "primary_only", "skip"

    if len(files) > 1:
        if multi_file_mode == "skip":
            log.info(f"Skipping Scene {scene['id']} because it has {len(files)} files (multi_file_mode=skip)")
            return files[0]["path"]
        elif multi_file_mode == "primary_only":
            log.debug(f"Scene {scene['id']} has {len(files)} files, processing only primary file")
            files_to_process = [files[0]]
        else:  # "all" mode - process all files
            log.debug(f"Scene {scene['id']} has {len(files)} files, processing all")
            files_to_process = files
    else:
        files_to_process = files

    primary_path = None
    original_primary_path = files_to_process[0]["path"]  # Store before loop
    used_paths = set()  # Track paths we've used to detect conflicts
    files_moved = False  # Track if any files were actually moved
    # Videos per source folder before anything moves: folder-level files (movie.nfo,
    # poster.jpg ...) only travel from a folder that held just this one video
    folder_video_counts = {
        os.path.dirname(f["path"]): _count_videos(os.path.dirname(f["path"])) for f in files_to_process
    }

    for idx, file_info in enumerate(files_to_process):
        video_path = file_info["path"]
        file_id = file_info["id"]

        # Create a temporary scene copy with this specific file as primary
        # This allows $Resolution and $Quality to be file-specific
        scene_for_file = scene.copy()
        scene_for_file["files"] = [file_info] + [f for f in files if f != file_info]

        # Calculate expected path for this specific file
        expected_path = get_new_path(
            scene_for_file,
            settings["renamer_path"],
            settings["renamer_path_template"],
            settings.get("renamer_filepath_budget", 250),
        )

        if not expected_path:
            if idx == 0:
                primary_path = video_path
            continue

        # If this path conflicts with one we've already used, add a suffix
        # This handles cases where files have the same resolution
        original_expected = expected_path
        suffix_num = 2
        while expected_path in used_paths:
            base, ext = os.path.splitext(original_expected)
            expected_path = f"{base} ({suffix_num}){ext}"
            suffix_num += 1

        # Check if we should rename this file
        renamer_path = settings.get("renamer_path", IMPOSSIBLE_PATH)
        renamer_ignore_in_path = settings.get("renamer_ignore_files_in_path", False)
        in_target_dir = is_inside(video_path, renamer_path)

        if renamer_ignore_in_path and in_target_dir:
            log.debug(f"Skipping file {idx + 1}: already in target directory")
            if idx == 0:
                primary_path = video_path
            continue

        if expected_path == video_path:
            log.debug(f"Skipping file {idx + 1}: already at expected path")
            used_paths.add(expected_path)
            if idx == 0:
                primary_path = video_path
            continue

        if pending.exists(expected_path):
            log.warning(f"File {idx + 1}: Destination already exists at {expected_path}")
            if idx == 0:
                primary_path = video_path
            continue

        dest_folder = os.path.dirname(expected_path)
        dest_basename = os.path.basename(expected_path)

        # Stash's moveFiles refuses a destination outside its libraries: don't ask it to
        if not __in_a_library(dest_folder, stash, settings):
            if dry_run:
                log.info(f"[DRY RUN] Would be refused by Stash (outside every library): {expected_path}")
            else:
                log.warning(
                    f"Not moving file {idx + 1} of Scene {scene['id']}: {expected_path} is outside "
                    "every Stash library, so Stash would refuse the move"
                )
            if idx == 0:
                primary_path = video_path
            continue

        # Track this path as used
        used_paths.add(expected_path)

        # The scene's other videos own their files even once this run has moved them
        sidecars = __collect_sidecars(video_path, settings, [f["path"] for f in files])

        # In dry run mode, log what would happen but don't actually do anything
        if dry_run:
            log.info(f"[DRY RUN] Would move file {idx + 1}: {video_path}")
            log.info(f"[DRY RUN]                    To: {expected_path}")
            pending.add(video_path, expected_path, video=True)
            __relocate_sidecars(video_path, expected_path, sidecars, settings, pending)
            __relocate_folder_level(video_path, expected_path, folder_video_counts, settings, pending)
            if idx == 0:
                primary_path = expected_path
            continue

        # Use GraphQL moveFiles mutation
        try:
            result = __move_file_graphql(stash, file_id, dest_folder, dest_basename)
            if not result:
                log.error(f"GraphQL moveFiles failed for file {idx + 1} of Scene {scene['id']}")
                if idx == 0:
                    primary_path = video_path
                continue

            log.info(f"Moved file {idx + 1} to: {expected_path}")
            files_moved = True
            if idx == 0:
                primary_path = expected_path
            # Runs before the organized update: that update fires the Scene.Update.Post
            # hook, which must find the NFO and poster already at their new paths.
            __relocate_sidecars(video_path, expected_path, sidecars, settings, pending)
            __relocate_folder_level(video_path, expected_path, folder_video_counts, settings, pending)

        except Exception as err:
            log.error(f"Error moving file {idx + 1} for Scene {scene['id']}: {err}")
            if idx == 0:
                primary_path = video_path
            continue

    # Mark as organized if enabled and files were actually moved
    if files_moved and settings.get("renamer_enable_mark_organized", False) and not dry_run:
        __mark_organized(scene["id"], stash, settings)

    return primary_path or files[0]["path"]


def __collect_sidecars(video_path, settings, other_videos=()):
    """Files to move with a video: all sidecars, or just its NFO and poster when the setting is off."""
    sidecars = find_sidecars(video_path, other_videos)
    if settings.get("renamer_move_sidecars", True):
        return sidecars
    keep = tuple(_render(t, video_path) for t in artwork_templates(settings) if t and not is_folder_level(t))
    return [p for p in sidecars if os.path.basename(p) in keep]


def __relocate_sidecars(video_path, new_video_path, sidecars, settings, pending):
    """Move each sidecar next to the moved video, keeping the part of its name after the stem."""
    old_stem = os.path.splitext(os.path.basename(video_path))[0]
    new_stem = os.path.splitext(os.path.basename(new_video_path))[0]
    new_folder = os.path.dirname(new_video_path)
    for sidecar in sidecars:
        rest = os.path.basename(sidecar)[len(old_stem):]
        dest = os.path.join(new_folder, new_stem + rest)
        __move_with_video(sidecar, dest, "sidecar", settings, pending)


def __relocate_folder_level(video_path, new_video_path, folder_video_counts, settings, pending):
    """Move folder-level files (movie.nfo, poster.jpg ...) with a video that was alone in its folder.

    Only into a folder that then holds just that video: elsewhere they would become
    the folder art of other videos. A dry run counts with the moves it recorded in pending.
    """
    old_folder = os.path.dirname(video_path)
    new_folder = os.path.dirname(new_video_path)
    if old_folder == new_folder or folder_video_counts.get(old_folder) != 1:
        return
    sources = [
        os.path.join(old_folder, template)
        for template in artwork_templates(settings)
        if template and is_folder_level(template) and os.path.isfile(os.path.join(old_folder, template))
    ]
    if not sources:
        return
    if pending.other_videos(new_video_path):
        log.warning(
            f"Not moving folder-level files ({', '.join(os.path.basename(s) for s in sources)}) "
            f"from {old_folder}: {new_folder} holds other videos"
        )
        return
    for source in sources:
        dest = os.path.join(new_folder, os.path.basename(source))
        __move_with_video(source, dest, "folder-level file", settings, pending)


def __move_with_video(source, dest, kind, settings, pending):
    """Move a file that travels with a video, never overwriting.

    A dry run makes the same destination check, then logs the move and records it in pending.
    """
    dry_run = is_dry_run(settings)
    if not dry_run:
        log.debug(f"Relocating {kind}: {source}")
    moved = rename_file(source, dest, settings)
    if moved and dry_run:
        log.info(f"[DRY RUN] Would move {kind}: {source} -> {dest}")
        pending.add(source, dest)
    return moved


def __library_roots(stash):
    """Stash's library folders (configuration.general.stashes[].path), or None if unreadable."""
    try:
        stashes = stash.get_configuration()["general"]["stashes"]
    except Exception as err:
        stashes = None
        problem = f": {err}"
    else:
        problem = ""
    if not isinstance(stashes, list):
        log.warning(
            f"Could not read Stash's library folders{problem}. Moves are not checked against them "
            "(Stash still refuses a move outside every library)."
        )
        return None
    return [s["path"] for s in stashes if isinstance(s, dict) and isinstance(s.get("path"), str) and s["path"]]


def __in_a_library(folder, stash, settings):
    """False if folder is outside every Stash library, where moveFiles refuses to move files.

    The library folders are read once per run and kept in settings["library_roots"].
    When they can't be read nothing is blocked.
    """
    if "library_roots" not in settings:
        settings["library_roots"] = __library_roots(stash)
    roots = settings["library_roots"]
    if roots is None:
        return True
    # Stash compares paths case-insensitively on Windows; normcase is a no-op elsewhere
    folder = os.path.normcase(folder)
    return any(is_inside(folder, os.path.normcase(root)) for root in roots)


def __mark_organized(scene_id, stash, settings):
    """Set organized=true, with a marker so the hook run this fires skips the scene.

    Stash runs the Scene.Update.Post hook inside update_scene, so the marker must exist
    before the call. With the hook off nothing would consume it, so none is written.
    """
    data_dir = settings.get("data_dir")
    marked = settings.get("enable_hook", False) and mark(scene_id, data_dir)
    try:
        stash.update_scene({"id": scene_id, "organized": True})
        log.debug(f"Marked Scene {scene_id} as organized")
    except Exception as err:
        if marked:
            consume(scene_id, data_dir)
        log.warning(f"Failed to mark scene as organized: {err}")


def __move_file_graphql(stash, file_id, dest_folder, dest_basename):
    """Move a file using Stash's GraphQL moveFiles mutation.

    Args:
        stash: StashInterface instance
        file_id: The file ID to move
        dest_folder: Destination folder path
        dest_basename: New filename (with extension)

    Returns:
        bool: True if successful, False otherwise
    """
    mutation = """
        mutation MoveFiles($input: MoveFilesInput!) {
            moveFiles(input: $input)
        }
    """
    variables = {
        "input": {
            "ids": [file_id],
            "destination_folder": dest_folder,
            "destination_basename": dest_basename
        }
    }

    try:
        result = stash.call_GQL(mutation, variables)
        return result.get("moveFiles", False)
    except Exception as err:
        log.error(f"GraphQL moveFiles error: {err}")
        return False


def __write_nfo(scene, filepath, settings, video_path=None, api_key=None, exists=None):
    """Write NFO file for a scene.

    Args:
        scene: Scene dict with metadata
        filepath: Destination path for NFO file
        settings: Plugin settings dict
        video_path: Path to the video file (for poster thumb references)
        api_key: Stash API key; only used to decide whether Plex actor thumb URLs are fetchable
        exists: Tells whether a path exists (default os.path.exists); a dry run passes
            one that knows the moves it skipped
    """
    exists = exists or os.path.exists

    # Check if we should skip existing NFO files
    skip_existing = settings.get("nfo_skip_existing", False)
    if skip_existing and exists(filepath):
        log.debug(f"Skipping existing NFO file: {filepath}")
        return

    try:
        nfo_xml = build_nfo_xml(scene, settings=settings, video_path=video_path, api_key=api_key)
        existed = exists(filepath)

        if is_dry_run(settings):
            log.info(f"[DRY RUN] Would {'update' if existed else 'create'} NFO: {filepath}")
            return

        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(nfo_xml)
        log.info(f"{'Updated' if existed else 'Created'} NFO file: {filepath}")

    except IOError as err:
        log.error(f"Error writing NFO file {filepath}: {err}")
    except Exception as err:
        log.error(f"Error building NFO for scene {scene.get('id', 'unknown')}: {err}")
