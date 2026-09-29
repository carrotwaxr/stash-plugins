#!/usr/bin/env python3
"""
Scene Matcher - Find StashDB matches using known attributes.
Searches StashDB for scenes matching a local scene's performers and/or studio.

Uses only Python standard library - no pip dependencies.
"""

import calendar
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from collections import namedtuple

import log
import plugin_data

# Import resilient StashDB API utilities
import stashbox_api

# Only for the plugin's own Stash server, which runs on this host. With HTTPS its cert
# names a public host while we connect via localhost, so hostname checks would fail.
# Stash-box requests go through stashbox_api, which verifies certificates.
SSL_CONTEXT = stashbox_api.create_ssl_context(verify=False)

# Threshold for falling back to individual performer/studio queries
# If combined query returns fewer than this many results, also try separate queries
MIN_COMBINED_RESULTS_THRESHOLD = stashbox_api.MIN_COMBINED_RESULTS_THRESHOLD
DEFAULT_MAX_RESULTS = 50
MIN_MAX_RESULTS = 10
MAX_MAX_RESULTS = 500


# ============================================================================
# Local Stash API
# ============================================================================

_stash_connection = None
_input_data = None


def normalize_endpoint(url):
    """Canonical form for comparing endpoints: trimmed, no trailing slash, lowercase."""
    return (url or "").strip().rstrip("/").lower()


def _strip_graphql(url):
    n = normalize_endpoint(url)
    return n[:-len("/graphql")] if n.endswith("/graphql") else n


def site_base(endpoint):
    """Site base URL for links: the text before /graphql, with no trailing slash
    (the UI appends "/scenes/<id>")."""
    m = re.match(r"(https?://.*?/)graphql", endpoint or "")
    return (m.group(1) if m else endpoint or "").rstrip("/")


def resolve_endpoint(requested, boxes, setting):
    """Pick a configured stash-box. Returns (box, None) or (None, error message).

    Order: the endpoint sent by the UI (must be a configured box, never called
    otherwise), then the plugin setting, then the first configured box.
    """
    if not boxes:
        return None, "No stash-box endpoints configured in Stash settings"
    available = ", ".join(b.get("name") or b.get("endpoint", "") for b in boxes)

    want = normalize_endpoint(requested)
    if want:
        for box in boxes:
            if normalize_endpoint(box.get("endpoint")) == want:
                return box, None
        return None, f"Stash-box endpoint '{requested}' is not configured in Stash. Available: {available}"

    pref = normalize_endpoint(setting)
    if pref:
        for box in boxes:
            if normalize_endpoint(box.get("endpoint")) == pref:
                return box, None
        for box in boxes:
            if _strip_graphql(box.get("endpoint")) == _strip_graphql(pref):
                return box, None
        return None, f"Configured stash-box endpoint '{setting}' not found. Available: {available}"

    return boxes[0], None


def get_stash_connection():
    """Get Stash connection details from plugin input."""
    global _stash_connection, _input_data

    if _stash_connection is not None:
        return _stash_connection

    try:
        if _input_data is None:
            _input_data = json.loads(sys.stdin.read())
        server_connection = _input_data.get("server_connection", {})
        # Handle 0.0.0.0 binding - can't connect TO 0.0.0.0, use localhost instead
        host = server_connection.get("Host", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        _stash_connection = {
            "url": server_connection.get("Scheme", "http") + "://" +
                   host + ":" +
                   str(server_connection.get("Port", 9999)) + "/graphql",
            "api_key": server_connection.get("SessionCookie", {}).get("Value"),
        }
        return _stash_connection
    except Exception as e:
        log.LogError(f"Failed to get Stash connection: {e}")
        _stash_connection = {"url": "http://localhost:9999/graphql", "api_key": None}
        return _stash_connection


def get_input_data():
    """Get the full input data from stdin (cached)."""
    global _input_data
    if _input_data is None:
        _input_data = json.loads(sys.stdin.read())
    return _input_data


def stash_graphql(query, variables=None):
    """Make a GraphQL request to local Stash instance."""
    conn = get_stash_connection()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if conn.get("api_key"):
        headers["Cookie"] = f"session={conn['api_key']}"

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(conn["url"], data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
            result = json.loads(response.read().decode("utf-8"))
            if "errors" in result:
                log.LogWarning(f"Stash GraphQL errors: {result['errors']}")
            return result.get("data")
    except Exception as e:
        log.LogError(f"Stash request error: {e}")
        raise


def get_stashbox_config():
    """Get configured stash-box endpoints from Stash settings."""
    query = """
    query Configuration {
        configuration {
            general {
                stashBoxes {
                    endpoint
                    api_key
                    name
                }
            }
        }
    }
    """
    data = stash_graphql(query)
    if data and "configuration" in data:
        return data["configuration"]["general"].get("stashBoxes", [])
    return []


def get_local_scene(scene_id):
    """Get a scene from local Stash with performers and studio stash_ids."""
    query = """
    query FindScene($id: ID!) {
        findScene(id: $id) {
            id
            title
            date
            files {
                path
                basename
                duration
            }
            stash_ids {
                endpoint
                stash_id
            }
            performers {
                id
                name
                stash_ids {
                    endpoint
                    stash_id
                }
            }
            studio {
                id
                name
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
    }
    """
    data = stash_graphql(query, {"id": scene_id})
    if data:
        return data.get("findScene")
    return None


LOCAL_IDS_TTL = 300  # seconds
LOCAL_IDS_PAGE_SIZE = 1000


def _cache_path(endpoint):
    key = normalize_endpoint(endpoint).encode("utf-8")
    try:
        digest = hashlib.md5(key, usedforsecurity=False).hexdigest()
    except TypeError:  # Python < 3.9
        digest = hashlib.md5(key).hexdigest()
    return os.path.join(plugin_data.current_dir(), f"local_ids_{digest}.json")


def _read_cache(path):
    """Return the cached set of IDs, or None on a miss (absent, stale or corrupt)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = data["ids"]
        fresh = 0 <= time.time() - float(data["ts"]) < LOCAL_IDS_TTL
        if not fresh or not isinstance(ids, list):
            return None
        return set(ids)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path, ids):
    """Write atomically via a unique temp file. Failure to cache is not an error."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="local_ids_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "ids": sorted(ids)}, f)
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        log.LogWarning(f"Could not write the local ID cache: {e}")
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def local_stash_ids(endpoint):
    """Stash IDs of local scenes linked to `endpoint` (the box's endpoint as configured
    in Stash). Cached in the data dir for LOCAL_IDS_TTL seconds."""
    path = _cache_path(endpoint)
    cached = _read_cache(path)
    if cached is not None:
        log.LogDebug(f"Using cached local stash_ids ({len(cached)} entries)")
        return cached

    query = """
    query FindLinkedScenes($scene_filter: SceneFilterType, $filter: FindFilterType) {
        findScenes(scene_filter: $scene_filter, filter: $filter) {
            count
            scenes {
                id
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
    }
    """
    target = normalize_endpoint(endpoint)
    ids = set()
    page = 1
    while True:
        data = stash_graphql(query, {
            "scene_filter": {"stash_id_endpoint": {"endpoint": endpoint, "modifier": "NOT_NULL"}},
            "filter": {"per_page": LOCAL_IDS_PAGE_SIZE, "page": page},
        })
        if not data or not data.get("findScenes"):
            raise RuntimeError("Could not list local scenes from Stash (no response to findScenes)")
        found = data["findScenes"]
        scenes = found.get("scenes") or []
        for scene in scenes:
            for sid in scene.get("stash_ids") or []:
                if normalize_endpoint(sid.get("endpoint")) == target and sid.get("stash_id"):
                    ids.add(sid["stash_id"])
        if not scenes or page * LOCAL_IDS_PAGE_SIZE >= (found.get("count") or 0):
            break
        page += 1

    log.LogInfo(f"Found {len(ids)} local scenes linked to {endpoint}")
    _write_cache(path, ids)
    return ids


