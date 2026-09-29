"""
tagManager - Stash Plugin for matching tags with StashDB.

Entry point for plugin operations. Handles different modes:
- search: Search for StashDB matches for a local tag
- fetch_all: Fetch all StashDB tags (for caching)
- get_cache_status: Get cache info for an endpoint
- refresh_cache: Force refresh cache for an endpoint
- clear_cache: Clear cache for an endpoint
- reset_sync_history: Forget which stash-box tags scene sync has applied, so the
  next sync adds back tags removed from scenes

Called via runPluginOperation from JavaScript UI.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import time

import log
import plugin_data
from stashdb_api import search_tags_by_name, query_all_tags, StashDBAPIError
from matcher import TagMatcher, load_synonyms
from blacklist import Blacklist
from stash_client import LocalStash, StashError
from sync_history import SyncHistory

# Plugin ID must match yml
PLUGIN_ID = "tagManager"

# Cache configuration
CACHE_MAX_AGE_HOURS = 24  # Cache expires after 24 hours


def get_plugin_dir():
    """Get the plugin directory path."""
    return os.path.dirname(os.path.abspath(__file__))


def load_default_settings():
    """
    Load canonical plugin defaults from shared JSON file.

    Returns:
        Dict with default plugin settings.
    """
    defaults_path = os.path.join(get_plugin_dir(), "assets", "default_settings.json")
    try:
        with open(defaults_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to load default settings file '{defaults_path}': {e}") from e


# Canonical plugin defaults (single source of truth for Python + JS).
# Note: tagManager.yml settings schema does not support "default" fields.
DEFAULT_PLUGIN_SETTINGS = load_default_settings()


def get_cache_dir():
    """Get the cache directory (inside Stash's config dir) for endpoint tag caches."""
    cache_dir = os.path.join(plugin_data.current_dir(), "tag_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_sync_history_path():
    """The scene sync history file (inside Stash's config dir)."""
    return os.path.join(plugin_data.current_dir(), "sync_history.sqlite")


def get_cache_file_path(endpoint_url):
    """
    Get the cache file path for a specific endpoint.

    Uses a hash of the endpoint URL to create a unique filename.
    """
    # Create a hash of the endpoint URL for the filename
    url_hash = hashlib.md5(endpoint_url.encode('utf-8')).hexdigest()[:12]
    # Also include a readable portion of the URL
    readable_part = endpoint_url.replace('https://', '').replace('http://', '').replace('/', '_')[:30]
    readable_part = re.sub(r"[^A-Za-z0-9._-]", "_", readable_part)
    filename = f"tags_{readable_part}_{url_hash}.json"
    return os.path.join(get_cache_dir(), filename)


def load_cached_tags(endpoint_url):
    """
    Load cached tags for an endpoint if available and not expired.

    Args:
        endpoint_url: The stash-box endpoint URL

    Returns:
        Dict with 'tags', 'timestamp', 'count' or None if cache miss
    """
    cache_path = get_cache_file_path(endpoint_url)
    log.LogDebug(f"Checking cache at: {cache_path}")

    if not os.path.exists(cache_path):
        log.LogDebug(f"Cache miss: file not found for {endpoint_url}")
        return None

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        timestamp = cache_data.get('timestamp', 0)
        age_hours = (time.time() - timestamp) / 3600
        max_age = CACHE_MAX_AGE_HOURS

        if age_hours > max_age:
            log.LogDebug(f"Cache expired: {age_hours:.1f} hours old (max: {max_age}h)")
            return None

        tag_count = len(cache_data.get('tags', []))
        log.LogInfo(f"Cache hit: {tag_count} tags from {endpoint_url} ({age_hours:.1f}h old)")

        return cache_data

    except (json.JSONDecodeError, OSError) as e:
        log.LogWarning(f"Cache read error for {endpoint_url}: {e}")
        return None


def save_tags_to_cache(endpoint_url, tags):
    """
    Save tags to cache file.

    Args:
        endpoint_url: The stash-box endpoint URL
        tags: List of tag dicts to cache

    Returns:
        Bool indicating success
    """
    if not tags:
        log.LogWarning(f"Refusing to cache empty tag list for {endpoint_url}")
        return False

    cache_path = get_cache_file_path(endpoint_url)
    tmp_path = cache_path + ".tmp"

    cache_data = {
        'endpoint': endpoint_url,
        'timestamp': time.time(),
        'count': len(tags),
        'tags': tags
    }

    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp_path, cache_path)

        log.LogInfo(f"Saved {len(tags)} tags to cache: {cache_path}")
        return True

    except OSError as e:
        log.LogError(f"Cache write error: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False


def get_cache_status(endpoint_url):
    """
    Get cache status for an endpoint.

    Args:
        endpoint_url: The stash-box endpoint URL

    Returns:
        Dict with cache status info
    """
    cache_path = get_cache_file_path(endpoint_url)

    if not os.path.exists(cache_path):
        return {
            'exists': False,
            'endpoint': endpoint_url,
            'path': cache_path
        }

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        timestamp = cache_data.get('timestamp', 0)
        age_hours = (time.time() - timestamp) / 3600

        return {
            'exists': True,
            'endpoint': endpoint_url,
            'path': cache_path,
            'count': cache_data.get('count', 0),
            'timestamp': timestamp,
            'age_hours': round(age_hours, 1),
            'expired': age_hours > CACHE_MAX_AGE_HOURS
        }

    except (json.JSONDecodeError, OSError) as e:
        log.LogWarning(f"Error reading cache status: {e}")
        return {
            'exists': False,
            'endpoint': endpoint_url,
            'error': str(e)
        }


def clear_cache(endpoint_url):
    """
    Clear cache for an endpoint.

    Args:
        endpoint_url: The stash-box endpoint URL

    Returns:
        Bool indicating success
    """
    cache_path = get_cache_file_path(endpoint_url)

    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            log.LogInfo(f"Cleared cache: {cache_path}")
            return True
        except OSError as e:
            log.LogError(f"Error clearing cache: {e}")
            return False

    return True  # Already cleared


def safe_int(value, default):
    """Coerce a stored config value to int, falling back to default.

    Stash stores an empty string for a cleared numeric field, and backfill only
    fills *missing* keys (not ""), so a bare int() can throw on real config.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def resolve_sync_dry_run(stash_config):
    """Read syncDryRun from a (possibly malformed) Stash config, safe-defaulting.

    Never raise: a missing/None config or plugin entry falls back to the safe
    dry-run default so a bad config can't trigger unintended writes.
    """
    try:
        plugin_config = (stash_config.get("plugins") or {}).get(PLUGIN_ID) or {}
        return plugin_config.get("syncDryRun", DEFAULT_PLUGIN_SETTINGS["syncDryRun"])
    except (AttributeError, TypeError):
        return DEFAULT_PLUGIN_SETTINGS["syncDryRun"]


def _normalize_endpoint(url):
    return (url or "").strip().rstrip("/").lower()


def resolve_stashbox(requested_url, stash_config):
    """
    Find the stash-box URL and API key to use, from Stash's own configuration.

    The UI picks the endpoint, but the URL we call and its key always come from
    Stash's config. Trusting both from the client would let any caller make this
    server POST to an arbitrary host.

    Args:
        requested_url: Endpoint selected in the UI (may be empty)
        stash_config: Stash `configuration` object (general.stashBoxes, plugins)

    Returns:
        (url, api_key) tuple

    Raises:
        ValueError: If no configured endpoint matches
    """
    config = stash_config or {}
    boxes = (config.get("general") or {}).get("stashBoxes") or []
    candidates = [(b.get("endpoint"), b.get("api_key") or "") for b in boxes if b.get("endpoint")]

    # Legacy plugin-level credentials, which the UI falls back to when Stash has no stash-boxes
    plugin_config = (config.get("plugins") or {}).get(PLUGIN_ID) or {}
    if plugin_config.get("stashdbApiKey"):
        candidates.append((
            plugin_config.get("stashdbEndpoint") or "https://stashdb.org/graphql",
            plugin_config["stashdbApiKey"],
        ))

    if not candidates:
        raise ValueError("No stash-box endpoints configured. Go to Settings > Metadata Providers to add StashDB.")
    if not requested_url:
        return candidates[0]

    wanted = _normalize_endpoint(requested_url)
    for url, api_key in candidates:
        if _normalize_endpoint(url) == wanted:
            return url, api_key
    raise ValueError(f"Stash-box endpoint is not configured in Stash: {requested_url}")


def get_settings_from_config(stash_config):
    """
    Extract plugin settings from Stash configuration.

    Args:
        stash_config: Plugin configuration dict from Stash

    Returns:
        Dict with normalized settings
    """
    config = stash_config or {}

    return {
        # Stash-box URL and API key are not read from here: resolve_stashbox()
        # takes them from Stash's config.
        "enable_fuzzy": config.get("enableFuzzySearch", DEFAULT_PLUGIN_SETTINGS["enableFuzzySearch"]),
        "enable_synonyms": config.get("enableSynonymSearch", DEFAULT_PLUGIN_SETTINGS["enableSynonymSearch"]),
        "fuzzy_threshold": safe_int(config.get("fuzzyThreshold", DEFAULT_PLUGIN_SETTINGS["fuzzyThreshold"]), DEFAULT_PLUGIN_SETTINGS["fuzzyThreshold"]),
        "page_size": safe_int(config.get("pageSize", DEFAULT_PLUGIN_SETTINGS["pageSize"]), DEFAULT_PLUGIN_SETTINGS["pageSize"]),
        "tag_blacklist": config.get("tagBlacklist", DEFAULT_PLUGIN_SETTINGS["tagBlacklist"]),
    }


def handle_search(tag_name, stashdb_url, stashdb_api_key, settings):
    """
    Search for StashDB matches for a local tag.

    Args:
        tag_name: Local tag name to match
        stashdb_url: StashDB GraphQL endpoint
        stashdb_api_key: StashDB API key
        settings: Plugin settings dict

    Returns:
        Dict with matches and search info. `fuzzy_unavailable` is True when fuzzy
        matching is enabled but there is no tag cache for this endpoint yet.
    """
    log.LogDebug(f"Searching for tag: {tag_name}")

    # Read settings defensively — callers may pass a partial dict (defaults apply).
    tag_blacklist = settings.get("tag_blacklist", DEFAULT_PLUGIN_SETTINGS["tagBlacklist"])
    enable_fuzzy = settings.get("enable_fuzzy", DEFAULT_PLUGIN_SETTINGS["enableFuzzySearch"])
    enable_synonyms = settings.get("enable_synonyms", DEFAULT_PLUGIN_SETTINGS["enableSynonymSearch"])
    fuzzy_threshold = safe_int(settings.get("fuzzy_threshold", DEFAULT_PLUGIN_SETTINGS["fuzzyThreshold"]), DEFAULT_PLUGIN_SETTINGS["fuzzyThreshold"])

    # Load blacklist from settings
    blacklist = Blacklist(tag_blacklist)

    # First try StashDB's name search (searches name + aliases)
    api_matches = search_tags_by_name(stashdb_url, stashdb_api_key, tag_name, limit=20)

    # If we have cached tags, also do local fuzzy matching
    local_matches = []
    fuzzy_unavailable = False
    cache = load_cached_tags(stashdb_url) if enable_fuzzy else None
    stashdb_tags = (cache or {}).get("tags") or []
    if enable_fuzzy and not stashdb_tags:
        fuzzy_unavailable = True
    if stashdb_tags and enable_fuzzy:
        synonyms_path = os.path.join(get_plugin_dir(), "synonyms.json")
        synonyms = load_synonyms(synonyms_path)

        matcher = TagMatcher(
            stashdb_tags,
            synonyms=synonyms,
            fuzzy_threshold=fuzzy_threshold
        )
        local_matches = matcher.find_matches(
            tag_name,
            enable_fuzzy=enable_fuzzy,
            enable_synonyms=enable_synonyms,
            limit=20
        )

    # Combine results: API matches first (they're pre-filtered by StashDB),
    # then add any local fuzzy matches not already present
    seen_ids = set()
    combined_matches = []

    # Add API matches (convert to our format)
    for tag in api_matches:
        seen_ids.add(tag["id"])
        # Determine match type and score
        if tag["name"].lower() == tag_name.lower():
            match_type = "exact"
            score = 100
        else:
            match_type = "alias"
            score = 95  # Slightly lower than exact matches
        combined_matches.append({
            "tag": tag,
            "match_type": match_type,
            "score": score,
            "matched_on": tag_name if match_type == "alias" else tag["name"]
        })

    # Add local fuzzy/synonym matches not already present
    for match in local_matches:
        if match["tag"]["id"] not in seen_ids:
            seen_ids.add(match["tag"]["id"])
            combined_matches.append(match)

    # Sort by score
    combined_matches.sort(key=lambda m: m["score"], reverse=True)

    # Filter out blacklisted tags
    if blacklist.count > 0:
        filtered_matches = []
        hidden_count = 0
        for match in combined_matches:
            if blacklist.is_blacklisted(match["tag"]["name"]):
                hidden_count += 1
            else:
                filtered_matches.append(match)
        combined_matches = filtered_matches
        if hidden_count > 0:
            log.LogDebug(f"Filtered {hidden_count} blacklisted tags from search results")

    log.LogInfo(f"Found {len(combined_matches)} matches for '{tag_name}'")

    return {
        "tag_name": tag_name,
        "matches": combined_matches[:20],  # Limit to top 20
        "total_matches": len(combined_matches),
        "fuzzy_unavailable": fuzzy_unavailable,
    }


def handle_fetch_all(stashdb_url, stashdb_api_key, force_refresh=False):
    """
    Fetch all tags from a stash-box endpoint, using cache if available.

    Args:
        stashdb_url: Stash-box GraphQL endpoint
        stashdb_api_key: Stash-box API key
        force_refresh: If True, skip cache and fetch fresh data

    Returns:
        Dict with tags, count, and cache info
    """
    log.LogDebug(f"handle_fetch_all called for {stashdb_url} (force_refresh={force_refresh})")

    # Try loading from cache first (unless force refresh)
    if not force_refresh:
        cached = load_cached_tags(stashdb_url)
        if cached:
            return {
                "tags": cached.get('tags', []),
                "count": cached.get('count', 0),
                "from_cache": True,
                "cache_age_hours": round((time.time() - cached.get('timestamp', 0)) / 3600, 1)
            }

    # Fetch fresh from API
    log.LogInfo(f"Fetching all tags from {stashdb_url}...")
    start_time = time.time()

    try:
        tags = query_all_tags(stashdb_url, stashdb_api_key)
    except StashDBAPIError as e:
        log.LogError(f"Error fetching tags from {stashdb_url}: {e}")
        return {"error": str(e), "auth_error": e.is_auth_error}

    elapsed = time.time() - start_time
    log.LogInfo(f"Fetched {len(tags)} tags in {elapsed:.1f}s")

    # Save to cache
    save_tags_to_cache(stashdb_url, tags)

    return {
        "tags": tags,
        "count": len(tags),
        "from_cache": False,
        "fetch_time_seconds": round(elapsed, 1)
    }


def handle_get_cache_status(stashdb_url):
    """
    Get cache status for an endpoint.

    Args:
        stashdb_url: Stash-box GraphQL endpoint

    Returns:
        Dict with cache status info
    """
    log.LogDebug(f"Getting cache status for {stashdb_url}")
    return get_cache_status(stashdb_url)


def handle_clear_cache(stashdb_url):
    """
    Clear cache for an endpoint.

    Args:
        stashdb_url: Stash-box GraphQL endpoint

    Returns:
        Dict with success status
    """
    log.LogDebug(f"Clearing cache for {stashdb_url}")
    success = clear_cache(stashdb_url)
    return {"success": success, "endpoint": stashdb_url}


def handle_sync_scene_tags(server_connection):
    """
    Handle sync_scene_tags mode - sync tags from every configured stash-box
    that has an API key to the local scenes linked to it.

    Args:
        server_connection: Stash server connection info

    Returns:
        Dict with sync results, or with `error` if the run stopped or no scene synced
    """
    from stashdb_scene_sync import sync_scene_tags

    try:
        stash_config = LocalStash(server_connection).configuration()
    except StashError as e:
        log.LogError(f"Failed to get Stash configuration: {e}")
        return {"error": f"Failed to get Stash configuration: {e}"}

    general = stash_config.get("general") or {}

    # Session cookies can expire during a long sync (stash#5332), so prefer the API key.
    api_key = general.get("apiKey") or None
    if not api_key:
        log.LogWarning("No Stash API key configured - using session cookie (may time out)")
    client = LocalStash(server_connection, api_key=api_key)

    boxes = []
    for box in general.get("stashBoxes") or []:
        if not box.get("endpoint"):
            continue
        if not box.get("api_key"):
            log.LogWarning(f"Skipping {box.get('name') or box['endpoint']}: no API key configured")
            continue
        boxes.append(box)
    if not boxes:
        log.LogWarning("No stash-box endpoints with an API key configured in Stash")
        return {"error": "No stash-box endpoints with an API key are configured. "
                         "Go to Settings > Metadata Providers to add StashDB."}

    # Settings come from what's saved in Stash (dry run safe-defaults on malformed config)
    plugin_config = (stash_config.get("plugins") or {}).get(PLUGIN_ID) or {}
    dry_run = resolve_sync_dry_run(stash_config)
    sync_settings = {
        "dry_run": dry_run,
        "tag_blacklist": plugin_config.get("tagBlacklist", DEFAULT_PLUGIN_SETTINGS["tagBlacklist"]),
    }

    # A dry run reads the history too (so its preview leaves out removed tags) but doesn't record.
    history_path = get_sync_history_path()
    try:
        history = SyncHistory(history_path)
    except sqlite3.Error as e:
        log.LogError(f"Could not open the scene sync history {history_path}: {e}")
        return {"error": f"Could not open the scene sync history {history_path}: {e}"}
    log.LogDebug(f"Scene sync history: {history_path}")

    try:
        stats = sync_scene_tags(client, boxes, sync_settings, history=history)
    except Exception as e:
        log.LogError(f"Sync failed: {e}")
        import traceback
        log.LogDebug(traceback.format_exc())
        return {"error": str(e)}
    finally:
        try:
            history.close()
        except sqlite3.Error as e:
            log.LogError(f"Could not save the scene sync history: {e}")

    result = {"dry_run": dry_run, **stats.counts(), "by_endpoint": stats.by_endpoint}
    error = stats.error
    if not error and stats.processed == 0 and stats.errors > 0:
        error = f"All {stats.errors} scenes failed to sync. See the Stash log for details."
    if error:
        return {"error": error, **result}
    return {"success": True, **result}


def handle_reset_sync_history():
    """
    Handle reset_sync_history mode - empty the scene sync history, so the next
    sync considers every stash-box tag again (adding back ones removed from scenes).

    Returns:
        Dict with `cleared` (scenes forgotten), or with `error`
    """
    path = get_sync_history_path()
    if not os.path.exists(path):
        log.LogInfo("Scene sync history is already empty")
        return {"success": True, "cleared": 0}
    try:
        history = SyncHistory(path)
        try:
            cleared = history.reset()
        finally:
            history.close()
    except sqlite3.Error as e:
        log.LogError(f"Could not reset the scene sync history {path}: {e}")
        return {"error": f"Could not reset the scene sync history: {e}"}
    log.LogInfo(f"Scene sync history reset: forgot {cleared} scenes")
    return {"success": True, "cleared": cleared}


def main():
    """Main entry point - reads input from stdin, routes to handler, outputs result."""
    try:
        raw_input = sys.stdin.read()
        log.LogTrace(f"Raw input length: {len(raw_input)} bytes")
        input_data = json.loads(raw_input)
    except json.JSONDecodeError as e:
        log.LogError(f"Failed to parse input JSON: {e}")
        print(json.dumps({"error": f"Failed to parse input: {e}"}))
        return

    args = input_data.get("args", {})
    mode = args.get("mode", "search")

    log.LogDebug(f"tagManager called with mode: {mode}")
    log.LogTrace(f"Args keys: {list(args.keys())}")

    server_connection = input_data.get("server_connection", {})
    plugin_data.configure(server_connection)
    settings = get_settings_from_config(DEFAULT_PLUGIN_SETTINGS)  # overridden from Stash's config below

    # Needs no stash-box (nor Stash)
    if mode == "reset_sync_history":
        log.LogInfo("Resetting scene tag sync history")
        print(json.dumps({"output": handle_reset_sync_history()}))
        return

    # The UI says which stash-box it has selected; the URL we use (also the cache key)
    # and its API key come from Stash's config. Scene sync resolves its own below.
    if mode != "sync_scene_tags":
        try:
            stash_config = LocalStash(server_connection).configuration()
            stashdb_url, stashdb_api_key = resolve_stashbox(args.get("stashdb_url"), stash_config)
            # Settings come from what's saved in Stash, never from the browser.
            plugin_config = (stash_config.get("plugins") or {}).get(plugin_data.PLUGIN_ID) or {}
            settings = get_settings_from_config({**DEFAULT_PLUGIN_SETTINGS, **plugin_config})
        except Exception as e:
            log.LogError(f"Could not resolve stash-box endpoint: {e}")
            print(json.dumps({"output": {"error": str(e)}}))
            return
        log.LogDebug(f"Using endpoint: {stashdb_url}")

    # Cache status doesn't require API key
    if mode == "get_cache_status":
        result = handle_get_cache_status(stashdb_url)
        print(json.dumps({"output": result}))
        return

    if mode == "clear_cache":
        result = handle_clear_cache(stashdb_url)
        print(json.dumps({"output": result}))
        return

    if mode == "sync_scene_tags":
        log.LogInfo("Starting scene tag sync task")
        result = handle_sync_scene_tags(server_connection)
        print(json.dumps({"output": result}))
        return

    # All other modes require an API key
    if not stashdb_api_key:
        log.LogWarning("No API key configured for endpoint")
        print(json.dumps({"output": {"error": f"No API key configured for endpoint: {stashdb_url}"}}))
        return

    try:
        if mode == "search":
            tag_name = args.get("tag_name", "")
            if not tag_name:
                log.LogWarning("search mode called without tag_name")
                print(json.dumps({"output": {"error": "No tag name provided"}}))
                return

            log.LogDebug(f"Searching for tag: {tag_name}")

            result = handle_search(tag_name, stashdb_url, stashdb_api_key, settings)

        elif mode == "fetch_all":
            force_refresh = args.get("force_refresh", False)
            log.LogDebug(f"fetch_all mode, force_refresh={force_refresh}")
            result = handle_fetch_all(stashdb_url, stashdb_api_key, force_refresh=force_refresh)

        else:
            log.LogWarning(f"Unknown mode requested: {mode}")
            result = {"error": f"Unknown mode: {mode}"}

        output = {"output": result}

    except StashDBAPIError as e:
        log.LogError(f"StashDB error in mode '{mode}': {e}")
        output = {"output": {"error": str(e), "auth_error": e.is_auth_error}}

    except Exception as e:
        log.LogError(f"Error in mode '{mode}': {e}")
        import traceback
        log.LogDebug(traceback.format_exc())
        output = {"output": {"error": str(e)}}

    print(json.dumps(output))


if __name__ == "__main__":
    main()
