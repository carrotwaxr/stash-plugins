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


def build_nfo_xml(scene):
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
    <studio>{studio}</studio>{performers}
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

    performers = INDENTED_NEWLINE
    i = 0

    for p in scene["performers"]:
        if i != 0:
            performers = performers + INDENTED_NEWLINE
        performer_name = escape_xml(p["name"])
        performers = (
            performers
            + """<actor>
        <name>{}</name>
        <role>{}</role>
        <order>{}</order>
        <type>Actor</type>
    </actor>""".format(performer_name, performer_name, i)
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
        details=details,
    )