# ============================================================================
# Title Cleaning
# ============================================================================

# Video extensions clean_title strips from the very end of a name. Only real ones:
# a name that has already lost its extension ends in a title word ("...Hot.Day").
# Two-letter ones (.ts, .rm) are left out because "TS" also ends real names.
VIDEO_EXTENSION = re.compile(
    r'\.(mp4|m4v|mkv|avi|wmv|mov|flv|f4v|webm|mpe?g|m2ts|mts|vob|3gp|ogv|divx|asf|rmvb)$',
    re.IGNORECASE)

# Resolution, encoding, source and container tags, as they appear in a raw release name.
RELEASE_TAG = (r'2160p|1080p|720p|480p|4k|uhd|hevc|h\.?26[45]|x26[45]|avc|'
               r'web-dl|webrip|web|bluray|bdrip|dvdrip|hdtv|xxx|mp4|mkv|wmv|m4v')

# A trailing "-GROUP" is a release group only when it follows one of those tags
# ("...1080p.MP4-WRB"); otherwise it is the last word of the title ("jane-doe-hot-scene").
RELEASE_GROUP = re.compile(r'(?<![a-z0-9])(' + RELEASE_TAG + r')-[a-z0-9]{2,12}\s*$', re.IGNORECASE)
BRACKET_GROUP = re.compile(r'\[[a-z]{2,8}\]\s*$', re.IGNORECASE)

# File sizes, matched before separators become spaces ("1.5GB", "Day_700MB")
FILE_SIZE = re.compile(r'(?<![0-9a-z])\d+(\.\d+)?\s*(gb|mb)(?![0-9a-z])', re.IGNORECASE)

# Tags to strip once separators are spaces (so "WEB-DL" is "WEB DL", "H.264" is "H 264").
# "sex" and "porn" are not here: they are title words ("Sex On The Beach").
STRIP_PATTERNS = [
    # Resolutions
    r'\b(2160p|1080p|720p|480p|4k|uhd)\b',
    # Encoding
    r'\b(hevc|h ?26[45]|x26[45]|avc)\b',
    # Sources
    r'\b(web ?dl|webrip|web|bluray|bdrip|dvdrip|hdtv)\b',
    # Adult-specific
    r'\bxxx\b',
    # Containers named as tags ("...1080p.MP4-WRB")
    r'\b(mp4|mkv|wmv|m4v)\b',
]

# Date shapes in names, not inside a longer word or number. The kind says how to read them.
_NB, _NA = r'(?<![0-9a-z])', r'(?![0-9a-z])'
DATE_PATTERNS = [
    # YYYY.MM.DD, YYYY-MM-DD, YYYY_MM_DD, YYYYMMDD
    ("ymd", re.compile(_NB + r'((?:19|20)\d\d)([._-]?)(\d\d)\2(\d\d)' + _NA, re.IGNORECASE)),
    # DD.MM.YYYY or MM.DD.YYYY
    ("dmy", re.compile(_NB + r'(\d\d)([._-])(\d\d)\2((?:19|20)\d\d)' + _NA, re.IGNORECASE)),
    # YY.MM.DD, the scene-release convention (dots only)
    ("yymd", re.compile(_NB + r'(\d\d)(\.)(\d\d)\.(\d\d)' + _NA, re.IGNORECASE)),
]


