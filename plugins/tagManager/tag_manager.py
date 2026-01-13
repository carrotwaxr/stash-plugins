"""
tagManager - Stash Plugin for matching tags with StashDB.

Entry point for plugin operations. Handles different modes:
- search: Search for StashDB matches for a local tag
- fetch_all: Fetch all StashDB tags (for caching)
- get_cache_status: Get cache info for an endpoint
- refresh_cache: Force refresh cache for an endpoint
- clear_cache: Clear cache for an endpoint

Called via runPluginOperation from JavaScript UI.
"""

import hashlib
import json
import os
import sys
import time

import log
from stashapi.stashapp import StashInterface
from stashdb_api import search_tags_by_name, query_all_tags
from matcher import TagMatcher, load_synonyms
from blacklist import Blacklist

# Plugin ID must match yml
PLUGIN_ID = "tagManager"

# Cache configuration
CACHE_MAX_AGE_HOURS = 24  # Cache expires after 24 hours


def get_plugin_dir():
    """Get the plugin directory path."""
    return os.path.dirname(os.path.abspath(__file__))


def get_cache_dir():
    """Get the cache directory path for storing endpoint tag caches."""
    # Use a cache directory within the plugin folder
    cache_dir = os.path.join(get_plugin_dir(), "cache")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        log.LogDebug(f"Created cache directory: {cache_dir}")
    return cache_dir


def get_cache_file_path(endpoint_url):
    """
    Get the cache file path for a specific endpoint.

    Uses a hash of the endpoint URL to create a unique filename.
    """
    # Create a hash of the endpoint URL for the filename
    url_hash = hashlib.md5(endpoint_url.encode('utf-8')).hexdigest()[:12]
    # Also include a readable portion of the URL
    readable_part = endpoint_url.replace('https://', '').replace('http://', '').replace('/', '_')[:30]
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
    cache_path = get_cache_file_path(endpoint_url)

    cache_data = {
        'endpoint': endpoint_url,
        'timestamp': time.time(),
        'count': len(tags),
        'tags': tags
    }

    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)

        log.LogInfo(f"Saved {len(tags)} tags to cache: {cache_path}")
        return True

    except OSError as e:
        log.LogError(f"Cache write error: {e}")
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
        "stashdb_url": config.get("stashdbEndpoint", "https://stashdb.org/graphql"),
        "stashdb_api_key": config.get("stashdbApiKey", ""),
        "enable_fuzzy": config.get("enableFuzzySearch") != False,  # Default True
        "enable_synonyms": config.get("enableSynonymSearch") != False,  # Default True
        "fuzzy_threshold": int(config.get("fuzzyThreshold", 80)),
        "page_size": int(config.get("pageSize", 25)),
        "tag_blacklist": config.get("tagBlacklist", ""),
    }


