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
import sqlite3
import tempfile

# Import Stash-compatible logging
import log
import plugin_data
import fingerprint_index

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
# Lives in Stash's config dir (plugin_data/missingScenes): main() sets it after
# plugin_data.configure(). Until then it is only the plugin-dir fallback's path; nothing is
# created at import, so a read-only plugin dir works. Cache functions read this at call time.
CACHE_DIR = plugin_data.default_dir()


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


class CursorError(ValueError):
    """A pagination cursor that can't be used for this request (message is for the UI)."""


_CURSOR_FIELD_LABELS = {
    "entity_type": "entity type",
    "entity_stash_id": "performer, studio or tag",
    "endpoint": "stash-box endpoint",
    "sort": "sort order",
    "direction": "sort direction",
}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_cursor(cursor, expected: dict) -> dict:
    """Decode a pagination cursor and check that it belongs to this request.

    Args:
        cursor: the cursor string the UI sent back
        expected: {field: value} the cursor must carry (entity, endpoint, ...)

    Returns:
        The cursor state, with int `stashdb_page` from 1 to MAX_STASHDB_PAGE and int
        `offset` >= 0.

    Raises:
        CursorError: unreadable, a bad page or offset, past the page limit, or made for
            another request.
    """
    state = decode_cursor(cursor)
    if not isinstance(state, dict):
        raise CursorError("Invalid pagination cursor (unreadable). Start a new search.")
    page, offset = state.get("stashdb_page"), state.get("offset")
    if not (_is_int(page) and page >= 1 and _is_int(offset) and offset >= 0):
        raise CursorError("Invalid pagination cursor (page and offset must be whole numbers). "
                          "Start a new search.")
    if page > MAX_STASHDB_PAGE:
        raise CursorError(f"This search reached the stash-box's page limit ({MAX_STASHDB_PAGE} pages "
                          f"of 100 scenes), so later scenes can't be listed. Narrow it with a "
                          f"favorites filter or another sort order.")
    for key in ("sort", "direction"):
        if key in state and not isinstance(state[key], str):
            raise CursorError(f"Invalid pagination cursor (bad {key}). Start a new search.")
    for key, want in expected.items():
        if state.get(key) != want:
            label = _CURSOR_FIELD_LABELS.get(key, key)
            raise CursorError(f"This pagination cursor is for a different {label}. Start a new search.")
    return state


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


def is_stashdb_endpoint(url):
    """True when a stash-box URL is StashDB's (host stashdb.org, any case, slash or port).

    Whisparr v3 stores and looks up StashDB scene IDs only, so every Whisparr action checks this.
    """
    text = str(url or "").strip().lower().rstrip("/")
    if not text:
        return False
    if "://" not in text:
        text = "https://" + text
    try:
        return urllib.parse.urlsplit(text).hostname == "stashdb.org"
    except ValueError:
        return False


def get_stashdb_endpoint(boxes):
    """The endpoint of the first configured stash-box that is StashDB, or None."""
    for box in boxes or []:
        endpoint = box.get("endpoint") if isinstance(box, dict) else None
        if is_stashdb_endpoint(endpoint):
            return endpoint
    return None


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