def _valid_dates(year, month, day):
    try:
        return {datetime.date(year, month, day).isoformat()}
    except ValueError:
        return set()


def _date_readings(kind, match):
    """The ISO dates a matched date shape can be read as: none, one, or two when
    DD.MM and MM.DD are both valid and differ."""
    a, b, c = int(match.group(1)), int(match.group(3)), int(match.group(4))
    if kind == "ymd":
        return _valid_dates(a, b, c)
    if kind == "yymd":
        # Two-digit years pivot like strptime's %y: 69-99 are 1900s, 00-68 are 2000s
        return _valid_dates(a + (1900 if a >= 69 else 2000), b, c)
    return _valid_dates(c, b, a) | _valid_dates(c, a, b)  # day-month, month-day


def extract_date(name):
    """
    The scene date in a filename or title as an ISO string, or None.

    Reads, in this order of preference:
    - YYYY.MM.DD, YYYY-MM-DD, YYYY_MM_DD and YYYYMMDD: a 4-digit year first is unambiguous.
    - DD.MM.YYYY when the first number is over 12, and MM.DD.YYYY when the second is.
      When both are 12 or under (and differ), it could be either, so it is skipped.
    - YY.MM.DD: scene releases put the year first ("Studio.24.01.15.Title"), so it is
      read that way whenever the month is 1-12 and the day 1-31, even though it could
      also be DD.MM.YY. Years 69-99 are 1900s, 00-68 are 2000s.
    Impossible dates (month 13, 30 February) are not dates.
    """
    if not name:
        return None
    for kind, pattern in DATE_PATTERNS:
        for match in pattern.finditer(name):
            readings = _date_readings(kind, match)
            if len(readings) == 1:
                return readings.pop()
    return None


def clean_title(title):
    """
    Clean a title/filename for search and scoring.

    Strips a real video extension, a release group after a release tag, dates, file
    sizes and release tags, and turns separators into spaces. Studio and performer
    names stay: the stash-box text search matches them. Scoring removes them itself.
    """
    if not title:
        return ""

    cleaned = VIDEO_EXTENSION.sub('', title.strip())

    # Release groups, before separators change: "[XC]", then "x265-GUSH" -> "x265"
    cleaned = BRACKET_GROUP.sub('', cleaned)
    cleaned = RELEASE_GROUP.sub(r'\1', cleaned)

    # Dates and sizes use the separators, so they go before separators become spaces.
    # Anything date-shaped goes, even when extract_date would call it ambiguous.
    for kind, pattern in DATE_PATTERNS:
        cleaned = pattern.sub(
            lambda m, kind=kind: ' ' if _date_readings(kind, m) else m.group(0), cleaned)
    cleaned = FILE_SIZE.sub(' ', cleaned)

    # Dots, underscores, dashes and brackets become spaces
    cleaned = re.sub(r'[._\-\[\](){}]+', ' ', cleaned)

    for pattern in STRIP_PATTERNS:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)

    return re.sub(r'\s+', ' ', cleaned).strip()


def build_search_query(studio_name, performer_names):
    """
    Build a search query from studio and performer names.
    Returns a query like "Studio Name Performer1 Performer2"
    """
    parts = []

    if studio_name:
        parts.append(studio_name)

    if performer_names:
        # Take first 2 performers to avoid overly long queries
        parts.extend(performer_names[:2])

    return " ".join(parts)


# ============================================================================
# StashDB API (using resilient stashbox_api module)
# ============================================================================

def query_stashdb_by_text(stashdb_url, api_key, search_term, limit=25, plugin_settings=None,
                          deadline=None):
    """
    Query StashDB using text search. Returns a list; raises StashBoxAPIError.
    Uses stashbox_api for retry logic and rate limiting.
    """
    return stashbox_api.search_scenes_by_text(
        stashdb_url, api_key, search_term,
        limit=limit,
        plugin_settings=plugin_settings,
        deadline=deadline,
    )


def query_stashdb_scenes_combined(stashdb_url, api_key, performer_ids, studio_id, plugin_settings=None,
                                  deadline=None):
    """
    Query StashDB with combined performer AND studio filter.
    Returns (scenes, error); raises StashBoxAPIError when the first page fails.
    Uses stashbox_api for retry logic and rate limiting.
    """
    return stashbox_api.query_scenes_combined(
        stashdb_url, api_key, performer_ids, studio_id,
        plugin_settings=plugin_settings,
        deadline=deadline,
    )


def query_stashdb_scenes_by_performers(stashdb_url, api_key, performer_ids, plugin_settings=None,
                                       deadline=None):
    """
    Query StashDB for scenes featuring any of the given performers.
    Returns (scenes, error); raises StashBoxAPIError when the first page fails.
    Uses stashbox_api for retry logic and rate limiting.
    """
    return stashbox_api.query_scenes_by_performers(
        stashdb_url, api_key, performer_ids,
        plugin_settings=plugin_settings,
        deadline=deadline,
    )


def query_stashdb_scenes_by_studio(stashdb_url, api_key, studio_id, plugin_settings=None,
                                   deadline=None):
    """
    Query StashDB for scenes from a studio.
    Returns (scenes, error); raises StashBoxAPIError when the first page fails.
    Uses stashbox_api for retry logic and rate limiting.
    """
    return stashbox_api.query_scenes_by_studio(
        stashdb_url, api_key, studio_id,
        plugin_settings=plugin_settings,
        deadline=deadline,
    )


