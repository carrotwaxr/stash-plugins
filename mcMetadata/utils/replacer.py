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

    return "8k"


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
        return os.path.sep.join(studios)
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


replacers = {
    "$Studio": __replacer_studio,
    "$Studios": __replacer_studios,
    "$StashID": __replacer_stash_id,
    "$Title": __replacer_title,
    "$ReleaseYear": __replacer_release_year,
    "$ReleaseDate": __replacer_release_date,
    "$Resolution": __replacer_resolution,
    "$Quality": __replacer_quality,
    "$FemalePerformers": __replacer_female_performers,
    "$MalePerformers": __replacer_male_performers,
    "$Performers": __replacer_performers,
    "$Tags": __replacer_tags,
}

truncable_replacer_keys = []


def get_new_path(scene, basepath, template, budget):
    try:
        log.debug("Determining what the renamed filepath would be")
        filename = template
        remaining_budget = budget - len(template)
        for key in replacers.keys():
            if key in template:
                value = replacers[key](scene)[:remaining_budget]
                if remaining_budget < 4:
                    value = ""
                filename = filename.replace(key, value)
                remaining_budget = budget - len(filename)
    except ValueError as err:
        log.error(f"Skipping renaming scene {scene["id"]}: {str(err)}")
        return False
    except Exception as err:
        log.error(f"Unexpected error renaming scene {str(err)}")
        return False

    video_path = scene["files"][0]["path"]
    __, ext = os.path.splitext(video_path)
    new_path = basepath + filename + ext
    log.debug(f"New Path: {new_path}")
    return new_path


def __replace_invalid_file_chars(filename):
    safe = re.sub('[<>\\/\?\*"\|]', " ", filename)
    safe = re.sub("[:]", "-", safe)
    safe = re.sub("[&]", "and", safe)
    return safe