def get_favorite_stash_ids_limited(entity_type: str, endpoint: str, limit: int = 100) -> list[str]:
    """Get stash_ids for favorited entities, most engaged first, with a limit.

    Performers are ordered by last_o_at, studios and tags by scene count (descending).

    Args:
        entity_type: "performer", "studio", or "tag"
        endpoint: StashDB endpoint URL
        limit: Maximum number of favorites to return

    Returns:
        The stash_ids of the top favorites, in that order (no duplicates). Callers that
        test membership make a set of it; ThePornDB browse uses the first few.
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
        return []

    stash_ids = []
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
                    if sid.get("stash_id") not in stash_ids:
                        stash_ids.append(sid.get("stash_id"))
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
    """A failed Whisparr request. `url` never contains the API key (it is sent in a header).

    tls: "certificate" when the certificate could not be verified (self-signed), "handshake"
    for another TLS failure (HTTPS to a plain HTTP port), None otherwise.
    """

    def __init__(self, message, status=None, url=None, body="", tls=None):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = (body or "")[:300]
        self.tls = tls


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


def whisparr_url_setting(settings):
    """The Whisparr URL setting, stripped and normalized ('' when unset)."""
    return normalize_whisparr_url(str((settings or {}).get("whisparrUrl") or "").strip())


def whisparr_root_folder_setting(settings):
    """The Whisparr root folder setting, stripped ('' when unset)."""
    return str((settings or {}).get("whisparrRootFolder") or "").strip()


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
        tls = None
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            tls = "certificate"
            msg = (f"Can't reach Whisparr at {url}: TLS certificate verification failed ({e.reason}). "
                   "If Whisparr uses a self-signed certificate, enable "
                   "'Whisparr: Skip TLS Verification' in the plugin settings.")
        elif isinstance(e.reason, ssl.SSLError):
            tls = "handshake"
            msg = (f"Can't reach Whisparr at {url}: the TLS handshake failed ({e.reason}). "
                   "If Whisparr doesn't serve HTTPS on this port, use http://.")
        else:
            msg = f"Can't reach Whisparr at {url}: {e.reason}"
        log.LogError(msg)
        raise WhisparrError(msg, url=url, tls=tls) from None
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


WHISPARR_QUEUE_PAGE_SIZE = 1000
WHISPARR_QUEUE_MAX_PAGES = 20


def whisparr_get_queue(whisparr_url, api_key):
    """Get Whisparr's whole download queue (every page). Raises WhisparrError.

    The queue decides what cleanup leaves alone, so it fails closed: a reply that isn't
    Whisparr's paging object with a list of item dicts, a totalRecords that the pages
    never reach, or more than WHISPARR_QUEUE_MAX_PAGES pages raises instead of giving
    a shorter queue.
    """
    base = normalize_whisparr_url(whisparr_url)
    records = []
    for page in range(1, WHISPARR_QUEUE_MAX_PAGES + 1):
        result = whisparr_request(whisparr_url, api_key,
                                  f"queue?page={page}&pageSize={WHISPARR_QUEUE_PAGE_SIZE}")
        batch = result.get("records") if isinstance(result, dict) else None
        if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
            raise WhisparrError("Whisparr returned an unexpected reply for the download queue "
                                "(no list of queue items), so nothing was changed.", url=base)
        records.extend(batch)
        total = result.get("totalRecords")
        if _is_int(total):
            if len(records) >= total:
                break
            if not batch:
                raise WhisparrError(f"Whisparr's download queue reports {total} items but returned "
                                    f"{len(records)}, so nothing was changed.", url=base)
        elif len(batch) < WHISPARR_QUEUE_PAGE_SIZE:
            break
    else:
        raise WhisparrError(f"Whisparr's download queue has more than "
                            f"{WHISPARR_QUEUE_MAX_PAGES * WHISPARR_QUEUE_PAGE_SIZE} items; "
                            "nothing was changed.", url=base)
    log.LogInfo(f"Found {len(records)} items in Whisparr queue")
    return records


def _queued_movie_ids(queue):
    """Whisparr movie ids that have an item in the download queue."""
    return {item.get("movieId") for item in queue or []
            if isinstance(item, dict) and item.get("movieId") is not None}


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

def get_or_build_cache(endpoint: str, fresh: bool = False) -> set[str]:
    """Get or build the local stash_id cache for a given endpoint.

    Checks: 1) in-memory cache, 2) disk cache, 3) builds from scratch.
    With fresh=True it skips 1 and 2 and always builds from Stash (for decisions such as
    Whisparr cleanup, where an index up to CACHE_TTL_SECONDS old, or one another run
    wrote meanwhile, won't do); the result still replaces both caches.
    """
    from datetime import datetime

    # 1. Check in-memory cache (same process only)
    if not fresh and endpoint in _local_stash_id_cache:
        if endpoint not in _cache_metadata:
            _cache_metadata[endpoint] = {"source": "memory", "count": len(_local_stash_id_cache[endpoint])}
        return _local_stash_id_cache[endpoint]

    # 2. Check disk cache (survives across process invocations)
    disk_cache = None if fresh else _read_cache_from_disk(endpoint)
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
# Fingerprint Index (scenes owned by fingerprint, #160)
# ============================================================================

FINGERPRINT_LIST_PAGE = 1000


def fingerprint_ownership(endpoint, plugin_settings, local_ids):
    """The stash-box scenes owned by fingerprint only, and the response fields about it.

    The index counts when the ignoreFingerprintMatches setting is off (the default) and
    an index for this endpoint has been built (a partial build counts).

    Returns:
        (ids, fields): the index's matched scene ids not already in local_ids, and
        {fingerprint_matching, fingerprint_index, fingerprint_index_complete (with an index)}.
    """
    if plugin_settings.get("ignoreFingerprintMatches"):
        return set(), {"fingerprint_matching": False, "fingerprint_index": False}
    index = fingerprint_index.read_index(CACHE_DIR, endpoint)
    if index is None:
        return set(), {"fingerprint_matching": True, "fingerprint_index": False}
    return {i for i in index["stash_ids"] if i not in local_ids}, {
        "fingerprint_matching": True,
        "fingerprint_index": True,
        "fingerprint_index_complete": index["complete"],
    }


def forget_fingerprint_matches(scene_id, endpoints):
    """Drop a local scene from each endpoint's fingerprint index, so its matches stop
    counting as owned. Best effort: a failure is logged, never raised."""
    for endpoint in endpoints:
        try:
            removed = fingerprint_index.forget_scene(CACHE_DIR, endpoint, scene_id)
        except (sqlite3.Error, OSError) as e:
            log.LogWarning(f"Could not drop scene {scene_id} from the fingerprint index for {endpoint}: {e}")
            continue
        if removed:
            log.LogInfo(f"Scene {scene_id}: dropped {removed} fingerprint match(es) from the {endpoint} index")


def list_scenes_without_stash_id(endpoint):
    """Every local scene with no stash_id for the endpoint: id, updated_at, and its files'
    duration and fingerprints.

    Uses stash_id_endpoint {endpoint, modifier: IS_NULL}. Stash puts the endpoint in the
    LEFT JOIN on scene_stash_ids and keeps rows whose stash_id is NULL
    (pkg/sqlite/criterion_handlers.go, stashIDCriterionHandler), so this lists untagged
    scenes and scenes tagged only on other boxes.

    Raises:
        RuntimeError: Stash didn't answer.
    """
    scenes = []
    page = 1
    while True:
        result = stash_graphql("""
            query FingerprintIndexScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
                findScenes(filter: $filter, scene_filter: $scene_filter) {
                    count
                    scenes {
                        id
                        updated_at
                        files {
                            duration
                            fingerprints {
                                type
                                value
                            }
                        }
                    }
                }
            }
        """, {
            "filter": {"page": page, "per_page": FINGERPRINT_LIST_PAGE, "sort": "id", "direction": "ASC"},
            "scene_filter": {"stash_id_endpoint": {"endpoint": endpoint, "modifier": "IS_NULL"}},
        })
        if not result:
            raise RuntimeError("Stash didn't answer the scene query (no response)")
        find_scenes = result.get("findScenes") or {}
        batch = find_scenes.get("scenes") or []
        scenes.extend(scene for scene in batch if isinstance(scene, dict))
        if not batch or page * FINGERPRINT_LIST_PAGE >= (find_scenes.get("count") or 0):
            return scenes
        page += 1


def build_fingerprint_index(plugin_settings, endpoint=None, progress=None):
    """Build or update the fingerprint index for one stash-box (the operation, and the task per box).

    Args:
        plugin_settings: Plugin configuration (request delay, retries)
        endpoint: stash-box GraphQL URL; default the stashBoxEndpoint setting, else the first box
        progress: optional progress(fraction), for the task

    Returns:
        fingerprint_index.build's counts (scanned, skipped, no_fingerprints, queried,
        remaining, matched, owned, partial, complete, and error/auth_error/rate_limited
        after a failed lookup) plus success, endpoint, stashdb_name and elapsed_ms.
        Just {"error"} when the stash-box isn't configured or Stash can't list the scenes.
    """
    boxes = get_stashbox_config()
    if not boxes:
        return {"error": "No stash-box endpoints configured in Stash settings"}
    target = (endpoint or plugin_settings.get("stashBoxEndpoint") or "").strip()
    box = next((b for b in boxes if b.get("endpoint") == target), None) if target else boxes[0]
    if box is None:
        available = ", ".join(b.get("name") or b.get("endpoint", "") for b in boxes)
        return {"error": f"Stash-box endpoint '{target}' not found. Available: {available}"}

    url = box["endpoint"]
    name = box.get("name") or url
    started = time.time()
    try:
        scenes = list_scenes_without_stash_id(url)
    except Exception as e:
        msg = f"Could not list the local scenes without a {name} ID: {e}"
        log.LogError(msg)
        return {"error": msg}
    log.LogInfo(f"Fingerprint index for {name}: {len(scenes)} local scenes have no {name} ID")

    def lookup(batches):
        return stashbox_api.find_scenes_by_fingerprints(url, box.get("api_key", ""), batches,
                                                        plugin_settings=plugin_settings)

    try:
        result = fingerprint_index.build(
            CACHE_DIR, url, scenes, lookup,
            request_delay=stashbox_api.get_config(plugin_settings, "request_delay"),
            box_name=name, progress=progress)
    except (sqlite3.Error, OSError) as e:
        msg = f"Could not write the fingerprint index for {name}: {e}"
        log.LogError(msg)
        return {"error": msg}

    result.update({
        "success": "error" not in result,
        "endpoint": url,
        "stashdb_name": name,
        "elapsed_ms": int((time.time() - started) * 1000),
    })
    summary = (f"Fingerprint index for {name}: {result['scanned']} scenes without a {name} ID, "
               f"{result['queried']} looked up, {result['skipped']} unchanged, "
               f"{result['no_fingerprints']} without fingerprints; {result['matched']} match "
               f"{result['owned']} {name} scenes")
    if "error" in result:
        log.LogWarning(f"{summary}. Stopped early: {result['error']}")
    else:
        log.LogInfo(summary)
    return result


def task_build_fingerprint_index(plugin_settings):
    """Task: build the fingerprint index for every configured stash-box.

    Returns {success, results: [build_fingerprint_index result per box]}; one box failing
    doesn't stop the others.
    """
    boxes = get_stashbox_config()
    if not boxes:
        msg = "No stash-box endpoints configured in Stash settings"
        log.LogWarning(msg)
        return {"success": False, "error": msg, "results": []}
    results = []
    for i, box in enumerate(boxes):
        def progress(fraction, i=i):
            log.LogProgress((i + fraction) / len(boxes))
        result = build_fingerprint_index(plugin_settings, endpoint=box["endpoint"], progress=progress)
        result.setdefault("endpoint", box["endpoint"])
        results.append(result)
        log.LogProgress((i + 1) / len(boxes))
    return {"success": all(r.get("success") for r in results), "results": results}


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


# Keys a fill result carries after a stash-box failure; the responses pass them through.
FETCH_FAILURE_KEYS = ("error", "partial", "auth_error", "rate_limited", "retry_after")

# A response with one of these keys carries results the UI renders even with an error:
# scenes (find_missing, browse_stashdb) or a fingerprint index build's counts.
RESULT_KEYS = ("missing_scenes", "scanned")


def _fetch_error_message(error, box_name, page):
    """A message for the UI naming the box and saying what to do."""
    if error.is_auth_error:
        return (f"{box_name} refused the request ({error}). Check the {box_name} API key in "
                f"Settings > Metadata Providers > Stash-box Endpoints.")
    if error.is_rate_limited:
        return (f"{box_name} is rate limiting requests ({error}). Retry later, "
                f"or raise the Request Delay plugin setting.")
    return f"{box_name} request for page {page} failed: {error}"


def _fill_page(fetch_page, qualifies, page_size, start_page, start_offset,
               plugin_settings, cursor_state, box_name, passed_over=None):
    """Fetch stash-box pages from (start_page, start_offset) until page_size scenes qualify.

    Shared by the missing-scenes and browse views.

    Args:
        fetch_page: fetch_page(page) -> {"scenes", "count", "has_more"}. It raises
            StashBoxAPIError on a failure; a None result (ThePornDB's functions) is a
            failed page too.
        qualifies: qualifies(scene) -> True when the scene belongs in the results.
        page_size: how many qualifying scenes fill the page.
        start_page, start_offset: where to start (from the cursor).
        plugin_settings: for `stashbox_request_delay`, slept between pages.
        cursor_state: the fields every cursor from this request carries (sort, endpoint...).
        box_name: the stash-box's name, for error messages.
        passed_over: optional passed_over(scene), called once for each scene this request
            skips for good (didn't qualify, and the next cursor starts after it).

    Stops at a full page, the last stash-box page, MAX_PAGES_PER_REQUEST pages
    (with a cursor to carry on), MAX_STASHDB_PAGE (with a cursor, which parse_cursor
    turns into a "page limit" error), or a failed page.

    Returns:
        dict with scenes, total_on_stashdb (None when no page was fetched), next_cursor
        (None when nothing is left to fetch), is_complete and stashdb_pages_fetched.
        After a failure it also has error, partial, auth_error, rate_limited and, when
        known, retry_after. partial is True when pages were fetched before the failure;
        next_cursor then resumes at the failed page. A failed first page gives no cursor.
    """
    request_delay = stashbox_api.get_config(plugin_settings, "request_delay")
    collected = []
    total_on_stashdb = None
    pages_fetched = 0
    page, offset = start_page, start_offset
    is_complete = False
    resume = None  # (page, offset) the next request starts from
    failure = None

    while True:
        if page > MAX_STASHDB_PAGE:
            # The cursor goes on, so the next request says the limit was reached
            log.LogWarning(f"Reached the stash-box page limit ({MAX_STASHDB_PAGE}); stopping")
            if pages_fetched:
                resume = (page, offset)
            break
        if pages_fetched >= MAX_PAGES_PER_REQUEST:
            log.LogInfo(f"Checked {pages_fetched} {box_name} pages without filling the page; "
                        f"the cursor continues from page {page}")
            resume = (page, offset)
            break
        if pages_fetched and request_delay > 0:
            time.sleep(request_delay)

        try:
            result = fetch_page(page)
            if result is None:
                raise stashbox_api.StashBoxAPIError("no response (see the Stash log for the cause)")
        except stashbox_api.StashBoxAPIError as e:
            log.LogWarning(f"{box_name} page {page} failed: {e}")
            failure = e
            if pages_fetched:
                resume = (page, offset)
            break

        pages_fetched += 1
        total_on_stashdb = result.get("count") or 0
        scenes = result.get("scenes") or []
        if not scenes:
            is_complete = True
            break

        filled_at = None
        for i in range(offset, len(scenes)):
            if qualifies(scenes[i]):
                collected.append(scenes[i])
                if len(collected) >= page_size:
                    filled_at = i
                    break
            elif passed_over:
                passed_over(scenes[i])

        if filled_at is not None:
            # Resume after the last scene taken, unless nothing after it can qualify:
            # then a "Load more" would only come back empty.
            rest = scenes[filled_at + 1:]
            if any(qualifies(scene) for scene in rest):
                resume = (page, filled_at + 1)
            else:
                if passed_over:
                    for scene in rest:
                        passed_over(scene)
                if result.get("has_more"):
                    resume = (page + 1, 0)
                else:
                    is_complete = True
            break

        if not result.get("has_more"):
            is_complete = True
            break
        page += 1
        offset = 0

    next_cursor = None
    if resume is not None:
        next_cursor = encode_cursor({"stashdb_page": resume[0], "offset": resume[1], **cursor_state})

    out = {
        "scenes": collected,
        "total_on_stashdb": total_on_stashdb,
        "next_cursor": next_cursor,
        "is_complete": is_complete,
        "stashdb_pages_fetched": pages_fetched,
    }
    if failure is not None:
        out["error"] = _fetch_error_message(failure, box_name, page)
        out["partial"] = pages_fetched > 0
        out["auth_error"] = failure.is_auth_error
        out["rate_limited"] = failure.is_rate_limited
        if failure.retry_after is not None:
            out["retry_after"] = failure.retry_after
    return out


def fetch_until_full(url, api_key, entity_type, entity_stash_id, local_ids,
                     page_size=PAGE_SIZE_DEFAULT, stashdb_page=1, offset=0,
                     sort="DATE", direction="DESC", plugin_settings=None,
                     favorite_performer_ids=None, favorite_studio_ids=None,
                     favorite_tag_ids=None, excluded_tag_ids=None,
                     box_name="The stash-box", fingerprint_ids=None):
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
        box_name: stash-box name for error messages
        fingerprint_ids: stash-box scene ids owned by fingerprint only (not in local_ids),
            from the fingerprint index; treated as owned

    Returns:
        dict from _fill_page: scenes, total_on_stashdb, next_cursor, is_complete,
        stashdb_pages_fetched, plus error/partial/auth_error/rate_limited/retry_after
        after a failure, and owned_by_fingerprint: how many scenes this request left out
        only because the fingerprint index owns them.
    """
    page_size = min(page_size, PAGE_SIZE_MAX)
    is_tpdb = theporndb_api.is_theporndb(url)

    def fetch_page(page):
        if is_tpdb:
            return theporndb_api.query_scenes_page(
                api_key, entity_type, entity_stash_id,
                page=page, per_page=100, sort=sort, direction=direction,
                plugin_settings=plugin_settings
            )
        return stashbox_api.query_scenes_page(
            url, api_key, entity_type, entity_stash_id,
            page=page, per_page=100, sort=sort, direction=direction,
            plugin_settings=plugin_settings
        )

    fingerprint_ids = fingerprint_ids or set()

    def wanted(scene):
        # Passes the favorite filters and has no excluded tags
        return (scene_passes_favorite_filters(scene, favorite_performer_ids,
                                              favorite_studio_ids, favorite_tag_ids)
                and not scene_has_excluded_tags(scene, excluded_tag_ids))

    def qualifies(scene):
        # Missing locally (by stash_id and by fingerprint), and wanted
        scene_id = scene.get("id")
        return (bool(scene_id) and scene_id not in local_ids
                and scene_id not in fingerprint_ids and wanted(scene))

    cursor_state = {
        "sort": sort,
        "direction": direction,
        "entity_type": entity_type,
        "entity_stash_id": entity_stash_id,
        "endpoint": url,
    }
    counter = _FingerprintCounter(fingerprint_ids, wanted)
    result = _fill_page(fetch_page, qualifies, page_size, stashdb_page, offset,
                        plugin_settings, cursor_state, box_name,
                        passed_over=counter if fingerprint_ids else None)
    result["owned_by_fingerprint"] = counter.count
    return result


class _FingerprintCounter:
    """passed_over for _fill_page: counts the wanted scenes left out only because the
    fingerprint index owns them."""

    def __init__(self, fingerprint_ids, wanted):
        self.fingerprint_ids = fingerprint_ids
        self.wanted = wanted
        self.seen = set()

    def __call__(self, scene):
        scene_id = scene.get("id")
        if scene_id in self.fingerprint_ids and self.wanted(scene):
            self.seen.add(scene_id)

    @property
    def count(self):
        return len(self.seen)


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
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"
        direction: Sort direction - "ASC" or "DESC"
        filter_favorite_performers: If True, only show scenes with favorite performers
        filter_favorite_studios: If True, only show scenes from favorite studios
        filter_favorite_tags: If True, only show scenes with favorite tags

    Returns:
        Dict with:
            - entity_name, entity_type, stashdb_name, stashdb_url
            - total_on_stashdb: total scenes on StashDB (None when the first page failed)
            - total_local: scenes you own (from cache)
            - missing_count_estimate: estimated missing, never negative (null when complete
              or filtered; absent when the first page failed)
            - missing_count_loaded: how many missing we've found so far
            - cursor: cursor for next page
            - has_more: whether more results available
            - is_complete: true if we've checked all StashDB scenes
            - missing_scenes: array of scene objects
            - whisparr_configured: boolean
            - owned_by_fingerprint: stash-box scenes this response left out only because a
              local scene without their stash_id matches them by fingerprint
            - fingerprint_matching: false when the ignoreFingerprintMatches setting is on
            - fingerprint_index: whether a fingerprint index was used (false when none has
              been built, or matching is off); fingerprint_index_complete with an index
            After a stash-box failure, also:
            - error: what failed, naming the stash-box
            - partial: true when missing_scenes holds the scenes found before the failure
              and cursor retries from the failed page; false when the first page failed
            - auth_error: the stash-box refused the API key or account
            - rate_limited (and retry_after, seconds, when the server sent it)
        A bad cursor, or a missing entity or endpoint, gives just {"error"}.
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

    if entity_type == "tag" and theporndb_api.is_theporndb(stashdb_url):
        # Its REST API has no scenes-by-tag listing; an empty page would read as "all found"
        return {"error": theporndb_api.TAG_VIEWS_UNSUPPORTED}

    # A cursor must come from this search: same entity, same endpoint, a sane position
    cursor_state = None
    if cursor:
        try:
            cursor_state = parse_cursor(cursor, {
                "entity_type": entity_type,
                "entity_stash_id": entity_stash_id,
                "endpoint": stashdb_url,
            })
        except CursorError as e:
            log.LogWarning(f"Rejected cursor: {e}")
            return {"error": str(e)}

    # Get or build local stash_id cache (for filtering), plus scenes owned by fingerprint
    local_ids = get_or_build_cache(stashdb_url)
    fingerprint_ids, fingerprint_fields = fingerprint_ownership(stashdb_url, plugin_settings, local_ids)

    # Parse excluded tags from settings (for client-side filtering)
    excluded_tags_str = plugin_settings.get("excludedTags", "").strip()
    excluded_tag_ids = set()
    if excluded_tags_str:
        excluded_tag_ids = {t.strip() for t in excluded_tags_str.split(",") if t.strip()}

    # Count local scenes matching this specific entity (for accurate "You Have" display)
    total_local = count_local_scenes_for_entity(stashdb_url, entity_type, entity_id)

    # Determine starting position
    if cursor_state:
        stashdb_page = cursor_state["stashdb_page"]
        offset = cursor_state["offset"]
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
            "whisparr_configured": bool(whisparr_url_setting(plugin_settings) and
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
        excluded_tag_ids=excluded_tag_ids,
        box_name=stashdb_name,
        fingerprint_ids=fingerprint_ids,
    )
    first_page_failed = "error" in result and not result.get("partial")

    # Format scenes
    formatted_scenes = []
    whisparr_status_map = {}
    whisparr_configured = False
    whisparr_url = whisparr_url_setting(plugin_settings)
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    whisparr_error = None
    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        if not first_page_failed:
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
    # (we'd have to scan all pages to know the filtered count).
    # Local scenes can outnumber the stash-box's (deleted or merged scenes), so clamp at 0.
    # With no page fetched there is nothing to estimate from, so the key is left out.
    filters_active = len(active_filters) > 0
    estimate = {}
    if not first_page_failed:
        estimate["missing_count_estimate"] = None
        if not filters_active and not is_complete and total_on_stashdb is not None:
            estimate["missing_count_estimate"] = max(0, total_on_stashdb - total_local)

    return {
        "entity_name": entity.get("name"),
        "entity_type": entity_type,
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": total_on_stashdb,
        "total_local": total_local,
        **estimate,
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
        "excluded_tags_applied": len(excluded_tag_ids) > 0 and not theporndb_api.is_theporndb(stashdb_url),
        "cache_info": _get_cache_info(stashdb_url),
        "owned_by_fingerprint": result.get("owned_by_fingerprint", 0),
        **fingerprint_fields,
        **{key: result[key] for key in FETCH_FAILURE_KEYS if key in result},
    }


def format_scene(scene, stash_id):
    """Format a StashDB scene for the frontend."""
    # Get the best image (prefer landscape for thumbnails)
    images = [i for i in (scene.get("images") or []) if isinstance(i, dict)]
    thumbnail = None
    if images:
        # Try to find a landscape image first
        for img in images:
            if (img.get("width") or 0) > (img.get("height") or 0):
                thumbnail = img.get("url")
                break
        if not thumbnail:
            thumbnail = images[0].get("url")

    # Format performers
    performers = []
    for perf in scene.get("performers") or []:
        if not isinstance(perf, dict):
            continue
        p = perf.get("performer") or {}
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
    urls = []
    for u in scene.get("urls") or []:
        if not isinstance(u, dict) or not u.get("url"):
            continue
        site = (u.get("site") or {}).get("name") if isinstance(u.get("site"), dict) else None
        urls.append({"url": u["url"], "site": site or (urllib.parse.urlparse(u["url"]).hostname or u["url"])})
    primary_url = urls[0]["url"] if urls else None

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
        "url": primary_url,
        "urls": urls,
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
        Dict with missing scenes and metadata, including owned_by_fingerprint,
        fingerprint_matching and fingerprint_index as in find_missing_scenes_paginated.
        After a stash-box failure it also has error, partial, auth_error, rate_limited
        (and retry_after), as in find_missing_scenes_paginated; total_on_stashdb is None
        when the first page failed. A bad cursor gives just {"error"}.
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

    # A cursor must come from this browse: same endpoint and sort, a sane position
    if cursor:
        try:
            cursor_state = parse_cursor(cursor, {"endpoint": stashdb_url, "sort": sort,
                                                 "direction": direction})
        except CursorError as e:
            log.LogWarning(f"Rejected cursor: {e}")
            return {"error": str(e)}
        stashdb_page, offset = cursor_state["stashdb_page"], cursor_state["offset"]
    else:
        stashdb_page, offset = 1, 0

    # Get local stash_id cache, plus scenes owned by fingerprint
    local_ids = get_or_build_cache(stashdb_url)
    fingerprint_ids, fingerprint_fields = fingerprint_ownership(stashdb_url, plugin_settings, local_ids)

    # Parse excluded tags from settings
    excluded_tags_str = plugin_settings.get("excludedTags", "").strip()
    excluded_tag_ids = []
    if excluded_tags_str:
        excluded_tag_ids = [t.strip() for t in excluded_tags_str.split(",") if t.strip()]

    # Get favorite limit
    favorite_limit = int(plugin_settings.get("favoriteLimit") or 100)

    # Fetch favorite IDs if filters enabled: lists, most engaged first (ThePornDB uses
    # the first few), and sets of the same ids for the filters
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

    performer_set = set(performer_ids) if performer_ids is not None else None
    studio_set = set(studio_ids) if studio_ids is not None else None
    tag_set = set(tag_ids) if tag_ids is not None else None

    # Fetch scenes using browse query
    is_tpdb = theporndb_api.is_theporndb(stashdb_url)
    query_args = {
        "per_page": 100,
        "sort": sort,
        "direction": direction,
        "performer_ids": list(performer_ids) if performer_ids else None,
        "studio_ids": list(studio_ids) if studio_ids else None,
        "tag_ids": list(tag_ids) if tag_ids else None,
        "excluded_tag_ids": excluded_tag_ids if excluded_tag_ids else None,
        "plugin_settings": plugin_settings,
    }

    favorites_limited = []

    def fetch_page(page):
        if is_tpdb:
            result = theporndb_api.query_scenes_browse(stashdb_api_key, page=page, **query_args)
            if result and result.get("favorites_limited"):
                favorites_limited.append(True)
            return result
        return stashbox_api.query_scenes_browse(stashdb_url, stashdb_api_key, page=page, **query_args)

    def wanted(scene):
        # Passes the favorite filters client-side (the query only handles excludes)
        return scene_passes_favorite_filters(scene, performer_set, studio_set, tag_set)

    def qualifies(scene):
        # Not owned (by stash_id or by fingerprint), and wanted
        scene_id = scene.get("id")
        return (bool(scene_id) and scene_id not in local_ids
                and scene_id not in fingerprint_ids and wanted(scene))

    counter = _FingerprintCounter(fingerprint_ids, wanted)
    result = _fill_page(fetch_page, qualifies, page_size, stashdb_page, offset, plugin_settings,
                        {"sort": sort, "direction": direction, "endpoint": stashdb_url},
                        stashdb_name, passed_over=counter if fingerprint_ids else None)
    collected = result["scenes"]
    first_page_failed = "error" in result and not result.get("partial")

    # Format scenes and add Whisparr status
    formatted_scenes = []
    whisparr_status_map = {}
    whisparr_configured = False
    whisparr_url = whisparr_url_setting(plugin_settings)
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    whisparr_error = None
    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        if not first_page_failed:
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
        "total_on_stashdb": result["total_on_stashdb"],
        "missing_count_loaded": len(formatted_scenes),
        "cursor": result["next_cursor"],
        "has_more": result["next_cursor"] is not None,
        "is_complete": result["is_complete"],
        "missing_scenes": formatted_scenes,
        "whisparr_configured": whisparr_configured,
        **({"whisparr_error": whisparr_error} if whisparr_error else {}),
        "filters_active": filters_active,
        "active_filter_tag_ids": list(tag_ids) if tag_ids else [],
        # ThePornDB's tag taxonomy differs, so excluded tags are never sent to it
        "excluded_tags_applied": len(excluded_tag_ids) > 0 and not is_tpdb,
        # ThePornDB takes one request per favorite, so only the first (most engaged) are used
        **({"favorites_limited": bool(favorites_limited),
            "favorites_query_limit": theporndb_api.MAX_BROWSE_QUERIES} if is_tpdb else {}),
        "cache_info": _get_cache_info(stashdb_url),
        "owned_by_fingerprint": counter.count,
        **fingerprint_fields,
        **{key: result[key] for key in FETCH_FAILURE_KEYS if key in result},
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
        "whisparr_configured": bool(whisparr_url_setting(plugin_settings) and
                                    plugin_settings.get("whisparrApiKey")),
        "filters_active": True,
        "excluded_tags_applied": False,
        "empty_filter_types": empty_filter_types
    }


def add_to_whisparr(stash_id, title, plugin_settings, endpoint=None):
    """Add a scene to Whisparr by its StashDB ID.

    Uses the same approach as Stasharr:
    1. Check if scene already exists (movie?stashId=X)
    2. Lookup scene in TPDB (lookup/scene?term=stash:X)
    3. Add scene to Whisparr (POST movie; with whisparrSearchOnAdd, Whisparr searches on add)
    4. For a scene already in Whisparr without a file, trigger a search if whisparrSearchOnAdd

    Args:
        stash_id: StashDB scene ID (UUID)
        title: Scene title (for logging/error messages)
        plugin_settings: Plugin configuration from Stash
        endpoint: stash-box endpoint the scene came from. Whisparr matches StashDB IDs only,
            so any other endpoint is refused without calling Whisparr. None or "" means StashDB.

    Returns:
        On success {success: True, message, search_triggered, already_exists?, scene?,
        search_error?}; search_error means the scene is in Whisparr but its search didn't start.
        On failure {success: False, error, whisparr_error?}.
    """
    # Older UI builds don't send the endpoint; treat that as StashDB, as they always did
    if str(endpoint or "").strip() and not is_stashdb_endpoint(endpoint):
        msg = (f"Can't add '{title}' to Whisparr: Whisparr matches scenes by StashDB ID, and this "
               f"scene is from {endpoint}. Only StashDB scenes can be added.")
        log.LogWarning(msg)
        return {"success": False, "error": msg}

    whisparr_url = whisparr_url_setting(plugin_settings)
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")
    quality_profile = int(plugin_settings.get("whisparrQualityProfile") or 1)  # Default to first profile
    root_folder = whisparr_root_folder_setting(plugin_settings)
    search_on_add = bool(plugin_settings.get("whisparrSearchOnAdd", False))  # Default to False for manual control

    if not whisparr_url or not whisparr_api_key:
        return {"success": False,
                "error": "Whisparr is not configured. Please set URL and API key in plugin settings."}

    if not root_folder:
        return {"success": False,
                "error": "Whisparr root folder is not configured. Please set it in plugin settings."}

    try:
        # Step 1: Check if scene already exists in Whisparr
        existing_scene = whisparr_get_scene_by_stash_id(whisparr_url, whisparr_api_key, stash_id)
        if existing_scene:
            if existing_scene.get("hasFile", False):
                return {
                    "success": True,
                    "message": f"Scene '{title}' already exists in Whisparr with file.",
                    "already_exists": True,
                    "search_triggered": False,
                }
            if not search_on_add:
                return {
                    "success": True,
                    "message": f"Scene '{title}' already in Whisparr (no file yet). Use Whisparr to search.",
                    "already_exists": True,
                    "search_triggered": False,
                }
            try:
                whisparr_trigger_search(whisparr_url, whisparr_api_key, existing_scene["id"])
            except WhisparrError as e:
                log.LogWarning(f"'{title}' is in Whisparr, but its search could not be started: {e}")
                return {
                    "success": True,
                    "message": f"Scene '{title}' already in Whisparr, but the search could not be started.",
                    "already_exists": True,
                    "search_triggered": False,
                    "search_error": str(e),
                }
            return {
                "success": True,
                "message": f"Scene '{title}' already in Whisparr. Triggered search.",
                "already_exists": True,
                "search_triggered": True,
            }

        # Step 2: Lookup scene in TPDB via Whisparr (raises WhisparrError with Whisparr's text)
        scene_data = whisparr_lookup_scene(whisparr_url, whisparr_api_key, stash_id)
        if not scene_data:
            return {
                "success": False,
                "error": f"Scene '{title}' not found in TPDB/Whisparr lookup. "
                         "It may not be indexed in ThePornDB yet, or the StashDB ID "
                         "doesn't have a matching TPDB entry. Try searching manually in Whisparr."
            }

        # Step 3: Add scene to Whisparr; addOptions.searchForMovie makes Whisparr search on add
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

        search_msg = " and triggered search" if search_on_add else " without a search"
        return {
            "success": True,
            "message": f"Added '{title}' to Whisparr{search_msg}.",
            "scene": added_scene,
            "search_triggered": search_on_add,
        }

    except WhisparrError as e:
        log.LogError(f"Error adding scene to Whisparr: {e}")
        return {"success": False, "error": str(e), "whisparr_error": str(e)}
    except Exception as e:
        log.LogError(f"Error adding scene to Whisparr: {e}")
        return {"success": False, "error": str(e)}


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

def _find_scene_with_stash_ids(scene_id):
    """The local scene {id, title, stash_ids}, or None when Stash has no such scene."""
    data = stash_graphql("""
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
    """, {"id": str(scene_id)})
    return (data or {}).get("findScene")


def _configured_endpoints(boxes):
    return [box["endpoint"] for box in boxes or [] if isinstance(box, dict) and box.get("endpoint")]


def handle_scene_update_hook(hook_context, plugin_settings):
    """Handle Scene.Update.Post: refresh the local indexes, and clean up Whisparr once a
    scene has its StashDB ID in Stash.

    hook_context is Stash's args.hookContext: {id, type, input, inputFields}. Nothing happens
    unless the update set stash_ids (inputFields). Then the local stash_id indexes are dropped
    (they may be stale), and the scene leaves the fingerprint index of every stash-box it now
    has a stash_id for (of every box when Stash no longer has it), so its matches stop counting
    as owned. With auto-cleanup on and Whisparr configured, the scene's StashDB ID is looked up
    in Whisparr. Whisparr keys on StashDB IDs, so other stash-boxes are ignored.
    Only an entry whose stashId equals that ID is touched, and not while it is in Whisparr's
    download queue. It is deleted, or unmonitored with unmonitorOnly.
    """
    hook_context = hook_context or {}
    if "stash_ids" not in (hook_context.get("inputFields") or []):
        log.LogDebug("Scene update did not set stash_ids; no Whisparr cleanup")
        return {"success": True, "message": "stash_ids not changed"}

    # scenesUpdate passes a list as input, so prefer the hook's own scene id
    hook_input = hook_context.get("input")
    scene_id = hook_context.get("id") or (hook_input.get("id") if isinstance(hook_input, dict) else None)
    if not scene_id:
        log.LogWarning("No scene ID in hook input")
        return {"success": False, "message": "No scene ID"}

    # The scene's stash_ids changed, so any box's local index may be missing it
    stashbox_configs = get_stashbox_config() or []
    endpoints = _configured_endpoints(stashbox_configs)
    for endpoint in endpoints:
        invalidate_cache(endpoint)

    fetched = {}

    def load_scene():  # one Stash query, shared by the fingerprint index and Whisparr
        if "scene" not in fetched:
            fetched["scene"] = _find_scene_with_stash_ids(scene_id)
        return fetched["scene"]

    indexed = [e for e in endpoints if fingerprint_index.has_index(CACHE_DIR, e)]
    if indexed:
        scene = load_scene()
        if scene is None:
            forget_fingerprint_matches(scene_id, indexed)  # gone from Stash
        else:
            tagged = {sid.get("endpoint") for sid in scene.get("stash_ids") or []
                      if isinstance(sid, dict) and sid.get("stash_id")}
            forget_fingerprint_matches(scene_id, [e for e in indexed if e in tagged])

    if not plugin_settings.get("enableAutoCleanup", False):
        log.LogDebug("Auto-cleanup is disabled, skipping")
        return {"success": True, "message": "Auto-cleanup disabled"}

    whisparr_url = whisparr_url_setting(plugin_settings)
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")
    if not whisparr_url or not whisparr_api_key:
        log.LogDebug("Whisparr not configured, skipping auto-cleanup")
        return {"success": True, "message": "Whisparr not configured"}

    if not get_stashdb_endpoint(stashbox_configs):
        log.LogDebug("No StashDB stash-box is configured; Whisparr auto-cleanup works with StashDB IDs only")
        return {"success": True, "message": "No StashDB stash-box configured"}

    # The scene with its stash_ids
    scene = load_scene()
    if not scene:
        log.LogWarning(f"Could not find scene {scene_id}")
        return {"success": False, "message": "Scene not found"}

    stash_id = next((sid.get("stash_id") for sid in scene.get("stash_ids") or []
                     if is_stashdb_endpoint(sid.get("endpoint")) and sid.get("stash_id")), None)
    if not stash_id:
        log.LogDebug(f"Scene {scene_id} has no StashDB ID, skipping")
        return {"success": True, "message": "Scene not tagged with StashDB"}

    scene_title = scene.get("title") or "Unknown"
    try:
        # Returns only an entry whose stashId equals this ID, never another scene
        whisparr_scene = whisparr_get_scene_by_stash_id(whisparr_url, whisparr_api_key, stash_id)
        if not whisparr_scene:
            log.LogDebug(f"Scene with StashDB ID {stash_id} not in Whisparr")
            return {"success": True, "message": "Scene not in Whisparr"}
        queued = _queued_movie_ids(whisparr_get_queue(whisparr_url, whisparr_api_key))
    except WhisparrError as e:
        log.LogError(f"Whisparr cleanup skipped for scene {scene_id}: {e}")
        return {"success": False, "message": str(e), "error": str(e), "whisparr_error": str(e)}

    movie_id = whisparr_scene.get("id")
    if movie_id is None:
        log.LogWarning(f"Whisparr returned '{scene_title}' without an id; leaving it alone")
        return {"success": False, "message": "Whisparr entry has no id", "error": "Whisparr entry has no id"}
    if movie_id in queued:
        log.LogInfo(f"'{scene_title}' is in Whisparr's download queue; leaving it in Whisparr")
        return {"success": True, "message": f"'{scene_title}' is in Whisparr's download queue; left alone"}

    unmonitor_only = plugin_settings.get("unmonitorOnly", False)
    try:
        if unmonitor_only:
            whisparr_unmonitor_scene(whisparr_url, whisparr_api_key, movie_id)
            log.LogInfo(f"Unmonitored '{scene_title}' in Whisparr after tagging")
            return {"success": True, "message": f"Unmonitored '{scene_title}' in Whisparr"}
        whisparr_delete_scene(whisparr_url, whisparr_api_key, movie_id)
        log.LogInfo(f"Removed '{scene_title}' from Whisparr after tagging")
        return {"success": True, "message": f"Removed '{scene_title}' from Whisparr"}
    except Exception as e:
        log.LogError(f"Failed to cleanup Whisparr for scene {scene_id}: {e}")
        return {"success": False, "message": str(e), "error": str(e)}


def handle_scene_destroy_hook(hook_context, plugin_settings):
    """Handle Scene.Destroy.Post: forget a deleted scene in the local indexes.

    hook_context is Stash's args.hookContext: {id, type, input}; input holds the destroy
    options plus the scene's checksum, oshash and path. The stash_id indexes are dropped (the
    scene's stash_ids no longer count as owned) and the scene leaves every stash-box's
    fingerprint index. Whisparr is never touched: deleting a scene is not tagging it.
    """
    hook_context = hook_context or {}
    hook_input = hook_context.get("input")
    scene_id = hook_context.get("id")
    if scene_id in (None, "") and isinstance(hook_input, dict):
        scene_id = hook_input.get("id")
    if scene_id in (None, ""):
        log.LogWarning("No scene ID in the Scene.Destroy.Post hook")
        return {"success": False, "message": "No scene ID"}

    endpoints = _configured_endpoints(get_stashbox_config())
    for endpoint in endpoints:
        invalidate_cache(endpoint)
    forget_fingerprint_matches(scene_id, endpoints)
    log.LogDebug(f"Scene {scene_id} deleted; dropped it from the local indexes")
    return {"success": True, "message": f"Scene {scene_id} dropped from the local indexes"}


def _split_scan_paths(value):
    """The ;-separated paths of the scanPath setting; empty segments are ignored."""
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def _inside(path, root):
    """True when `path` is `root` or below it (a path-boundary check: /data2 is not in /data)."""
    path, root = os.path.abspath(path), os.path.abspath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


def task_scan_for_new_scenes(plugin_settings):
    """Task: Trigger a Stash scan on the configured scan path(s).

    scanPath holds one path or several separated by ;. Every path must be inside a Stash
    library (Settings > Library), or the scan is refused. Uses the user's default scan
    settings from Stash configuration, and drops the stash-box caches once the scan starts.
    """
    scan_paths = _split_scan_paths(plugin_settings.get("scanPath"))

    if not scan_paths:
        log.LogWarning("Scan path not configured")
        return {"success": False, "message": "Scan path not configured. Set it in plugin settings."}

    log.LogInfo(f"Triggering scan on path: {'; '.join(scan_paths)}")

    try:
        # The user-configured scan defaults are in configuration.ui.taskDefaults.scan
        # (configuration.defaults.scan is something else). Library roots: general.stashes.
        config_result = stash_graphql("""
            query Configuration {
                configuration {
                    ui
                    general {
                        stashes {
                            path
                        }
                    }
                }
            }
        """)
        configuration = (config_result or {}).get("configuration") or {}

        roots = [str(s["path"]) for s in (configuration.get("general") or {}).get("stashes") or []
                 if isinstance(s, dict) and s.get("path")]
        outside = [p for p in scan_paths if not any(_inside(p, r) for r in roots)]
        if outside:
            listed = ", ".join(roots) if roots else "none configured"
            message = (f"Scan path {', '.join(outside)} is not inside a Stash library. "
                       f"Library paths: {listed}. Fix the Scan Path setting or add the folder "
                       f"under Settings > Library.")
            log.LogWarning(message)
            return {"success": False, "message": message}

        scan_input = {"paths": scan_paths}

        ui_config = configuration.get("ui") or {}
        # ui is a JSON Map, parse it if it's a string
        if isinstance(ui_config, str):
            ui_config = json.loads(ui_config)

        scan_defaults = (ui_config.get("taskDefaults") or {}).get("scan") or {}
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

        if not result or not result.get("metadataScan"):  # metadataScan is the job id
            return {"success": False, "message": "Failed to start scan"}

        # The scan adds scenes, so any box's local index is stale (best effort: the scan is running)
        try:
            for box in get_stashbox_config() or []:
                if isinstance(box, dict) and box.get("endpoint"):
                    invalidate_cache(box["endpoint"])
        except Exception as e:
            log.LogWarning(f"Scan started, but the stash-box caches could not be cleared: {e}")

        shown = "; ".join(scan_paths)
        log.LogInfo(f"Scan started for {shown}")
        return {"success": True, "message": f"Scan started for {shown}"}
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
        elif e.tls == "certificate":
            problems.append(f"Whisparr's certificate at {url} can't be verified (self-signed?). Turn on "
                            "'Whisparr: Skip TLS Verification' (whisparrSkipTlsVerify) in the plugin "
                            "settings, or give Whisparr a certificate Stash trusts.")
        elif e.tls == "handshake":
            problems.append(f"The TLS handshake with {url} failed. If Whisparr doesn't serve HTTPS on this "
                            "port, change the Whisparr URL to http://.")
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

    root = whisparr_root_folder_setting(settings)
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
    """Task: remove (or unmonitor) Whisparr scenes whose StashDB ID a local scene now has.

    Uses the StashDB stash-box only (Whisparr keys on StashDB IDs) and a freshly built local
    stash_id index (a stash_id_endpoint query, not a library scan). Entries in Whisparr's
    download queue are skipped.

    Returns {success: True, message, cleaned, skipped_in_queue, errors}, or
    {success: False, message, error} when Whisparr, its queue or Stash can't be read.
    """
    whisparr_url = whisparr_url_setting(plugin_settings)
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    if not whisparr_url or not whisparr_api_key:
        msg = "Whisparr is not configured. Set the URL and API key in the plugin settings."
        log.LogWarning(msg)
        return {"success": False, "message": msg, "error": msg}

    stashdb_endpoint = get_stashdb_endpoint(get_stashbox_config())
    if not stashdb_endpoint:
        msg = ("No StashDB stash-box is configured in Stash. Whisparr cleanup matches StashDB IDs "
               "only; add StashDB under Settings > Metadata Providers.")
        log.LogWarning(msg)
        return {"success": False, "message": msg, "error": msg}

    unmonitor_only = plugin_settings.get("unmonitorOnly", False)

    log.LogInfo("Starting Whisparr cleanup task...")

    try:
        whisparr_scenes = whisparr_get_all_scenes(whisparr_url, whisparr_api_key)
        queued = _queued_movie_ids(whisparr_get_queue(whisparr_url, whisparr_api_key))
    except WhisparrError as e:
        log.LogError(f"Whisparr cleanup failed: {e}")
        return {"success": False, "message": str(e), "error": str(e), "whisparr_error": str(e)}

    try:
        # Built from Stash now, never read from a cache: this decides what leaves Whisparr
        invalidate_cache(stashdb_endpoint)
        local_stash_ids = get_or_build_cache(stashdb_endpoint, fresh=True)
    except Exception as e:
        msg = f"Whisparr cleanup failed: could not read the local StashDB IDs: {e}"
        log.LogError(msg)
        return {"success": False, "message": msg, "error": msg}
    log.LogInfo(f"Found {len(local_stash_ids)} local scenes tagged with {stashdb_endpoint}")

    cleaned_count = 0
    skipped_in_queue = 0
    errors = []

    for scene in whisparr_scenes:
        stash_id = scene.get("stashId") if isinstance(scene, dict) else None
        if not stash_id or stash_id not in local_stash_ids:
            continue
        title = scene.get("title") or "Unknown"
        if scene.get("id") in queued:
            skipped_in_queue += 1
            log.LogInfo(f"Skipped '{title}': it is in Whisparr's download queue")
            continue
        try:
            if unmonitor_only:
                whisparr_unmonitor_scene(whisparr_url, whisparr_api_key, scene["id"])
            else:
                whisparr_delete_scene(whisparr_url, whisparr_api_key, scene["id"])
            cleaned_count += 1
            log.LogInfo(f"Cleaned up: {title}")
        except Exception as e:
            errors.append(f"{title}: {e}")

    action = "unmonitored" if unmonitor_only else "removed"
    message = f"{action.title()} {cleaned_count} scenes from Whisparr"
    if skipped_in_queue:
        message += f", skipped {skipped_in_queue} in the download queue"
    if errors:
        message += f" ({len(errors)} errors)"

    log.LogInfo(message)
    return {"success": True, "message": message, "cleaned": cleaned_count,
            "skipped_in_queue": skipped_in_queue, "errors": errors}


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

        log.LogDebug(f"Hook triggered: {hook_type}")

        if hook_type == "Scene.Update.Post":
            output = handle_scene_update_hook(hook_context, plugin_settings)
        elif hook_type == "Scene.Destroy.Post":
            output = handle_scene_destroy_hook(hook_context, plugin_settings)
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

    if mode == "build_fingerprint_index":
        log.LogInfo("Running task: Build Fingerprint Index")
        output = task_build_fingerprint_index(plugin_settings)
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
            # Older UI builds don't send it; add_to_whisparr treats a missing endpoint as StashDB
            endpoint = args.get("endpoint")

            if not stash_id:
                output = {"error": "stash_id is required"}
            else:
                output = add_to_whisparr(stash_id, title, plugin_settings, endpoint=endpoint)

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

        elif operation == "build_fingerprint_index":
            # Long on a large library; the UI recommends the task
            output = build_fingerprint_index(plugin_settings, endpoint=args.get("endpoint"))

        elif operation == "refresh_index":
            endpoint = args.get("endpoint")
            if not endpoint:
                boxes = get_stashbox_config()
                endpoint = boxes[0]["endpoint"] if boxes else None
            if not endpoint:
                output = {"error": "No stash-box endpoint configured"}
            else:
                invalidate_cache(endpoint)
                ids = get_or_build_cache(endpoint, fresh=True)
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

    # Wrap output in PluginOutput structure expected by Stash. Stash drops `output`
    # when `error` is set, so a failure that carries results or details the UI renders
    # (partial scenes, a retry cursor, auth_error, a build's counts) goes out as output
    # with its error field.
    if "error" in output and not any(key in output for key in RESULT_KEYS):
        plugin_output = {"error": output["error"]}
    else:
        plugin_output = {"output": output}

    print(json.dumps(plugin_output))


if __name__ == "__main__":
    main()
