import configparser
import sys
import stashapi.log as log
import utils.validators as validators

REQUIRED_SETTINGS = ["dry_run", "enable_hook", "enable_renamer"]
REQUIRED_SETTINGS_IF_RENAMER = [
    "renamer_path",
    "renamer_ignore_files_in_path",
    "renamer_enable_mark_organized",
    "renamer_path_template",
]
SETTINGS_BOOLEANS = [
    "dry_run",
    "enable_hook",
    "enable_renamer",
    "renamer_ignore_files_in_path",
    "renamer_enable_mark_organized",
]
SETTINGS_VALIDATORS = {
    "dry_run": validators.dry_run,
    "enable_hook": validators.enable_hook,
    "enable_renamer": validators.enable_renamer,
    "renamer_ignore_files_in_path": validators.renamer_ignore_files_in_path,
    "renamer_enable_mark_organized": validators.renamer_enable_mark_organized,
    "renamer_filename_budget": validators.renamer_filename_budget,
    "renamer_path": validators.renamer_path,
    "renamer_path_template": validators.renamer_path_template,
}


parser = configparser.ConfigParser()
settings = {}


def update_setting(filepath, key, value):
    try:
        log.debug("Updating setting " + key + " to " + str(value))
        parser.set("settings", key, value)
        with open(filepath, "w") as f:
            parser.write(f)
            log.info(key + " set to " + value)
    except PermissionError as err:
        log.error(f"You don't have the permission to edit settings.ini ({err})")


def read_settings(filepath):
    log.debug("Reading settings file at " + filepath)
    try:
        parser.read(filepath)

        for key, value in parser.items("settings"):
            # coerce booleans to booleans
            if key in SETTINGS_BOOLEANS:
                settings[key] = parser.getboolean("settings", key)
            else:
                settings[key] = value

        __validate_settings()

        return settings
    except Exception as err:
        log.error("Error reading settings file" + str(err))
        sys.exit(1)


def __validate_settings():
    log.debug("Validating settings")
    # check for existence of necessary fields in settings
    try:
        # required fields (will throw if not found)
        for key in REQUIRED_SETTINGS:
            parser["settings"][key]

        # optional fields (only needed if enable_renamer is True)
        if parser["settings"]["enable_renamer"]:
            for key in REQUIRED_SETTINGS_IF_RENAMER:
                parser["settings"][key]
    except KeyError as key:
        log.error(
            str(key)
            + " is not defined in settings.ini, but is needed for this script to proceed"
        )
        sys.exit(1)

    try:
        for key in SETTINGS_VALIDATORS.keys():
            SETTINGS_VALIDATORS[key](settings)
    except Exception as err:
        log.error(str(err))
        sys.exit(1)
