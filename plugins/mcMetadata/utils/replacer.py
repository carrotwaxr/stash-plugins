import os
import re
import stashapi.log as log


def __replacer_female_performers(scene):
    female_performers = []
    for performer in scene["performers"]:
        if performer["gender"] == "FEMALE":
            performer_name = __replace_invalid_file_chars(performer["name"])
            female_performers.append(performer_name)
    return " ".join(female_performers)


def __replacer_male_performers(scene):
    male_performers = []
    for performer in scene["performers"]:
        if performer["gender"] == "MALE":
            performer_name = __replace_invalid_file_chars(performer["name"])
            male_performers.append(performer_name)
    return " ".join(male_performers)


def __replacer_performers(scene):
    performers = []
    for performer in scene["performers"]:
        performer_name = __replace_invalid_file_chars(performer["name"])
        performers.append(performer_name)
    return " ".join(performers)


def __replacer_quality(scene):
    height = scene["files"][0]["height"]

    if not height:
        raise ValueError("No file height value")

    if height < 480:
        return "LOW"

    if height < 720:
        return "SD"

    if height < 1080:
        return "HD"

    if height < 1440:
        if scene["files"][0]["width"] < 2048:
            return "FHD"
        else:
            return "2K"

    if height < 2160:
        return "QHD"

    if height < 4320:
        return "UHD"

    return "FUHD"


def __replacer_release_date(scene):
    if scene["date"]:
        return scene["date"]
    else:
        raise ValueError("No date value")


def __replacer_release_year(scene):
    if scene["date"]:
        return scene["date"].split("-")[0]
    else:
        raise ValueError("No date value")


def __replacer_resolution(scene):
    height = scene["files"][0]["height"]

    if not height:
        raise ValueError("No file height value")

    if height < 480:
        return str(scene["files"][0]["height"]) + "p"

    if height < 720:
        return "480p"

    if height < 1080:
        return "720p"

    if height < 1440:
        return "1080p"

    if height < 2160:
        return "1440p"

    if height < 4320:
        return "4K"

    return "8K"


def __replacer_stash_id(scene):
    if len(scene["stash_ids"]):
        return scene["stash_ids"][0]["stash_id"]
    else:
        raise ValueError("No stash_id value")


def __replacer_studio(scene):
    if scene["studio"]:
        return __replace_invalid_file_chars(scene["studio"]["name"])
    else:
        raise ValueError("No studio value")


def __replacer_studios(scene):
    if scene["studio"]:
        studios = []
        i = 0

        cur_node = scene["studio"]
        while i == 0:
            studios.append(__replace_invalid_file_chars(cur_node["name"]))
            if not cur_node["parent_studio"]:
                i = 1
            else:
                cur_node = cur_node["parent_studio"]
        studios.reverse()

        sep = os.path.sep
        if sep == "\\":
            sep = "\\\\"
        result = sep.join(studios)
        return result
    else:
        raise ValueError("No studio value")


def __replacer_tags(scene):
    tags = []
    for tag in scene["tags"]:
        tag_name = __replace_invalid_file_chars(tag["name"])
        tags.append(tag_name)
    return " ".join(tags)


def __replacer_title(scene):
    if scene["title"]:
        return __replace_invalid_file_chars(scene["title"])
    else:
        raise ValueError("No title value")


truncable_replacers = {
    # order here matters. they are arranged in order of priority
    "$FemalePerformers": __replacer_female_performers,
    "$MalePerformers": __replacer_male_performers,
    "$Performers": __replacer_performers,
    "$Tags": __replacer_tags,
}
replacers = {
    # these replacers are not truncable. they will throw an error if the filepath budget cannot be managed
    "$StashID": __replacer_stash_id,
    "$Studios": __replacer_studios,
    "$Studio": __replacer_studio,
    "$Title": __replacer_title,
    "$ReleaseDate": __replacer_release_date,
    "$ReleaseYear": __replacer_release_year,
    "$Resolution": __replacer_resolution,
    "$Quality": __replacer_quality,
}
# add truncable_replacers to replacers
replacers.update(truncable_replacers)


def __get_replacer_regex(key):
    return r"\$" + key[1:] + r"(?=[^a-zA-Z]|$)"


def get_new_path(scene, basepath, template, budget):
    try:
        log.debug("Determining what the renamed filepath would be")

        video_path = scene["files"][0]["path"]
        __, ext = os.path.splitext(video_path)
        # subtract the file extension from our budget
        budget -= len(ext)

        if len(basepath) + len(template) > budget:
            raise ValueError(
                "Your renamer_path and renamer_path_template exceed your renamer_filename_budget"
            )

        replacer_values = {}
        keys_len = 0
        values_len = 0

        for key in replacers.keys():
            if re.search(__get_replacer_regex(key), template) is not None:
                value = replacers[key](scene)
                replacer_values[key] = value
                keys_len += len(key)
                values_len += len(value)

        truncable_len = 0
        for key in replacer_values.keys():
            if key in truncable_replacers.keys():
                truncable_len += len(replacer_values[key])

        budget_remaining = (
            budget - len(basepath) - ((len(template) - keys_len) + values_len)
        )
        if (budget_remaining + truncable_len) < 0:
            raise ValueError(
                "Filepath would exceed your renamer_filename_budget. If your system allows, consider raising the value. Windows systems can now have their filepath limitation increased, a quick search will yield instructions for doing this. If the value cannot be increased, consider adjusting your renamer_path_template or the Scene title if applicable."
            )

        filename = template

        for key in replacer_values.keys():
            value = replacer_values[key]

            if key in truncable_replacers.keys():
                trunced = value[:budget_remaining]
                budget_remaining -= len(trunced)
                value = trunced

            filename = re.sub(__get_replacer_regex(key), value, filename)

    except ValueError as err:
        log.error(f"Skipping renaming Scene ID {scene['id']}: {str(err)}")
        return False
    except Exception as err:
        log.error(f"Unexpected error renaming Scene ID {scene['id']}:{str(err)}")
        return False

    new_path = basepath + __trim_filename(filename) + ext
    log.debug(f"New Path: {new_path}")
    return new_path


def __trim_filename(filename):
    empty_brackets_removed = re.sub(r"\[\]", "", filename)
    empty_parens_removed = re.sub(r"\(\)", "", empty_brackets_removed)
    multiple_spaces_replaced = re.sub(r"\s{2,}", " ", empty_parens_removed)
    multiple_hyphens_removed = re.sub(r"-{2,}", "-", multiple_spaces_replaced)

    return multiple_hyphens_removed.strip()


def __replace_invalid_file_chars(filename):
    safe = re.sub(r'[<>\\/\?\*"\|]', " ", filename)
    safe = re.sub(r"[:]", "-", safe)
    safe = re.sub(r"[&]", "and", safe)
    return safe
