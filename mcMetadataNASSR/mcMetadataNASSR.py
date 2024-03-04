import configparser
import datetime
import json
import os
import re
import sqlite3
import sys
import urllib.request
import stashapi.log as log
from stashapi.stashapp import StashInterface
from utils.nfo import build_nfo_xml

CONFIG_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.ini"
)
PER_PAGE = 100

# start of the Program
json_input = json.loads(sys.stdin.read())
FRAGMENT_SERVER = json_input["server_connection"]
stash = StashInterface(FRAGMENT_SERVER)
PLUGIN_ARGS = json_input["args"]["mode"]

# will get updated on load
config = None
settings_parser = configparser.ConfigParser()

# TODO: Collect data as it runs to provide summary on complete
# TODO: Improve logging
# TODO: Default configs better
# TODO: Documentation
# TODO: filename budget, error if required values not truthy
# TODO: Add more to NFO, test with various media servers
# TODO: File permissions (Stash runs as root)
# TODO: Add additional filename replacers and validate for sufficient uniqueness


def process_all():
    log.info("Getting scene count")
    count = stash.find_scenes(
        f={
            "stash_id_endpoint": {
                "endpoint": "",
                "modifier": "NOT_NULL",
                "stash_id": "",
            }
        },
        filter={"per_page": 1},
        get_count=True,
    )[0]
    log.info(str(count) + " scenes to scan.")
    for r in range(1, int(count / PER_PAGE) + 1):
        log.info("Processing " + str(r * PER_PAGE) + " - " + str(count))
        scenes = stash.find_scenes(
            f={
                "stash_id_endpoint": {
                    "endpoint": "",
                    "modifier": "NOT_NULL",
                    "stash_id": "",
                }
            },
            filter={"page": r, "per_page": PER_PAGE},
        )
        for scene in scenes:
            process_scene(scene)


def process_scene(scene):
    # get primary video file path

    target_video_path = rename_video(scene)

    # overwrite nfo named after file, at file location (use renamed path if applicable)
    nfo_path = __replace_file_ext(target_video_path, "nfo")
    __write_nfo(scene, nfo_path)

    # download any missing artwork images from stash into path
    poster_path = __replace_file_ext(target_video_path, "jpg", "-poster")
    if not os.path.exists(poster_path):
        screenshot_url = scene["paths"]["screenshot"] + "&apikey=" + api_key
        __download_image(screenshot_url, poster_path)


def rename_video(scene):
    # get primary video file path
    video_path = scene["files"][0]["path"]
    expected_path = __get_rename_path(scene)

    # check if we should rename it
    in_target_dir = video_path.startswith(getattr(config, "renamer_path", "$%^&@"))

    do_ignore = False
    if (
        getattr(config, "renamer_ignore_files_in_path", False) is True
        and in_target_dir is True
    ):
        do_ignore = True
    do_rename = config["enable_renamer"] is True and do_ignore is False

    if not do_rename or expected_path == video_path:
        return video_path

    if os.path.exists(expected_path):
        log.info("Duplicate: " + video_path + "\n   =>" + expected_path)
        return video_path

    # rename/move video file and any metadata files, mark organized, update db
    video_renamed_path = __rename_file(video_path, expected_path)
    if not video_renamed_path:
        return video_path

    # locate any existing metadata files, rename them as well
    potential_nfo_path = __replace_file_ext(video_path, "nfo")
    if os.path.exists(potential_nfo_path):
        __rename_file(potential_nfo_path, __replace_file_ext(video_renamed_path, "nfo"))

    potential_poster_path = __replace_file_ext(video_path, "jpg", "-poster")
    if os.path.exists(potential_poster_path):
        __rename_file(
            potential_poster_path,
            __replace_file_ext(video_renamed_path, "jpg", "-poster"),
        )

    try:
        db_rename_refactor(scene["id"], video_path, video_renamed_path)
    except Exception:
        log.error(
            "Error updating database for Scene ID: "
            + scene["id"]
            + ". You can likely resolve this by running a scan on your library."
        )
    return video_renamed_path


def db_rename_refactor(scene_id, old_filepath, new_filepath):
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
            if config["renamer_enable_mark_organized"]:
                cursor.execute(
                    "UPDATE scenes SET organized=? WHERE id=?;", [True, scene_id]
                )
            cursor.execute()
        else:
            raise Exception("Failed to find file_id")
    else:
        raise Exception(
            f"You need to setup a library with the new location ({new_dir}) and scan at least 1 file"
        )


def __download_image(url, dest_filepath):
    if DRY_RUN is False:
        urllib.request.urlretrieve(url, dest_filepath)
    log.debug("Downloading image " + url + " to " + dest_filepath)


