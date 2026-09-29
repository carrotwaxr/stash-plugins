import math
import os
import re
import utils.logger as log
from utils.paths import fit_bytes, join_under, sanitize_component

# Largest file or folder name, in UTF-8 bytes, on common filesystems (ext4, NTFS, APFS)
MAX_COMPONENT_BYTES = 255
# Truncable values are never cut shorter than this many characters
MIN_TRUNCATED_CHARS = 1


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
    """The studio chain, root first, one sanitized folder per level, joined by os.sep.

    get_new_path splits the rendered template on separators, so each level becomes its
    own path component. The value is inserted literally (no re.sub), so the Windows
    separator needs no escaping. A loop in the chain stops at the first repeated studio.
    """
    if not scene["studio"]:
        raise ValueError("No studio value")

    studios = []
    seen = set()
    cur_node = scene["studio"]
    while cur_node:
        node_key = cur_node.get("id") or id(cur_node)
        if node_key in seen:
            break
        seen.add(node_key)
        if not cur_node.get("name"):
            raise ValueError(f"Studio {cur_node.get('id', '?')} in the studio hierarchy has no name")
        studios.append(__replace_invalid_file_chars(cur_node["name"]))
        cur_node = cur_node.get("parent_studio")
    studios.reverse()

    return os.sep.join(studios)


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
    # order here matters: when the path is over budget, these are shortened in this
    # order (see get_new_path)
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

# Every known $Key in one alternation, longest first so $Studios wins over $Studio.
# The lookahead keeps $Titles or $StudioX from matching a shorter key.
_KEY_PATTERN = re.compile(
    r"\$("
    + "|".join(sorted((re.escape(key[1:]) for key in replacers), key=len, reverse=True))
    + r")(?![A-Za-z])"
)


def __render_value(key, scene):
    """A replacer's value, sanitized as (part of) one path component.

    $Studios is already one sanitized component per studio level, joined by os.sep.
    Empty values stay empty (no '_' placeholder) so the template collapses around them.
    """
    value = replacers[key](scene)
    if key == "$Studios" or not value:
        return value
    return sanitize_component(value)


def __template_keys(template):
    """Each $Key occurrence in the template, in order (repeats included)."""
    return ["$" + match.group(1) for match in _KEY_PATTERN.finditer(template)]


def __render(template, values):
    """Substitute every $Key in ONE pass: substituted text is never scanned again."""
    return _KEY_PATTERN.sub(lambda match: values["$" + match.group(1)], template)


def resolve_conditionals(template, scene, substitute=True):
    """Resolve conditional blocks in a template string.

    Syntax: {literal$Variableliteral} — the entire block (including literal
    text) is included only if ALL $Variables inside resolve to non-empty values.
    If any variable raises ValueError or resolves empty, the whole block is removed.

    Blocks without any $Variables are left as-is (treated as literal braces).

    Args:
        template: Template string potentially containing {conditional} blocks
        scene: Scene dict for variable resolution
        substitute: If False, a kept block keeps its $Variables unexpanded (only the
            braces go), so get_new_path can render the whole template in one pass.

    Returns:
        str: Template with conditional blocks resolved
    """
    def _resolve_block(match):
        block_content = match.group(1)

        # Find all $Variables in this block
        var_matches = re.findall(r'\$[A-Za-z]+', block_content)
        if not var_matches:
            # No variables — treat braces as literal
            return match.group(0)

        # Check each variable resolves to a non-empty value
        values = {}
        for var in var_matches:
            if var not in replacers:
                return ""  # Unknown variable — remove block
            try:
                value = __render_value(var, scene)
            except (ValueError, KeyError):
                return ""  # Variable can't resolve — remove block
            if not value:
                return ""
            values[var] = value

        if not substitute:
            return block_content
        return __render(block_content, values)

    return re.sub(r'\{([^}]*\$[A-Za-z][^}]*)\}', _resolve_block, template)


