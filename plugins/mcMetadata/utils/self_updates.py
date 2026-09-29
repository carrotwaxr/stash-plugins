"""Recognize mcMetadata's own organized updates when the Scene.Update.Post hook fires.

Stash runs post hooks synchronously: when the renamer marks a scene organized, the
update_scene call blocks while a nested mcMetadata run handles the same scene. Right
before that call the renamer writes a marker file for the scene (mark); the nested run
finds it and skips (should_skip_hook, which consumes it). Once update_scene returns the
nested run is over, so the renamer removes a marker it left, and a later Organized
click by the user is never taken for the plugin's own.

Markers live in <plugin data dir>/self_updates/, one file per scene named after its id,
holding the time it was written. The plugin data dir is <Stash config Dir>/plugin_data/
mcMetadata. When that can't be created, markers go under <system temp>/mcMetadata, and
consume checks both places so either process can fall back on its own.
"""

import os
import tempfile
import time

import utils.logger as log

MARKER_TTL = 30  # seconds
MARKER_FOLDER = "self_updates"
SELF_UPDATE_FIELDS = {"id", "organized"}


def plugin_data_dir(server_connection):
    """<Stash config Dir>/plugin_data/mcMetadata, or None when Stash sent no Dir."""
    stash_dir = (server_connection or {}).get("Dir")
    if not stash_dir:
        return None
    return os.path.join(stash_dir, "plugin_data", "mcMetadata")


def _fallback_dir():
    return os.path.join(tempfile.gettempdir(), "mcMetadata")


def _marker_folders(data_dir):
    """Folders that may hold markers, the preferred one first."""
    folders = [os.path.join(data_dir, MARKER_FOLDER)] if data_dir else []
    fallback = os.path.join(_fallback_dir(), MARKER_FOLDER)
    if fallback not in folders:
        folders.append(fallback)
    return folders


def _marker_name(scene_id):
    """The scene id's digits, so an id can never name a path; None if it has none."""
    digits = "".join(ch for ch in str(scene_id) if ch.isdigit())
    return digits or None


def _purge_expired(folders):
    """Remove files older than MARKER_TTL (markers, and temp or claimed files a crash left)."""
    now = time.time()
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            try:
                if os.path.isfile(path) and abs(now - os.path.getmtime(path)) > MARKER_TTL:
                    os.remove(path)
            except OSError:
                pass


def mark(scene_id, data_dir):
    """Write a marker saying mcMetadata is about to update this scene.

    Expired markers are purged first. Returns the marker path, or None if no marker
    could be written (the nested hook run then processes the scene as it did before
    markers existed).
    """
    name = _marker_name(scene_id)
    if name is None:
        log.warning(f"Not marking scene {scene_id!r}: its id has no digits")
        return None

    _purge_expired(_marker_folders(data_dir))
    for folder in _marker_folders(data_dir):
        path = os.path.join(folder, name)
        tmp_path = os.path.join(folder, f".{name}.{os.getpid()}.tmp")
        try:
            os.makedirs(folder, exist_ok=True)
            with open(tmp_path, "w", encoding="ascii") as f:
                f.write(repr(time.time()))
            os.replace(tmp_path, path)
            return path
        except OSError as err:
            log.debug(f"Could not write self-update marker in {folder}: {err}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    log.warning(f"Could not write a self-update marker for scene {scene_id}; the hook may process it again")
    return None


def consume(scene_id, data_dir):
    """Remove the scene's marker; True if one was there and at most MARKER_TTL old.

    A marker is claimed by renaming it first, so of two runs consuming at once only
    one gets it. Old or unreadable markers are removed and count as absent.
    """
    name = _marker_name(scene_id)
    if name is None:
        return False

    fresh = False
    for folder in _marker_folders(data_dir):
        path = os.path.join(folder, name)
        claimed = f"{path}.{os.getpid()}.claimed"
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            continue
        except OSError as err:
            log.debug(f"Could not claim self-update marker {path}: {err}")
            continue
        try:
            with open(claimed, encoding="ascii") as f:
                written = float(f.read().strip())
            if abs(time.time() - written) <= MARKER_TTL:
                fresh = True
        except (OSError, ValueError) as err:
            log.debug(f"Ignoring unreadable self-update marker {path}: {err}")
        finally:
            try:
                os.remove(claimed)
            except OSError:
                pass
    return fresh


def _is_organized_only_update(hook_context):
    """True if the hook's update could be mcMetadata's own {id, organized: true}.

    - An input that sets organized to anything but true never qualifies.
    - With inputFields: it must be exactly {"id", "organized"}, in any order.
    - Without inputFields: the input itself must be exactly {id, organized: true}.
    """
    update_input = hook_context.get("input")
    if isinstance(update_input, dict) and "organized" in update_input and update_input["organized"] is not True:
        return False

    fields = hook_context.get("inputFields")
    if fields is not None:
        return isinstance(fields, (list, tuple, set)) and set(fields) == SELF_UPDATE_FIELDS

    return (
        isinstance(update_input, dict)
        and set(update_input) == SELF_UPDATE_FIELDS
        and update_input.get("organized") is True
    )


def should_skip_hook(hook_context, data_dir):
    """True if this Scene.Update.Post is mcMetadata's own organized update.

    Skips only when the update touched nothing but organized AND a fresh marker exists
    for the scene; the marker is consumed then, so the user's next Organized click is
    processed. A user edit of more fields leaves the marker alone (the renamer removes
    it when its update_scene returns).
    """
    if not isinstance(hook_context, dict):
        return False
    scene_id = hook_context.get("id")
    if scene_id is None or not _is_organized_only_update(hook_context):
        return False
    return consume(scene_id, data_dir)
