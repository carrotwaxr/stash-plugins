import os
from xml.sax.saxutils import escape

INDENTED_NEWLINE = "\n    "


def escape_xml(text):
    """Escape special XML characters in text.

    Handles: & < > " '

    Args:
        text: String to escape (can be None)

    Returns:
        str: Escaped string, or empty string if input is None
    """
    if text is None:
        return ""
    return escape(str(text), {'"': '&quot;', "'": '&apos;'})


def _get_actor_thumb_path(performer_name, settings):
    """Get the local actor image path for NFO <thumb> tags.

    Only returns a path when actor images are enabled and the media server
    supports external performer images.

    Args:
        performer_name: Performer name (unescaped)
        settings: Plugin settings dict

    Returns:
        str or None: Local path to performer image, or None
    """
    if not settings:
        return None
    if not settings.get("enable_actor_images", False):
        return None

    # Import here to avoid circular imports
    from performer import get_actor_image_path
    return get_actor_image_path(performer_name, settings)


def build_nfo_xml(scene, settings=None, video_path=None):
    """Build NFO XML for a scene.

    Args:
        scene: Scene dict from Stash API
        settings: Plugin settings dict (optional, enables artwork references)
        video_path: Path to the video file (optional, enables poster thumb tag)

    Returns:
        str: NFO XML content
    """
    ret = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
    <name>{title}</name>
    <title>{title}</title>
    <originaltitle>{title}</originaltitle>
    <sorttitle>{title}</sorttitle>
    <criticrating>{custom_rating}</criticrating>
    <rating>{rating}</rating>
    <userrating>{rating}</userrating>
    <plot><![CDATA[{details}]]></plot>
    <premiered>{date}</premiered>
    <releasedate>{date}</releasedate>
    <year>{year}</year>
    <studio>{studio}</studio>{poster_thumb}{performers}
    <genre>Adult</genre>{tags}
    <uniqueid type="stash">{id}</uniqueid>
</movie>"""

    id = scene["id"]
    details = scene["details"] or ""

    title = ""
    if scene["title"] is not None and scene["title"] != "":
        title = escape_xml(scene["title"])
    else:
        title = escape_xml(os.path.basename(os.path.normpath(scene["files"][0]["path"])))

    custom_rating = ""
    rating = ""
    if scene["rating100"] is not None:
        rating = round(int(scene["rating100"]) / 10)
        custom_rating = scene["rating100"]

    date = ""
    year = ""
    if scene["date"] is not None:
        date = scene["date"]
        year = scene["date"].split("-")[0]

    studio = ""
    if scene["studio"] is not None:
        studio = escape_xml(scene["studio"]["name"])

    # Poster thumb tag referencing local poster image
    poster_thumb = ""
    if video_path:
        base = os.path.splitext(os.path.basename(video_path))[0]
        poster_filename = f"{base}-poster.jpg"
        poster_thumb = INDENTED_NEWLINE + f'<thumb aspect="poster">{escape_xml(poster_filename)}</thumb>'

    performers = INDENTED_NEWLINE
    i = 0

    for p in scene["performers"]:
        if i != 0:
            performers = performers + INDENTED_NEWLINE
        performer_name = escape_xml(p["name"])

        # Build optional <thumb> tag for actor image
        actor_thumb = ""
        actor_image_path = _get_actor_thumb_path(p["name"], settings)
        if actor_image_path:
            actor_thumb = "\n        <thumb>{}</thumb>".format(escape_xml(actor_image_path))

        performers = (
            performers
            + """<actor>
        <name>{}</name>
        <role>{}</role>
        <order>{}</order>
        <type>Actor</type>{}
    </actor>""".format(performer_name, performer_name, i, actor_thumb)
        )
        i += 1
    if performers == INDENTED_NEWLINE:
        performers = ""

    tags = INDENTED_NEWLINE
    iTwo = 0
    for t in scene["tags"]:
        if iTwo != 0:
            tags = tags + INDENTED_NEWLINE
        tags = tags + """<tag>{}</tag>""".format(escape_xml(t["name"]))
        iTwo += 1
    if tags == INDENTED_NEWLINE:
        tags = ""

    return ret.format(
        title=title,
        custom_rating=custom_rating,
        rating=rating,
        id=id,
        tags=tags,
        date=date,
        year=year,
        studio=studio,
        performers=performers,
        poster_thumb=poster_thumb,
        details=details,
    )