# ============================================================================
# Main Operations
# ============================================================================

def format_scene(scene, stash_id):
    """Format a StashDB scene for the frontend."""
    images = scene.get("images", [])
    thumbnail = None
    if images:
        for img in images:
            if img.get("width", 0) > img.get("height", 0):
                thumbnail = img.get("url")
                break
        if not thumbnail:
            thumbnail = images[0].get("url")

    performers = []
    for perf in scene.get("performers", []):
        p = perf.get("performer", {})
        performers.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "disambiguation": p.get("disambiguation"),
            "gender": p.get("gender"),
            "as": perf.get("as")
        })

    studio = scene.get("studio")
    studio_info = None
    if studio:
        studio_info = {
            "id": studio.get("id"),
            "name": studio.get("name")
        }

    urls = scene.get("urls", [])
    primary_url = urls[0].get("url") if urls else None

    return {
        "stash_id": stash_id,
        "title": scene.get("title") or "Unknown Title",
        "details": scene.get("details"),
        "release_date": scene.get("release_date"),
        "duration": scene.get("duration"),
        "code": scene.get("code"),
        "director": scene.get("director"),
        "thumbnail": thumbnail,
        "studio": studio_info,
        "performers": performers,
        "url": primary_url
    }


def normalize_title(title):
    """Normalize a title for comparison."""
    if not title:
        return ""
    # Lowercase; apostrophes join ("Kate's" -> "kates", as release names write it);
    # other punctuation and underscores split; collapse whitespace
    normalized = title.lower()
    normalized = re.sub(r"['\u2019`]", '', normalized)  # ' and the typographic right quote
    normalized = re.sub(r'[\W_]+', ' ', normalized)
    return normalized.strip()


def tokenize(text):
    """Split normalized text into tokens (words)."""
    if not text:
        return []
    return text.split()


def levenshtein_ratio(s1, s2):
    """
    Calculate similarity ratio between two strings using Levenshtein distance.
    Returns a score from 0 to 1, where 1 is an exact match.
    """
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)

    # Create distance matrix with space optimization (only need 2 rows)
    prev = list(range(len2 + 1))
    curr = [0] * (len2 + 1)

    for i in range(1, len1 + 1):
        curr[0] = i
        for j in range(1, len2 + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, prev

    distance = prev[len2]
    max_len = max(len1, len2)
    return 1.0 - (distance / max_len)


def token_similarity(tokens1, tokens2, fuzzy_threshold=0.75, contained=False):
    """
    Calculate similarity between two token lists using fuzzy token matching.

    For each token in the reference list, finds the best matching unused token in
    the other list. Tokens with similarity >= fuzzy_threshold are considered matches.

    By default the shorter list is the reference and the sum is divided by the longer
    list's length, so extra tokens on either side cost. With contained=True, tokens1
    is the reference and the sum is divided by its length: how much of tokens1 is
    found in tokens2, whatever else tokens2 holds.

    Returns a score from 0 to 1.
    """
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0

    # Work with the shorter list as the reference
    if not contained and len(tokens1) > len(tokens2):
        tokens1, tokens2 = tokens2, tokens1

    total_score = 0.0
    used_indices = set()

    for token1 in tokens1:
        best_score = 0.0
        best_idx = -1

        for idx, token2 in enumerate(tokens2):
            if idx in used_indices:
                continue

            # Calculate fuzzy similarity between tokens
            score = levenshtein_ratio(token1, token2)

            if score > best_score:
                best_score = score
                best_idx = idx

        # Only count matches above threshold
        if best_score >= fuzzy_threshold:
            total_score += best_score
            if best_idx >= 0:
                used_indices.add(best_idx)

    if contained:
        return total_score / len(tokens1)
    # Score is average of best matches, penalized by unmatched tokens
    # Denominator is max of token counts to penalize missing words
    max_tokens = max(len(tokens1), len(tokens2))
    return total_score / max_tokens


STOP_WORDS = frozenset("the a an and of in on with to for".split())


def _remove_phrases(tokens, phrases):
    """Drop every run of tokens that spells one of the phrases (token lists), longest first."""
    for phrase in sorted(phrases, key=len, reverse=True):
        n = len(phrase)
        out, i = [], 0
        while i < len(tokens):
            if tokens[i:i + n] == phrase:
                i += n
            else:
                out.append(tokens[i])
                i += 1
        tokens = out
    return tokens


def _content_tokens(normalized, phrases):
    return [t for t in _remove_phrases(tokenize(normalized), phrases) if t not in STOP_WORDS]


def title_similarity(title1, title2, ignore_names=()):
    """
    How much of title2 (the stash-box title) is found in title1 (the local title,
    already cleaned), from 0 to 1.

    - Containment: the share of title2's tokens with a fuzzy match (>= 0.75) in title1,
      so a local name that also holds a studio, performers or tags still scores 1.0.
    - Stop words (the, a, an, and, of, in, on, with, to, for) don't count on either side.
    - ignore_names (studio and performer names) are removed from both titles as whole
      phrases first: they say nothing about the title and score elsewhere.
    - Identical titles score 1.0 before any of that.

    Handles word reordering ("Summer Beach" vs "Beach Summer") and typos
    ("Adventrue" vs "Adventure").
    """
    if not title1 or not title2:
        return 0.0

    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)

    if not norm1 or not norm2:
        return 0.0

    # Exact match
    if norm1 == norm2:
        return 1.0

    phrases = [p for p in (tokenize(normalize_title(n)) for n in ignore_names if n) if p]
    local = _content_tokens(norm1, phrases)
    remote = _content_tokens(norm2, phrases)

    if not local or not remote:
        return 0.0

    # Short-title guard: a stash-box title whose only content is one 1-2 character
    # token ("2", "VR") turns up inside many local names by chance, so containment
    # would call it a full match. Score it symmetrically instead, which divides by the
    # longer side: it reaches 0.9 only when the local title is that token too.
    if len(remote) == 1 and len(remote[0]) <= 2:
        return token_similarity(remote, local)

    return token_similarity(remote, local, contained=True)