def get_new_path(scene, basepath, template, budget):
    """The renamed path for the scene's primary file, or None if it can't be renamed.

    - The template is rendered in one pass, so metadata containing "$Key" text is
      never expanded again.
    - Each value is sanitized as a single path component. Only the template's own
      "/" (and os.sep) and the $Studios chain create folders; an empty folder collapses.
    - The result is joined under basepath and can't escape it. An empty basepath
      means no rename.
    - budget (renamerFilepathBudget) is the total path length in characters,
      extension included. Only the overflow is trimmed, from the truncable values.
      Each component is also capped at MAX_COMPONENT_BYTES bytes of UTF-8.

    Errors are logged; the return value is then None.
    """
    try:
        log.debug("Determining what the renamed filepath would be")

        if not basepath:
            raise ValueError("renamerPath (Renamer Base Path) is empty")
        budget = __coerce_budget(budget)

        video_path = scene["files"][0]["path"]
        __, ext = os.path.splitext(video_path)

        # Drop unresolvable conditional blocks; kept blocks are rendered below
        template = resolve_conditionals(template, scene, substitute=False)

        keys = __template_keys(template)
        values = {key: __render_value(key, scene) for key in keys}

        new_path = __build_path(basepath, template, values, ext)
        overflow = len(new_path) - budget

        # Trim only the overflow. Truncable values are cut from their end in
        # truncable_replacers order (FemalePerformers, MalePerformers, Performers,
        # Tags), the order the earlier code cut them in. Each keeps at least
        # MIN_TRUNCATED_CHARS characters, and we stop as soon as the path fits.
        for key in truncable_replacers:
            if overflow <= 0:
                break
            if key not in values:
                continue
            original = values[key]
            occurrences = keys.count(key)
            keep = len(original)
            while overflow > 0 and keep > MIN_TRUNCATED_CHARS:
                keep -= min(math.ceil(overflow / occurrences), keep - MIN_TRUNCATED_CHARS)
                values[key] = sanitize_component(original[:keep])
                new_path = __build_path(basepath, template, values, ext)
                overflow = len(new_path) - budget

        if overflow > 0:
            raise ValueError(
                f"Filepath would exceed your renamerFilepathBudget ({len(new_path)} > {budget} characters). If your system allows, consider raising the value. Windows systems can now have their filepath limitation increased, a quick search will yield instructions for doing this. If the value cannot be increased, consider adjusting your renamerPathTemplate or the Scene title if applicable."
            )

    except ValueError as err:
        log.error(f"Skipping renaming Scene ID {scene['id']}: {str(err)}")
        return None
    except Exception as err:
        log.error(f"Unexpected error renaming Scene ID {scene['id']}:{str(err)}")
        return None

    log.debug(f"New Path: {new_path}")
    return new_path


def __coerce_budget(budget):
    """renamerFilepathBudget as an int (Stash NUMBER settings can arrive as floats)."""
    try:
        return int(float(budget))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"renamerFilepathBudget must be a number, not {budget!r}") from None


def __build_path(basepath, template, values, ext):
    """Render the template, split it into components and join them under basepath."""
    rendered = __render(template, values)
    separators = "|".join(re.escape(sep) for sep in {"/", os.sep})
    parts = [__trim_filename(part) for part in re.split(separators, rendered)]
    *folders, filename = parts
    if not filename.strip(". "):
        raise ValueError("renamerPathTemplate renders an empty filename")
    # an empty folder (e.g. "$Tags/" with no tags) collapses, as the old "//" did
    components = [__fit_component(folder) for folder in folders if folder]
    components.append(__fit_component(filename, ext))
    return join_under(basepath, *components)


def __fit_component(name, suffix=""):
    """Cap one component at MAX_COMPONENT_BYTES bytes of UTF-8, keeping suffix intact."""
    full = name + suffix
    fitted = fit_bytes(full, MAX_COMPONENT_BYTES, keep_suffix=suffix)
    if fitted == full:
        return full
    # the cut can leave a trailing space or dot, which Windows drops
    return fitted[: len(fitted) - len(suffix)].rstrip(" .") + suffix


def __trim_filename(filename):
    """Tidy one rendered path component.

    Drops empty [] and (), collapses runs of spaces and hyphens, and strips spaces
    around it and dots at its end (Windows drops trailing dots and spaces). A component
    of only dots is left alone so join_under rejects a "../" in the template.
    """
    empty_brackets_removed = re.sub(r"\[\]", "", filename)
    empty_parens_removed = re.sub(r"\(\)", "", empty_brackets_removed)
    multiple_spaces_replaced = re.sub(r"\s{2,}", " ", empty_parens_removed)
    multiple_hyphens_removed = re.sub(r"-{2,}", "-", multiple_spaces_replaced)

    trimmed = multiple_hyphens_removed.strip()
    if trimmed.strip("."):
        trimmed = trimmed.rstrip(" .")
    return trimmed


def __replace_invalid_file_chars(filename):
    """Sanitize a performer, studio, tag or title name as one path component.

    '&' becomes 'and' only here, i.e. only for those names, as before 1.6.0. Other
    values ($StashID, dates, ...) get sanitize_component alone, which keeps '&'.
    """
    return sanitize_component(filename.replace("&", "and"))
