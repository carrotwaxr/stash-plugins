import configparser
from datetime import datetime
import json
import os
import sqlite3
import sys
import urllib.request
import stashapi.log as log
from stashapi.stashapp import StashInterface
from utils.nfo import build_nfo_xml
from utils.renamer import get_new_path
from utils.settings import read_settings, update_setting

# json context payload passed to us from Stash when any plugin is triggered
json_input = json.loads(sys.stdin.read())
# initialize INI reader
settings_parser = configparser.ConfigParser()
# initialize Stash API module
stash = StashInterface(json_input["server_connection"])

# Constants
BATCH_SIZE = 100
IMPOSSIBLE_PATH = "$%^&@"
PLUGIN_ARGS = json_input["args"]
SETTINGS_FILEPATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.ini"
)
SETTINGS = read_settings(SETTINGS_FILEPATH)

QUERY_WHERE_STASH_ID_NOT_NULL = {
    "stash_id_endpoint": {
        "endpoint": "",
        "modifier": "NOT_NULL",
        "stash_id": "",
    }
}

# TODO:
#   Collect data as it runs to provide summary on complete; Improve logging
#   Documentation
#   Test with various media servers
#       Emby - performer images
#       Jellyfin - performer images
#       Plex
#   Check all modes, settings and replacers
#   Write unit tests?
#   Add more config settings to allow users to customize behavior more
#   New notifications


def process_all():
    log.info("Getting scene count")
    count = stash.find_scenes(
        f=QUERY_WHERE_STASH_ID_NOT_NULL,
        filter={"per_page": 1},
        get_count=True,
    )[0]
    log.info(str(count) + " scenes to scan.")
    for r in range(1, int(count / BATCH_SIZE) + 1):
        start = r * BATCH_SIZE
        end = start + BATCH_SIZE
        log.info("Processing " + str(start) + " - " + str(end))
        scenes = stash.find_scenes(
            f=QUERY_WHERE_STASH_ID_NOT_NULL,
            filter={"page": r, "per_page": BATCH_SIZE},
        )
        for scene in scenes:
            process_scene(scene)


def process_scene(scene):
    try:
        log.debug("Processing Scene " + scene["id"])
        scene = hydrate_scene(scene)
        # rename/move primary video file if settings configured for that
        # if not, function will just return the current path and we'll proceed with that
        target_video_path = rename_video(scene)

        # overwrite nfo named after file, at file location (use renamed path if applicable)
        log.debug("Updating NFO file with scene metadata")
        nfo_path = __replace_file_ext(target_video_path, "nfo")
        __write_nfo(scene, nfo_path)

        # download any missing artwork images from stash into path
        log.debug("Downloading Poster image")
        poster_path = __replace_file_ext(target_video_path, "jpg", "-poster")
        if not os.path.exists(poster_path):
            screenshot_url = scene["paths"]["screenshot"] + "&apikey=" + api_key
            __download_image(screenshot_url, poster_path)
    except Exception as err:
        log.error("Error processing Scene " + scene["id"] + ": " + str(err))


def hydrate_scene(scene):
    fragmented_performers = scene["performers"] or []
    performers = []
    for fragmented_performer in fragmented_performers:
        performer = stash.find_performer(
            fragmented_performer["id"], False, "id name gender"
        )
        performers.append(performer)
    scene["performers"] = sorted(
        performers,
        key=lambda performer: str(performer.get("gender", "UNKNOWN"))
        + performer["name"],
    )

    if scene["studio"]:
        scene["studio"] = stash.find_studio(
            scene["studio"]["id"], "id name parent_studio { ...Studio }"
        )

    return scene