def calculate_duration_score(local_duration, stashdb_duration):
    """
    Calculate a duration match score.
    Returns a score from 0.1 to 1.0 based on how close the durations are.
    Scores closer to 1.0 are better matches.
    """
    if local_duration is None or stashdb_duration is None:
        return 0.5  # Neutral score when we can't compare

    diff = abs(local_duration - stashdb_duration)

    # Perfect match or within 30 seconds
    if diff <= 30:
        return 1.0
    # Within 1 minute
    elif diff <= 60:
        return 0.9
    # Within 2 minutes
    elif diff <= 120:
        return 0.8
    # Within 5 minutes
    elif diff <= 300:
        return 0.6
    # Within 10 minutes
    elif diff <= 600:
        return 0.3
    # More than 10 minutes off - penalize but don't exclude
    else:
        return 0.1


_PARTIAL_DATE = re.compile(r'^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$')


def parse_partial_date(value):
    """(year, month, day) for "YYYY", "YYYY-MM" or "YYYY-MM-DD" (month and day may be
    None), or None when the value is missing, malformed or impossible."""
    if not isinstance(value, str):
        return None
    m = _PARTIAL_DATE.match(value.strip()[:10])
    if not m:
        return None
    year, month, day = (int(g) if g else None for g in m.groups())
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not _valid_dates(year, month, day):
        return None
    return year, month, day


def date_bonus(local_date, stashbox_date):
    """
    Points for the stash-box release date agreeing with the local scene's date.

    +3 for the same day. +1 when only the year-month can match: the stash-box date is
    just "YYYY-MM" and the month is the same, or the dates are a day apart (release
    time zones). A year-only stash-box date earns nothing. Missing or malformed: 0.
    """
    local, remote = parse_partial_date(local_date), parse_partial_date(stashbox_date)
    if not local or not remote or local[1] is None or remote[1] is None:
        return 0
    if local[2] is not None and remote[2] is not None:
        days = abs((datetime.date(*local) - datetime.date(*remote)).days)
        return 3 if days == 0 else 1 if days == 1 else 0
    return 1 if local[:2] == remote[:2] else 0


def _scene_names(scene):
    """The stash-box scene's studio name and performer names and credited aliases."""
    names = [(scene.get("studio") or {}).get("name")]
    for perf in scene.get("performers") or []:
        names.append((perf.get("performer") or {}).get("name"))
        names.append(perf.get("as"))
    return [n for n in names if n]


class SceneScore(namedtuple("SceneScore", "score matching_performers title_match duration_score")):
    """What score_scene returns. It unpacks as the four values it has always returned;
    matches_date (a date bonus was earned) rides along as an attribute."""

    def __new__(cls, score, matching_performers, title_match, duration_score, matches_date=False):
        self = super().__new__(cls, score, matching_performers, title_match, duration_score)
        self.matches_date = matches_date
        return self


def score_scene(scene, performer_stash_ids, studio_stash_id, local_title=None, local_duration=None,
                local_date=None, known_names=()):
    """
    Calculate relevance score for a scene.

    local_title should already be cleaned (clean_title). known_names are the local
    scene's studio and performer names; they and the stash-box scene's own names are
    left out of the title comparison (see title_similarity).

    +10 when the title similarity is >= 0.9, +5 when it is >= 0.5.
    +3 for matching studio, +2 per matching performer.
    +3 for the same release date as local_date, +1 for a near one (see date_bonus).
    Score is then multiplied by duration proximity (0.5 + 0.5 * duration_score).
    Returns a SceneScore.
    """
    base_score = 0
    title_match = False

    # Check title match (highest priority)
    if local_title:
        stashdb_title = scene.get("title", "")
        names = list(known_names or ()) + _scene_names(scene)
        similarity = title_similarity(local_title, stashdb_title, ignore_names=names)
        if similarity >= 0.9:
            base_score += 10
            title_match = True
        elif similarity >= 0.5:
            base_score += 5
            title_match = True

    bonus = date_bonus(local_date, scene.get("release_date"))
    base_score += bonus

    # Check studio match
    if studio_stash_id and scene.get("studio"):
        if scene["studio"].get("id") == studio_stash_id:
            base_score += 3

    # Check performer matches
    scene_performer_ids = set()
    for perf in scene.get("performers", []):
        p = perf.get("performer", {})
        if p.get("id"):
            scene_performer_ids.add(p.get("id"))

    matching_performers = performer_stash_ids & scene_performer_ids
    base_score += len(matching_performers) * 2

    # Apply duration score as a multiplier (0.5 to 1.0 range)
    duration_score = calculate_duration_score(local_duration, scene.get("duration"))
    final_score = base_score * (0.5 + 0.5 * duration_score)

    return SceneScore(final_score, len(matching_performers), title_match, duration_score,
                      matches_date=bonus > 0)


def _sort_date(value):
    """(end-of-period ordinal, precision) for a stash-box date, or (0, 0) if missing or
    malformed. A partial date counts as the LAST day of its period ("2024" is
    2024-12-31, "2024-05" is 2024-05-31), so it ranks above older periods and a vague
    date is never pushed below an earlier one; on the same end day the more precise
    date comes first."""
    parsed = parse_partial_date(value)
    if not parsed:
        return 0, 0
    year, month, day = parsed
    precision = 1 + (month is not None) + (day is not None)
    if month is None:
        month = 12
    if day is None:
        day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, day).toordinal(), precision


