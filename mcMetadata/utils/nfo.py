import os


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
    <studio>{studio}</studio>
    {performers}
    <genre>Adult</genre>
    {tags}
    <uniqueid type="stash">{id}</uniqueid>
</movie>"""

    id = scene["id"]
    details = scene["details"] or ""

    title = ""
    if scene["title"] is not None and scene["title"] != "":
        title = scene["title"]
    else:
        title = os.path.basename(os.path.normpath(scene["files"][0]["path"]))

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
        studio = scene["studio"]["name"]

    performers = ""
    i = 0

    for p in scene["performers"]:
        if i != 0:
            performers = performers + "\n    "
        performers = (
            performers
            + """<actor>
        <name>{}</name>
        <role>{}</role>
        <order>{}</order>
        <type>Actor</type>
    </actor>""".format(p["name"], p["name"], i)
        )
        i += 1

    tags = ""
    iTwo = 0
    for t in scene["tags"]:
        if iTwo != 0:
            tags = tags + "\n    "
        tags = tags + """<tag>{}</tag>""".format(t["name"])
        iTwo += 1

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
