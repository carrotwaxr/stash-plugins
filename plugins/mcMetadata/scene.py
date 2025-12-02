import os
import stashapi.log as log
from performer import process_performer
from utils.files import download_image, rename_file, replace_file_ext
from utils.nfo import build_nfo_xml
from utils.replacer import get_new_path

BATCH_SIZE = 100
IMPOSSIBLE_PATH = "$%^&@"
QUERY_WHERE_STASH_ID_NOT_NULL = {
    "stash_id_endpoint": {
        "endpoint": "",
        "modifier": "NOT_NULL",
        "stash_id": "",
    }
}


def process_all_scenes(stash, settings, api_key):
    """Process all scenes that have a StashID.

    Args:
        stash: StashInterface instance
        settings: Plugin settings dict
        api_key: Stash API key for image URLs
    """
    count = stash.find_scenes(
        f=QUERY_WHERE_STASH_ID_NOT_NULL,
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.info(f"Found {count} scenes with StashIDs to process")

    if count == 0:
        log.info("No scenes to process")
        return

    # Calculate total pages (ceiling division)
    total_pages = (count + BATCH_SIZE - 1) // BATCH_SIZE
    processed = 0
    errors = 0

    # Pages are 1-indexed in Stash API
    for page in range(1, total_pages + 1):
        start = (page - 1) * BATCH_SIZE + 1
        end = min(page * BATCH_SIZE, count)

        log.info(f"Processing scenes {start}-{end} of {count} (page {page}/{total_pages})")

        scenes = stash.find_scenes(
            f=QUERY_WHERE_STASH_ID_NOT_NULL,
            filter={"page": page, "per_page": BATCH_SIZE},
        )

        for scene in scenes:
            try:
                process_scene(scene, stash, settings, api_key)
                processed += 1
            except Exception as err:
                errors += 1
                log.error(f"Error processing scene {scene.get('id', 'unknown')}: {err}")

    log.info(f"Bulk scene update complete. Processed: {processed}, Errors: {errors}")


def process_scene(scene, stash, settings, api_key):
    """Process a single scene: rename video, generate NFO, copy performer images.

    Args:
        scene: Scene dict from Stash API
        stash: StashInterface instance
        settings: Plugin settings dict
        api_key: Stash API key for image URLs
    """
    scene_id = scene.get("id", "unknown")
    log.debug(f"Processing Scene ID: {scene_id}")

    scene = __hydrate_scene(scene, stash)

    # rename/move video files if settings configured for that
    # if not, function will just return the current path and we'll proceed with that
    target_video_path = __rename_videos(scene, stash, settings)

    # overwrite nfo named after file, at file location (use renamed path if applicable)
    nfo_path = replace_file_ext(target_video_path, "nfo")
    __write_nfo(scene, nfo_path, settings)

    # copy any performer images to people directory
    for performer in scene["performers"] or []:
        try:
            process_performer(performer, settings, api_key)
        except Exception as err:
            log.error(f"Error processing performer image for {performer.get('name', 'unknown')}: {err}")

    # download any missing artwork images from stash into path
    poster_path = replace_file_ext(target_video_path, "jpg", "-poster")
    if not os.path.exists(poster_path):
        screenshot_url = f"{scene['paths']['screenshot']}&apikey={api_key}"
        download_image(screenshot_url, poster_path, settings)


def __hydrate_scene(scene, stash):
    fragmented_performers = scene["performers"] or []
    performers = []
    for fragmented_performer in fragmented_performers:
        performer = stash.find_performer(
            fragmented_performer["id"], False, "id name gender image_path"
        )
        performers.append(performer)
    scene["performers"] = sorted(
        performers,
        key=lambda performer: f"{str(performer.get('gender', 'UNKNOWN'))}_{performer['name']}",
    )

    if scene["studio"]:
        scene["studio"] = stash.find_studio(
            scene["studio"]["id"], "id name parent_studio { ...Studio }"
        )

    return scene


def __rename_videos(scene, stash, settings):
    """Rename/move all video files for a scene according to template settings.

    Handles scenes with multiple files. If the template includes file-level variables
    like $Resolution or $Quality, each file may get a unique name. Otherwise, files
    after the first get a suffix like (2), (3), etc.

    Multi-file modes:
    - "all" (default): Process all files, use resolution/suffix to differentiate
    - "primary_only": Only process the first/primary file
    - "skip": Skip scenes that have multiple files entirely

    Args:
        scene: Hydrated scene dict
        stash: StashInterface instance for GraphQL mutations
        settings: Plugin settings dict

    Returns:
        str: The primary video path (renamed or original) for NFO generation
    """
    files = scene.get("files", [])
    if not files:
        log.warning(f"Scene {scene['id']} has no files")
        return None

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

        if expected_path is False:
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
        in_target_dir = video_path.startswith(renamer_path)

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

        if os.path.exists(expected_path):
            log.warning(f"File {idx + 1}: Destination already exists at {expected_path}")
            if idx == 0:
                primary_path = video_path
            continue

        # Track this path as used
        used_paths.add(expected_path)

        # In dry run mode, log what would happen but don't actually do anything
        if settings["dry_run"]:
            log.info(f"[DRY RUN] Would move file {idx + 1}: {video_path}")
            log.info(f"[DRY RUN]                    To: {expected_path}")
            if idx == 0:
                primary_path = video_path
            continue

        # Use GraphQL moveFiles mutation
        dest_folder = os.path.dirname(expected_path)
        dest_basename = os.path.basename(expected_path)

        try:
            result = __move_file_graphql(stash, file_id, dest_folder, dest_basename)
            if not result:
                log.error(f"GraphQL moveFiles failed for file {idx + 1} of Scene {scene['id']}")
                if idx == 0:
                    primary_path = video_path
                continue

            log.info(f"Moved file {idx + 1} to: {expected_path}")
            if idx == 0:
                primary_path = expected_path

        except Exception as err:
            log.error(f"Error moving file {idx + 1} for Scene {scene['id']}: {err}")
            if idx == 0:
                primary_path = video_path
            continue

    # Mark as organized if enabled (only once per scene)
    if settings.get("renamer_enable_mark_organized", False) and not settings["dry_run"]:
        try:
            stash.update_scene({"id": scene["id"], "organized": True})
            log.debug(f"Marked Scene {scene['id']} as organized")
        except Exception as err:
            log.warning(f"Failed to mark scene as organized: {err}")

    # Relocate metadata files for primary video
    if primary_path and primary_path != original_primary_path:
        potential_nfo_path = replace_file_ext(original_primary_path, "nfo")
        if os.path.exists(potential_nfo_path):
            log.debug(f"Relocating existing NFO file: {potential_nfo_path}")
            rename_file(
                potential_nfo_path, replace_file_ext(primary_path, "nfo"), settings
            )

        potential_poster_path = replace_file_ext(original_primary_path, "jpg", "-poster")
        if os.path.exists(potential_poster_path):
            log.debug(f"Relocating existing Poster image: {potential_poster_path}")
            rename_file(
                potential_poster_path,
                replace_file_ext(primary_path, "jpg", "-poster"),
                settings,
            )

    return primary_path or files[0]["path"]


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


def __write_nfo(scene, filepath, settings):
    """Write NFO file for a scene.

    Args:
        scene: Scene dict with metadata
        filepath: Destination path for NFO file
        settings: Plugin settings dict
    """
    # Check if we should skip existing NFO files
    skip_existing = settings.get("nfo_skip_existing", False)
    if skip_existing and os.path.exists(filepath):
        log.debug(f"Skipping existing NFO file: {filepath}")
        return

    try:
        nfo_xml = build_nfo_xml(scene)

        if settings["dry_run"]:
            if os.path.exists(filepath):
                log.info(f"[DRY RUN] Would update NFO: {filepath}")
            else:
                log.info(f"[DRY RUN] Would create NFO: {filepath}")
            return

        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(nfo_xml)
        log.info(f"{'Updated' if os.path.exists(filepath) else 'Created'} NFO file: {filepath}")

    except IOError as err:
        log.error(f"Error writing NFO file {filepath}: {err}")
    except Exception as err:
        log.error(f"Error building NFO for scene {scene.get('id', 'unknown')}: {err}")