def result_sort_key(x):
    """Sort results: not in local stash first, then score, duration score and date, all descending."""
    in_stash = 1 if x["in_local_stash"] else 0
    ordinal, precision = _sort_date(x.get("release_date"))
    return (in_stash, -x["score"], -x.get("duration_score", 0.5), -ordinal, -precision)


def get_scene_context(scene_id, plugin_settings, endpoint=None):
    """
    Get scene context needed for searching.
    Returns scene data, stashbox config, and extracted attributes.
    `endpoint` is the stash-box the UI selected; it must match a configured box.
    """
    stashbox_configs = get_stashbox_config()
    stashbox, err = resolve_endpoint(
        endpoint, stashbox_configs, (plugin_settings or {}).get("stashBoxEndpoint", ""))
    if err:
        return None, {"error": err}

    graphql_url = stashbox["endpoint"]
    target = normalize_endpoint(graphql_url)
    stashdb_url = site_base(graphql_url)
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    # Get the local scene
    scene = get_local_scene(scene_id)
    if not scene:
        return None, {"error": f"Scene not found: {scene_id}"}

    # Reject only if already linked to the resolved endpoint
    for stash_id in scene.get("stash_ids", []):
        if normalize_endpoint(stash_id.get("endpoint")) == target:
            return None, {"error": f"Scene already has a {stashdb_name} ID. No matching needed."}

    # Extract performer stash_ids and names
    # performer_stash_ids: only performers linked to this endpoint (for filter queries)
    # performer_names: ALL performer names (for text search)
    performer_stash_ids = set()
    performer_names = []
    for performer in scene.get("performers", []):
        # Always add name for text search
        if performer.get("name"):
            performer_names.append(performer.get("name"))
        # Check if linked to this endpoint
        for stash_id in performer.get("stash_ids", []):
            if normalize_endpoint(stash_id.get("endpoint")) == target:
                performer_stash_ids.add(stash_id.get("stash_id"))
                break

    # Extract studio stash_id and name
    # studio_name: always use studio name for text search
    # studio_stash_id: only if linked to this endpoint (for filter queries)
    studio_stash_id = None
    studio_name = None
    studio = scene.get("studio")
    if studio:
        studio_name = studio.get("name")  # Always use name for text search
        for stash_id in studio.get("stash_ids", []):
            if normalize_endpoint(stash_id.get("endpoint")) == target:
                studio_stash_id = stash_id.get("stash_id")
                break

    # Get file info
    files = scene.get("files", [])
    local_duration = files[0].get("duration") if files else None
    local_title = scene.get("title") or ""
    local_filename = ""
    if files:
        basename = files[0].get("basename", "")
        if basename:
            local_filename = basename.rsplit(".", 1)[0] if "." in basename else basename

    # The scene's own date when set, else a date found in the filename or title
    local_date = scene.get("date") if parse_partial_date(scene.get("date")) else None
    local_date = local_date or extract_date(local_filename) or extract_date(local_title)

    context = {
        "scene": scene,
        "stashdb_url": stashdb_url,
        "endpoint": graphql_url,
        "stashdb_api_key": stashdb_api_key,
        "stashdb_name": stashdb_name,
        "performer_stash_ids": performer_stash_ids,
        "performer_names": performer_names,
        "studio_stash_id": studio_stash_id,
        "studio_name": studio_name,
        "local_duration": local_duration,
        "local_title": local_title,
        "local_filename": local_filename,
        "local_date": local_date,
    }

    return context, None


def format_results(all_scenes, context, local_stash_ids):
    """Format and score all scenes for the response."""
    performer_stash_ids = context["performer_stash_ids"]
    studio_stash_id = context["studio_stash_id"]
    # The same cleaned title the phase-1 text search uses
    local_title = clean_title(context["local_title"] or context["local_filename"])
    local_duration = context["local_duration"]
    known_names = [context.get("studio_name")] + list(context.get("performer_names") or [])
    local_date = context.get("local_date")

    # Score and format results
    results = []
    for stashdb_scene_id, stashdb_scene in all_scenes.items():
        scored = score_scene(
            stashdb_scene, performer_stash_ids, studio_stash_id,
            local_title=local_title,
            local_duration=local_duration,
            local_date=local_date,
            known_names=known_names,
        )

        formatted = format_scene(stashdb_scene, stashdb_scene_id)
        formatted["score"] = scored.score
        formatted["matching_performers"] = scored.matching_performers
        formatted["matches_title"] = scored.title_match
        formatted["matches_date"] = scored.matches_date
        formatted["duration_score"] = scored.duration_score
        formatted["matches_studio"] = (
            studio_stash_id is not None and
            (stashdb_scene.get("studio") or {}).get("id") == studio_stash_id
        )
        formatted["in_local_stash"] = stashdb_scene_id in local_stash_ids

        results.append(formatted)

    results.sort(key=result_sort_key)
    return results


def _auth_message(name, error):
    return (f"{name} rejected the request ({error}). Check the API key for {name} "
            f"in Stash Settings > Metadata Providers.")


def _out_of_time(errors):
    return any(isinstance(e, stashbox_api.BudgetExceeded) for e in errors)


def _warnings(name, errors):
    """One warning per distinct error message, in order."""
    out = []
    for e in errors:
        w = f"{name}: {e}"
        if w not in out:
            out.append(w)
    return out