def handle_search(tag_name, stashdb_url, stashdb_api_key, settings, stashdb_tags=None):
    """
    Search for StashDB matches for a local tag.

    Args:
        tag_name: Local tag name to match
        stashdb_url: StashDB GraphQL endpoint
        stashdb_api_key: StashDB API key
        settings: Plugin settings dict
        stashdb_tags: Optional cached StashDB tags (avoids re-fetch)

    Returns:
        Dict with matches and search info
    """
    log.LogDebug(f"Searching for tag: {tag_name}")

    # Load blacklist from settings
    blacklist = Blacklist(settings.get('tag_blacklist', ''))

    # First try StashDB's name search (searches name + aliases)
    api_matches = search_tags_by_name(stashdb_url, stashdb_api_key, tag_name, limit=20)

    # If we have cached tags, also do local fuzzy matching
    local_matches = []
    if stashdb_tags and settings.get("enable_fuzzy", True):
        synonyms_path = os.path.join(get_plugin_dir(), "synonyms.json")
        synonyms = load_synonyms(synonyms_path)

        matcher = TagMatcher(
            stashdb_tags,
            synonyms=synonyms,
            fuzzy_threshold=settings.get("fuzzy_threshold", 80)
        )
        local_matches = matcher.find_matches(
            tag_name,
            enable_fuzzy=settings.get("enable_fuzzy", True),
            enable_synonyms=settings.get("enable_synonyms", True),
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
        "total_matches": len(combined_matches)
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
    except Exception as e:
        log.LogError(f"Error fetching tags from {stashdb_url}: {e}")
        raise

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


def handle_sync_scene_tags(server_connection, stash_config, api_key):
    """
    Handle sync_scene_tags mode - sync tags from StashDB to local scenes.

    Args:
        server_connection: Stash server connection info
        stash_config: Full Stash configuration
        api_key: Stash API key for authentication

    Returns:
        Dict with sync results
    """
    from stashdb_scene_sync import sync_scene_tags

    # Initialize Stash interface with API key for long-running operations
    # Session cookies can expire during long sync operations (see stash#5332)
    connection_with_api_key = {**server_connection, "ApiKey": api_key}
    stash = StashInterface(connection_with_api_key)

    # Get StashDB configuration from Stash
    try:
        stash_boxes = stash_config.get("general", {}).get("stashBoxes", [])
    except Exception as e:
        log.LogError(f"Failed to get Stash configuration: {e}")
        return {"error": f"Failed to get Stash configuration: {e}"}

    if not stash_boxes:
        log.LogWarning("No stash-box endpoints configured in Stash")
        return {"error": "No stash-box endpoints configured. Go to Settings > Metadata Providers to add StashDB."}

    # Use first stash-box (typically StashDB)
    stashdb_config = stash_boxes[0]
    stashdb_url = stashdb_config.get("endpoint", "")
    stashdb_api_key = stashdb_config.get("api_key", "")

    if not stashdb_url or not stashdb_api_key:
        log.LogError("StashDB endpoint or API key not configured")
        return {"error": "StashDB endpoint or API key not configured"}

    log.LogInfo(f"Using stash-box endpoint: {stashdb_url}")

    # Get dry_run setting from plugin config
    try:
        plugin_config = stash_config.get("plugins", {}).get(PLUGIN_ID, {})
        dry_run = plugin_config.get("syncDryRun", True)  # Default to safe mode
    except Exception:
        dry_run = True

    sync_settings = {
        "dry_run": dry_run
    }

    # Run sync
    try:
        stats = sync_scene_tags(stash, stashdb_url, stashdb_api_key, sync_settings)
        return {
            "success": True,
            "dry_run": dry_run,
            "total_scenes": stats.total_scenes,
            "processed": stats.processed,
            "updated": stats.updated,
            "no_changes": stats.no_changes,
            "skipped": stats.skipped,
            "errors": stats.errors,
            "tags_added": stats.tags_added_total,
            "tags_skipped": stats.tags_skipped_total
        }
    except Exception as e:
        log.LogError(f"Sync failed: {e}")
        import traceback
        log.LogDebug(traceback.format_exc())
        return {"error": str(e)}


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

    # Get settings from server connection or args
    server_connection = input_data.get("server_connection", {})
    # For now, settings come from args (JS passes them)
    settings = get_settings_from_config(args.get("settings", {}))

    # Stash-box URL and API key can come from args (for multi-endpoint support)
    stashdb_url = args.get("stashdb_url") or settings["stashdb_url"]
    stashdb_api_key = args.get("stashdb_api_key") or settings["stashdb_api_key"]

    log.LogDebug(f"Using endpoint: {stashdb_url}")

    # Cache status doesn't require API key
    if mode == "get_cache_status":
        if not stashdb_url:
            print(json.dumps({"output": {"error": "No endpoint URL provided"}}))
            return
        result = handle_get_cache_status(stashdb_url)
        print(json.dumps({"output": result}))
        return

    if mode == "clear_cache":
        if not stashdb_url:
            print(json.dumps({"output": {"error": "No endpoint URL provided"}}))
            return
        result = handle_clear_cache(stashdb_url)
        print(json.dumps({"output": result}))
        return

    if mode == "sync_scene_tags":
        log.LogInfo("Starting scene tag sync task")
        # Get stash config for stash-box credentials and API key
        stash = StashInterface(server_connection)
        try:
            stash_config = stash.get_configuration()
        except Exception as e:
            log.LogError(f"Failed to get Stash configuration: {e}")
            print(json.dumps({"output": {"error": f"Failed to get configuration: {e}"}}))
            return

        # Get the Stash API key for long-running sync operations
        # Session cookies can expire during multi-hour syncs (stash#5332)
        api_key = stash_config.get("general", {}).get("apiKey", "")
        if not api_key:
            log.LogWarning("No Stash API key configured - using session cookie (may timeout)")

        result = handle_sync_scene_tags(server_connection, stash_config, api_key)
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

            # Pass cached tags if provided (from JS cache)
            stashdb_tags = args.get("stashdb_tags")
            if stashdb_tags:
                log.LogDebug(f"Using {len(stashdb_tags)} cached tags from JS")
            result = handle_search(tag_name, stashdb_url, stashdb_api_key, settings, stashdb_tags)

        elif mode == "fetch_all":
            force_refresh = args.get("force_refresh", False)
            log.LogDebug(f"fetch_all mode, force_refresh={force_refresh}")
            result = handle_fetch_all(stashdb_url, stashdb_api_key, force_refresh=force_refresh)

        else:
            log.LogWarning(f"Unknown mode requested: {mode}")
            result = {"error": f"Unknown mode: {mode}"}

        output = {"output": result}

    except Exception as e:
        log.LogError(f"Error in mode '{mode}': {e}")
        import traceback
        log.LogDebug(traceback.format_exc())
        output = {"output": {"error": str(e)}}

    print(json.dumps(output))


if __name__ == "__main__":
    main()
