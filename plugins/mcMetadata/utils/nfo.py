import os
import re
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import utils.logger as log
from utils.videos import is_video

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


def _is_plex(settings):
    return bool(settings) and str(settings.get("media_server") or "").strip().lower() == "plex"


# --- configurable NFO / artwork filenames -------------------------------------------

DEFAULT_NFO_NAME = "{basename}.nfo"
DEFAULT_POSTER_NAME = "{basename}-poster.jpg"
PLEX_FANART_NAME = "{basename}-fanart.jpg"
BASENAME_PLACEHOLDER = "{basename}"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
RATING_FIELD_CHOICES = ("both", "rating", "userrating")
_warned = set()


def _warn_once(message):
    if message not in _warned:
        _warned.add(message)
        log.warning(message)


def _valid_name(value, setting, extensions, default):
    """A filename template if it is usable, else `default` (with a warning).

    Usable: no path separator, ends in one of `extensions`. Empty means unset.
    """
    value = str(value or "").strip()
    if not value:
        return default
    if "/" in value or "\\" in value:
        _warn_once(f"Ignoring {setting} {value!r}: it must be a file name, not a path")
        return default
    if not value.lower().endswith(extensions):
        _warn_once(f"Ignoring {setting} {value!r}: it must end in {' or '.join(extensions)}")
        return default
    return value


def render_filename(template, video_path):
    """The file name template gives for video_path: {basename} becomes the video's stem."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    return template.replace(BASENAME_PLACEHOLDER, stem)


def is_folder_level(template):
    """A name without {basename} is shared by every video in the folder."""
    return BASENAME_PLACEHOLDER not in template


def count_videos(folder):
    """Video files directly in folder (0 if it can't be listed)."""
    try:
        return sum(
            1 for n in os.listdir(folder)
            if is_video(n) and os.path.isfile(os.path.join(folder, n))
        )
    except OSError:
        return 0


def artwork_templates(settings):
    """(nfo, poster, backdrop) filename templates from settings; backdrop may be None.

    In plex mode the backdrop falls back to `{basename}-fanart.jpg`; elsewhere it is
    off unless backdropFilename is set.
    """
    settings = settings or {}
    nfo = _valid_name(settings.get("nfo_filename"), "nfoFilename", (".nfo",), DEFAULT_NFO_NAME)
    poster = _valid_name(
        settings.get("poster_filename"), "posterFilename", IMAGE_EXTENSIONS, DEFAULT_POSTER_NAME
    )
    backdrop = _valid_name(settings.get("backdrop_filename"), "backdropFilename", IMAGE_EXTENSIONS, None)
    if backdrop is None and _is_plex(settings):
        backdrop = PLEX_FANART_NAME
    return nfo, poster, backdrop


def artwork_filenames(settings, video_path, warn=True, video_count=None):
    """Rendered {"nfo", "poster", "backdrop"} file names for a video (None = don't write).

    Folder-level names (no {basename}: movie.nfo, poster.jpg, folder.jpg ...) are only
    safe when the folder holds a single video, so they are None otherwise. video_count
    overrides the number of videos counted in the folder (a dry run passes the count
    the folder would have after its moves).
    """
    nfo, poster, backdrop = artwork_templates(settings)
    result = {}
    folder = os.path.dirname(video_path) if video_path else ""
    for key, template in (("nfo", nfo), ("poster", poster), ("backdrop", backdrop)):
        if template is not None and is_folder_level(template) and video_count is None:
            video_count = count_videos(folder)
        if template is None:
            result[key] = None
        elif is_folder_level(template) and video_count > 1:
            if warn:
                _warn_once(
                    f"Skipping {template} in {folder}: a folder-level file name needs exactly "
                    f"one video per folder, and this folder has more"
                )
            result[key] = None
        else:
            result[key] = render_filename(template, video_path)
    return result


def _endpoint_type(endpoint):
    """uniqueid type for a stash-box endpoint URL: stashdb, theporndb, fansdb, else the host."""
    host = (urlparse(endpoint or "").hostname or "").lower()
    if not host:
        return ""
    if host == "stashdb.org" or host.endswith(".stashdb.org"):
        return "stashdb"
    if host == "theporndb.net" or host.endswith(".theporndb.net"):
        return "theporndb"
    if host.startswith("fansdb."):
        return "fansdb"
    return host


def _plex_actor_thumb_url(performer, settings, api_key):
    """Stash performer image URL for a Plex actor <thumb>, or None.

    Plex's NFO provider only loads actor images from URLs, and never with
    credentials, so the URL (with no API key) is only usable when Stash has no
    API key configured.
    """
    if not settings.get("enable_actor_images", False) or api_key:
        return None
    return performer.get("image_path") or None


def build_nfo_xml(scene, settings=None, video_path=None, api_key=None):
    """Build NFO XML for a scene.

    Args:
        scene: Scene dict from Stash API
        settings: Plugin settings dict (optional, enables artwork references and field exclusion)
        video_path: Path to the video file (optional, enables poster thumb tag)
        api_key: Stash API key (optional; plex mode omits actor thumb URLs when set)

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

    rating_field = str((settings or {}).get("nfo_rating_field") or "both").strip().lower()
    if rating_field not in RATING_FIELD_CHOICES:
        _warn_once(
            f"Ignoring nfoRatingField {rating_field!r}: use one of {', '.join(RATING_FIELD_CHOICES)}"
        )
        rating_field = "both"
    if rating_field == "rating":
        exclude.add("userrating")
    elif rating_field == "userrating":
        exclude.add("rating")

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

    director = escape_xml(scene.get("director")).strip()

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
        "director": (director, f"    <director>{director}</director>"),
    }

    for field_name, (value, line) in field_lines.items():
        if field_name not in exclude and value != "":
            lines.append(line)

    # Poster thumb (needs video_path)
    if video_path and not exclude & {"thumb", "poster"}:
        poster_filename = artwork_filenames(settings, video_path, warn=False)["poster"]
        if poster_filename:
            lines.append(f'    <thumb aspect="poster">{escape_xml(poster_filename)}</thumb>')

    # Performers
    for i, p in enumerate([] if "actor" in exclude else scene["performers"]):
        performer_name = escape_xml(p["name"])
        actor_thumb = ""
        if _is_plex(settings):
            actor_image_path = _plex_actor_thumb_url(p, settings, api_key)
        else:
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
        lines.append(f'    <uniqueid type="stash" default="true">{id}</uniqueid>')
        theporndb_ids = []
        for sid in scene.get("stash_ids") or []:
            box = _endpoint_type(sid.get("endpoint"))
            value = escape_xml(sid.get("stash_id")).strip()
            if not box or not value:
                continue
            lines.append(f'    <uniqueid type="{escape_xml(box)}">{value}</uniqueid>')
            if box == "theporndb":
                theporndb_ids.append(value)
        for value in theporndb_ids[:1]:
            lines.append(f"    <theporndbid>{value}</theporndbid>")

    lines.append("</movie>")

    return "\n".join(lines)
