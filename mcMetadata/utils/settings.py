import configparser
import os
import re
import sys
import stashapi.log as log

REQUIRED_SETTINGS = ["dry_run", "enable_actor_images", "enable_hook", "enable_renamer"]
REQUIRED_SETTINGS_IF_RENAMER = [
    "renamer_path",
    "renamer_ignore_files_in_path",
    "renamer_enable_mark_organized",
    "renamer_path_template",
]
REQUIRED_SETTINGS_IF_ACTORS = [
    "media_server",
    "actor_metadata_path",
]
SETTINGS_BOOLEANS = [
    "dry_run",
    "enable_actor_images",
    "enable_hook",
    "enable_renamer",
    "renamer_ignore_files_in_path",
    "renamer_enable_mark_organized",
]
VALID_MEDIA_SERVERS = ["emby", "jellyfin"]


def __validate_boolean(name, value):
    if value is not False and value is not True:
        raise ValueError(name, "must be True or False")


def validate_actor_metadata_path(settings):
    if settings["enable_actor_images"]:
        if not os.path.exists(settings["actor_metadata_path"]):
            raise SystemError(
                "actor_metadata_path is invalid or you don't have sufficient permissions to this location"
            )


def validate_dry_run(settings):
    __validate_boolean("dry_run", settings["dry_run"])


def validate_enable_actor_images(settings):
    __validate_boolean("enable_actor_images", settings["enable_actor_images"])


def validate_enable_hook(settings):
    __validate_boolean("enable_hook", settings["enable_hook"])


def validate_enable_renamer(settings):
    __validate_boolean("enable_renamer", settings["enable_renamer"])


def validate_media_server(settings):
    if settings["enable_actor_images"]:
        if settings["media_server"] not in VALID_MEDIA_SERVERS:
            raise ValueError(
                f"Valid media_server values are: {str(VALID_MEDIA_SERVERS)}"
            )


def validate_renamer_ignore_files_in_path(settings):
    if settings["enable_renamer"]:
        __validate_boolean(
            "renamer_ignore_files_in_path", settings["renamer_ignore_files_in_path"]
        )


def validate_renamer_enable_mark_organized(settings):
    if settings["enable_renamer"]:
        __validate_boolean(
            "renamer_enable_mark_organized", settings["renamer_enable_mark_organized"]
        )


def validate_renamer_filename_budget(settings):
    if settings["renamer_filename_budget"]:
        value = int(settings["renamer_filename_budget"])
        if value < 40 or value > 800:
            raise ValueError(
                "renamer_filename_budget should be a number between 40 and 800"
            )


def validate_renamer_path(settings):
    if settings["enable_renamer"]:
        try:
            if not os.path.exists(settings["renamer_path"]):
                os.makedirs(settings["renamer_path"])
                if not os.path.exists(settings["renamer_path"]):
                    raise SystemError(
                        f"Failed to create directory {settings["renamer_path"]}"
                    )

        except Exception as err:
            log.error(
                f"renamer_path is invalid or you don't have sufficient permissions to this location: {str(err)}"
            )
            raise SystemError(err)


def validate_renamer_path_template(settings):
    if settings["enable_renamer"]:
        is_valid_uniqueness = False
        for uniquenessKeys in VALID_TEMPLATE_UNIQUENESS:
            has_all = True
            for key in uniquenessKeys:
                if key not in settings["renamer_path_template"]:
                    has_all = False
                    break
            if has_all is True:
                is_valid_uniqueness = True
                break
        if is_valid_uniqueness is False:
            raise ValueError("renamer_path_template does not meet the uniqueness rules")

        stripped_template = settings["renamer_path_template"]
        for key in VALID_TEMPLATE_VARS:
            stripped_template = stripped_template.replace(key, "")

        if INVALID_TEMPLATE_REGEX.search(stripped_template):
            raise ValueError(
                f"renamer_path_template contains an invalid char. Invalid chars: {str(INVALID_TEMPLATE_CHARS)}"
            )


SETTINGS_VALIDATORS = {
    "actor_metadata_path": validate_actor_metadata_path,
    "dry_run": validate_dry_run,
    "enable_actor_images": validate_enable_actor_images,
    "enable_hook": validate_enable_hook,
    "enable_renamer": validate_enable_renamer,
    "media_server": validate_media_server,
    "renamer_ignore_files_in_path": validate_renamer_ignore_files_in_path,
    "renamer_enable_mark_organized": validate_renamer_enable_mark_organized,
    "renamer_filename_budget": validate_renamer_filename_budget,
    "renamer_path": validate_renamer_path,
    "renamer_path_template": validate_renamer_path_template,
}

# renamer template constants
INVALID_SEP = "\\" if os.path.sep == "/" else "/"
INVALID_TEMPLATE_CHARS = "".join(["<", ">", ":", '"', INVALID_SEP, "|", "?", "*"])
INVALID_TEMPLATE_REGEX = re.compile(r"[" + re.escape(INVALID_TEMPLATE_CHARS) + r"]")

VALID_TEMPLATE_VARS = [
    "$FemalePerformers",
    "$MalePerformers",
    "$Performers",
    "$Quality",
    "$ReleaseDate",
    "$ReleaseYear",
    "$Resolution",
    "$StashID",
    "$Studio",
    "$Studios",
    "$Tags",
    "$Title",
]

VALID_TEMPLATE_UNIQUENESS = [
    ["$StashID"],
    ["$Studio", "$Title", "$ReleaseDate"],
    ["$Studios", "$Title", "$ReleaseDate"],
]


parser = configparser.ConfigParser()
settings = {}


def update_setting(filepath, key, value):
    try:
        log.debug(f"Updating setting {key} to {str(value)}")
        parser.set("settings", key, value)
        with open(filepath, "w") as f:
            parser.write(f)
            log.info(f"{key} set to {value}")
    except PermissionError as err:
        log.error(f"You don't have the permission to edit settings.ini ({err})")


def read_settings(filepath):
    log.debug(f"Reading settings file at {filepath}")
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
        log.error(f"Error reading settings file {str(err)}")
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

        # optional fields (only needed if enable_actor_images is True)
        if parser["settings"]["enable_actor_images"]:
            for key in REQUIRED_SETTINGS_IF_ACTORS:
                parser["settings"][key]
    except KeyError as key:
        log.error(
            f"{str(key)} is not defined in settings.ini, but is needed for this script to proceed"
        )
        sys.exit(1)

    try:
        for key in SETTINGS_VALIDATORS.keys():
            SETTINGS_VALIDATORS[key](settings)
    except Exception as err:
        log.error(str(err))
        sys.exit(1)
