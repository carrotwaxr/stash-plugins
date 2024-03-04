import configparser
import sys
import stashapi.log as log

settings = configparser.ConfigParser()


def update_setting(filepath, key, value):
    try:
        log.debug("Updating setting " + key + " to " + value)
        settings.set("settings", key, value)
        with open(filepath, "w") as f:
            settings.write(f)
            log.info(key + " set to " + value)
    except PermissionError as err:
        log.error(f"You don't have the permission to edit settings.ini ({err})")
    return settings


def read_settings(filepath):
    log.debug("Reading settings file at " + filepath)
    try:
        settings.read(filepath)
        # validate config values
        try:
            # required config (will throw if not found)
            settings["dry_run"]
            settings["enable_hook"]
            # optional config (only needed if enable_renamer is True)
            if settings["enable_renamer"]:
                settings["renamer_path"]
                settings["renamer_ignore_files_in_path"]
                settings["renamer_enable_mark_organized"]
                settings["renamer_path_template"]

            return settings
        except KeyError as key:
            log.error(
                str(key)
                + " is not defined in settings.ini, but is needed for this script to proceed"
            )
            sys.exit(1)
    except Exception:
        log.error("Error reading settings file")
        sys.exit(1)
