#!/usr/bin/env python3
"""
Scene Matcher - Find StashDB matches using known attributes.
Searches StashDB for scenes matching a local scene's performers and/or studio.

Uses only Python standard library - no pip dependencies.
"""

import json
import re
import sys
import urllib.request
import urllib.error
import ssl

import log

# Create SSL context that doesn't verify certificates (for self-signed certs)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE


# ============================================================================
# GraphQL Helpers
# ============================================================================

def graphql_request(url, query, variables=None, api_key=None, timeout=30):
    """Make a GraphQL request to the specified endpoint."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if api_key:
        headers["ApiKey"] = api_key

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
            result = json.loads(response.read().decode("utf-8"))
            if "errors" in result:
                log.LogWarning(f"GraphQL errors: {result['errors']}")
            return result.get("data")
    except urllib.error.HTTPError as e:
        log.LogError(f"HTTP error {e.code}: {e.reason}")
        raise
    except urllib.error.URLError as e:
        log.LogError(f"URL error: {e.reason}")
        raise
    except Exception as e:
        log.LogError(f"Request error: {e}")
        raise


# ============================================================================
# Local Stash API
# ============================================================================

_stash_connection = None
_input_data = None


def get_stash_connection():
    """Get Stash connection details from plugin input."""
    global _stash_connection, _input_data

    if _stash_connection is not None:
        return _stash_connection

    try:
        if _input_data is None:
            _input_data = json.loads(sys.stdin.read())
        server_connection = _input_data.get("server_connection", {})
        _stash_connection = {
            "url": server_connection.get("Scheme", "http") + "://" +
                   server_connection.get("Host", "localhost") + ":" +
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


def get_local_scene_stash_ids(endpoint):
    """Get all stash_ids for scenes that are linked to a specific stash-box endpoint."""
    query = """
    query FindScenes($filter: FindFilterType) {
        findScenes(filter: $filter) {
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

    all_stash_ids = set()
    page = 1
    per_page = 100

    while True:
        data = stash_graphql(query, {
            "filter": {
                "page": page,
                "per_page": per_page
            }
        })

        if not data or "findScenes" not in data:
            break

        scenes = data["findScenes"].get("scenes", [])
        if not scenes:
            break

        for scene in scenes:
            for stash_id in scene.get("stash_ids", []):
                if stash_id.get("endpoint") == endpoint:
                    all_stash_ids.add(stash_id.get("stash_id"))

        total = data["findScenes"].get("count", 0)
        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"Found {len(all_stash_ids)} local scenes linked to {endpoint}")
    return all_stash_ids


# ============================================================================
# Title Cleaning
# ============================================================================

# Common tags to strip from filenames (applied AFTER separator conversion)
STRIP_PATTERNS = [
    # Resolutions
    r'\b(2160p|1080p|720p|480p|4k|uhd)\b',
    # Encoding
    r'\b(hevc|h\.?264|h\.?265|x264|x265|avc)\b',
    # Sources
    r'\b(web|webrip|web-dl|bluray|bdrip|dvdrip|hdtv)\b',
    # Adult-specific
    r'\b(xxx|porn|sex)\b',
    # File size patterns
    r'\b\d+(\.\d+)?\s*(gb|mb)\b',
    # Date patterns that aren't scene dates
    r'\b(19|20)\d{2}[-.]?(0[1-9]|1[0-2])[-.]?(0[1-9]|[12]\d|3[01])\b',
]


def clean_title(title):
    """
    Clean a title/filename for search.
    Strips extensions, dots, underscores, and common release tags.
    """
    if not title:
        return ""

    cleaned = title

    # Remove file extension if present
    cleaned = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', cleaned)

    # Strip release groups BEFORE converting separators (they often use - or [])
    # e.g., "-RARBG", "[YTS]", "-FGT"
    cleaned = re.sub(r'[-]\s*[a-z]{2,8}\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[[a-z]{2,8}\]\s*$', '', cleaned, flags=re.IGNORECASE)

    # Replace dots, underscores, and dashes with spaces
    cleaned = re.sub(r'[._-]+', ' ', cleaned)

    # Apply remaining strip patterns (case insensitive)
    for pattern in STRIP_PATTERNS:
        cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)

    # Collapse multiple spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


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
# StashDB API
# ============================================================================

