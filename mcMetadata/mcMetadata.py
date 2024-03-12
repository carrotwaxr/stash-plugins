import json
import os
import sqlite3
import sys
import stashapi.log as log
from stashapi.stashapp import StashInterface
from performer import process_performer
from scene import process_scene
from utils.settings import read_settings, update_setting

# json context payload passed to us from Stash when any plugin is triggered
json_input = json.loads(sys.stdin.read())
# initialize Stash API module
stash = StashInterface(json_input["server_connection"])

# Constants
BATCH_SIZE = 100
PLUGIN_ARGS = json_input["args"]
SETTINGS_FILEPATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.ini"
)
SETTINGS = read_settings(SETTINGS_FILEPATH)
SETTINGS_MODES = ["disable", "dryrun", "enable", "renamer"]

QUERY_WHERE_STASH_ID_NOT_NULL = {
    "stash_id_endpoint": {
        "endpoint": "",
        "modifier": "NOT_NULL",
        "stash_id": "",
    }
}

# TODO:
#   Collect data as it runs to provide summary on complete; Improve logging, new notifications
#   Add more config settings to allow users to customize behavior more
#   Test with various media servers
#       Emby
#           performer images: save to /config/metadata/people/k/Karlie Montana/folder.jpg
#       Jellyfin
#           performer images: save to /config/data/metadata/People/K/Karlie Montana/folder.jpg
#       Plex
#   Check all modes, settings and replacers
#   Write unit tests?
#   Documentation
#   Playlist creator
#   Performer Image Sync
#       Need to know which media server(s) they're using
#       Can create a bulk job for this (overwrite)
#       Can also be ran by Scene Updater
#       Hooks?: on Performer Delete, on Performer Update (overwrite)


def process_all_scenes():
    count = stash.find_scenes(
        f=QUERY_WHERE_STASH_ID_NOT_NULL,
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.debug(f"{str(count)} scenes to scan.")

    for r in range(1, int(count / BATCH_SIZE) + 1):
        start = r * BATCH_SIZE
        end = start + BATCH_SIZE
        if end > count:
            end = count

        log.debug(f"Processing {str(start)}-{str(end)}")

        scenes = stash.find_scenes(
            f=QUERY_WHERE_STASH_ID_NOT_NULL,
            filter={"page": r, "per_page": BATCH_SIZE},
        )

        for scene in scenes:
            process_scene(scene, stash, SETTINGS, cursor, api_key)


def process_all_performers():
    count = stash.find_performers(
        f={},
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.debug(f"{str(count)} performers to scan.")

    for r in range(1, int(count / BATCH_SIZE) + 1):
        start = r * BATCH_SIZE
        end = start + BATCH_SIZE
        if end > count:
            end = count

        log.debug(f"Processing {str(start)}-{str(end)}")

        performers = stash.find_performers(
            f={},
            filter={"page": r, "per_page": BATCH_SIZE},
        )

        for performer in performers:
            process_performer(performer, SETTINGS, api_key, True)


def __get_plugin_mode():
    mode = PLUGIN_ARGS.get("mode", None)
    hook_context = PLUGIN_ARGS.get("hookContext", None)

    if mode is None and hook_context is None:
        raise ValueError("Invalid plugin args")

    return mode or hook_context["type"]


# if triggered via one of the plugin tasks in the UI
mode = __get_plugin_mode()
log.debug(f"Initializing plugin with args: {str(PLUGIN_ARGS)}")
DRY_RUN = SETTINGS["dry_run"]
if mode in SETTINGS_MODES:
    match mode:
        case "enable":
            log.info("Enabling hooks")
            update_setting(SETTINGS_FILEPATH, "enable_hook", "true")
        case "disable":
            log.info("Disabling hooks")
            update_setting(SETTINGS_FILEPATH, "enable_hook", "false")
        case "dryrun":
            if DRY_RUN is True:
                log.info("Disabling dry run")
                update_setting(SETTINGS_FILEPATH, "dry_run", "false")
            else:
                log.info("Enabling dry run")
                update_setting(SETTINGS_FILEPATH, "dry_run", "true")
        case "renamer":
            if SETTINGS["enable_renamer"] is True:
                log.info("Disabling renamer")
                update_setting(SETTINGS_FILEPATH, "enable_renamer", "false")
            else:
                log.info("Enabling renamer")
                update_setting(SETTINGS_FILEPATH, "enable_renamer", "true")
    sys.exit(0)


# establish db connection
try:
    stash_config = stash.get_configuration()["general"]
    api_key = stash_config["apiKey"]
    sqliteConnection = sqlite3.connect(stash_config["databasePath"])
    cursor = sqliteConnection.cursor()
    log.debug("Successfully connected to database")
except sqlite3.Error as error:
    log.error("FATAL SQLITE Error: ", error)
    sys.exit(1)

log.debug(f"Dry Run: {str(DRY_RUN)}")

match mode:
    case "bulk":
        log.info("Running bulk scene updater")
        process_all_scenes()
    case "performers":
        log.info("Running bulk performer updater")
        process_all_performers()
    case "Scene.Update.Post":
        if not SETTINGS["enable_hook"]:
            log.debug("Hook disabled")
            sys.exit(0)

        scene_id = PLUGIN_ARGS["hookContext"]["id"]
        scene = stash.find_scene(scene_id)
        stash_ids = scene["stash_ids"]
        if stash_ids is not None and len(stash_ids) > 0:
            log.info("Running scene updater")
            process_scene(scene, stash, SETTINGS, cursor, api_key)


# commit db changes & cleanup
if DRY_RUN is False:
    log.debug("Committing database changes")
    sqliteConnection.commit()
cursor.close()
sqliteConnection.close()
