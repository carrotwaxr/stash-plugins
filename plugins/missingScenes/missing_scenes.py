#!/usr/bin/env python3
"""
Missing Scenes - StashDB Scene Discovery Backend
Discovers scenes from StashDB that you don't have locally.
Supports performers and studios with optional Whisparr integration.

Uses only Python standard library - no pip dependencies.
"""

import json
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
import ssl
import base64
import os
import time
import hashlib
import tempfile

# Import Stash-compatible logging
import log
import plugin_data

# Import resilient StashDB API utilities
import stashbox_api
import theporndb_api

# The plugin's own Stash server runs on this host. With HTTPS its cert names a public
# host while we connect via localhost, so hostname checks would fail. Don't verify.
STASH_SSL_CONTEXT = stashbox_api.create_ssl_context(verify=False)

# Whisparr: verified unless the user opts out (whisparrSkipTlsVerify) for a self-signed cert
WHISPARR_SSL_CONTEXT = stashbox_api.create_ssl_context()


def configure_tls(plugin_settings):
    """Apply TLS-related plugin settings."""
    global WHISPARR_SSL_CONTEXT
    if plugin_settings.get("whisparrSkipTlsVerify"):
        WHISPARR_SSL_CONTEXT = stashbox_api.create_ssl_context(verify=False)


# ============================================================================
# Local Stash_ID Cache for Pagination
# ============================================================================

# In-memory cache: endpoint -> set of stash_ids
_local_stash_id_cache: dict[str, set[str]] = {}

# Metadata about the cache
_cache_metadata: dict[str, dict] = {}

# Disk cache configuration
CACHE_TTL_SECONDS = 300  # 5 minutes
# Lives in Stash's config dir (plugin_data/missingScenes); main() re-points it after
# plugin_data.configure(). Cache functions read this at call time.
CACHE_DIR = plugin_data.current_dir()


def _md5_hex(text: str) -> str:
    """md5 as a cache key only (not security); flag it so FIPS builds allow it."""
    try:
        return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()
    except TypeError:  # Python < 3.9
        return hashlib.md5(text.encode()).hexdigest()


def _get_cache_filepath(endpoint: str) -> str:
    """Get the file path for a cached endpoint's stash_ids."""
    return os.path.join(CACHE_DIR, f".cache_stashids_{_md5_hex(endpoint)[:12]}.json")


def _read_cache_from_disk(endpoint: str) -> set[str] | None:
    """Read cached stash_ids from disk if fresh enough. Returns None if stale/missing/corrupt."""
    filepath = _get_cache_filepath(endpoint)
    try:
        if not os.path.exists(filepath):
            return None
        mtime = os.path.getmtime(filepath)
        if time.time() - mtime > CACHE_TTL_SECONDS:
            log.LogDebug(f"Cache file expired for {endpoint}")
            return None
        with open(filepath, 'r') as f:
            data = json.load(f)
        ids = data.get("stash_ids") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            log.LogWarning(f"Ignoring malformed cache file for {endpoint}; rebuilding")
            return None
        stash_ids = set(ids)
        log.LogInfo(f"Loaded {len(stash_ids)} stash_ids from disk cache for {endpoint}")
        return stash_ids
    except Exception as e:
        log.LogWarning(f"Failed to read the cache file ({e}); rebuilding")
        return None


def _write_cache_to_disk(endpoint: str, stash_ids: set[str]) -> None:
    """Write stash_ids to disk cache (unique temp file, then atomic replace)."""
    filepath = _get_cache_filepath(endpoint)
    tmp_path = None
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), prefix=".cache_tmp_", suffix=".json")
        data = {"stash_ids": sorted(stash_ids), "endpoint": endpoint, "count": len(stash_ids)}
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_path, filepath)
        tmp_path = None
        log.LogDebug(f"Wrote {len(stash_ids)} stash_ids to disk cache")
    except Exception as e:
        log.LogWarning(f"Failed to write cache to disk: {e}")
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def invalidate_cache(endpoint: str) -> None:
    """Drop the in-memory and on-disk stash_id cache for an endpoint."""
    _local_stash_id_cache.pop(endpoint, None)
    _cache_metadata.pop(endpoint, None)
    try:
        os.remove(_get_cache_filepath(endpoint))
    except FileNotFoundError:
        pass
    except OSError as e:
        log.LogWarning(f"Could not remove the cache file: {e}")


def _get_cache_info(endpoint: str) -> dict:
    """Get cache metadata for API responses."""
    meta = _cache_metadata.get(endpoint, {})
    return {
        "source": meta.get("source", "unknown"),
        "count": meta.get("count", 0),
        "build_time_ms": meta.get("build_time_ms", 0),
    }


# ============================================================================
# Cursor Encoding/Decoding for Pagination
# ============================================================================

def encode_cursor(state: dict) -> str:
    """Encode pagination state as a base64 cursor string."""
    json_str = json.dumps(state, separators=(',', ':'))
    return base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('ascii')


def decode_cursor(cursor: str) -> dict | None:
    """Decode a base64 cursor string back to pagination state.

    Returns None if cursor is invalid, empty, or None.
    """
    if not cursor:
        return None
    try:
        json_str = base64.urlsafe_b64decode(cursor.encode('ascii')).decode('utf-8')
        return json.loads(json_str)
    except Exception:
        return None


# ============================================================================
# Local Stash API
# ============================================================================

# Global to cache the connection info (stdin can only be read once)
_stash_connection = None
_input_data = None


def get_stash_connection():
    """Get Stash connection details from plugin input."""
    global _stash_connection, _input_data

    if _stash_connection is not None:
        return _stash_connection

    # These are passed by the Stash plugin system
    try:
        if _input_data is None:
            _input_data = json.loads(sys.stdin.read())
        server_connection = _input_data.get("server_connection", {})

        # Handle 0.0.0.0 binding - can't connect TO 0.0.0.0, use localhost instead
        # See: https://github.com/stashapp/stash/issues/5103
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

    # Use session cookie if available
    if conn.get("api_key"):
        headers["Cookie"] = f"session={conn['api_key']}"

    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    req = urllib.request.Request(conn["url"], data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30, context=STASH_SSL_CONTEXT) as response:
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


def get_available_endpoints_for_entity(entity_stash_ids):
    """Get stash-box endpoints that both the entity is linked to AND are configured in Stash.

    Args:
        entity_stash_ids: List of {endpoint, stash_id} dicts from the entity

    Returns:
        List of {endpoint, name, stash_id} dicts for valid endpoints
    """
    configured_boxes = get_stashbox_config()
    if not configured_boxes:
        return []

    # Build lookup of configured endpoints
    configured_lookup = {box["endpoint"]: box for box in configured_boxes}

    available = []
    for sid in entity_stash_ids:
        endpoint = sid.get("endpoint")
        if endpoint in configured_lookup:
            box = configured_lookup[endpoint]
            available.append({
                "endpoint": endpoint,
                "name": box.get("name", endpoint),
                "stash_id": sid.get("stash_id")
            })

    return available


def get_local_performer(performer_id):
    """Get a performer from local Stash with their stash_ids."""
    query = """
    query FindPerformer($id: ID!) {
        findPerformer(id: $id) {
            id
            name
            stash_ids {
                endpoint
                stash_id
            }
        }
    }
    """
    data = stash_graphql(query, {"id": performer_id})
    if data:
        return data.get("findPerformer")
    return None


def get_local_studio(studio_id):
    """Get a studio from local Stash with their stash_ids."""
    query = """
    query FindStudio($id: ID!) {
        findStudio(id: $id) {
            id
            name
            stash_ids {
                endpoint
                stash_id
            }
        }
    }
    """
    data = stash_graphql(query, {"id": studio_id})
    if data:
        return data.get("findStudio")
    return None


def get_local_tag(tag_id):
    """Get a tag from local Stash with its stash_ids."""
    query = """
    query FindTag($id: ID!) {
        findTag(id: $id) {
            id
            name
            stash_ids {
                endpoint
                stash_id
            }
        }
    }
    """
    data = stash_graphql(query, {"id": tag_id})
    if data:
        return data.get("findTag")
    return None


def get_local_scene_stash_ids(endpoint):
    """Get all stash_ids for scenes that are linked to a specific stash-box endpoint."""
    # Get all scenes with stash_ids
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

        # Check if we've gotten all scenes
        total = data["findScenes"].get("count", 0)
        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"Found {len(all_stash_ids)} local scenes linked to {endpoint}")
    return all_stash_ids


