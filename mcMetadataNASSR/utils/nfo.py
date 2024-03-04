import os

def build_nfo_xml(scene):
    ret = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
    <name>{title}</name>
    <title>{title}</title>
    <originaltitle>{title}</originaltitle>
    <sorttitle>{title}</sorttitle>
    <criticrating>{rating}</criticrating>
    <plot><![CDATA[{details}]]></plot>
    <premiered>{date}</premiered>
    <releasedate>{date}</releasedate>
    <studio>{studio}</studio>
    {performers}
    {genres}
    {tags}
    <uniqueid type="stash">{id}</uniqueid>
</movie>"""

    id = scene["id"]
    details = scene["details"] or ""

    title = ""
    if scene["title"] != None and scene["title"] != "":
        title = scene["title"]
    else:
        title = os.path.basename(os.path.normpath(scene["files"][0]["path"]))

    rating = ""
    if scene["rating100"] != None:
        rating = scene["rating100"]

    date = ""
    if scene["date"] != None:
        date = scene["date"]

    studio = ""
    if scene["studio"] != None:
        studio = scene["studio"]["name"]

    performers = ""
    i = 0
    for p in scene["performers"]:
        if i != 0:
            performers = performers + "\n    "
        performers = performers + """<actor>
        <name>{}</name>
        <role>{}</role>
        <order>{}</order>
        <type>Actor</type>
    </actor>""".format(p["name"], p["name"], i)
        i += 1

    genres = ""
    tags = ""
    iTwo = 0
    for t in scene["tags"]:
        if iTwo != 0:
            genres = genres + "\n    "
            tags = tags + "\n    "
        tags = tags + """<tag>{}</tag>""".format(t["name"])
        genres = genres + """<genre>{}</genre>""".format(t["name"])
        iTwo += 1

    return ret.format(title = title, rating = rating, id = id, tags = tags, date = date, studio = studio, performers = performers, details = details, genres = genres)