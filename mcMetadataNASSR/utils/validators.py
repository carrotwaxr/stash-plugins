import os
import re
import stashapi.log as log

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


def __validate_boolean(name, value):
    if value is not False and value is not True:
        raise ValueError(name, "must be True or False")


def dry_run(settings):
    __validate_boolean("dry_run", settings["dry_run"])


def enable_hook(settings):
    __validate_boolean("enable_hook", settings["enable_hook"])


def enable_renamer(settings):
    __validate_boolean("enable_renamer", settings["enable_renamer"])


def renamer_ignore_files_in_path(settings):
    if settings["enable_renamer"]:
        __validate_boolean(
            "renamer_ignore_files_in_path", settings["renamer_ignore_files_in_path"]
        )


def renamer_enable_mark_organized(settings):
    if settings["enable_renamer"]:
        __validate_boolean(
            "renamer_enable_mark_organized", settings["renamer_enable_mark_organized"]
        )


def renamer_filename_budget(settings):
    if settings["renamer_filename_budget"]:
        value = int(settings["renamer_filename_budget"])
        if value < 40 or value > 800:
            raise ValueError(
                "renamer_filename_budget should be a number between 40 and 800"
            )


def renamer_path(settings):
    if settings["enable_renamer"]:
        try:
            if not os.path.exists(settings["renamer_path"]):
                os.makedirs(settings["renamer_path"])
                if not os.path.exists(settings["renamer_path"]):
                    raise SystemError(
                        "Failed to create directory " + settings["renamer_path"]
                    )

        except Exception as err:
            log.error(
                "renamer_path is invalid or you don't have sufficient permissions to this location: "
                + str(err)
            )
            raise SystemError(err)


def renamer_path_template(settings):
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
                "renamer_path_template contains an invalid char. Invalid chars: "
                + INVALID_TEMPLATE_CHARS
            )