def __get_rename_path(scene):
    if config["enable_renamer"] is False:
        return ""
    # resolution
    resolution = str(scene["files"][0]["height"]) + "p"
    video_path = scene["files"][0]["path"]
    __, ext = os.path.splitext(video_path)

    # performers
    female_performers = []
    male_performers = []
    for performer in scene["performers"]:
        performer_name = __replace_invalid_file_chars(performer["name"])
        if performer["gender"] == "FEMALE":
            female_performers.append(performer_name)
        elif performer["gender"] == "MALE":
            male_performers.append(performer_name)
    female_performers = " ".join(female_performers)
    male_performers = " ".join(male_performers)

    template = config["renamer_path_template"]
    filename = template.replace(
        "$Studio", __replace_invalid_file_chars(scene["studio"])
    )
    filename = filename.replace("$Title", __replace_invalid_file_chars(scene["title"]))
    filename = filename.replace(
        "$ReleaseDate", __replace_invalid_file_chars(scene["date"])
    )
    filename = filename.replace("$Resolution", __replace_invalid_file_chars(resolution))
    filename = filename.replace(
        "$MalePerformers", __replace_invalid_file_chars(male_performers)
    )
    filename = filename.replace(
        "$FemalePerformers", __replace_invalid_file_chars(female_performers)
    )
    return config["renamer_path"] + filename + ext


def __load_config():
    log.debug("Loading settings.ini")
    try:
        config = settings_parser.read(CONFIG_FILE_PATH)
    except Exception:
        log.error("Could not load settings.ini")
        sys.exit(1)
    try:
        # validate config
        # required config (will throw if not found)
        config["dry_run"]
        config["enable_hook"]
        # optional config (only needed if enable_renamer is True)
        if config["enable_renamer"]:
            config["renamer_path"]
            config["renamer_ignore_files_in_path"]
            config["renamer_enable_mark_organized"]
            config["renamer_path_template"]

    except KeyError as key:
        log.error(
            str(key)
            + " is not defined in settings.ini, but is needed for this script to proceed"
        )
        sys.exit(1)


def __modify_config(key, value):
    try:
        settings_parser.set("settings", key, value)
        with open(CONFIG_FILE_PATH, "w") as f:
            settings_parser.write(f)
            return True
    except PermissionError as err:
        log.error(f"You don't have the permission to edit settings.ini ({err})")

    return False


def __rename_file(filepath, dest_filepath):
    dir = os.path.dirname(filepath)
    try:
        if not os.path.exists(dir) and DRY_RUN is False:
            os.makedirs(dir)
        try:
            if DRY_RUN is False:
                os.rename(filepath, dest_filepath)
            log.debug("Renamed: " + filepath + "\n   =>" + dest_filepath)
            return dest_filepath
        except Exception:
            log.error("Error renaming file: " + filepath + "\n   =>" + dest_filepath)
            return False
    except Exception:
        log.error("Error creating directory: " + dir)
        return False


def __replace_invalid_file_chars(filename):
    safe = re.sub('[<>\\/\?\*"\|]', " ", filename)
    safe = re.sub("[:]", "-", safe)
    safe = re.sub("[&]", "and", safe)
    return safe


def __replace_file_ext(filepath, ext, suffix=""):
    path = os.path.splitext(filepath)
    return path + suffix + "." + ext


def __write_nfo(scene, filepath):
    nfo_xml = build_nfo_xml(scene)
    if DRY_RUN is False:
        f = open(filepath, "w", encoding="utf-8-sig")
        f.write(nfo_xml)
        f.close()
    log.debug("Updated NFO file: " + filepath)


# modify config if applicable
__load_config()
if PLUGIN_ARGS:
    log.debug("--Starting Plugin 'Renamer'--")
    if "bulk" not in PLUGIN_ARGS:
        if "enable" in PLUGIN_ARGS:
            log.info("Enable hook")
            success = __modify_config("enable_hook", True)
        elif "disable" in PLUGIN_ARGS:
            log.info("Disable hook")
            success = __modify_config("enable_hook", False)
        elif "dryrun" in PLUGIN_ARGS:
            if config["dry_run"]:
                log.info("Disable dryrun")
                success = __modify_config("dry_run", False)
            else:
                log.info("Enable dryrun")
                success = __modify_config("dry_run", True)
        elif "renamer" in PLUGIN_ARGS:
            if config["enable_renamer"]:
                log.info("Disable renamer")
                success = __modify_config("enable_renamer", False)
            else:
                log.info("Enable renamer")
                success = __modify_config("enable_renamer", True)
        if not success:
            log.error("Failed to modify the config value")
        log.info("Config value modified")
        sys.exit(0)
else:
    # bail if hook should not run
    if not config["enable_hook"]:
        log.debug("Hook disabled")
        sys.exit(0)
    log.debug("--Starting Hook 'Renamer'--")


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
DRY_RUN = config["dry_run"]
if PLUGIN_ARGS:
    if "bulk" in PLUGIN_ARGS:
        process_all()
else:
    try:
        scene_id = json_input["args"]["hookContext"]["id"]
        scene = stash.find_scene(scene_id)
        stash_ids = scene["stash_ids"]
        if stash_ids is not None and len(stash_ids) > 0:
            process_scene(scene)
    except Exception as err:
        log.error(f"Hook error: {err}")

# commit db changes & cleanup
if DRY_RUN is False:
    sqliteConnection.commit()
cursor.close()
sqliteConnection.close()