def rename_video(scene):
    # get primary video file path
    video_path = scene["files"][0]["path"]

    if SETTINGS["enable_renamer"] is not True:
        log.debug("Skipping renaming because it's disabled in settings")
        return video_path

    expected_path = get_new_path(
        scene,
        SETTINGS["renamer_path"],
        SETTINGS["renamer_path_template"],
        SETTINGS.get("renamer_filepath_budget", 250),
    )

    if expected_path is False:
        return video_path

    # check if we should rename it
    renamer_path = SETTINGS.get("renamer_path", IMPOSSIBLE_PATH)
    renamer_ignore_in_path = SETTINGS.get("renamer_ignore_files_in_path", False)
    in_target_dir = video_path.startswith(renamer_path)

    do_ignore = False
    if renamer_ignore_in_path is True and in_target_dir is True:
        do_ignore = True

    if do_ignore is True or expected_path == video_path:
        log.debug("Skipping renaming because file is already organized")
        return video_path

    if os.path.exists(expected_path):
        log.info("Duplicate: " + video_path + "\n   =>" + expected_path)
        return video_path

    # rename/move video file. Will return False if it errors
    video_renamed_path = __rename_file(video_path, expected_path)
    if not video_renamed_path:
        return video_path

    # update database with new file location
    try:
        db_rename_refactor(scene["id"], video_path, video_renamed_path)
    except Exception as err:
        log.error(
            "Error updating database for Scene "
            + str(scene["id"])
            + ": "
            + str(err)
            + "\nYou can likely resolve this by running a scan on your library."
        )

    # locate any existing metadata files, rename them as well
    potential_nfo_path = __replace_file_ext(video_path, "nfo")
    if os.path.exists(potential_nfo_path):
        log.debug("Relocating existing NFO file: " + potential_nfo_path)
        __rename_file(potential_nfo_path, __replace_file_ext(video_renamed_path, "nfo"))

    potential_poster_path = __replace_file_ext(video_path, "jpg", "-poster")
    if os.path.exists(potential_poster_path):
        log.debug("Relocating existing Poster image: " + potential_poster_path)
        __rename_file(
            potential_poster_path,
            __replace_file_ext(video_renamed_path, "jpg", "-poster"),
        )

    return video_renamed_path


def db_rename_refactor(scene_id, old_filepath, new_filepath):
    log.debug(
        "Updating database for Scene "
        + str(scene_id)
        + ". Old Path: "
        + old_filepath
        + ", New Path: "
        + new_filepath
    )
    old_dir = os.path.dirname(old_filepath)
    new_dir = os.path.dirname(new_filepath)
    new_filename = os.path.basename(new_filepath)
    # 2022-09-17T11:25:52+02:00
    mod_time = datetime.now().astimezone().isoformat("T", "seconds")

    # get the next id that we should use if needed
    cursor.execute("SELECT MAX(id) from folders")
    new_id = cursor.fetchall()[0][0] + 1

    # get the old folder id
    cursor.execute("SELECT id FROM folders WHERE path=?", [old_dir])
    old_folder_id = cursor.fetchall()[0][0]

    # check if the folder of file is created in db
    cursor.execute("SELECT id FROM folders WHERE path=?", [new_dir])
    folder_id = cursor.fetchall()
    if not folder_id:
        dir = new_dir
        # reduce the path to find a parent folder
        for _ in range(1, len(new_dir.split(os.sep))):
            dir = os.path.dirname(dir)
            cursor.execute("SELECT id FROM folders WHERE path=?", [dir])
            parent_id = cursor.fetchall()
            if parent_id:
                # create a new row with the new folder with the parent folder find above
                cursor.execute(
                    "INSERT INTO 'main'.'folders'('id', 'path', 'parent_folder_id', 'mod_time', 'created_at', 'updated_at', 'zip_file_id') VALUES (?, ?, ?, ?, ?, ?, ?);",
                    [
                        new_id,
                        new_dir,
                        parent_id[0][0],
                        mod_time,
                        mod_time,
                        mod_time,
                        None,
                    ],
                )
                folder_id = new_id
                break
    else:
        folder_id = folder_id[0][0]
    if folder_id:
        cursor.execute(
            "SELECT file_id from scenes_files WHERE scene_id=?",
            [scene_id],
        )
        file_ids = cursor.fetchall()
        file_id = None
        for f in file_ids:
            # it can have multiple file for a scene
            cursor.execute("SELECT parent_folder_id from files WHERE id=?", [f[0]])
            check_parent = cursor.fetchall()[0][0]
            # if the parent id is the one found above section, we find our file.s
            if check_parent == old_folder_id:
                file_id = f[0]
                break
        if file_id:
            cursor.execute(
                "UPDATE files SET basename=?, parent_folder_id=?, updated_at=? WHERE id=?;",
                [new_filename, folder_id, mod_time, file_id],
            )
            if SETTINGS["renamer_enable_mark_organized"]:
                cursor.execute(
                    "UPDATE scenes SET organized=? WHERE id=?;", [True, scene_id]
                )
        else:
            raise Exception("Failed to find file_id")
    else:
        raise Exception(
            f"You need to setup a library with the new location ({new_dir}) and scan at least 1 file"
        )
    log.debug("Database updated")


