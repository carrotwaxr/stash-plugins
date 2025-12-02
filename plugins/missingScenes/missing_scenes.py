#!/usr/bin/env python3
"""
Missing Scenes - StashDB Scene Discovery Backend
Discovers scenes from StashDB that you don't have locally.
Supports performers and studios with optional Whisparr integration.

Uses only Python standard library - no pip dependencies.
"""

import json
import sys
import urllib.request
import urllib.parse
import urllib.error
import ssl

# Import Stash-compatible logging
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

    # Use session cookie if available
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


# ============================================================================
# StashDB API
# ============================================================================

def query_stashdb_performer_scenes(stashdb_url, api_key, performer_stash_id, max_pages=10):
    """Query StashDB for all scenes featuring a performer using paginated queryScenes."""
    # First get the performer name for logging
    name_query = """
    query FindPerformer($id: ID!) {
        findPerformer(id: $id) {
            name
        }
    }
    """
    name_data = graphql_request(stashdb_url, name_query, {"id": performer_stash_id}, api_key)
    performer_name = "Unknown"
    if name_data and "findPerformer" in name_data:
        performer_name = name_data["findPerformer"].get("name", "Unknown")

    # Use queryScenes with performer filter for proper pagination
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
                    "value": [performer_stash_id],
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
        log.LogInfo(f"StashDB: Fetched page {page}, {len(scenes)} scenes (total: {total})")

        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"StashDB: Found {len(all_scenes)} scenes for {performer_name}")
    return all_scenes