def _failure_response(base, name, errors):
    """Response for a phase where every stash-box request failed."""
    auth = any(getattr(e, "is_auth_error", False) for e in errors)
    out = dict(base)
    if auth:
        first = next(e for e in errors if getattr(e, "is_auth_error", False))
        out["error"] = _auth_message(name, first)
        out["auth_error"] = True
    else:
        out["error"] = f"{name} request failed: {errors[0]}"
    return out


def find_matches_fast(scene_id, plugin_settings, endpoint=None):
    """
    Phase 1: Fast text-based searches.
    Uses cleaned title and constructed studio+performer query.
    Returns quickly with initial results.
    """
    context, error = get_scene_context(scene_id, plugin_settings, endpoint=endpoint)
    if error:
        return error

    stashdb_url = context["endpoint"]  # GraphQL URL for requests
    stashdb_api_key = context["stashdb_api_key"]
    stashdb_name = context["stashdb_name"]
    performer_names = context["performer_names"]
    studio_name = context["studio_name"]
    local_title = context["local_title"]
    local_filename = context["local_filename"]

    log.LogInfo(f"Phase 1 (fast): text searches for scene {scene_id}")

    all_scenes = {}
    errors = []
    searches = 0
    deadline = stashbox_api.Deadline()  # both searches share one time budget

    def run_search(term):
        nonlocal searches
        searches += 1
        if _out_of_time(errors):
            errors.append(stashbox_api.BudgetExceeded(deadline.seconds))
            return
        try:
            found = query_stashdb_by_text(stashdb_url, stashdb_api_key, term, plugin_settings=plugin_settings,
                                          deadline=deadline)
        except stashbox_api.StashBoxAPIError as e:
            log.LogWarning(f"Text search failed on {stashdb_name}: {e}")
            errors.append(e)
            return
        for s in found:
            all_scenes[s["id"]] = s

    # Search 1: Cleaned title
    search_title = clean_title(local_title or local_filename)
    if search_title and len(search_title) >= 3:
        log.LogDebug(f"Text search: cleaned title '{search_title}'")
        run_search(search_title)

    # Search 2: Constructed query (studio + performers)
    constructed_query = build_search_query(studio_name, performer_names)
    if constructed_query and len(constructed_query) >= 3:
        log.LogDebug(f"Text search: constructed query '{constructed_query}'")
        run_search(constructed_query)

    base = {
        "phase": 1,
        "endpoint": stashdb_url,
        "endpoint_name": stashdb_name,
        "stashdb_name": stashdb_name,
    }
    if errors and len(errors) >= searches:
        return _failure_response(base, stashdb_name, errors)

    # Get local scene stash_ids to mark which results user already has
    local_ids = local_stash_ids(context["endpoint"])

    results = format_results(all_scenes, context, local_ids)

    log.LogInfo(f"Phase 1: returning {len(results)} scenes from text searches")

    response = {
        "phase": 1,
        "scene_title": context["scene"].get("title"),
        "search_attributes": {
            "performers": performer_names,
            "studio": studio_name,
            "cleaned_title": search_title,
            "constructed_query": constructed_query
        },
        "stashdb_name": stashdb_name,
        "stashdb_url": context["stashdb_url"],
        "endpoint": stashdb_url,
        "endpoint_name": stashdb_name,
        "total_results": len(results),
        "results": results,
        "has_more": bool(context["performer_stash_ids"] or context["studio_stash_id"])
    }
    if errors:
        response["warnings"] = _warnings(stashdb_name, errors)
    if _out_of_time(errors):
        response["partial"] = True

    return response


def get_max_results(plugin_settings):
    """The maxResults setting, clamped to 10-500; anything not a number gives 50."""
    value = (plugin_settings or {}).get("maxResults")
    if isinstance(value, bool):
        return DEFAULT_MAX_RESULTS
    try:
        value = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_RESULTS
    return max(MIN_MAX_RESULTS, min(MAX_MAX_RESULTS, value))


