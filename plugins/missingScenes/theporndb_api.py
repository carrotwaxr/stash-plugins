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

# api.theporndb.net is a public host; verify its certificate
SSL_CONTEXT = stashbox_api.SSL_CONTEXT

# TPDB REST API base URL
TPDB_API_BASE = "https://api.theporndb.net"

# Browse makes one request per favorite performer/studio; this caps them per page
MAX_BROWSE_QUERIES = 10

# Cache: TPDB site UUID → numeric site_id (avoids repeated lookups)
_site_id_cache: dict[str, int] = {}


def is_theporndb(endpoint_url: str) -> bool:
    """Check if a stash-box endpoint is ThePornDB."""
    return "theporndb.net" in (endpoint_url or "")


# ============================================================================
# REST Request with Retry
# ============================================================================

def _retry_after(error):
    """Seconds from a Retry-After header, or None."""
    try:
        value = float(error.headers.get("Retry-After"))
        return value if value >= 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


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
        Parsed JSON response, or None on failure

    Raises:
        StashBoxAPIError: on 401/403 (auth) and on 429 after the retries, so the
            caller can say which it was.
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
        "User-Agent": stashbox_api.USER_AGENT,
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
                    raise stashbox_api.StashBoxAPIError(
                        "HTTP 429: rate limited", status_code=429, retryable=True,
                        retry_after=_retry_after(e))

            if status_code in stashbox_api.RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(
                    f"TPDB HTTP {status_code} on {operation_name or 'request'}. "
                    f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay = min(delay * backoff_multiplier, max_delay)
                continue

            log.LogError(f"TPDB HTTP error {status_code}: {e.reason}")
            if status_code in stashbox_api.AUTH_STATUS_CODES:
                raise stashbox_api.StashBoxAPIError(
                    f"HTTP {status_code}: {e.reason} (check the ThePornDB API key)",
                    status_code=status_code, auth=True)
            return None

        except urllib.error.URLError as e:
            last_error = e

            # A bad certificate won't fix itself on retry
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                log.LogError(f"TPDB TLS certificate verification failed: {e.reason}")
                return None

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

def _name_of(value):
    """A name string from a string or an object with a name, else None."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) and name else None
    return None


def _image(url, poster=None):
    poster = poster if isinstance(poster, dict) else {}
    return {"id": poster.get("id"), "url": url,
            "width": poster.get("width") or 0, "height": poster.get("height") or 0}


def _transform_images(rest_scene):
    """Images from posters: a string, a list of strings/objects, or a dict of size→url.
    Falls back to the scalar image fields."""
    posters = rest_scene.get("posters")
    images = []
    if isinstance(posters, str):
        posters = [posters]
    if isinstance(posters, dict):
        # {"full": "url", "large": "url", ...}: one thumbnail is enough
        images = [_image(u) for u in posters.values() if isinstance(u, str) and u][:1]
    elif isinstance(posters, list):
        for poster in posters:
            if isinstance(poster, str) and poster:
                images.append(_image(poster))
            elif isinstance(poster, dict) and isinstance(poster.get("url"), str) and poster["url"]:
                images.append(_image(poster["url"], poster))
    if not images:
        for key in ("poster", "image", "background", "posters_url"):
            value = rest_scene.get(key)
            if isinstance(value, str) and value:
                images.append(_image(value))
                break
    return images


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

    # Director: first entry from directors (objects, strings, or one string)
    directors = rest_scene.get("directors")
    if isinstance(directors, (str, dict)):
        directors = [directors]
    scene["director"] = None
    for director in directors if isinstance(directors, list) else []:
        name = _name_of(director)
        if name:
            scene["director"] = name
            break

    # Studio: from site object (or a bare name string)
    site = rest_scene.get("site")
    if isinstance(site, dict):
        scene["studio"] = {"id": site.get("uuid"), "name": site.get("name")}
    elif isinstance(site, str) and site:
        scene["studio"] = {"id": None, "name": site}
    else:
        scene["studio"] = None

    scene["images"] = _transform_images(rest_scene)

    # Performers: nest under performer key to match GraphQL shape
    rest_performers = rest_scene.get("performers")
    scene["performers"] = []
    for perf in rest_performers if isinstance(rest_performers, list) else []:
        if isinstance(perf, str):
            perf = {"name": perf}
        if not isinstance(perf, dict):
            continue
        # TPDB nests the canonical performer under "parent"
        parent = perf.get("parent")
        if isinstance(parent, str) and parent:
            parent = {"name": parent}
        if not isinstance(parent, dict) or not parent:
            parent = perf
        scene["performers"].append({
            "performer": {
                "id": parent.get("id"),
                "name": parent.get("name"),
                "disambiguation": parent.get("disambiguation"),
                "gender": parent.get("gender"),
            },
            "as": perf.get("as"),
        })

    # Tags: uuid→id, name stays; bare strings are names
    rest_tags = rest_scene.get("tags")
    scene["tags"] = []
    for tag in rest_tags if isinstance(rest_tags, list) else []:
        if isinstance(tag, str) and tag:
            scene["tags"].append({"id": None, "name": tag})
        elif isinstance(tag, dict):
            scene["tags"].append({"id": tag.get("uuid") or tag.get("id"), "name": tag.get("name")})

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

    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
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
        dict with scenes, count, page, has_more, favorites_limited (more favorites
        than MAX_BROWSE_QUERIES, so the extra ones were left out); None on failure.
        Raises StashBoxAPIError on an auth or rate-limit failure.
    """
    # The scenes endpoint takes one performer and one site_id per request. Its
    # performers[]/tags[] parameters are keyed by numeric TPDB ids, which Stash's
    # UUID favorites don't have, and there is no array form of site_id. So each
    # favorite gets its own request (sorted, so the order is stable) and the results
    # are merged. Excluded tags are not sent: TPDB's tag taxonomy differs.
    performers = sorted(performer_ids) if performer_ids else [None]
    site_ids = []
    for studio_uuid in sorted(studio_ids) if studio_ids else []:
        site_id = resolve_site_id(api_key, studio_uuid, plugin_settings)
        if site_id:
            site_ids.append(site_id)
    if studio_ids and not site_ids:
        return {"scenes": [], "count": 0, "page": page, "has_more": False,
                "favorites_limited": False}
    sites = site_ids or [None]

    combos = [(p, s) for p in performers for s in sites]
    favorites_limited = len(combos) > MAX_BROWSE_QUERIES
    combos = combos[:MAX_BROWSE_QUERIES]

    sort_field, sort_order = _map_sort(sort, direction)
    merged, seen = [], set()
    count, has_more = 0, False
    for performer, site_id in combos:
        params = {"page": page, "limit": per_page}
        if sort_field:
            params["sort"] = sort_field
            params["sort_order"] = sort_order
        if performer:
            params["performer"] = performer
        if site_id:
            params["site_id"] = site_id
        result = _fetch_scenes(api_key, "/scenes", params, page, per_page,
                               plugin_settings, "browse scenes")
        if result is None:
            return None
        count += result["count"]
        has_more = has_more or result["has_more"]
        for scene in result["scenes"]:
            if scene.get("id") in seen:
                continue
            seen.add(scene.get("id"))
            merged.append(scene)

    if len(combos) > 1:
        merged = _sort_merged(merged, sort, direction)
        count = max(count, len(merged))
    return {"scenes": merged, "count": count, "page": page, "has_more": has_more,
            "favorites_limited": favorites_limited}


def _sort_merged(scenes, sort, direction):
    """Order merged scenes by the requested sort (stable; other sorts keep request order)."""
    field = {"DATE": "release_date", "TITLE": "title"}.get(sort)
    if not field:
        return scenes
    reverse = direction != "ASC"
    have = [s for s in scenes if s.get(field)]
    lack = [s for s in scenes if not s.get(field)]
    key = (lambda s: str(s[field]).lower()) if field == "title" else (lambda s: str(s[field]))
    return sorted(have, key=key, reverse=reverse) + lack


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

    if not isinstance(data, dict):
        if data is not None:
            log.LogWarning(f"TPDB {operation_name}: unexpected response of type {type(data).__name__}")
        return None

    # TPDB wraps results in a "data" key with pagination in "meta"
    scenes_data = data.get("data")
    if not isinstance(scenes_data, list):
        scenes_data = []
    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}

    total = meta.get("total")
    total = total if isinstance(total, int) and not isinstance(total, bool) else 0
    last_page = meta.get("last_page")
    last_page = last_page if isinstance(last_page, int) and not isinstance(last_page, bool) else 1

    transformed = []
    for s in scenes_data:
        scene_id = s.get("id") if isinstance(s, dict) else None
        try:
            if not isinstance(s, dict):
                raise TypeError(f"expected an object, got {type(s).__name__}")
            transformed.append(transform_scene(s))
        except Exception as e:
            log.LogWarning(f"TPDB {operation_name}: skipping scene {scene_id or '(no id)'}: {e}")

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