def query_stashdb_studio_scenes(stashdb_url, api_key, studio_stash_id, max_pages=10):
    """Query StashDB for all scenes from a studio."""
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
                    "value": [studio_stash_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "TRENDING",  # Use TRENDING for popularity-based sorting
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
        log.LogInfo(f"StashDB: Fetched page {page}, {len(scenes)} scenes (total: {total})")

        if page * per_page >= total:
            break

        page += 1

    log.LogInfo(f"StashDB: Found {len(all_scenes)} total scenes for studio")
    return all_scenes


# ============================================================================
# Whisparr API
# ============================================================================

def whisparr_request(whisparr_url, api_key, endpoint, method="GET", payload=None):
    """Make a request to the Whisparr API."""
    url = f"{whisparr_url.rstrip('/')}/api/v3/{endpoint}"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    data = None
    if payload:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        log.LogError(f"Whisparr HTTP error {e.code}: {e.reason} - {body}")
        raise
    except Exception as e:
        log.LogError(f"Whisparr request error: {e}")
        raise


def whisparr_lookup_scene(whisparr_url, api_key, stash_id):
    """Lookup a scene in Whisparr by its StashDB ID."""
    try:
        # Use the lookup endpoint to search by stash ID
        endpoint = f"lookup/scene?term=stash:{urllib.parse.quote(stash_id)}"
        result = whisparr_request(whisparr_url, api_key, endpoint)
        if result and len(result) > 0:
            return result[0].get("movie")
        return None
    except Exception as e:
        log.LogWarning(f"Whisparr lookup failed for {stash_id}: {e}")
        return None


def whisparr_add_scene(whisparr_url, api_key, scene_data, quality_profile_id, root_folder, search_on_add=True):
    """Add a scene to Whisparr."""
    payload = {
        "foreignId": f"stash:{scene_data['stash_id']}",
        "title": scene_data.get("title", "Unknown"),
        "rootFolderPath": root_folder,
        "qualityProfileId": quality_profile_id,
        "monitored": True,
        "tags": [],
        "addOptions": {
            "searchForMovie": search_on_add
        }
    }

    try:
        result = whisparr_request(whisparr_url, api_key, "movie", "POST", payload)
        log.LogInfo(f"Added scene to Whisparr: {scene_data.get('title')}")
        return result
    except Exception as e:
        log.LogError(f"Failed to add scene to Whisparr: {e}")
        raise


def whisparr_get_existing_stash_ids(whisparr_url, api_key):
    """Get all StashDB IDs already in Whisparr."""
    stash_ids = set()
    page = 1
    per_page = 100

    while True:
        try:
            endpoint = f"movie?page={page}&pageSize={per_page}"
            result = whisparr_request(whisparr_url, api_key, endpoint)

            if not result:
                break

            # Handle both list response and paginated response
            if isinstance(result, list):
                scenes = result
            else:
                scenes = result.get("records", result)

            if not scenes:
                break

            for scene in scenes:
                foreign_id = scene.get("foreignId", "")
                if foreign_id.startswith("stash:"):
                    stash_ids.add(foreign_id.replace("stash:", ""))

            # If we got fewer results than requested, we're done
            if len(scenes) < per_page:
                break

            page += 1

        except Exception as e:
            log.LogWarning(f"Error fetching Whisparr scenes page {page}: {e}")
            break

    log.LogInfo(f"Found {len(stash_ids)} scenes in Whisparr")
    return stash_ids


# ============================================================================
# Main Operations
# ============================================================================

def find_missing_scenes(entity_type, entity_id, plugin_settings):
    """
    Find scenes from StashDB that are not in local Stash.

    Args:
        entity_type: "performer" or "studio"
        entity_id: Local Stash ID of the performer/studio
        plugin_settings: Plugin configuration from Stash

    Returns:
        Dict with missing scenes and metadata
    """

    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

    # Check if user specified a preferred endpoint
    preferred_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()

    # Find the matching stash-box config
    stashbox = None
    if preferred_endpoint:
        # User specified an endpoint - find it
        for config in stashbox_configs:
            if config["endpoint"] == preferred_endpoint:
                stashbox = config
                break
        if not stashbox:
            # Endpoint not found in configured list
            available = ", ".join([c.get("name", c["endpoint"]) for c in stashbox_configs])
            return {"error": f"Configured stash-box endpoint '{preferred_endpoint}' not found. Available: {available}"}
    else:
        # Use the first stash-box (usually StashDB)
        stashbox = stashbox_configs[0]

    stashdb_url = stashbox["endpoint"]
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    log.LogInfo(f"Using stash-box: {stashdb_name} ({stashdb_url})")

    # Get the local entity and its stash_id
    if entity_type == "performer":
        entity = get_local_performer(entity_id)
    elif entity_type == "studio":
        entity = get_local_studio(entity_id)
    else:
        return {"error": f"Unknown entity type: {entity_type}"}

    if not entity:
        return {"error": f"{entity_type.title()} not found: {entity_id}"}

    # Find the stash_id for this stash-box endpoint
    stash_id = None
    for sid in entity.get("stash_ids", []):
        if sid.get("endpoint") == stashdb_url:
            stash_id = sid.get("stash_id")
            break

    if not stash_id:
        return {
            "error": f"{entity_type.title()} '{entity.get('name')}' is not linked to {stashdb_name}. "
                     f"Please use the Tagger to link this {entity_type} first."
        }

    log.LogInfo(f"Found {entity_type} '{entity.get('name')}' with StashDB ID: {stash_id}")

    # Query StashDB for all scenes
    if entity_type == "performer":
        stashdb_scenes = query_stashdb_performer_scenes(stashdb_url, stashdb_api_key, stash_id)
    else:
        stashdb_scenes = query_stashdb_studio_scenes(stashdb_url, stashdb_api_key, stash_id)

    if not stashdb_scenes:
        return {
            "entity_name": entity.get("name"),
            "entity_type": entity_type,
            "stashdb_name": stashdb_name,
            "total_on_stashdb": 0,
            "total_local": 0,
            "missing_count": 0,
            "missing_scenes": []
        }

    # Get all local scene stash_ids
    local_stash_ids = get_local_scene_stash_ids(stashdb_url)

    # Also check Whisparr if configured
    whisparr_stash_ids = set()
    whisparr_configured = False
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")

    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        try:
            whisparr_stash_ids = whisparr_get_existing_stash_ids(whisparr_url, whisparr_api_key)
        except Exception as e:
            log.LogWarning(f"Could not fetch Whisparr scenes: {e}")

    # Find missing scenes
    missing_scenes = []
    for scene in stashdb_scenes:
        scene_stash_id = scene.get("id")
        if scene_stash_id not in local_stash_ids:
            # Format the scene data
            formatted = format_scene(scene, scene_stash_id)

            # Mark if it's in Whisparr
            formatted["in_whisparr"] = scene_stash_id in whisparr_stash_ids

            missing_scenes.append(formatted)

    # Sort by release date (newest first)
    # Note: For studio queries, StashDB returns results sorted by TRENDING (popularity),
    # but we re-sort by date here for consistency. For performer queries, scenes come
    # back unsorted from StashDB's findPerformer endpoint.
    missing_scenes.sort(key=lambda s: s.get("release_date") or "", reverse=True)

    log.LogInfo(f"Found {len(missing_scenes)} missing scenes out of {len(stashdb_scenes)} total")

    return {
        "entity_name": entity.get("name"),
        "entity_type": entity_type,
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": len(stashdb_scenes),
        "total_local": len(stashdb_scenes) - len(missing_scenes),
        "missing_count": len(missing_scenes),
        "missing_scenes": missing_scenes,
        "whisparr_configured": whisparr_configured
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


def add_to_whisparr(stash_id, title, plugin_settings):
    """Add a scene to Whisparr by its StashDB ID."""
    whisparr_url = plugin_settings.get("whisparrUrl", "")
    whisparr_api_key = plugin_settings.get("whisparrApiKey", "")
    quality_profile = int(plugin_settings.get("whisparrQualityProfile") or 1)
    root_folder = plugin_settings.get("whisparrRootFolder", "")
    search_on_add = plugin_settings.get("whisparrSearchOnAdd", True)

    if not whisparr_url or not whisparr_api_key:
        return {"error": "Whisparr is not configured. Please set URL and API key in plugin settings."}

    if not root_folder:
        return {"error": "Whisparr root folder is not configured. Please set it in plugin settings."}

    try:
        # First lookup the scene to get full details
        scene = whisparr_lookup_scene(whisparr_url, whisparr_api_key, stash_id)

        if not scene:
            return {"error": f"Scene not found in Whisparr lookup. It may not be available for download yet."}

        # Add the scene
        result = whisparr_add_scene(
            whisparr_url,
            whisparr_api_key,
            {"stash_id": stash_id, "title": title},
            quality_profile,
            root_folder,
            search_on_add
        )

        return {
            "success": True,
            "message": f"Added '{title}' to Whisparr",
            "scene": result
        }

    except Exception as e:
        return {"error": str(e)}


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

    # Get the operation arguments
    args = input_data.get("args", {})
    operation = args.get("operation", "")

    # Get plugin settings
    server_connection = input_data.get("server_connection", {})
    plugin_settings = {}

    # Try to get plugin settings from the configuration
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

    output = {"error": "Unknown operation"}

    try:
        if operation == "find_missing":
            entity_type = args.get("entity_type", "performer")
            entity_id = args.get("entity_id", "")

            if not entity_id:
                output = {"error": "entity_id is required"}
            else:
                output = find_missing_scenes(entity_type, entity_id, plugin_settings)

        elif operation == "add_to_whisparr":
            stash_id = args.get("stash_id", "")
            title = args.get("title", "Unknown")

            if not stash_id:
                output = {"error": "stash_id is required"}
            else:
                output = add_to_whisparr(stash_id, title, plugin_settings)

        else:
            output = {"error": f"Unknown operation: {operation}"}

    except Exception as e:
        log.LogError(f"Operation failed: {e}")
        output = {"error": str(e)}

    # Wrap output in PluginOutput structure expected by Stash
    # Structure: {"output": <data>} or {"error": "message"}
    if "error" in output:
        plugin_output = {"error": output["error"]}
    else:
        plugin_output = {"output": output}

    print(json.dumps(plugin_output))


if __name__ == "__main__":
    main()