def find_matches_thorough(scene_id, plugin_settings, exclude_ids=None, endpoint=None):
    """
    Phase 2: Thorough performer/studio searches.
    Uses combined filters when possible, higher page limits.
    Returns additional results not found in Phase 1.
    """
    context, error = get_scene_context(scene_id, plugin_settings, endpoint=endpoint)
    if error:
        return error

    stashdb_url = context["endpoint"]  # GraphQL URL for requests
    stashdb_api_key = context["stashdb_api_key"]
    stashdb_name = context["stashdb_name"]
    performer_stash_ids = context["performer_stash_ids"]
    studio_stash_id = context["studio_stash_id"]
    performer_names = context["performer_names"]
    studio_name = context["studio_name"]

    if not performer_stash_ids and not studio_stash_id:
        return {
            "phase": 2,
            "scene_title": context["scene"].get("title"),
            "search_attributes": {
                "performers": performer_names,
                "studio": studio_name
            },
            "stashdb_name": stashdb_name,
            "stashdb_url": context["stashdb_url"],
            "endpoint": stashdb_url,
            "endpoint_name": stashdb_name,
            "total_results": 0,
            "results": [],
            "message": "No performers or studio linked - skipping thorough search"
        }

    log.LogInfo(f"Phase 2 (thorough): performer/studio queries for scene {scene_id}")

    # Track which IDs to exclude (already found in Phase 1)
    exclude_set = set(exclude_ids or [])

    all_scenes = {}
    raw_ids = set()  # everything the queries returned, before phase-1 ids are dropped
    capped_totals = []  # stash-box totals of queries that the page cap stopped short
    errors = []
    succeeded = 0
    deadline = stashbox_api.Deadline()  # every query and wait in this phase shares one budget

    def run_query(fn, *args):
        """Run one paginated query; keep whatever it returned and note failures."""
        nonlocal succeeded
        if _out_of_time(errors):
            return  # the budget is spent: the queries not yet run are skipped
        try:
            scenes, error = fn(stashdb_url, stashdb_api_key, *args, plugin_settings=plugin_settings,
                               deadline=deadline)
        except stashbox_api.StashBoxAPIError as e:
            log.LogWarning(f"Query failed on {stashdb_name}: {e}")
            errors.append(e)
            return
        succeeded += 1
        if error:
            errors.append(error)
        if getattr(scenes, "truncated", False) and getattr(scenes, "total", None):
            capped_totals.append(scenes.total)
        for s in scenes:
            raw_ids.add(s["id"])
            if s["id"] not in exclude_set:
                all_scenes[s["id"]] = s

    def auth_failed():
        return any(getattr(e, "is_auth_error", False) for e in errors)

    # Strategy 1: Combined filter if we have both performer AND studio
    if performer_stash_ids and studio_stash_id:
        log.LogDebug("Trying combined performer+studio query")
        run_query(query_stashdb_scenes_combined, list(performer_stash_ids), studio_stash_id)

    # Strategy 2: Individual queries (if combined didn't find enough or we don't have both)
    # An auth failure would repeat on every query, so stop there.
    if len(raw_ids) < MIN_COMBINED_RESULTS_THRESHOLD and not auth_failed():
        # Query by performers
        if performer_stash_ids:
            log.LogDebug(f"Querying by {len(performer_stash_ids)} performers")
            run_query(query_stashdb_scenes_by_performers, list(performer_stash_ids))

        # Query by studio
        if studio_stash_id and not auth_failed():
            log.LogDebug(f"Querying by studio: {studio_name}")
            run_query(query_stashdb_scenes_by_studio, studio_stash_id)

    if errors and not succeeded:
        return _failure_response({
            "phase": 2,
            "endpoint": stashdb_url,
            "endpoint_name": stashdb_name,
            "stashdb_name": stashdb_name,
        }, stashdb_name, errors)

    # Get local scene stash_ids
    local_ids = local_stash_ids(context["endpoint"])

    results = format_results(all_scenes, context, local_ids)  # sorted best first
    candidates = len(results)
    max_results = get_max_results(plugin_settings)
    results = results[:max_results]
    cut = len(results) < candidates
    if capped_totals:
        # Page cap stopped paging: the server's count is the real number of candidates
        candidates = max(candidates, max(capped_totals))

    log.LogInfo(f"Phase 2: returning {len(results)} additional scenes from performer/studio queries")

    response = {
        "phase": 2,
        "scene_title": context["scene"].get("title"),
        "search_attributes": {
            "performers": performer_names,
            "studio": studio_name
        },
        "stashdb_name": stashdb_name,
        "stashdb_url": context["stashdb_url"],
        "endpoint": stashdb_url,
        "endpoint_name": stashdb_name,
        "total_results": len(results),
        "results": results
    }
    if cut or capped_totals:
        response["truncated"] = True
        response["total_candidates"] = candidates
    if errors:
        response["partial"] = True
        response["warnings"] = _warnings(stashdb_name, errors)

    return response


def main():
    """Main entry point for the plugin."""
    try:
        input_data = get_input_data()
    except json.JSONDecodeError as e:
        output = {"error": f"Invalid JSON input: {e}"}
        print(json.dumps(output))
        return

    plugin_data.configure(input_data.get("server_connection"))

    # Get plugin settings
    plugin_settings = {}
    try:
        config_data = stash_graphql("""
            query Configuration {
                configuration {
                    plugins
                }
            }
        """)
        if config_data and "configuration" in config_data:
            plugins_config = config_data["configuration"].get("plugins", {})
            plugin_settings = plugins_config.get("sceneMatcher", {})
    except Exception as e:
        log.LogWarning(f"Could not load plugin settings: {e}")

    # Handle operations from UI
    args = input_data.get("args", {})
    operation = args.get("operation", "")
    output = {"error": "Unknown operation"}

    try:
        if operation == "find_matches_fast":
            # Phase 1: Fast text searches
            scene_id = args.get("scene_id", "")
            if not scene_id:
                output = {"error": "scene_id is required"}
            else:
                output = find_matches_fast(
                    scene_id, plugin_settings,
                    endpoint=args.get("endpoint")
                )

        elif operation == "find_matches_thorough":
            # Phase 2: Thorough performer/studio searches
            scene_id = args.get("scene_id", "")
            if not scene_id:
                output = {"error": "scene_id is required"}
            else:
                exclude_ids = args.get("exclude_ids", [])
                output = find_matches_thorough(
                    scene_id, plugin_settings,
                    exclude_ids=exclude_ids,
                    endpoint=args.get("endpoint")
                )

        elif operation:
            output = {"error": f"Unknown operation: {operation}"}
        else:
            output = {"success": True, "message": "No operation specified"}

    except Exception as e:
        log.LogError(f"Operation failed: {e}")
        output = {"error": str(e)}

    # Wrap output. Stash-box failures carry a "phase" and stay structured so the
    # UI can render them; other errors (bad input, unexpected exceptions) are top-level.
    if "error" in output and "phase" not in output:
        plugin_output = {"error": output["error"]}
    else:
        plugin_output = {"output": output}

    print(json.dumps(plugin_output))


if __name__ == "__main__":
    main()