def get_favorite_stash_ids(entity_type: str, endpoint: str) -> set[str]:
    """Get stash_ids for all favorited entities of a given type.

    Fetches all favorited performers/studios/tags from local Stash and returns
    the set of their StashDB stash_ids for the specified endpoint.

    Args:
        entity_type: "performer", "studio", or "tag"
        endpoint: StashDB endpoint URL to match stash_ids against

    Returns:
        Set of StashDB IDs for favorites linked to that endpoint
    """
    # Build query based on entity type
    if entity_type == "performer":
        query = """
        query FindFavoritePerformers($filter: FindFilterType) {
            findPerformers(
                filter: $filter
                performer_filter: { filter_favorites: true }
            ) {
                count
                performers {
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
        result_key = "findPerformers"
        items_key = "performers"
    elif entity_type == "studio":
        query = """
        query FindFavoriteStudios($filter: FindFilterType) {
            findStudios(
                filter: $filter
                studio_filter: { favorite: true }
            ) {
                count
                studios {
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
        result_key = "findStudios"
        items_key = "studios"
    elif entity_type == "tag":
        query = """
        query FindFavoriteTags($filter: FindFilterType) {
            findTags(
                filter: $filter
                tag_filter: { favorite: true }
            ) {
                count
                tags {
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
        result_key = "findTags"
        items_key = "tags"
    else:
        log.LogWarning(f"Unknown entity type for favorites: {entity_type}")
        return set()

    stash_ids = set()
    page = 1
    per_page = 100

    while True:
        data = stash_graphql(query, {
            "filter": {
                "page": page,
                "per_page": per_page
            }
        })

        if not data or result_key not in data:
            break

        result = data[result_key]
        items = result.get(items_key, [])

        if not items:
            break

        for item in items:
            for sid in item.get("stash_ids", []):
                if sid.get("endpoint") == endpoint:
                    stash_ids.add(sid.get("stash_id"))

        # Check if we've fetched all items
        total = result.get("count", 0)
        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"Found {len(stash_ids)} favorite {entity_type}s linked to {endpoint}")
    return stash_ids


def get_favorite_stash_ids_limited(entity_type: str, endpoint: str, limit: int = 100) -> set[str]:
    """Get stash_ids for favorited entities, sorted by engagement with a limit.

    Args:
        entity_type: "performer", "studio", or "tag"
        endpoint: StashDB endpoint URL
        limit: Maximum number of favorites to return

    Returns:
        Set of StashDB IDs for top favorites
    """
    # Determine sort field based on entity type
    if entity_type == "performer":
        sort_field = "last_o_at"
        query = """
        query FindFavoritePerformers($filter: FindFilterType) {
            findPerformers(
                filter: $filter
                performer_filter: { filter_favorites: true }
            ) {
                count
                performers {
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
        result_key = "findPerformers"
        items_key = "performers"
    elif entity_type == "studio":
        sort_field = "scenes_count"
        query = """
        query FindFavoriteStudios($filter: FindFilterType) {
            findStudios(
                filter: $filter
                studio_filter: { favorite: true }
            ) {
                count
                studios {
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
        result_key = "findStudios"
        items_key = "studios"
    elif entity_type == "tag":
        sort_field = "scenes_count"
        query = """
        query FindFavoriteTags($filter: FindFilterType) {
            findTags(
                filter: $filter
                tag_filter: { favorite: true }
            ) {
                count
                tags {
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
        result_key = "findTags"
        items_key = "tags"
    else:
        log.LogWarning(f"Unknown entity type for favorites: {entity_type}")
        return set()

    stash_ids = set()
    collected = 0
    page = 1
    per_page = min(100, limit)  # Don't fetch more than needed

    while collected < limit:
        data = stash_graphql(query, {
            "filter": {
                "page": page,
                "per_page": per_page,
                "sort": sort_field,
                "direction": "DESC"
            }
        })

        if not data or result_key not in data:
            break

        result = data[result_key]
        items = result.get(items_key, [])

        if not items:
            break

        for item in items:
            if collected >= limit:
                break
            for sid in item.get("stash_ids", []):
                if sid.get("endpoint") == endpoint:
                    stash_ids.add(sid.get("stash_id"))
                    collected += 1
                    break  # Only count once per entity

        total = result.get("count", 0)
        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"Found {len(stash_ids)} favorite {entity_type}s (limit: {limit}) linked to {endpoint}")
    return stash_ids


# ============================================================================
# Whisparr API (v3 - Compatible with Stasharr approach)
# ============================================================================

class WhisparrError(Exception):
    """A failed Whisparr request. `url` never contains the API key (it is sent in a header)."""

    def __init__(self, message, status=None, url=None, body=""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = (body or "")[:300]


def normalize_whisparr_url(raw):
    """Turn what a user typed into the Whisparr base URL (scheme + host + optional URL Base)."""
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    url = url.split("#", 1)[0].split("?", 1)[0]
    url = re.sub(r"/api(/.*)?$", "", url.rstrip("/"), flags=re.IGNORECASE)
    return url.rstrip("/")


def _whisparr_detail(body):
    """Pull Whisparr's validation messages out of an error body; else a raw snippet."""
    snippet = (body or "")[:300]
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return snippet
    items = parsed if isinstance(parsed, list) else [parsed]
    texts = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("errorMessage") or item.get("message")
            if text:
                texts.append(str(text))
    return "; ".join(texts) if texts else snippet


def whisparr_request(whisparr_url, api_key, endpoint, method="GET", payload=None):
    """Make a request to the Whisparr API. Raises WhisparrError on any failure."""
    url = f"{normalize_whisparr_url(whisparr_url)}/api/v3/{endpoint}"
    # The endpoint may carry a query but the key never goes in the URL
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = None
    if payload:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    log.LogDebug(f"[Whisparr] Starting {method} request to {endpoint}...")
    try:
        with urllib.request.urlopen(req, timeout=30, context=WHISPARR_SSL_CONTEXT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        except Exception:
            err_body = ""
        detail = _whisparr_detail(err_body)
        msg = f"Whisparr returned HTTP {e.code} for {method} {url}"
        if detail:
            msg += f": {detail}"
        log.LogError(msg)
        raise WhisparrError(msg, status=e.code, url=url, body=err_body) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            msg = (f"Can't reach Whisparr at {url}: TLS certificate verification failed ({e.reason}). "
                   "If Whisparr uses a self-signed certificate, enable "
                   "'Whisparr: Skip TLS Verification' in the plugin settings.")
        else:
            msg = f"Can't reach Whisparr at {url}: {e.reason}"
        log.LogError(msg)
        raise WhisparrError(msg, url=url) from None
    except (OSError, ValueError) as e:  # timeouts, resets, bad URLs
        msg = f"Can't reach Whisparr at {url}: {e}"
        log.LogError(msg)
        raise WhisparrError(msg, url=url) from None

    log.LogDebug(f"[Whisparr] {method} {endpoint} completed successfully")
    # DELETE requests return empty body on success
    if not body or body.strip() == "":
        return None
    try:
        return json.loads(body)
    except ValueError:
        msg = (f"Whisparr at {url} did not return JSON. Check the Whisparr URL "
               "(it should be the address of Whisparr itself, plus any URL Base).")
        log.LogError(msg)
        raise WhisparrError(msg, url=url, body=body) from None


def whisparr_get_scene_by_stash_id(whisparr_url, api_key, stash_id):
    """Find a scene in Whisparr by its StashDB ID.

    Returns the scene dict whose stashId equals stash_id, or None if Whisparr has no such
    scene. Raises WhisparrError if the request fails.
    """
    endpoint = f"movie?stashId={urllib.parse.quote(stash_id)}"
    result = whisparr_request(whisparr_url, api_key, endpoint)
    if isinstance(result, list):
        for scene in result:
            if isinstance(scene, dict) and scene.get("stashId") == stash_id:
                return scene
    return None


def whisparr_lookup_scene(whisparr_url, api_key, stash_id):
    """Lookup a scene in TPDB via Whisparr by its StashDB ID (lookup/scene?term=stash:<id>).

    Returns the scene data to add, or None if nothing matches. When results carry a
    stashId, only the one equal to stash_id is used. Raises WhisparrError on failure.
    """
    endpoint = f"lookup/scene?term=stash:{urllib.parse.quote(stash_id)}"
    result = whisparr_request(whisparr_url, api_key, endpoint)
    if not isinstance(result, list):
        return None
    scenes = []
    for item in result:
        # The lookup returns a wrapper with a 'movie' field
        scene = item.get("movie") if isinstance(item, dict) and "movie" in item else item
        if isinstance(scene, dict):
            scenes.append(scene)
    for scene in scenes:
        if scene.get("stashId") == stash_id:
            return scene
    if any(scene.get("stashId") for scene in scenes):
        return None  # results identify other scenes; don't add the wrong one
    return scenes[0] if scenes else None


def whisparr_add_scene(whisparr_url, api_key, scene_data, quality_profile_id, root_folder, search_on_add=False):
    """Add a scene to Whisparr (POST movie). Returns the added scene; raises WhisparrError."""
    payload = {
        "foreignId": scene_data.get("foreignId"),
        "title": scene_data.get("title"),
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder,
        "monitored": True,
        "tags": [],
        "addOptions": {
            "searchForMovie": search_on_add
        }
    }

    result = whisparr_request(whisparr_url, api_key, "movie", "POST", payload)
    log.LogInfo(f"Added scene to Whisparr: {scene_data.get('title')}")
    invalidate_whisparr_status(whisparr_url)
    return result


def whisparr_trigger_search(whisparr_url, api_key, movie_id):
    """Trigger a search for a specific scene in Whisparr. Raises WhisparrError on failure."""
    payload = {
        "name": "MoviesSearch",
        "movieIds": [movie_id]
    }
    result = whisparr_request(whisparr_url, api_key, "command", "POST", payload)
    log.LogInfo(f"Triggered search for movie {movie_id}")
    return result


def whisparr_get_all_scenes(whisparr_url, api_key):
    """Get all scenes from Whisparr (one request, no pagination). Raises WhisparrError."""
    scenes = whisparr_request(whisparr_url, api_key, "movie")
    if scenes is None:
        scenes = []
    if not isinstance(scenes, list):
        raise WhisparrError("Whisparr returned an unexpected reply for the movie list.",
                            url=normalize_whisparr_url(whisparr_url))
    log.LogInfo(f"Found {len(scenes)} scenes in Whisparr")
    return scenes


def whisparr_get_queue(whisparr_url, api_key):
    """Get the current download queue from Whisparr. Raises WhisparrError."""
    result = whisparr_request(whisparr_url, api_key, "queue?pageSize=1000")
    records = result.get("records") or [] if isinstance(result, dict) else []
    log.LogInfo(f"Found {len(records)} items in Whisparr queue")
    return records


WHISPARR_STATUS_TTL_SECONDS = 60


def _whisparr_status_path(whisparr_url):
    digest = hashlib.sha256(normalize_whisparr_url(whisparr_url).encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"whisparr_status_{digest}.json")


def invalidate_whisparr_status(whisparr_url):
    """Drop the cached status map (after we change something in Whisparr)."""
    try:
        os.remove(_whisparr_status_path(whisparr_url))
    except OSError:
        pass


def _read_whisparr_status_cache(whisparr_url):
    try:
        with open(_whisparr_status_path(whisparr_url)) as f:
            data = json.load(f)
        if time.time() - float(data["ts"]) < WHISPARR_STATUS_TTL_SECONDS and isinstance(data["map"], dict):
            return data["map"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_whisparr_status_cache(whisparr_url, status_map):
    filepath = _whisparr_status_path(whisparr_url)
    tmp_path = None
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), prefix=".whisparr_tmp_", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"ts": time.time(), "map": status_map}, f)
        os.replace(tmp_path, filepath)
        tmp_path = None
    except Exception as e:
        log.LogWarning(f"Failed to cache the Whisparr status: {e}")
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def whisparr_get_status_map(whisparr_url, api_key):
    """Build a map of StashDB IDs to their Whisparr status (cached for 60s on disk).

    Combines the movie list and the download queue.

    Status values:
        - "downloading": Actively downloading (with progress %)
        - "queued": In queue, waiting to start
        - "stalled": In queue but stalled/warning
        - "waiting": In Whisparr, no file, not in queue (needs search)
        - "downloaded": Has file

    Returns:
        Dict mapping stash_id -> {"status", "progress", "eta", "error", "whisparr_id"}
        (progress/eta/error only where they apply).

    Raises:
        WhisparrError if Whisparr can't be queried (errors are never cached).
    """
    cached = _read_whisparr_status_cache(whisparr_url)
    if cached is not None:
        return cached

    status_map = {}

    log.LogDebug("[Whisparr] Fetching all scenes...")
    scenes = whisparr_get_all_scenes(whisparr_url, api_key)
    log.LogDebug(f"[Whisparr] Got {len(scenes)} scenes")

    # Build a map of whisparr movie ID -> stash_id for queue lookups
    whisparr_id_to_stash_id = {}

    for scene in scenes:
        # Whisparr stores the StashDB ID in stashId field directly (UUID format)
        stash_id = scene.get("stashId") or ""

        if not stash_id:
            continue

        whisparr_id = scene.get("id")
        has_file = scene.get("hasFile", False)

        whisparr_id_to_stash_id[whisparr_id] = stash_id

        # Initial status based on hasFile; "waiting" may be updated by the queue check
        status_map[stash_id] = {
            "status": "downloaded" if has_file else "waiting",
            "whisparr_id": whisparr_id
        }

    log.LogDebug("[Whisparr] Fetching queue...")
    queue = whisparr_get_queue(whisparr_url, api_key)
    log.LogDebug(f"[Whisparr] Got {len(queue)} queue items")

    for item in queue:
        movie_id = item.get("movieId")
        stash_id = whisparr_id_to_stash_id.get(movie_id)

        if not stash_id:
            continue

        # Any of these can be null in Whisparr's JSON
        queue_status = (item.get("status") or "").lower()
        tracked_state = (item.get("trackedDownloadState") or "").lower()
        error_message = item.get("errorMessage") or ""

        size = item.get("size") or 0
        size_left = item.get("sizeleft") or 0
        progress = 0
        if size > 0:
            progress = round(((size - size_left) / size) * 100, 1)

        eta = item.get("timeleft")

        if queue_status == "warning" or "stalled" in error_message.lower():
            status_map[stash_id] = {
                "status": "stalled",
                "progress": progress,
                "eta": eta,
                "error": error_message,
                "whisparr_id": movie_id
            }
        elif queue_status == "downloading" or tracked_state == "downloading":
            status_map[stash_id] = {
                "status": "downloading",
                "progress": progress,
                "eta": eta,
                "whisparr_id": movie_id
            }
        elif queue_status == "queued":
            status_map[stash_id] = {
                "status": "queued",
                "eta": eta,
                "whisparr_id": movie_id
            }
        else:
            # Some other queue state - mark as queued with progress info
            status_map[stash_id] = {
                "status": "queued",
                "progress": progress,
                "eta": eta,
                "whisparr_id": movie_id
            }

    log.LogInfo(f"Built status map for {len(status_map)} Whisparr scenes")
    _write_whisparr_status_cache(whisparr_url, status_map)
    return status_map


def _whisparr_status_for_response(whisparr_url, api_key):
    """(status_map, error_string) for attaching to a response; error is None on success."""
    try:
        return whisparr_get_status_map(whisparr_url, api_key), None
    except WhisparrError as e:
        return {}, str(e)
    except Exception as e:
        log.LogError(f"Unexpected error fetching the Whisparr status: {e}")
        return {}, f"Could not fetch the Whisparr status: {e}"


# ============================================================================
# Local Stash_ID Cache Building
# ============================================================================

def get_or_build_cache(endpoint: str) -> set[str]:
    """Get or build the local stash_id cache for a given endpoint.

    Checks: 1) in-memory cache, 2) disk cache, 3) builds from scratch.
    """
    from datetime import datetime

    # 1. Check in-memory cache (same process only)
    if endpoint in _local_stash_id_cache:
        if endpoint not in _cache_metadata:
            _cache_metadata[endpoint] = {"source": "memory", "count": len(_local_stash_id_cache[endpoint])}
        return _local_stash_id_cache[endpoint]

    # 2. Check disk cache (survives across process invocations)
    disk_cache = _read_cache_from_disk(endpoint)
    if disk_cache is not None:
        _local_stash_id_cache[endpoint] = disk_cache
        _cache_metadata[endpoint] = {
            "count": len(disk_cache),
            "built_at": datetime.now().isoformat(),
            "source": "disk"
        }
        return disk_cache

    # 3. Build from scratch
    log.LogInfo(f"Building local stash_id cache for {endpoint}...")
    build_start = time.time()

    stash_ids = set()
    page = 1
    per_page = 1000

    while True:
        result = stash_graphql("""
            query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
                findScenes(filter: $filter, scene_filter: $scene_filter) {
                    count
                    scenes {
                        stash_ids {
                            endpoint
                            stash_id
                        }
                    }
                }
            }
        """, {
            "filter": {"page": page, "per_page": per_page},
            "scene_filter": {
                "stash_id_endpoint": {
                    "endpoint": endpoint,
                    "modifier": "NOT_NULL"
                }
            }
        })

        if not result:
            raise RuntimeError("Could not read scenes from Stash to build the local index (no response)")
        find_scenes = result.get("findScenes") or {}
        total_count = find_scenes.get("count", 0)
        scenes = find_scenes.get("scenes", [])

        for scene in scenes:
            for sid in scene.get("stash_ids", []):
                if sid.get("endpoint") == endpoint:
                    stash_ids.add(sid.get("stash_id"))

        if page * per_page >= total_count:
            break
        page += 1

    # Save to both memory and disk
    _local_stash_id_cache[endpoint] = stash_ids
    build_time_ms = int((time.time() - build_start) * 1000)
    _cache_metadata[endpoint] = {
        "count": len(stash_ids),
        "built_at": datetime.now().isoformat(),
        "source": "built",
        "build_time_ms": build_time_ms
    }
    _write_cache_to_disk(endpoint, stash_ids)

    log.LogInfo(f"Cache built: {len(stash_ids)} scenes with stash_ids for {endpoint} ({build_time_ms}ms)")
    return stash_ids


def count_local_scenes_for_entity(endpoint: str, entity_type: str, entity_id: str) -> int:
    """Count local scenes that match an entity AND have a stash_id for the endpoint.

    Args:
        endpoint: StashDB endpoint URL
        entity_type: "performer", "studio", or "tag"
        entity_id: Local Stash ID of the entity

    Returns:
        Count of matching scenes with stash_ids for the endpoint
    """
    # Build the scene filter based on entity type
    if entity_type == "performer":
        entity_filter = {
            "performers": {
                "value": [entity_id],
                "modifier": "INCLUDES"
            }
        }
    elif entity_type == "studio":
        entity_filter = {
            "studios": {
                "value": [entity_id],
                "modifier": "INCLUDES",
                "depth": -1  # Include child studios
            }
        }
    elif entity_type == "tag":
        entity_filter = {
            "tags": {
                "value": [entity_id],
                "modifier": "INCLUDES",
                "depth": -1  # Include child tags
            }
        }
    else:
        log.LogWarning(f"Unknown entity type: {entity_type}")
        return 0

    # Combine with stash_id filter
    scene_filter = {
        **entity_filter,
        "stash_id_endpoint": {
            "endpoint": endpoint,
            "modifier": "NOT_NULL"
        }
    }

    # Query just the count (no need to fetch all scenes)
    result = stash_graphql("""
        query FindScenes($scene_filter: SceneFilterType) {
            findScenes(scene_filter: $scene_filter) {
                count
            }
        }
    """, {
        "scene_filter": scene_filter
    })

    if result and "findScenes" in result:
        count = result["findScenes"].get("count", 0)
        log.LogDebug(f"Local scenes for {entity_type} {entity_id} with {endpoint}: {count}")
        return count

    return 0


# ============================================================================
# Fetch Until Full Pagination
# ============================================================================

# Safety limits
MAX_PAGES_PER_REQUEST = 50  # Max StashDB pages to fetch in one request
MAX_STASHDB_PAGE = 1000  # Absolute limit (100,000 scenes)
PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 50


def scene_passes_favorite_filters(scene, favorite_performer_ids, favorite_studio_ids, favorite_tag_ids):
    """Check if a scene passes all enabled favorite filters.

    Args:
        scene: Scene object from StashDB
        favorite_performer_ids: Set of favorite performer stash_ids, or None if not filtering
        favorite_studio_ids: Set of favorite studio stash_ids, or None if not filtering
        favorite_tag_ids: Set of favorite tag stash_ids, or None if not filtering

    Returns:
        True if scene passes all enabled filters (AND logic)
    """
    # If no filters enabled, pass
    if favorite_performer_ids is None and favorite_studio_ids is None and favorite_tag_ids is None:
        return True

    # Check performer filter (scene must have at least one favorite performer)
    if favorite_performer_ids is not None:
        scene_performer_ids = set()
        for p in scene.get("performers", []):
            performer = p.get("performer", {})
            if performer.get("id"):
                scene_performer_ids.add(performer["id"])
        if not scene_performer_ids & favorite_performer_ids:
            return False

    # Check studio filter (scene's studio must be a favorite)
    if favorite_studio_ids is not None:
        studio = scene.get("studio")
        if not studio or studio.get("id") not in favorite_studio_ids:
            return False

    # Check tag filter (scene must have at least one favorite tag)
    if favorite_tag_ids is not None:
        scene_tag_ids = {t.get("id") for t in scene.get("tags", []) if t.get("id")}
        if not scene_tag_ids & favorite_tag_ids:
            return False

    return True


def scene_has_excluded_tags(scene, excluded_tag_ids):
    """Check if a scene has any excluded tags.

    Args:
        scene: Scene object from StashDB
        excluded_tag_ids: Set of tag stash_ids to exclude, or None/empty if not filtering

    Returns:
        True if scene has at least one excluded tag (should be filtered out)
    """
    if not excluded_tag_ids:
        return False

    scene_tag_ids = {t.get("id") for t in scene.get("tags", []) if t.get("id")}
    return bool(scene_tag_ids & excluded_tag_ids)


def fetch_until_full(url, api_key, entity_type, entity_stash_id, local_ids,
                     page_size=PAGE_SIZE_DEFAULT, stashdb_page=1, offset=0,
                     sort="DATE", direction="DESC", plugin_settings=None,
                     favorite_performer_ids=None, favorite_studio_ids=None,
                     favorite_tag_ids=None, excluded_tag_ids=None):
    """
    Fetch scenes from StashDB until we have page_size missing scenes.

    This implements the "fetch until full" pagination strategy where we
    incrementally fetch StashDB pages, filtering against local_ids cache,
    until we have enough missing scenes to fill the requested page.

    Args:
        url: StashDB GraphQL endpoint URL
        api_key: API key for authentication
        entity_type: "performer", "studio", or "tag"
        entity_stash_id: StashDB ID of the entity
        local_ids: Set of stash_ids we own locally
        page_size: Number of missing scenes to return
        stashdb_page: StashDB page to start from
        offset: Number of scenes to skip on the first page
        sort: Sort field for StashDB query
        direction: Sort direction
        plugin_settings: Plugin configuration
        favorite_performer_ids: Set of favorite performer stash_ids to filter by, or None
        favorite_studio_ids: Set of favorite studio stash_ids to filter by, or None
        favorite_tag_ids: Set of favorite tag stash_ids to filter by, or None
        excluded_tag_ids: Set of tag stash_ids to exclude, or None

    Returns:
        dict with:
            - scenes: list of missing scene objects (up to page_size)
            - total_on_stashdb: total scene count on StashDB
            - next_cursor: cursor for fetching next page (None if complete)
            - is_complete: True if we've checked all StashDB scenes
            - stashdb_pages_fetched: number of pages fetched
    """
    page_size = min(page_size, PAGE_SIZE_MAX)
    collected = []
    total_on_stashdb = 0
    pages_fetched = 0
    current_page = stashdb_page
    current_offset = offset
    is_complete = False
    # Track cursor position for continuation
    resume_page = stashdb_page
    resume_offset = offset

    while len(collected) < page_size and pages_fetched < MAX_PAGES_PER_REQUEST:
        if current_page > MAX_STASHDB_PAGE:
            log.LogWarning(f"Reached max StashDB page limit ({MAX_STASHDB_PAGE})")
            is_complete = True
            break

        if theporndb_api.is_theporndb(url):
            result = theporndb_api.query_scenes_page(
                api_key, entity_type, entity_stash_id,
                page=current_page,
                per_page=100,
                sort=sort,
                direction=direction,
                plugin_settings=plugin_settings
            )
        else:
            result = stashbox_api.query_scenes_page(
                url, api_key, entity_type, entity_stash_id,
                page=current_page,
                per_page=100,
                sort=sort,
                direction=direction,
                plugin_settings=plugin_settings
            )

        if not result:
            log.LogWarning(f"Failed to fetch StashDB page {current_page}")
            break

        pages_fetched += 1
        total_on_stashdb = result["count"]
        scenes = result["scenes"]

        if not scenes:
            is_complete = True
            break

        # Process scenes starting from offset
        filled = False
        for i, scene in enumerate(scenes):
            if i < current_offset:
                continue

            scene_id = scene.get("id")
            # Check if scene is missing locally, passes favorite filters, and has no excluded tags
            if scene_id and scene_id not in local_ids:
                if (scene_passes_favorite_filters(scene, favorite_performer_ids,
                                                  favorite_studio_ids, favorite_tag_ids)
                        and not scene_has_excluded_tags(scene, excluded_tag_ids)):
                    collected.append(scene)
                if len(collected) >= page_size:
                    # Save position for next request
                    # We've processed up to index i (inclusive), so next starts at i+1
                    resume_offset = i + 1
                    if resume_offset >= len(scenes):
                        # Move to next page
                        resume_page = current_page + 1
                        resume_offset = 0
                    else:
                        resume_page = current_page
                    filled = True
                    break

        # If we didn't fill up, move to next page
        if not filled:
            if not result["has_more"]:
                is_complete = True
                break
            current_page += 1
            current_offset = 0

    # Build cursor for continuation
    next_cursor = None
    if not is_complete and len(collected) >= page_size:
        cursor_state = {
            "stashdb_page": resume_page,
            "offset": resume_offset,
            "sort": sort,
            "direction": direction,
            "entity_type": entity_type,
            "entity_stash_id": entity_stash_id,
            "endpoint": url
        }
        next_cursor = encode_cursor(cursor_state)

    return {
        "scenes": collected,
        "total_on_stashdb": total_on_stashdb,
        "next_cursor": next_cursor,
        "is_complete": is_complete,
        "stashdb_pages_fetched": pages_fetched
    }


# ============================================================================
# Main Operations
# ============================================================================

def find_missing_scenes_paginated(entity_type, entity_id, plugin_settings,
                                   endpoint_override=None, page_size=PAGE_SIZE_DEFAULT,
                                   cursor=None, sort="DATE", direction="DESC",
                                   filter_favorite_performers=False,
                                   filter_favorite_studios=False,
                                   filter_favorite_tags=False):
    """
    Find missing scenes with pagination support.

    This is the new paginated version that returns results incrementally
    using the "fetch until full" strategy.

    Args:
        entity_type: "performer", "studio", or "tag"
        entity_id: Local Stash ID of the entity
        plugin_settings: Plugin configuration from Stash
        endpoint_override: Optional endpoint URL to use
        page_size: Number of missing scenes per page (default 50, max 100)
        cursor: Pagination cursor from previous request (None for first page)
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT"
        direction: Sort direction - "ASC" or "DESC"
        filter_favorite_performers: If True, only show scenes with favorite performers
        filter_favorite_studios: If True, only show scenes from favorite studios
        filter_favorite_tags: If True, only show scenes with favorite tags

    Returns:
        Dict with:
            - entity_name, entity_type, stashdb_name, stashdb_url
            - total_on_stashdb: total scenes on StashDB
            - total_local: scenes you own (from cache)
            - missing_count_estimate: estimated missing (null until complete)
            - missing_count_loaded: how many missing we've found so far
            - cursor: cursor for next page
            - has_more: whether more results available
            - is_complete: true if we've checked all StashDB scenes
            - missing_scenes: array of scene objects
            - whisparr_configured: boolean
    """
    page_size = min(max(1, page_size), PAGE_SIZE_MAX)

    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

    # Determine endpoint
    target_endpoint = endpoint_override or plugin_settings.get("stashBoxEndpoint", "").strip()

    stashbox = None
    if target_endpoint:
        for config in stashbox_configs:
            if config["endpoint"] == target_endpoint:
                stashbox = config
                break
        if not stashbox:
            available = ", ".join([c.get("name", c["endpoint"]) for c in stashbox_configs])
            return {"error": f"Stash-box endpoint '{target_endpoint}' not found. Available: {available}"}
    else:
        stashbox = stashbox_configs[0]

    stashdb_url = stashbox["endpoint"]
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    # Decode cursor if provided
    cursor_state = None
    if cursor:
        cursor_state = decode_cursor(cursor)
        if cursor_state:
            # Validate cursor matches request
            if (cursor_state.get("entity_type") != entity_type or
                cursor_state.get("endpoint") != stashdb_url):
                log.LogWarning("Cursor mismatch, starting fresh")
                cursor_state = None

    # Get entity stash_id
    if entity_type == "performer":
        entity = get_local_performer(entity_id)
    elif entity_type == "studio":
        entity = get_local_studio(entity_id)
    elif entity_type == "tag":
        entity = get_local_tag(entity_id)
    else:
        return {"error": f"Unknown entity type: {entity_type}"}

    if not entity:
        return {"error": f"{entity_type.title()} not found: {entity_id}"}

    # Find stash_id for this endpoint
    entity_stash_id = None
    for sid in entity.get("stash_ids", []):
        if sid.get("endpoint") == stashdb_url:
            entity_stash_id = sid.get("stash_id")
            break

    if not entity_stash_id:
        return {
            "error": f"{entity_type.title()} '{entity.get('name')}' is not linked to {stashdb_name}. "
                     f"Please use the Tagger to link this {entity_type} first."
        }

    # Get or build local stash_id cache (for filtering)
    local_ids = get_or_build_cache(stashdb_url)

    # Parse excluded tags from settings (for client-side filtering)
    excluded_tags_str = plugin_settings.get("excludedTags", "").strip()
    excluded_tag_ids = set()
    if excluded_tags_str:
        excluded_tag_ids = {t.strip() for t in excluded_tags_str.split(",") if t.strip()}

    # Count local scenes matching this specific entity (for accurate "You Have" display)
    total_local = count_local_scenes_for_entity(stashdb_url, entity_type, entity_id)

    # Determine starting position
    if cursor_state:
        stashdb_page = cursor_state.get("stashdb_page", 1)
        offset = cursor_state.get("offset", 0)
        sort = cursor_state.get("sort", sort)
        direction = cursor_state.get("direction", direction)
    else:
        stashdb_page = 1
        offset = 0

    # Fetch favorite stash_ids if any filters are enabled
    favorite_performer_ids = None
    favorite_studio_ids = None
    favorite_tag_ids = None

    # Build list of enabled filters that have no favorites (for early return)
    empty_filters = []

    if filter_favorite_performers:
        favorite_performer_ids = get_favorite_stash_ids("performer", stashdb_url)
        if not favorite_performer_ids:
            empty_filters.append("performers")

    if filter_favorite_studios:
        favorite_studio_ids = get_favorite_stash_ids("studio", stashdb_url)
        if not favorite_studio_ids:
            empty_filters.append("studios")

    if filter_favorite_tags:
        favorite_tag_ids = get_favorite_stash_ids("tag", stashdb_url)
        if not favorite_tag_ids:
            empty_filters.append("tags")

    # Early return if any filter is enabled but has no favorites
    # (AND logic means if any filter can't match, no results are possible)
    if empty_filters:
        log.LogInfo(f"Filters enabled but no favorites found for: {', '.join(empty_filters)}")
        return {
            "entity_name": entity.get("name"),
            "entity_type": entity_type,
            "stashdb_name": stashdb_name,
            "stashdb_url": stashdb_url.replace("/graphql", ""),
            "total_on_stashdb": 0,
            "total_local": total_local,
            "missing_count_estimate": None,
            "missing_count_loaded": 0,
            "cursor": None,
            "has_more": False,
            "is_complete": True,
            "missing_scenes": [],
            "whisparr_configured": bool(plugin_settings.get("whisparrUrl") and
                                        plugin_settings.get("whisparrApiKey")),
            "empty_filter_types": empty_filters  # Frontend can use this for messaging
        }

    # Fetch missing scenes
    result = fetch_until_full(
        url=stashdb_url,
        api_key=stashdb_api_key,
        entity_type=entity_type,
        entity_stash_id=entity_stash_id,
        local_ids=local_ids,
        page_size=page_size,
        stashdb_page=stashdb_page,
        offset=offset,
        sort=sort,
        direction=direction,
        plugin_settings=plugin_settings,
        favorite_performer_ids=favorite_performer_ids,
        favorite_studio_ids=favorite_studio_ids,
        favorite_tag_ids=favorite_tag_ids,
        excluded_tag_ids=excluded_tag_ids
    )

    # Format scenes
    formatted_scenes = []
    whisparr_status_map = {}
    whisparr_configured = False
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    whisparr_error = None
    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        whisparr_status_map, whisparr_error = _whisparr_status_for_response(whisparr_url, whisparr_api_key)

    for scene in result["scenes"]:
        scene_stash_id = scene.get("id")
        formatted = format_scene(scene, scene_stash_id)
        if scene_stash_id in whisparr_status_map:
            formatted["whisparr_status"] = whisparr_status_map[scene_stash_id]
        else:
            formatted["whisparr_status"] = None
        formatted["in_whisparr"] = scene_stash_id in whisparr_status_map
        formatted_scenes.append(formatted)

    total_on_stashdb = result["total_on_stashdb"]
    is_complete = result["is_complete"]

    # Determine which filters are active (for frontend display)
    active_filters = []
    if filter_favorite_performers:
        active_filters.append("performers")
    if filter_favorite_studios:
        active_filters.append("studios")
    if filter_favorite_tags:
        active_filters.append("tags")

    # When filters are active, the estimate is not meaningful
    # (we'd have to scan all pages to know the filtered count)
    filters_active = len(active_filters) > 0
    missing_count_estimate = None
    if not filters_active and not is_complete:
        missing_count_estimate = total_on_stashdb - total_local

    return {
        "entity_name": entity.get("name"),
        "entity_type": entity_type,
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": total_on_stashdb,
        "total_local": total_local,
        "missing_count_estimate": missing_count_estimate,
        "missing_count_loaded": len(formatted_scenes),
        "cursor": result["next_cursor"],
        "has_more": result["next_cursor"] is not None,
        "is_complete": is_complete,
        "missing_scenes": formatted_scenes,
        "whisparr_configured": whisparr_configured,
        **({"whisparr_error": whisparr_error} if whisparr_error else {}),
        "filters_active": filters_active,
        "active_filters": active_filters,
        "active_filter_tag_ids": list(favorite_tag_ids) if favorite_tag_ids else [],
        "excluded_tags_applied": len(excluded_tag_ids) > 0,
        "cache_info": _get_cache_info(stashdb_url),
    }


def format_scene(scene, stash_id):
    """Format a StashDB scene for the frontend."""
    # Get the best image (prefer landscape for thumbnails)
    images = scene.get("images", [])
    thumbnail = None
    if images:
        # Try to find a landscape image first
        for img in images:
            if img.get("width", 0) > img.get("height", 0):
                thumbnail = img.get("url")
                break
        if not thumbnail:
            thumbnail = images[0].get("url")

    # Format performers
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

    # Get studio
    studio = scene.get("studio")
    studio_info = None
    if studio:
        studio_info = {
            "id": studio.get("id"),
            "name": studio.get("name")
        }

    # Get primary URL
    urls = scene.get("urls", [])
    primary_url = urls[0].get("url") if urls else None

    # Format tags
    tags = [
        {"id": t.get("id"), "name": t.get("name")}
        for t in scene.get("tags", [])
        if t.get("id") and t.get("name")
    ]

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
        "tags": tags,
        "url": primary_url
    }


def browse_stashdb(plugin_settings, endpoint_override=None, page_size=50, cursor=None,
                   sort="DATE", direction="DESC",
                   filter_favorite_performers=False, filter_favorite_studios=False,
                   filter_favorite_tags=False):
    """
    Browse all StashDB scenes without entity context.

    Args:
        plugin_settings: Plugin configuration
        endpoint_override: Optional endpoint URL to use (from UI dropdown)
        page_size: Number of missing scenes per page
        cursor: Pagination cursor
        sort: Sort field
        direction: Sort direction
        filter_favorite_performers: Filter by favorite performers
        filter_favorite_studios: Filter by favorite studios
        filter_favorite_tags: Filter by favorite tags

    Returns:
        Dict with missing scenes and metadata
    """
    page_size = min(max(1, page_size), PAGE_SIZE_MAX)

    # Validate sort and direction
    valid_sorts = {"DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"}
    valid_directions = {"ASC", "DESC"}
    if sort not in valid_sorts:
        sort = "DATE"
    if direction not in valid_directions:
        direction = "DESC"

    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

    # Use endpoint override, configured default, or first endpoint
    target_endpoint = endpoint_override or plugin_settings.get("stashBoxEndpoint", "").strip()
    stashbox = None

    if target_endpoint:
        for config in stashbox_configs:
            if config["endpoint"] == target_endpoint:
                stashbox = config
                break
        if not stashbox:
            return {"error": f"Stash-box endpoint '{target_endpoint}' not found"}
    else:
        stashbox = stashbox_configs[0]

    stashdb_url = stashbox["endpoint"]
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    # Decode cursor if provided
    cursor_state = decode_cursor(cursor) if cursor else None
    if cursor_state:
        stashdb_page = cursor_state.get("stashdb_page", 1)
        offset = cursor_state.get("offset", 0)
        # Validate cursor values
        if not isinstance(stashdb_page, int) or stashdb_page < 1:
            stashdb_page = 1
        if not isinstance(offset, int) or offset < 0:
            offset = 0
    else:
        stashdb_page = 1
        offset = 0

    # Get local stash_id cache
    local_ids = get_or_build_cache(stashdb_url)

    # Parse excluded tags from settings
    excluded_tags_str = plugin_settings.get("excludedTags", "").strip()
    excluded_tag_ids = []
    if excluded_tags_str:
        excluded_tag_ids = [t.strip() for t in excluded_tags_str.split(",") if t.strip()]

    # Get favorite limit
    favorite_limit = int(plugin_settings.get("favoriteLimit") or 100)

    # Fetch favorite IDs if filters enabled
    performer_ids = None
    studio_ids = None
    tag_ids = None

    if filter_favorite_performers:
        performer_ids = get_favorite_stash_ids_limited("performer", stashdb_url, limit=favorite_limit)
        if not performer_ids:
            return _empty_browse_result(stashdb_name, stashdb_url, plugin_settings, ["performers"])

    if filter_favorite_studios:
        studio_ids = get_favorite_stash_ids_limited("studio", stashdb_url, limit=favorite_limit)
        if not studio_ids:
            return _empty_browse_result(stashdb_name, stashdb_url, plugin_settings, ["studios"])

    if filter_favorite_tags:
        tag_ids = get_favorite_stash_ids_limited("tag", stashdb_url, limit=favorite_limit)
        if not tag_ids:
            return _empty_browse_result(stashdb_name, stashdb_url, plugin_settings, ["tags"])

    # Fetch scenes using browse query
    collected = []
    pages_fetched = 0
    total_on_stashdb = 0
    is_complete = False
    current_page = stashdb_page
    current_offset = offset
    resume_page = current_page
    resume_offset = current_offset

    while len(collected) < page_size and pages_fetched < MAX_PAGES_PER_REQUEST:
        if theporndb_api.is_theporndb(stashdb_url):
            result = theporndb_api.query_scenes_browse(
                stashdb_api_key,
                page=current_page,
                per_page=100,
                sort=sort,
                direction=direction,
                performer_ids=list(performer_ids) if performer_ids else None,
                studio_ids=list(studio_ids) if studio_ids else None,
                tag_ids=list(tag_ids) if tag_ids else None,
                excluded_tag_ids=excluded_tag_ids if excluded_tag_ids else None,
                plugin_settings=plugin_settings
            )
        else:
            result = stashbox_api.query_scenes_browse(
                stashdb_url, stashdb_api_key,
                page=current_page,
                per_page=100,
                sort=sort,
                direction=direction,
                performer_ids=list(performer_ids) if performer_ids else None,
                studio_ids=list(studio_ids) if studio_ids else None,
                tag_ids=list(tag_ids) if tag_ids else None,
                excluded_tag_ids=excluded_tag_ids if excluded_tag_ids else None,
                plugin_settings=plugin_settings
            )

        if not result:
            log.LogWarning(f"Failed to fetch browse page {current_page}")
            break

        pages_fetched += 1
        total_on_stashdb = result["count"]
        scenes = result["scenes"]

        if not scenes:
            is_complete = True
            break

        # Filter out owned scenes and apply favorite filters
        for i, scene in enumerate(scenes):
            if i < current_offset:
                continue

            scene_id = scene.get("id")
            if scene_id and scene_id not in local_ids:
                # Apply favorite filters client-side (StashDB query only handles excludes)
                if scene_passes_favorite_filters(scene, performer_ids, studio_ids, tag_ids):
                    collected.append(scene)

            if len(collected) >= page_size:
                # Save position for next request
                resume_offset = i + 1
                if resume_offset >= len(scenes):
                    resume_page = current_page + 1
                    resume_offset = 0
                else:
                    resume_page = current_page
                break

        if len(collected) >= page_size:
            break

        if not result["has_more"]:
            is_complete = True
            break

        current_page += 1
        current_offset = 0

    # Build cursor for continuation
    next_cursor = None
    if not is_complete and len(collected) >= page_size:
        cursor_state = {
            "stashdb_page": resume_page,
            "offset": resume_offset,
            "sort": sort,
            "direction": direction
        }
        next_cursor = encode_cursor(cursor_state)

    # Format scenes and add Whisparr status
    formatted_scenes = []
    whisparr_status_map = {}
    whisparr_configured = False
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    whisparr_error = None
    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        whisparr_status_map, whisparr_error = _whisparr_status_for_response(whisparr_url, whisparr_api_key)

    for scene in collected:
        scene_stash_id = scene.get("id")
        formatted = format_scene(scene, scene_stash_id)
        formatted["whisparr_status"] = whisparr_status_map.get(scene_stash_id)
        formatted["in_whisparr"] = scene_stash_id in whisparr_status_map
        formatted_scenes.append(formatted)

    filters_active = filter_favorite_performers or filter_favorite_studios or filter_favorite_tags

    return {
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": total_on_stashdb,
        "missing_count_loaded": len(formatted_scenes),
        "cursor": next_cursor,
        "has_more": next_cursor is not None,
        "is_complete": is_complete,
        "missing_scenes": formatted_scenes,
        "whisparr_configured": whisparr_configured,
        **({"whisparr_error": whisparr_error} if whisparr_error else {}),
        "filters_active": filters_active,
        "active_filter_tag_ids": list(tag_ids) if tag_ids else [],
        "excluded_tags_applied": len(excluded_tag_ids) > 0,
        "cache_info": _get_cache_info(stashdb_url),
    }


def _empty_browse_result(stashdb_name, stashdb_url, plugin_settings, empty_filter_types):
    """Return empty result when a filter has no favorites."""
    return {
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": 0,
        "missing_count_loaded": 0,
        "cursor": None,
        "has_more": False,
        "is_complete": True,
        "missing_scenes": [],
        "whisparr_configured": bool(plugin_settings.get("whisparrUrl") and
                                    plugin_settings.get("whisparrApiKey")),
        "filters_active": True,
        "excluded_tags_applied": False,
        "empty_filter_types": empty_filter_types
    }


def add_to_whisparr(stash_id, title, plugin_settings):
    """Add a scene to Whisparr by its StashDB ID.

    Uses the same approach as Stasharr:
    1. Check if scene already exists (movie?stashId=X)
    2. Lookup scene in TPDB (lookup/scene?term=stash:X)
    3. Add scene to Whisparr (POST movie)
    4. Optionally trigger search

    Args:
        stash_id: StashDB scene ID (UUID)
        title: Scene title (for logging/error messages)
        plugin_settings: Plugin configuration from Stash
    """
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")
    quality_profile = int(plugin_settings.get("whisparrQualityProfile") or 1)  # Default to first profile
    root_folder = plugin_settings.get("whisparrRootFolder", "")
    search_on_add = plugin_settings.get("whisparrSearchOnAdd", False)  # Default to False for manual control

    if not whisparr_url or not whisparr_api_key:
        return {"error": "Whisparr is not configured. Please set URL and API key in plugin settings."}

    if not root_folder:
        return {"error": "Whisparr root folder is not configured. Please set it in plugin settings."}

    try:
        # Step 1: Check if scene already exists in Whisparr
        existing_scene = whisparr_get_scene_by_stash_id(whisparr_url, whisparr_api_key, stash_id)
        if existing_scene:
            has_file = existing_scene.get("hasFile", False)
            if has_file:
                return {
                    "success": True,
                    "message": f"Scene '{title}' already exists in Whisparr with file.",
                    "already_exists": True
                }
            else:
                # Scene exists but no file - maybe trigger search?
                if search_on_add:
                    whisparr_trigger_search(whisparr_url, whisparr_api_key, existing_scene["id"])
                    return {
                        "success": True,
                        "message": f"Scene '{title}' already in Whisparr. Triggered search.",
                        "already_exists": True
                    }
                return {
                    "success": True,
                    "message": f"Scene '{title}' already in Whisparr (no file yet). Use Whisparr to search.",
                    "already_exists": True
                }

        # Step 2: Lookup scene in TPDB via Whisparr
        scene_data = whisparr_lookup_scene(whisparr_url, whisparr_api_key, stash_id)
        if not scene_data:
            return {
                "error": f"Scene '{title}' not found in TPDB/Whisparr lookup. "
                         "It may not be indexed in ThePornDB yet, or the StashDB ID "
                         "doesn't have a matching TPDB entry. Try searching manually in Whisparr."
            }

        # Step 3: Add scene to Whisparr
        added_scene = whisparr_add_scene(
            whisparr_url,
            whisparr_api_key,
            scene_data,
            quality_profile,
            root_folder,
            search_on_add=search_on_add
        )

        if not added_scene:
            return {"success": False, "error": f"Whisparr accepted the request but returned nothing for '{title}'."}

        search_msg = " and triggered search" if search_on_add else ""
        return {
            "success": True,
            "message": f"Added '{title}' to Whisparr{search_msg}.",
            "scene": added_scene
        }

    except WhisparrError as e:
        log.LogError(f"Error adding scene to Whisparr: {e}")
        return {"success": False, "error": str(e), "whisparr_error": str(e)}
    except Exception as e:
        log.LogError(f"Error adding scene to Whisparr: {e}")
        return {"error": str(e)}


def whisparr_delete_scene(whisparr_url, api_key, movie_id, delete_files=False):
    """Delete a scene from Whisparr.

    Args:
        whisparr_url: Whisparr base URL
        api_key: Whisparr API key
        movie_id: Whisparr movie/scene ID
        delete_files: Whether to delete associated files (default False)

    Returns:
        True on success, raises on failure
    """
    endpoint = f"movie/{movie_id}?deleteFiles={'true' if delete_files else 'false'}"
    try:
        whisparr_request(whisparr_url, api_key, endpoint, method="DELETE")
    except WhisparrError as e:
        if e.status == 404:
            # Scene already deleted - that's fine
            log.LogInfo(f"Scene {movie_id} already removed from Whisparr (404)")
            invalidate_whisparr_status(whisparr_url)
            return True
        raise
    log.LogInfo(f"Deleted scene {movie_id} from Whisparr")
    invalidate_whisparr_status(whisparr_url)
    return True


def whisparr_unmonitor_scene(whisparr_url, api_key, movie_id):
    """Unmonitor a scene in Whisparr (keeps it but prevents re-downloading).

    Args:
        whisparr_url: Whisparr base URL
        api_key: Whisparr API key
        movie_id: Whisparr movie/scene ID

    Returns:
        Updated scene data, raises on failure
    """
    endpoint = f"movie/{movie_id}"
    scene = whisparr_request(whisparr_url, api_key, endpoint)
    if not isinstance(scene, dict):
        raise WhisparrError(f"Whisparr has no scene with id {movie_id} to unmonitor.",
                            url=f"{normalize_whisparr_url(whisparr_url)}/api/v3/{endpoint}")

    scene["monitored"] = False
    result = whisparr_request(whisparr_url, api_key, endpoint, method="PUT", payload=scene)
    log.LogInfo(f"Unmonitored scene {movie_id} in Whisparr")
    invalidate_whisparr_status(whisparr_url)
    return result


# ============================================================================
# Automation: Hook and Task Handlers
# ============================================================================

def handle_scene_update_hook(scene_input, plugin_settings):
    """Handle Scene.Update.Post hook - cleanup Whisparr when scene is tagged.

    This is triggered whenever a scene is updated. We check if:
    1. Auto-cleanup is enabled
    2. Whisparr is configured
    3. The scene now has a StashDB stash_id
    4. The scene exists in Whisparr

    If all conditions are met, we remove/unmonitor the scene from Whisparr.
    """
    # Check if auto-cleanup is enabled
    if not plugin_settings.get("enableAutoCleanup", False):
        log.LogDebug("Auto-cleanup is disabled, skipping")
        return {"success": True, "message": "Auto-cleanup disabled"}

    # Check if Whisparr is configured
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    if not whisparr_url or not whisparr_api_key:
        log.LogDebug("Whisparr not configured, skipping auto-cleanup")
        return {"success": True, "message": "Whisparr not configured"}

    # Get the scene ID from the hook input
    scene_id = scene_input.get("id")
    if not scene_id:
        log.LogWarning("No scene ID in hook input")
        return {"success": False, "message": "No scene ID"}

    # Fetch the scene with its stash_ids
    scene_data = stash_graphql("""
        query FindScene($id: ID!) {
            findScene(id: $id) {
                id
                title
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
    """, {"id": scene_id})

    if not scene_data or not scene_data.get("findScene"):
        log.LogWarning(f"Could not find scene {scene_id}")
        return {"success": False, "message": "Scene not found"}

    scene = scene_data["findScene"]
    stash_ids = scene.get("stash_ids", [])

    if not stash_ids:
        log.LogDebug(f"Scene {scene_id} has no stash_ids, skipping")
        return {"success": True, "message": "Scene not tagged"}

    # Get the stash-box endpoint we're using
    stashbox_configs = get_stashbox_config()
    preferred_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()

    if preferred_endpoint:
        target_endpoint = preferred_endpoint
    elif stashbox_configs:
        target_endpoint = stashbox_configs[0].get("endpoint", "")
    else:
        log.LogWarning("No stash-box endpoint configured")
        return {"success": False, "message": "No stash-box configured"}

    # Find the stash_id for our target endpoint
    stash_id = None
    for sid in stash_ids:
        if sid.get("endpoint") == target_endpoint:
            stash_id = sid.get("stash_id")
            break

    if not stash_id:
        log.LogDebug(f"Scene {scene_id} not tagged with {target_endpoint}")
        return {"success": True, "message": "Scene not tagged with target endpoint"}

    # Check if scene exists in Whisparr
    try:
        whisparr_scene = whisparr_get_scene_by_stash_id(whisparr_url, whisparr_api_key, stash_id)
    except WhisparrError as e:
        log.LogError(f"Whisparr cleanup skipped for scene {scene_id}: {e}")
        return {"success": False, "message": str(e), "error": str(e), "whisparr_error": str(e)}

    if not whisparr_scene:
        log.LogDebug(f"Scene with StashDB ID {stash_id} not in Whisparr")
        return {"success": True, "message": "Scene not in Whisparr"}

    # Remove or unmonitor the scene
    unmonitor_only = plugin_settings.get("unmonitorOnly", False)
    scene_title = scene.get("title", "Unknown")

    try:
        if unmonitor_only:
            whisparr_unmonitor_scene(whisparr_url, whisparr_api_key, whisparr_scene["id"])
            log.LogInfo(f"Unmonitored '{scene_title}' in Whisparr after tagging")
            return {"success": True, "message": f"Unmonitored '{scene_title}' in Whisparr"}
        else:
            whisparr_delete_scene(whisparr_url, whisparr_api_key, whisparr_scene["id"])
            log.LogInfo(f"Removed '{scene_title}' from Whisparr after tagging")
            return {"success": True, "message": f"Removed '{scene_title}' from Whisparr"}
    except Exception as e:
        log.LogError(f"Failed to cleanup Whisparr for scene {scene_id}: {e}")
        return {"success": False, "message": str(e), "error": str(e)}


def task_scan_for_new_scenes(plugin_settings):
    """Task: Trigger a Stash scan on the configured scan path.

    Uses the user's default scan settings from Stash configuration.
    """
    scan_path = plugin_settings.get("scanPath", "").strip()

    if not scan_path:
        log.LogWarning("Scan path not configured")
        return {"success": False, "message": "Scan path not configured. Set it in plugin settings."}

    log.LogInfo(f"Triggering scan on path: {scan_path}")

    try:
        # First, get the user's scan settings from UI taskDefaults
        # Note: configuration.defaults.scan is different from the UI settings
        # The actual user-configured scan defaults are in configuration.ui.taskDefaults.scan
        config_result = stash_graphql("""
            query Configuration {
                configuration {
                    ui
                }
            }
        """)

        # Build scan input with user's defaults
        scan_input = {"paths": [scan_path]}

        if config_result and "configuration" in config_result:
            ui_config = config_result["configuration"].get("ui", {})
            # ui is a JSON Map, parse it if it's a string
            if isinstance(ui_config, str):
                import json as json_module
                ui_config = json_module.loads(ui_config)

            task_defaults = ui_config.get("taskDefaults", {})
            scan_defaults = task_defaults.get("scan", {})

            if scan_defaults:
                # Map the UI task defaults to scan input fields
                scan_input["scanGenerateCovers"] = scan_defaults.get("scanGenerateCovers", True)
                scan_input["scanGeneratePreviews"] = scan_defaults.get("scanGeneratePreviews", False)
                scan_input["scanGenerateImagePreviews"] = scan_defaults.get("scanGenerateImagePreviews", False)
                scan_input["scanGenerateSprites"] = scan_defaults.get("scanGenerateSprites", True)
                scan_input["scanGeneratePhashes"] = scan_defaults.get("scanGeneratePhashes", True)
                scan_input["scanGenerateThumbnails"] = scan_defaults.get("scanGenerateThumbnails", False)
                scan_input["scanGenerateClipPreviews"] = scan_defaults.get("scanGenerateClipPreviews", False)
                log.LogInfo(f"Using user's scan defaults: covers={scan_input['scanGenerateCovers']}, previews={scan_input['scanGeneratePreviews']}, sprites={scan_input['scanGenerateSprites']}")

        result = stash_graphql("""
            mutation MetadataScan($input: ScanMetadataInput!) {
                metadataScan(input: $input)
            }
        """, {"input": scan_input})

        if result:
            log.LogInfo(f"Scan started for {scan_path}")
            return {"success": True, "message": f"Scan started for {scan_path}"}
        else:
            return {"success": False, "message": "Failed to start scan"}
    except Exception as e:
        log.LogError(f"Failed to trigger scan: {e}")
        return {"success": False, "message": str(e)}


def test_whisparr_connection(settings):
    """Check the Whisparr settings against the real service.

    Returns {ok, app, version, url, root_folders, quality_profiles, problems}. `ok` is true only
    when `problems` is empty. `url` is the normalized base URL (never the API key).
    """
    settings = settings or {}
    raw_url = str(settings.get("whisparrUrl") or "").strip()
    api_key = str(settings.get("whisparrApiKey") or "").strip()
    result = {"ok": False, "app": None, "version": None, "url": normalize_whisparr_url(raw_url),
              "root_folders": [], "quality_profiles": [], "problems": []}
    problems = result["problems"]

    if not raw_url:
        problems.append("Whisparr URL is empty. Set it in the plugin settings.")
    if not api_key:
        problems.append("Whisparr API Key is empty. Set it in the plugin settings.")
    if problems:
        return result
    url = result["url"]

    try:
        status = whisparr_request(url, api_key, "system/status")
    except WhisparrError as e:
        if e.status == 401:
            problems.append("API key rejected (HTTP 401). Copy it from Whisparr Settings > General > Security.")
        elif e.status == 404:
            problems.append(f"Not found at {url}/api/v3 (HTTP 404); check URL Base and include it in the "
                            "Whisparr URL if Whisparr uses one.")
        elif e.status is None and e.body:
            problems.append(f"{url} is the wrong service: it did not answer with JSON. Use the address "
                            "of Whisparr itself.")
        elif e.status is None:
            problems.append(f"Can't reach Whisparr at {url}. Check the address, port and that Whisparr is running.")
        else:
            problems.append(f"Whisparr answered HTTP {e.status} at {url}.")
        return result

    if not isinstance(status, dict):
        problems.append(f"{url} is the wrong service: unexpected answer from system/status.")
        return result
    app = str(status.get("appName") or "")
    result["app"] = app or None
    result["version"] = status.get("version")
    if app and app.lower() != "whisparr":
        problems.append(f"{url} is the wrong service: it is {app}, not Whisparr.")
        return result
    if str(result["version"] or "").startswith("2."):
        problems.append(f"Whisparr v3 is required; this is v{result['version']}. Use the v3 (eros) image.")

    try:
        roots = whisparr_request(url, api_key, "rootfolder")
        profiles = whisparr_request(url, api_key, "qualityprofile")
    except WhisparrError as e:
        problems.append(str(e))
        return result
    result["root_folders"] = [r.get("path") for r in roots or [] if isinstance(r, dict) and r.get("path")]
    result["quality_profiles"] = [{"id": q.get("id"), "name": q.get("name")}
                                  for q in profiles or [] if isinstance(q, dict)]

    root = str(settings.get("whisparrRootFolder") or "").strip()
    if root and root not in result["root_folders"]:
        problems.append(f"Root folder '{root}' is not in Whisparr. Valid: "
                        f"{', '.join(result['root_folders']) or 'none configured'}.")
    profile = settings.get("whisparrQualityProfile")
    if profile not in (None, ""):
        try:
            profile_id = int(profile)
        except (TypeError, ValueError):
            profile_id = None
        if profile_id not in [q["id"] for q in result["quality_profiles"]]:
            valid = ", ".join(f"{q['id']}: {q['name']}" for q in result["quality_profiles"])
            problems.append(f"Quality profile {profile} does not exist in Whisparr. Valid: {valid or 'none'}.")

    result["ok"] = not problems
    return result


def task_test_whisparr(plugin_settings):
    """Task: test the Whisparr connection and log a readable summary."""
    result = test_whisparr_connection(plugin_settings)
    if result["ok"]:
        log.LogInfo(f"Whisparr OK: {result['app']} {result['version']} at {result['url']}; "
                    f"{len(result['root_folders'])} root folder(s), "
                    f"{len(result['quality_profiles'])} quality profile(s)")
    else:
        for problem in result["problems"]:
            log.LogWarning(f"Whisparr: {problem}")
    return result


def task_cleanup_whisparr(plugin_settings):
    """Task: Remove scenes from Whisparr that are now tagged in Stash."""
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    if not whisparr_url or not whisparr_api_key:
        log.LogWarning("Whisparr not configured")
        return {"success": False, "message": "Whisparr not configured"}

    # Get the stash-box endpoint
    stashbox_configs = get_stashbox_config()
    preferred_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()

    if preferred_endpoint:
        target_endpoint = preferred_endpoint
    elif stashbox_configs:
        target_endpoint = stashbox_configs[0].get("endpoint", "")
    else:
        log.LogWarning("No stash-box endpoint configured")
        return {"success": False, "message": "No stash-box configured"}

    unmonitor_only = plugin_settings.get("unmonitorOnly", False)

    log.LogInfo("Starting Whisparr cleanup task...")

    # Get all scenes from Whisparr
    try:
        whisparr_scenes = whisparr_get_all_scenes(whisparr_url, whisparr_api_key)
    except WhisparrError as e:
        log.LogError(f"Whisparr cleanup failed: {e}")
        return {"success": False, "message": str(e), "error": str(e), "whisparr_error": str(e)}
    log.LogInfo(f"Found {len(whisparr_scenes)} scenes in Whisparr")

    # Get all local scenes with StashDB IDs
    local_stash_ids = get_local_scene_stash_ids(target_endpoint)
    log.LogInfo(f"Found {len(local_stash_ids)} local scenes tagged with {target_endpoint}")

    # Find and cleanup scenes that are in both
    cleaned_count = 0
    errors = []

    for scene in whisparr_scenes:
        stash_id = scene.get("stashId")
        if not stash_id:
            continue

        if stash_id in local_stash_ids:
            try:
                if unmonitor_only:
                    whisparr_unmonitor_scene(whisparr_url, whisparr_api_key, scene["id"])
                else:
                    whisparr_delete_scene(whisparr_url, whisparr_api_key, scene["id"])
                cleaned_count += 1
                log.LogInfo(f"Cleaned up: {scene.get('title', 'Unknown')}")
            except Exception as e:
                errors.append(f"{scene.get('title', 'Unknown')}: {e}")

    action = "unmonitored" if unmonitor_only else "removed"
    message = f"{action.title()} {cleaned_count} scenes from Whisparr"
    if errors:
        message += f" ({len(errors)} errors)"

    log.LogInfo(message)
    return {"success": True, "message": message, "cleaned": cleaned_count, "errors": errors}


# ============================================================================
# Plugin Entry Point
# ============================================================================

def main():
    """Main entry point for the plugin."""

    # Read input from stdin (uses cached version to avoid double-read issue)
    try:
        input_data = get_input_data()
    except json.JSONDecodeError as e:
        output = {"error": f"Invalid JSON input: {e}"}
        print(json.dumps(output))
        return

    # Keep state in Stash's config dir (never raises)
    global CACHE_DIR
    plugin_data.configure(input_data.get("server_connection") or {})
    CACHE_DIR = plugin_data.current_dir()

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
            plugin_settings = plugins_config.get("missingScenes", {})
    except Exception as e:
        log.LogWarning(f"Could not load plugin settings: {e}")

    configure_tls(plugin_settings)

    # Check if this is a hook call
    hook_context = input_data.get("args", {}).get("hookContext")
    if hook_context:
        hook_type = hook_context.get("type", "")
        hook_input = hook_context.get("input", {})

        log.LogDebug(f"Hook triggered: {hook_type}")

        if hook_type == "Scene.Update.Post":
            output = handle_scene_update_hook(hook_input, plugin_settings)
        else:
            output = {"success": True, "message": f"Unhandled hook type: {hook_type}"}

        print(json.dumps({"output": output}))
        return

    # Check if this is a task call
    args = input_data.get("args", {})
    mode = args.get("mode", "")

    if mode == "scan":
        log.LogInfo("Running task: Scan for New Scenes")
        output = task_scan_for_new_scenes(plugin_settings)
        print(json.dumps({"output": output}))
        return

    if mode == "cleanup":
        log.LogInfo("Running task: Cleanup Whisparr")
        output = task_cleanup_whisparr(plugin_settings)
        print(json.dumps({"output": output}))
        return

    if mode == "test_whisparr":
        log.LogInfo("Running task: Test Whisparr Connection")
        output = task_test_whisparr(plugin_settings)
        print(json.dumps({"output": output}))
        return

    # Handle regular operations (from UI plugin)
    operation = args.get("operation", "")
    output = {"error": "Unknown operation"}

    try:
        if operation == "find_missing":
            entity_type = args.get("entity_type", "performer")
            entity_id = args.get("entity_id", "")
            endpoint = args.get("endpoint")  # Optional endpoint override

            page_size = args.get("page_size")
            cursor = args.get("cursor")
            sort = args.get("sort", "DATE")
            direction = args.get("direction", "DESC")

            # Favorite entity filters
            filter_favorite_performers = args.get("filter_favorite_performers", False)
            filter_favorite_studios = args.get("filter_favorite_studios", False)
            filter_favorite_tags = args.get("filter_favorite_tags", False)

            if not entity_id:
                output = {"error": "entity_id is required"}
            else:
                output = find_missing_scenes_paginated(
                    entity_type, entity_id, plugin_settings,
                    endpoint_override=endpoint,
                    page_size=page_size or PAGE_SIZE_DEFAULT,
                    cursor=cursor,
                    sort=sort,
                    direction=direction,
                    filter_favorite_performers=filter_favorite_performers,
                    filter_favorite_studios=filter_favorite_studios,
                    filter_favorite_tags=filter_favorite_tags
                )

        elif operation == "browse_stashdb":
            endpoint = args.get("endpoint")
            page_size = args.get("page_size", PAGE_SIZE_DEFAULT)
            cursor = args.get("cursor")
            sort = args.get("sort", "DATE")
            direction = args.get("direction", "DESC")
            filter_favorite_performers = args.get("filter_favorite_performers", False)
            filter_favorite_studios = args.get("filter_favorite_studios", False)
            filter_favorite_tags = args.get("filter_favorite_tags", False)

            output = browse_stashdb(
                plugin_settings=plugin_settings,
                endpoint_override=endpoint,
                page_size=page_size,
                cursor=cursor,
                sort=sort,
                direction=direction,
                filter_favorite_performers=filter_favorite_performers,
                filter_favorite_studios=filter_favorite_studios,
                filter_favorite_tags=filter_favorite_tags
            )

        elif operation == "add_to_whisparr":
            stash_id = args.get("stash_id", "")
            title = args.get("title", "Unknown")

            if not stash_id:
                output = {"error": "stash_id is required"}
            else:
                output = add_to_whisparr(stash_id, title, plugin_settings)

        elif operation == "get_endpoints":
            entity_type = args.get("entity_type", "")
            entity_id = args.get("entity_id", "")

            if not entity_id or not entity_type:
                output = {"error": "entity_type and entity_id are required"}
            else:
                # Get the entity
                if entity_type == "performer":
                    entity = get_local_performer(entity_id)
                elif entity_type == "studio":
                    entity = get_local_studio(entity_id)
                elif entity_type == "tag":
                    entity = get_local_tag(entity_id)
                else:
                    entity = None

                if not entity:
                    output = {"error": f"{entity_type.title()} not found: {entity_id}"}
                else:
                    stash_ids = entity.get("stash_ids", [])
                    available = get_available_endpoints_for_entity(stash_ids)

                    # Determine which endpoint to use by default
                    preferred = plugin_settings.get("stashBoxEndpoint", "").strip()
                    default_endpoint = None

                    if preferred:
                        # Check if preferred endpoint is in available list
                        for ep in available:
                            if ep["endpoint"] == preferred:
                                default_endpoint = preferred
                                break

                    if not default_endpoint and available:
                        default_endpoint = available[0]["endpoint"]

                    output = {
                        "entity_name": entity.get("name"),
                        "available_endpoints": available,
                        "default_endpoint": default_endpoint,
                    }

        elif operation == "test_whisparr":
            output = test_whisparr_connection(plugin_settings)

        elif operation == "refresh_index":
            endpoint = args.get("endpoint")
            if not endpoint:
                boxes = get_stashbox_config()
                endpoint = boxes[0]["endpoint"] if boxes else None
            if not endpoint:
                output = {"error": "No stash-box endpoint configured"}
            else:
                invalidate_cache(endpoint)
                ids = get_or_build_cache(endpoint)
                output = {"success": True, "endpoint": endpoint, "count": len(ids)}

        elif operation == "get_all_endpoints":
            configured_boxes = get_stashbox_config()
            preferred = plugin_settings.get("stashBoxEndpoint", "").strip()

            endpoints = []
            for box in configured_boxes:
                endpoints.append({
                    "endpoint": box["endpoint"],
                    "name": box.get("name", box["endpoint"]),
                })

            default_endpoint = None
            if preferred:
                for ep in endpoints:
                    if ep["endpoint"] == preferred:
                        default_endpoint = preferred
                        break
            if not default_endpoint and endpoints:
                default_endpoint = endpoints[0]["endpoint"]

            output = {
                "available_endpoints": endpoints,
                "default_endpoint": default_endpoint,
            }

        elif operation:
            output = {"error": f"Unknown operation: {operation}"}
        else:
            # No operation specified - could be empty call
            output = {"success": True, "message": "No operation specified"}

    except Exception as e:
        log.LogError(f"Operation failed: {e}")
        output = {"error": str(e)}

    # Wrap output in PluginOutput structure expected by Stash
    if "error" in output:
        plugin_output = {"error": output["error"]}
    else:
        plugin_output = {"output": output}

    print(json.dumps(plugin_output))


if __name__ == "__main__":
    main()