def query_stashdb_by_text(stashdb_url, api_key, search_term, limit=25):
    """
    Query StashDB using text search.
    This is fast and good for finding exact or similar titles.
    """
    if not search_term or len(search_term) < 3:
        return []

    query = """
    query SearchScene($term: String!, $limit: Int) {
        searchScene(term: $term, limit: $limit) {
            id
            title
            details
            release_date
            duration
            code
            director
            urls {
                url
                site {
                    name
                }
            }
            studio {
                id
                name
            }
            images {
                id
                url
                width
                height
            }
            performers {
                performer {
                    id
                    name
                    disambiguation
                    gender
                }
                as
            }
        }
    }
    """

    try:
        data = graphql_request(stashdb_url, query, {"term": search_term, "limit": limit}, api_key)
        if data and "searchScene" in data:
            scenes = data["searchScene"] or []
            log.LogInfo(f"StashDB text search '{search_term[:30]}...': found {len(scenes)} scenes")
            return scenes
    except Exception as e:
        log.LogWarning(f"Text search failed: {e}")

    return []


def query_stashdb_scenes_combined(stashdb_url, api_key, performer_ids, studio_id, max_pages=10):
    """
    Query StashDB with combined performer AND studio filter.
    This dramatically reduces result set for large catalogs.
    """
    if not performer_ids or not studio_id:
        return []

    query = """
    query QueryScenes($input: SceneQueryInput!) {
        queryScenes(input: $input) {
            count
            scenes {
                id
                title
                details
                release_date
                duration
                code
                director
                urls {
                    url
                    site {
                        name
                    }
                }
                studio {
                    id
                    name
                }
                images {
                    id
                    url
                    width
                    height
                }
                performers {
                    performer {
                        id
                        name
                        disambiguation
                        gender
                    }
                    as
                }
            }
        }
    }
    """

    all_scenes = []
    page = 1
    per_page = 100

    while page <= max_pages:
        variables = {
            "input": {
                "performers": {
                    "value": performer_ids,
                    "modifier": "INCLUDES"
                },
                "studios": {
                    "value": [studio_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

        data = graphql_request(stashdb_url, query, variables, api_key)
        if not data or "queryScenes" not in data:
            break

        scenes = data["queryScenes"].get("scenes", [])
        if not scenes:
            break

        all_scenes.extend(scenes)

        total = data["queryScenes"].get("count", 0)
        log.LogDebug(f"StashDB combined query: page {page}, got {len(scenes)} scenes (total: {total})")

        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"StashDB combined (performer+studio): found {len(all_scenes)} scenes")
    return all_scenes


def query_stashdb_scenes_by_performers(stashdb_url, api_key, performer_ids, max_pages=10):
    """Query StashDB for scenes featuring any of the given performers."""
    if not performer_ids:
        return []

    query = """
    query QueryScenes($input: SceneQueryInput!) {
        queryScenes(input: $input) {
            count
            scenes {
                id
                title
                details
                release_date
                duration
                code
                director
                urls {
                    url
                    site {
                        name
                    }
                }
                studio {
                    id
                    name
                }
                images {
                    id
                    url
                    width
                    height
                }
                performers {
                    performer {
                        id
                        name
                        disambiguation
                        gender
                    }
                    as
                }
            }
        }
    }
    """

    all_scenes = []
    page = 1
    per_page = 100

    while page <= max_pages:
        variables = {
            "input": {
                "performers": {
                    "value": performer_ids,
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

        data = graphql_request(stashdb_url, query, variables, api_key)
        if not data or "queryScenes" not in data:
            break

        scenes = data["queryScenes"].get("scenes", [])
        if not scenes:
            break

        all_scenes.extend(scenes)

        total = data["queryScenes"].get("count", 0)
        log.LogDebug(f"StashDB performers query: page {page}, got {len(scenes)} scenes (total: {total})")

        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"StashDB: Found {len(all_scenes)} scenes for {len(performer_ids)} performers")
    return all_scenes


def query_stashdb_scenes_by_studio(stashdb_url, api_key, studio_id, max_pages=10):
    """Query StashDB for scenes from a studio."""
    if not studio_id:
        return []

    query = """
    query QueryScenes($input: SceneQueryInput!) {
        queryScenes(input: $input) {
            count
            scenes {
                id
                title
                details
                release_date
                duration
                code
                director
                urls {
                    url
                    site {
                        name
                    }
                }
                studio {
                    id
                    name
                }
                images {
                    id
                    url
                    width
                    height
                }
                performers {
                    performer {
                        id
                        name
                        disambiguation
                        gender
                    }
                    as
                }
            }
        }
    }
    """

    all_scenes = []
    page = 1
    per_page = 100

    while page <= max_pages:
        variables = {
            "input": {
                "studios": {
                    "value": [studio_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

        data = graphql_request(stashdb_url, query, variables, api_key)
        if not data or "queryScenes" not in data:
            break

        scenes = data["queryScenes"].get("scenes", [])
        if not scenes:
            break

        all_scenes.extend(scenes)

        total = data["queryScenes"].get("count", 0)
        log.LogDebug(f"StashDB studio query: page {page}, got {len(scenes)} scenes (total: {total})")

        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"StashDB: Found {len(all_scenes)} scenes for studio")
    return all_scenes


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
    # Lowercase, remove common punctuation, collapse whitespace
    import re
    normalized = title.lower()
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


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


def token_similarity(tokens1, tokens2, fuzzy_threshold=0.75):
    """
    Calculate similarity between two token lists using fuzzy token matching.

    For each token in the shorter list, finds the best matching token in the
    longer list. Tokens with similarity >= fuzzy_threshold are considered matches.

    Returns a score from 0 to 1.
    """
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0

    # Work with the shorter list as the reference
    if len(tokens1) > len(tokens2):
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

    # Score is average of best matches, penalized by unmatched tokens
    # Denominator is max of token counts to penalize missing words
    max_tokens = max(len(tokens1), len(tokens2))
    return total_score / max_tokens


def title_similarity(title1, title2):
    """
    Calculate similarity between two titles using token-based fuzzy matching.

    Handles:
    - Word reordering ("Summer Beach" vs "Beach Summer")
    - Typos ("Adventrue" vs "Adventure")
    - Extra/missing words (partial matches still score)

    Returns a score from 0 to 1.
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

    # Tokenize and compare
    tokens1 = tokenize(norm1)
    tokens2 = tokenize(norm2)

    if not tokens1 or not tokens2:
        return 0.0

    return token_similarity(tokens1, tokens2)


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


def score_scene(scene, performer_stash_ids, studio_stash_id, local_title=None, local_duration=None):
    """
    Calculate relevance score for a scene.
    +10 for exact title match, +5 for partial title match
    +3 for matching studio, +2 per matching performer.
    Score is then multiplied by duration proximity (0.5 + 0.5 * duration_score).
    """
    base_score = 0
    title_match = False

    # Check title match (highest priority)
    if local_title:
        stashdb_title = scene.get("title", "")
        similarity = title_similarity(local_title, stashdb_title)
        if similarity >= 0.9:
            base_score += 10
            title_match = True
        elif similarity >= 0.5:
            base_score += 5
            title_match = True

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

    return final_score, len(matching_performers), title_match, duration_score


def get_scene_context(scene_id, plugin_settings):
    """
    Get scene context needed for searching.
    Returns scene data, stashbox config, and extracted attributes.
    """
    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return None, {"error": "No stash-box endpoints configured in Stash settings"}

    # Find the stash-box to use
    preferred_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()
    stashbox = None

    if preferred_endpoint:
        for config in stashbox_configs:
            if config["endpoint"] == preferred_endpoint:
                stashbox = config
                break
        if not stashbox:
            available = ", ".join([c.get("name", c["endpoint"]) for c in stashbox_configs])
            return None, {"error": f"Configured stash-box endpoint '{preferred_endpoint}' not found. Available: {available}"}
    else:
        stashbox = stashbox_configs[0]

    stashdb_url = stashbox["endpoint"]
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    # Get the local scene
    scene = get_local_scene(scene_id)
    if not scene:
        return None, {"error": f"Scene not found: {scene_id}"}

    # Check if scene already has a StashDB ID
    for stash_id in scene.get("stash_ids", []):
        if stash_id.get("endpoint") == stashdb_url:
            return None, {"error": "Scene already has a StashDB ID. No matching needed."}

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
            if stash_id.get("endpoint") == stashdb_url:
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
            if stash_id.get("endpoint") == stashdb_url:
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

    context = {
        "scene": scene,
        "stashdb_url": stashdb_url,
        "stashdb_api_key": stashdb_api_key,
        "stashdb_name": stashdb_name,
        "performer_stash_ids": performer_stash_ids,
        "performer_names": performer_names,
        "studio_stash_id": studio_stash_id,
        "studio_name": studio_name,
        "local_duration": local_duration,
        "local_title": local_title,
        "local_filename": local_filename,
    }

    return context, None


def format_results(all_scenes, context, local_stash_ids, cache_hit):
    """Format and score all scenes for the response."""
    performer_stash_ids = context["performer_stash_ids"]
    studio_stash_id = context["studio_stash_id"]
    local_title = context["local_title"]
    local_filename = context["local_filename"]
    local_duration = context["local_duration"]

    # Score and format results
    results = []
    for stashdb_scene_id, stashdb_scene in all_scenes.items():
        score, matching_performer_count, title_match, duration_score = score_scene(
            stashdb_scene, performer_stash_ids, studio_stash_id,
            local_title=local_title or local_filename,
            local_duration=local_duration
        )

        formatted = format_scene(stashdb_scene, stashdb_scene_id)
        formatted["score"] = score
        formatted["matching_performers"] = matching_performer_count
        formatted["matches_title"] = title_match
        formatted["duration_score"] = duration_score
        formatted["matches_studio"] = (
            studio_stash_id is not None and
            stashdb_scene.get("studio", {}).get("id") == studio_stash_id
        )
        formatted["in_local_stash"] = stashdb_scene_id in local_stash_ids

        results.append(formatted)

    # Sort: not in local stash first, then by score descending, then by duration score, then by date
    def sort_key(x):
        in_stash = 1 if x["in_local_stash"] else 0
        score = -x["score"]
        duration = -x.get("duration_score", 0.5)
        date_str = x.get("release_date") or ""
        date_int = int(date_str[:10].replace("-", "")) if date_str else 0
        return (in_stash, score, duration, -date_int)

    results.sort(key=sort_key)
    return results


def find_matches_fast(scene_id, plugin_settings, cached_stash_ids=None, cache_endpoint=None):
    """
    Phase 1: Fast text-based searches.
    Uses cleaned title and constructed studio+performer query.
    Returns quickly with initial results.
    """
    context, error = get_scene_context(scene_id, plugin_settings)
    if error:
        return error

    stashdb_url = context["stashdb_url"]
    stashdb_api_key = context["stashdb_api_key"]
    stashdb_name = context["stashdb_name"]
    performer_names = context["performer_names"]
    studio_name = context["studio_name"]
    local_title = context["local_title"]
    local_filename = context["local_filename"]

    log.LogInfo(f"Phase 1 (fast): text searches for scene {scene_id}")

    all_scenes = {}

    # Search 1: Cleaned title
    search_title = clean_title(local_title or local_filename)
    if search_title and len(search_title) >= 3:
        log.LogDebug(f"Text search: cleaned title '{search_title}'")
        title_scenes = query_stashdb_by_text(stashdb_url, stashdb_api_key, search_title)
        for s in title_scenes:
            all_scenes[s["id"]] = s

    # Search 2: Constructed query (studio + performers)
    constructed_query = build_search_query(studio_name, performer_names)
    if constructed_query and len(constructed_query) >= 3:
        log.LogDebug(f"Text search: constructed query '{constructed_query}'")
        constructed_scenes = query_stashdb_by_text(stashdb_url, stashdb_api_key, constructed_query)
        for s in constructed_scenes:
            all_scenes[s["id"]] = s

    # Get local scene stash_ids to mark which results user already has
    local_stash_ids = None
    cache_hit = False

    if cached_stash_ids and cache_endpoint == stashdb_url:
        local_stash_ids = set(cached_stash_ids)
        cache_hit = True
        log.LogDebug(f"Using cached local stash_ids ({len(local_stash_ids)} entries)")
    else:
        log.LogDebug("Fetching local scene stash_ids...")
        local_stash_ids = get_local_scene_stash_ids(stashdb_url)

    results = format_results(all_scenes, context, local_stash_ids, cache_hit)

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
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_results": len(results),
        "results": results,
        "has_more": bool(context["performer_stash_ids"] or context["studio_stash_id"])
    }

    # Include stash_ids for JS to cache (only on cache miss)
    if not cache_hit:
        response["local_stash_ids"] = list(local_stash_ids)

    return response


def find_matches_thorough(scene_id, plugin_settings, cached_stash_ids=None, cache_endpoint=None, exclude_ids=None):
    """
    Phase 2: Thorough performer/studio searches.
    Uses combined filters when possible, higher page limits.
    Returns additional results not found in Phase 1.
    """
    context, error = get_scene_context(scene_id, plugin_settings)
    if error:
        return error

    stashdb_url = context["stashdb_url"]
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
            "stashdb_url": stashdb_url.replace("/graphql", ""),
            "total_results": 0,
            "results": [],
            "message": "No performers or studio linked - skipping thorough search"
        }

    log.LogInfo(f"Phase 2 (thorough): performer/studio queries for scene {scene_id}")

    # Track which IDs to exclude (already found in Phase 1)
    exclude_set = set(exclude_ids or [])

    all_scenes = {}

    # Strategy 1: Combined filter if we have both performer AND studio
    if performer_stash_ids and studio_stash_id:
        log.LogDebug("Trying combined performer+studio query")
        combined_scenes = query_stashdb_scenes_combined(
            stashdb_url, stashdb_api_key,
            list(performer_stash_ids), studio_stash_id
        )
        for s in combined_scenes:
            if s["id"] not in exclude_set:
                all_scenes[s["id"]] = s

    # Strategy 2: Individual queries (if combined didn't find enough or we don't have both)
    if len(all_scenes) < 10:  # If combined found few results, try individual
        # Query by performers
        if performer_stash_ids:
            log.LogDebug(f"Querying by {len(performer_stash_ids)} performers")
            performer_scenes = query_stashdb_scenes_by_performers(
                stashdb_url, stashdb_api_key, list(performer_stash_ids)
            )
            for s in performer_scenes:
                if s["id"] not in exclude_set:
                    all_scenes[s["id"]] = s

        # Query by studio
        if studio_stash_id:
            log.LogDebug(f"Querying by studio: {studio_name}")
            studio_scenes = query_stashdb_scenes_by_studio(
                stashdb_url, stashdb_api_key, studio_stash_id
            )
            for s in studio_scenes:
                if s["id"] not in exclude_set:
                    all_scenes[s["id"]] = s

    # Get local scene stash_ids
    local_stash_ids = None
    cache_hit = False

    if cached_stash_ids and cache_endpoint == stashdb_url:
        local_stash_ids = set(cached_stash_ids)
        cache_hit = True
    else:
        local_stash_ids = get_local_scene_stash_ids(stashdb_url)

    results = format_results(all_scenes, context, local_stash_ids, cache_hit)

    log.LogInfo(f"Phase 2: returning {len(results)} additional scenes from performer/studio queries")

    response = {
        "phase": 2,
        "scene_title": context["scene"].get("title"),
        "search_attributes": {
            "performers": performer_names,
            "studio": studio_name
        },
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_results": len(results),
        "results": results
    }

    # Include stash_ids for JS to cache (only on cache miss)
    if not cache_hit:
        response["local_stash_ids"] = list(local_stash_ids)

    return response


# Legacy function for backward compatibility
def find_matching_scenes(scene_id, plugin_settings, cached_stash_ids=None, cache_endpoint=None):
    """
    Find StashDB scenes matching a local scene.
    This is the legacy single-call version that runs both phases.
    """
    # Run Phase 1
    phase1 = find_matches_fast(scene_id, plugin_settings, cached_stash_ids, cache_endpoint)
    if "error" in phase1:
        return phase1

    # Run Phase 2, excluding Phase 1 results
    phase1_ids = [r["stash_id"] for r in phase1.get("results", [])]
    phase2 = find_matches_thorough(
        scene_id, plugin_settings,
        cached_stash_ids=phase1.get("local_stash_ids"),
        cache_endpoint=phase1.get("stashdb_url", "").rstrip("/") + "/graphql",
        exclude_ids=phase1_ids
    )

    # Merge results
    all_results = phase1.get("results", []) + phase2.get("results", [])

    # Re-sort merged results
    def sort_key(x):
        in_stash = 1 if x["in_local_stash"] else 0
        score = -x["score"]
        duration = -x.get("duration_score", 0.5)
        date_str = x.get("release_date") or ""
        date_int = int(date_str[:10].replace("-", "")) if date_str else 0
        return (in_stash, score, duration, -date_int)

    all_results.sort(key=sort_key)

    return {
        "scene_title": phase1.get("scene_title"),
        "search_attributes": phase1.get("search_attributes"),
        "stashdb_name": phase1.get("stashdb_name"),
        "stashdb_url": phase1.get("stashdb_url"),
        "total_results": len(all_results),
        "results": all_results,
        "local_stash_ids": phase1.get("local_stash_ids") or phase2.get("local_stash_ids")
    }


# ============================================================================
# Plugin Entry Point
# ============================================================================

def main():
    """Main entry point for the plugin."""
    try:
        input_data = get_input_data()
    except json.JSONDecodeError as e:
        output = {"error": f"Invalid JSON input: {e}"}
        print(json.dumps(output))
        return

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
                cached_stash_ids = args.get("cached_local_stash_ids")
                cache_endpoint = args.get("cache_endpoint")
                output = find_matches_fast(
                    scene_id, plugin_settings,
                    cached_stash_ids=cached_stash_ids,
                    cache_endpoint=cache_endpoint
                )

        elif operation == "find_matches_thorough":
            # Phase 2: Thorough performer/studio searches
            scene_id = args.get("scene_id", "")
            if not scene_id:
                output = {"error": "scene_id is required"}
            else:
                cached_stash_ids = args.get("cached_local_stash_ids")
                cache_endpoint = args.get("cache_endpoint")
                exclude_ids = args.get("exclude_ids", [])
                output = find_matches_thorough(
                    scene_id, plugin_settings,
                    cached_stash_ids=cached_stash_ids,
                    cache_endpoint=cache_endpoint,
                    exclude_ids=exclude_ids
                )

        elif operation == "find_matches":
            # Legacy: Run both phases in one call
            scene_id = args.get("scene_id", "")
            if not scene_id:
                output = {"error": "scene_id is required"}
            else:
                cached_stash_ids = args.get("cached_local_stash_ids")
                cache_endpoint = args.get("cache_endpoint")
                output = find_matching_scenes(
                    scene_id, plugin_settings,
                    cached_stash_ids=cached_stash_ids,
                    cache_endpoint=cache_endpoint
                )

        elif operation:
            output = {"error": f"Unknown operation: {operation}"}
        else:
            output = {"success": True, "message": "No operation specified"}

    except Exception as e:
        log.LogError(f"Operation failed: {e}")
        output = {"error": str(e)}

    # Wrap output
    if "error" in output:
        plugin_output = {"error": output["error"]}
    else:
        plugin_output = {"output": output}

    print(json.dumps(plugin_output))


if __name__ == "__main__":
    main()
