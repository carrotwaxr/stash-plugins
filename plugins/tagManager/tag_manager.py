"""
tagManager - Stash Plugin for matching tags with StashDB.

Entry point for plugin operations. Handles different modes:
- search: Search for StashDB matches for a local tag
- fetch_all: Fetch all StashDB tags (for caching)

Called via runPluginOperation from JavaScript UI.
"""

import json
import os
import sys

import log
from stashdb_api import search_tags_by_name, query_all_tags
from matcher import TagMatcher, load_synonyms

# Plugin ID must match yml
PLUGIN_ID = "tagManager"


def get_plugin_dir():
    """Get the plugin directory path."""
    return os.path.dirname(os.path.abspath(__file__))


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
        # Determine match type
        match_type = "exact" if tag["name"].lower() == tag_name.lower() else "alias"
        combined_matches.append({
            "tag": tag,
            "match_type": match_type,
            "score": 100,
            "matched_on": tag_name if match_type == "alias" else tag["name"]
        })

    # Add local fuzzy/synonym matches not already present
    for match in local_matches:
        if match["tag"]["id"] not in seen_ids:
            seen_ids.add(match["tag"]["id"])
            combined_matches.append(match)

    # Sort by score
    combined_matches.sort(key=lambda m: m["score"], reverse=True)

    log.LogInfo(f"Found {len(combined_matches)} matches for '{tag_name}'")

    return {
        "tag_name": tag_name,
        "matches": combined_matches[:20],  # Limit to top 20
        "total_matches": len(combined_matches)
    }


def handle_fetch_all(stashdb_url, stashdb_api_key):
    """
    Fetch all tags from StashDB for local caching.

    Args:
        stashdb_url: StashDB GraphQL endpoint
        stashdb_api_key: StashDB API key

    Returns:
        Dict with all StashDB tags
    """
    log.LogInfo("Fetching all StashDB tags...")
    tags = query_all_tags(stashdb_url, stashdb_api_key)

    return {
        "tags": tags,
        "count": len(tags)
    }


def main():
    """Main entry point - reads input from stdin, routes to handler, outputs result."""
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Failed to parse input: {e}"}))
        return

    args = input_data.get("args", {})
    mode = args.get("mode", "search")

    log.LogDebug(f"tagManager called with mode: {mode}")

    # Get settings from server connection or args
    server_connection = input_data.get("server_connection", {})
    # For now, settings come from args (JS passes them)
    settings = get_settings_from_config(args.get("settings", {}))

    stashdb_url = args.get("stashdb_url") or settings["stashdb_url"]
    stashdb_api_key = args.get("stashdb_api_key") or settings["stashdb_api_key"]

    if not stashdb_api_key:
        print(json.dumps({"error": "StashDB API key not configured"}))
        return

    try:
        if mode == "search":
            tag_name = args.get("tag_name", "")
            if not tag_name:
                print(json.dumps({"error": "No tag name provided"}))
                return

            # Pass cached tags if provided
            stashdb_tags = args.get("stashdb_tags")
            result = handle_search(tag_name, stashdb_url, stashdb_api_key, settings, stashdb_tags)

        elif mode == "fetch_all":
            result = handle_fetch_all(stashdb_url, stashdb_api_key)

        else:
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
