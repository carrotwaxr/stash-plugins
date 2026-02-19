"""
ThePornDB REST API adapter for Missing Scenes plugin.

TPDB's stash-box GraphQL endpoint is a fork missing queryScenes, so we use
their REST API (api.theporndb.net) and transform responses to match the
GraphQL shape that format_scene() expects.

Features:
- Transparent detection of TPDB endpoints
- REST request with retry/rate limiting (mirrors stashbox_api patterns)
- Response transformation: REST scene → GraphQL-shaped scene dict
- Performer, studio, and browse scene queries
"""

import json
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse

import log
import stashbox_api

# SSL context (reuse pattern from stashbox_api)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# TPDB REST API base URL
TPDB_API_BASE = "https://api.theporndb.net"

# Cache: TPDB site UUID → numeric site_id (avoids repeated lookups)
_site_id_cache: dict[str, int] = {}


def is_theporndb(endpoint_url: str) -> bool:
    """Check if a stash-box endpoint is ThePornDB."""
    return "theporndb.net" in (endpoint_url or "")


# ============================================================================
# REST Request with Retry
# ============================================================================

def rest_request(api_key, path, params=None, plugin_settings=None,
                 operation_name=None):
    """
    Make a REST request to the TPDB API with retry logic.

    Args:
        api_key: TPDB API key (Bearer token)
        path: API path (e.g., "/scenes", "/performers/{id}/scenes")
        params: Query parameters dict
        plugin_settings: Plugin configuration for retry/timeout settings
        operation_name: Human-readable name for logging

    Returns:
        Parsed JSON response dict, or None on failure
    """
    max_retries = stashbox_api.get_config(plugin_settings, "max_retries")
    initial_delay = stashbox_api.get_config(plugin_settings, "initial_retry_delay")
    max_delay = stashbox_api.get_config(plugin_settings, "max_retry_delay")
    backoff_multiplier = stashbox_api.get_config(plugin_settings, "retry_backoff_multiplier")
    timeout = stashbox_api.get_config(plugin_settings, "request_timeout")
    rate_limit_pause = stashbox_api.get_config(plugin_settings, "rate_limit_pause")

    # Build URL with query parameters
    url = f"{TPDB_API_BASE}{path}"
    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(url, headers=headers, method="GET")

    last_error = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            status_code = e.code
            last_error = e

            if status_code == 429:
                if attempt < max_retries:
                    log.LogWarning(
                        f"TPDB rate limited (429) on {operation_name or 'request'}. "
                        f"Pausing {rate_limit_pause}s before retry {attempt + 1}/{max_retries}"
                    )
                    time.sleep(rate_limit_pause)
                    continue
                else:
                    log.LogError("TPDB rate limited (429) - max retries exceeded")
                    return None

            if status_code in stashbox_api.RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(
                    f"TPDB HTTP {status_code} on {operation_name or 'request'}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"TPDB HTTP error {status_code}: {e.reason}")
            return None

        except urllib.error.URLError as e:
            last_error = e

            if attempt < max_retries:
                log.LogWarning(
                    f"TPDB connection error on {operation_name or 'request'}: {e.reason}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"TPDB URL error after {max_retries} retries: {e.reason}")
            return None

        except Exception as e:
            log.LogError(f"TPDB unexpected error: {e}")
            return None

    log.LogError(f"TPDB failed after {max_retries} retries: {last_error}")
    return None


# ============================================================================
# Scene Transformer: REST → GraphQL format
# ============================================================================

def transform_scene(rest_scene: dict) -> dict:
    """
    Transform a TPDB REST API scene into the GraphQL-shaped dict
    that format_scene() expects.

    REST field          → GraphQL field
    ─────────────────────────────────────
    id (uuid)           → id
    title               → title
    description         → details
    date                → release_date
    sku                 → code
    duration            → duration
    directors[0]        → director
    site.uuid/name      → studio.id/name
    posters[]           → images[]
    performers[].parent → performers[].performer
    tags[].uuid/name    → tags[].id/name
    url (string)        → urls[0].url
    """
    scene = {
        "id": rest_scene.get("id"),
        "title": rest_scene.get("title"),
        "details": rest_scene.get("description"),
        "release_date": rest_scene.get("date"),
        "code": rest_scene.get("sku"),
        "duration": rest_scene.get("duration"),
    }

    # Director: first entry from directors array
    directors = rest_scene.get("directors") or []
    scene["director"] = directors[0] if directors else None

    # Studio: from site object
    site = rest_scene.get("site")
    if site:
        scene["studio"] = {
            "id": site.get("uuid"),
            "name": site.get("name"),
        }
    else:
        scene["studio"] = None

    # Images: from posters array
    posters = rest_scene.get("posters") or []
    scene["images"] = [
        {
            "id": poster.get("id"),
            "url": poster.get("url"),
            "width": poster.get("width", 0),
            "height": poster.get("height", 0),
        }
        for poster in posters
        if poster.get("url")
    ]

    # Performers: nest under performer key to match GraphQL shape
    rest_performers = rest_scene.get("performers") or []
    scene["performers"] = []
    for perf in rest_performers:
        # TPDB nests the canonical performer under "parent"
        parent = perf.get("parent") or perf
        scene["performers"].append({
            "performer": {
                "id": parent.get("id"),
                "name": parent.get("name"),
                "disambiguation": parent.get("disambiguation"),
                "gender": parent.get("gender"),
            },
            "as": perf.get("as"),
        })

    # Tags: uuid→id, name stays
    rest_tags = rest_scene.get("tags") or []
    scene["tags"] = [
        {"id": tag.get("uuid") or tag.get("id"), "name": tag.get("name")}
        for tag in rest_tags
    ]

    # URLs: single url string → urls array
    url = rest_scene.get("url")
    if url:
        scene["urls"] = [{"url": url, "site": {"name": "ThePornDB"}}]
    else:
        scene["urls"] = []

    return scene


# ============================================================================
# Site UUID → numeric ID resolution (for studio queries)
# ============================================================================

def resolve_site_id(api_key, site_uuid, plugin_settings=None):
    """
    Resolve a TPDB site UUID to its numeric site_id.

    The /scenes endpoint requires a numeric site_id for filtering,
    but Stash stores the UUID. This does a one-time lookup and caches.

    Returns:
        Numeric site_id, or None if resolution fails
    """
    if site_uuid in _site_id_cache:
        return _site_id_cache[site_uuid]

    data = rest_request(
        api_key, f"/sites/{site_uuid}",
        plugin_settings=plugin_settings,
        operation_name=f"resolve site {site_uuid}"
    )

    if not data or "data" not in data:
        log.LogWarning(f"TPDB: Could not resolve site UUID {site_uuid}")
        return None

    site_data = data["data"]
    site_id = site_data.get("id")
    if site_id is not None:
        _site_id_cache[site_uuid] = site_id
        log.LogDebug(f"TPDB: Resolved site {site_uuid} → numeric ID {site_id}")
        return site_id

    log.LogWarning(f"TPDB: Site {site_uuid} has no numeric ID")
    return None


# ============================================================================
# Query Functions (same return format as stashbox_api.query_scenes_page)
# ============================================================================

def query_scenes_page(api_key, entity_type, entity_stash_id, page=1,
                      per_page=100, sort="DATE", direction="DESC",
                      plugin_settings=None):
    """
    Fetch a single page of scenes from TPDB for pagination.

    Dispatches to performer/studio-specific queries based on entity_type.
    Tags return empty results (TPDB tag taxonomy differs from stash-box).

    Returns:
        dict with scenes, count, page, has_more — same as stashbox_api.query_scenes_page
        Returns None on error.
    """
    if entity_type == "performer":
        return _query_scenes_by_performer(
            api_key, entity_stash_id, page, per_page, sort, direction,
            plugin_settings
        )
    elif entity_type == "studio":
        return _query_scenes_by_studio(
            api_key, entity_stash_id, page, per_page, sort, direction,
            plugin_settings
        )
    elif entity_type == "tag":
        log.LogInfo("TPDB: Tag-based scene queries are not supported (different taxonomy)")
        return {"scenes": [], "count": 0, "page": page, "has_more": False}
    else:
        log.LogError(f"TPDB: Unknown entity type: {entity_type}")
        return None


def query_scenes_browse(api_key, page=1, per_page=100, sort="DATE",
                        direction="DESC", performer_ids=None, studio_ids=None,
                        tag_ids=None, excluded_tag_ids=None,
                        plugin_settings=None):
    """
    Browse all scenes on TPDB with optional filters.

    Args:
        api_key: TPDB API key
        page: Page number (1-indexed)
        per_page: Results per page
        sort: Sort field
        direction: Sort direction
        performer_ids: List of performer UUIDs to filter by
        studio_ids: List of studio UUIDs to filter by
        tag_ids: Ignored (TPDB tag taxonomy differs)
        excluded_tag_ids: Ignored (TPDB tag taxonomy differs)
        plugin_settings: Plugin configuration

    Returns:
        dict with scenes, count, page, has_more
    """
    params = {
        "page": page,
        "limit": per_page,
    }

    # Map sort fields
    sort_field, sort_order = _map_sort(sort, direction)
    if sort_field:
        params["sort"] = sort_field
        params["sort_order"] = sort_order

    # Apply filters
    if performer_ids:
        # TPDB only supports filtering by a single performer
        params["performer"] = performer_ids[0]

    if studio_ids:
        # Resolve first studio UUID to numeric ID
        site_id = resolve_site_id(api_key, studio_ids[0], plugin_settings)
        if site_id:
            params["site_id"] = site_id

    return _fetch_scenes(api_key, "/scenes", params, page, per_page,
                         plugin_settings, "browse scenes")


# ============================================================================
# Internal Query Helpers
# ============================================================================

def _map_sort(sort: str, direction: str) -> tuple[str | None, str]:
    """Map GraphQL sort fields to TPDB REST API sort parameters."""
    sort_map = {
        "DATE": "date",
        "TITLE": "title",
        "CREATED_AT": "created_at",
        "UPDATED_AT": "updated_at",
        "TRENDING": "trending",
    }
    direction_map = {
        "ASC": "asc",
        "DESC": "desc",
    }
    return sort_map.get(sort), direction_map.get(direction, "desc")


def _query_scenes_by_performer(api_key, performer_stash_id, page, per_page,
                                sort, direction, plugin_settings):
    """Query TPDB for scenes featuring a performer."""
    params = {
        "page": page,
        "limit": per_page,
    }

    sort_field, sort_order = _map_sort(sort, direction)
    if sort_field:
        params["sort"] = sort_field
        params["sort_order"] = sort_order

    return _fetch_scenes(
        api_key, f"/performers/{performer_stash_id}/scenes",
        params, page, per_page, plugin_settings,
        f"performer {performer_stash_id} scenes"
    )


def _query_scenes_by_studio(api_key, studio_stash_id, page, per_page,
                              sort, direction, plugin_settings):
    """Query TPDB for scenes from a studio."""
    # Resolve site UUID → numeric ID
    site_id = resolve_site_id(api_key, studio_stash_id, plugin_settings)
    if site_id is None:
        log.LogWarning(f"TPDB: Cannot query scenes — failed to resolve studio {studio_stash_id}")
        return {"scenes": [], "count": 0, "page": page, "has_more": False}

    params = {
        "page": page,
        "limit": per_page,
        "site_id": site_id,
    }

    sort_field, sort_order = _map_sort(sort, direction)
    if sort_field:
        params["sort"] = sort_field
        params["sort_order"] = sort_order

    return _fetch_scenes(
        api_key, "/scenes", params, page, per_page, plugin_settings,
        f"studio {studio_stash_id} scenes"
    )


def _fetch_scenes(api_key, path, params, page, per_page, plugin_settings,
                   operation_name):
    """
    Fetch scenes from a TPDB endpoint, transform, and return in standard format.

    Returns:
        dict with scenes, count, page, has_more — or None on error
    """
    data = rest_request(
        api_key, path, params=params,
        plugin_settings=plugin_settings,
        operation_name=operation_name
    )

    if not data:
        return None

    # TPDB wraps results in a "data" key with pagination in "meta"
    scenes_data = data.get("data", [])
    meta = data.get("meta", {})

    total = meta.get("total", 0)
    last_page = meta.get("last_page", 1)

    transformed = [transform_scene(s) for s in scenes_data]

    log.LogDebug(
        f"TPDB {operation_name}: page {page}/{last_page}, "
        f"got {len(transformed)} scenes (total: {total})"
    )

    return {
        "scenes": transformed,
        "count": total,
        "page": page,
        "has_more": page < last_page,
    }