def __download_image(url, dest_filepath):
    if DRY_RUN is False:
        urllib.request.urlretrieve(url, dest_filepath)
        log.debug("Downloading image " + url + " to " + dest_filepath)


def __rename_file(filepath, dest_filepath):
    dir = os.path.dirname(dest_filepath)
    try:
        if not os.path.exists(dir) and DRY_RUN is False:
            os.makedirs(dir)
        try:
            if DRY_RUN is False:
                os.rename(filepath, dest_filepath)
                log.debug("Renamed: " + filepath + " => " + dest_filepath)
            return dest_filepath
        except Exception as err:
            log.error(
                "Error renaming file: "
                + str(err)
                + ". File "
                + filepath
                + " => "
                + dest_filepath
            )
            return False
    except Exception as d_err:
        log.error("Error creating directory " + dir + ": " + str(d_err))
        return False


def __replace_file_ext(filepath, ext, suffix=""):
    path = os.path.splitext(filepath)
    return path[0] + suffix + "." + ext


def __write_nfo(scene, filepath):
    try:
        nfo_xml = build_nfo_xml(scene)
        if DRY_RUN is False:
            f = open(filepath, "w", encoding="utf-8-sig")
            f.write(nfo_xml)
            f.close()
            log.info("Updated NFO file: " + filepath)
    except Exception as err:
        log.error("Error writing NFO: " + str(err))


# if triggered via one of the plugin tasks in the UI
mode = PLUGIN_ARGS.get("mode", None)
log.debug("mode: " + str(mode))
if mode:
    log.debug("--Starting Plugin 'Renamer'--")
    if "bulk" not in mode:
        if "enable" in mode:
            log.info("Enabling Scene Update hook")
            update_setting(SETTINGS_FILEPATH, "enable_hook", "true")
        elif "disable" in mode:
            log.info("Disabling Scene Update hook")
            update_setting(SETTINGS_FILEPATH, "enable_hook", "false")
        elif "dryrun" in mode:
            if SETTINGS["dry_run"]:
                log.info("Disable dryrun")
                update_setting(SETTINGS_FILEPATH, "dry_run", "false")
            else:
                log.info("Enable dryrun")
                update_setting(SETTINGS_FILEPATH, "dry_run", "true")
        elif "renamer" in mode:
            if SETTINGS["enable_renamer"]:
                log.info("Disable renamer")
                update_setting(SETTINGS_FILEPATH, "enable_renamer", "false")
            else:
                log.info("Enable renamer")
                update_setting(SETTINGS_FILEPATH, "enable_renamer", "true")

        sys.exit(0)


# establish db connection
try:
    stash_config = stash.get_configuration()["general"]
    api_key = stash_config["apiKey"]
    sqliteConnection = sqlite3.connect(stash_config["databasePath"])
    cursor = sqliteConnection.cursor()
    log.debug("Python successfully connected to SQLite")
except sqlite3.Error as error:
    log.error("FATAL SQLITE Error: ", error)
    sys.exit(1)

# determine which controller function to run
DRY_RUN = SETTINGS["dry_run"]
log.debug("DRY RUN: " + str(DRY_RUN))
if mode:
    # if running Bulk task action
    if "bulk" in mode:
        process_all()
else:
    # if triggered via Scene Update hook

    # bail if hook should not run
    if not SETTINGS["enable_hook"]:
        log.debug("Hook disabled")
        sys.exit(0)

    scene_id = json_input["args"]["hookContext"]["id"]
    scene = stash.find_scene(scene_id)
    stash_ids = scene["stash_ids"]
    if stash_ids is not None and len(stash_ids) > 0:
        process_scene(scene)

# commit db changes & cleanup
if DRY_RUN is False:
    log.debug("Committing database changes")
    sqliteConnection.commit()
cursor.close()
sqliteConnection.close()
