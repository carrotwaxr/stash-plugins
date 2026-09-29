import os
import re
from xml.sax.saxutils import escape

import utils.logger as log

# Characters XML 1.0 does not allow: C0 controls except tab/LF/CR, surrogates, U+FFFE/U+FFFF
_XML_ILLEGAL = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

# Keys accepted in nfoExcludeFields. thumb/poster are aliases for the poster <thumb>.
EXCLUDABLE_FIELDS = frozenset({
    "name", "title", "originaltitle", "sorttitle", "criticrating", "rating", "userrating",
    "plot", "premiered", "releasedate", "year", "studio", "genre", "uniqueid",
    "thumb", "poster", "actor", "tag",
})


def xml_safe(text):
    """Remove characters outside XML 1.0's allowed set (None becomes "")."""
    if text is None:
        return ""
    return _XML_ILLEGAL.sub("", str(text))


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
    return escape(xml_safe(text), {'"': '&quot;', "'": '&apos;'})


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
        settings: Plugin settings dict (optional, enables artwork references and field exclusion)
        video_path: Path to the video file (optional, enables poster thumb tag)

    Returns:
        str: NFO XML content
    """
    exclude = set()
    if settings:
        exclude = {str(f).strip().lower() for f in (settings.get("nfo_exclude_fields") or [])}
        unknown = sorted(exclude - EXCLUDABLE_FIELDS)
        if unknown:
            log.warning(
                f"Ignoring unknown nfoExcludeFields: {', '.join(unknown)}. "
                f"Valid: {', '.join(sorted(EXCLUDABLE_FIELDS))}"
            )

    id = scene["id"]
    details = xml_safe(scene["details"]).strip()

    title = xml_safe(scene["title"]).strip()
    if not title:
        title = os.path.basename(os.path.normpath(scene["files"][0]["path"]))
    title = escape_xml(title)

    custom_rating = ""
    rating = ""
    if scene["rating100"] is not None:
        rating = round(int(scene["rating100"]) / 10)
        custom_rating = scene["rating100"]

    date = ""
    year = ""
    if scene["date"]:
        date = escape_xml(scene["date"]).strip()
        year = date.split("-")[0]

    studio = ""
    if scene["studio"] is not None:
        studio = escape_xml(scene["studio"]["name"]).strip()

    # A CDATA section cannot contain "]]>": split it across two sections
    plot_cdata = details.replace("]]>", "]]]]><![CDATA[>")

    lines = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>', '<movie>']

    field_lines = {
        "name": ("title", f"    <name>{title}</name>"),
        "title": ("title", f"    <title>{title}</title>"),
        "originaltitle": ("title", f"    <originaltitle>{title}</originaltitle>"),
        "sorttitle": ("title", f"    <sorttitle>{title}</sorttitle>"),
        "criticrating": (custom_rating, f"    <criticrating>{custom_rating}</criticrating>"),
        "rating": (rating, f"    <rating>{rating}</rating>"),
        "userrating": (rating, f"    <userrating>{rating}</userrating>"),
        "plot": (details, f"    <plot><![CDATA[{plot_cdata}]]></plot>"),
        "premiered": (date, f"    <premiered>{date}</premiered>"),
        "releasedate": (date, f"    <releasedate>{date}</releasedate>"),
        "year": (year, f"    <year>{year}</year>"),
        "studio": (studio, f"    <studio>{studio}</studio>"),
    }

    for field_name, (value, line) in field_lines.items():
        if field_name not in exclude and value != "":
            lines.append(line)

    # Poster thumb (needs video_path)
    if video_path and not exclude & {"thumb", "poster"}:
        base = os.path.splitext(os.path.basename(video_path))[0]
        poster_filename = f"{base}-poster.jpg"
        lines.append(f'    <thumb aspect="poster">{escape_xml(poster_filename)}</thumb>')

    # Performers
    for i, p in enumerate([] if "actor" in exclude else scene["performers"]):
        performer_name = escape_xml(p["name"])
        actor_thumb = ""
        actor_image_path = _get_actor_thumb_path(p["name"], settings)
        if actor_image_path:
            actor_thumb = f"\n        <thumb>{escape_xml(actor_image_path)}</thumb>"

        lines.append(f"""    <actor>
        <name>{performer_name}</name>
        <role>{performer_name}</role>
        <order>{i}</order>
        <type>Actor</type>{actor_thumb}
    </actor>""")

    if "genre" not in exclude:
        lines.append("    <genre>Adult</genre>")

    if "tag" not in exclude:
        for t in scene["tags"]:
            lines.append(f"    <tag>{escape_xml(t['name'])}</tag>")

    if "uniqueid" not in exclude:
        lines.append(f'    <uniqueid type="stash">{id}</uniqueid>')

    lines.append("</movie>")

    return "\n".join(lines)
