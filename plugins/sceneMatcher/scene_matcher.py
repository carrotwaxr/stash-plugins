#!/usr/bin/env python3
"""
Scene Matcher - Find StashDB matches using known attributes.
Searches StashDB for scenes matching a local scene's performers and/or studio.

Uses only Python standard library - no pip dependencies.
"""

import json
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
# StashDB API
# ============================================================================

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


def score_scene(scene, performer_stash_ids, studio_stash_id):
    """
    Calculate relevance score for a scene.
    +3 for matching studio, +2 per matching performer.
    """
    score = 0

    # Check studio match
    if studio_stash_id and scene.get("studio"):
        if scene["studio"].get("id") == studio_stash_id:
            score += 3

    # Check performer matches
    scene_performer_ids = set()
    for perf in scene.get("performers", []):
        p = perf.get("performer", {})
        if p.get("id"):
            scene_performer_ids.add(p.get("id"))

    matching_performers = performer_stash_ids & scene_performer_ids
    score += len(matching_performers) * 2

    return score, len(matching_performers)


def find_matching_scenes(scene_id, plugin_settings):
    """
    Find StashDB scenes matching a local scene's performers and/or studio.
    """
    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

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
            return {"error": f"Configured stash-box endpoint '{preferred_endpoint}' not found. Available: {available}"}
    else:
        stashbox = stashbox_configs[0]

    stashdb_url = stashbox["endpoint"]
    stashdb_api_key = stashbox.get("api_key", "")
    stashdb_name = stashbox.get("name", "StashDB")

    log.LogInfo(f"Using stash-box: {stashdb_name} ({stashdb_url})")

    # Get the local scene
    scene = get_local_scene(scene_id)
    if not scene:
        return {"error": f"Scene not found: {scene_id}"}

    # Check if scene already has a StashDB ID
    for stash_id in scene.get("stash_ids", []):
        if stash_id.get("endpoint") == stashdb_url:
            return {"error": "Scene already has a StashDB ID. No matching needed."}

    # Extract performer and studio stash_ids for this endpoint
    performer_stash_ids = set()
    performer_names = []
    for performer in scene.get("performers", []):
        for stash_id in performer.get("stash_ids", []):
            if stash_id.get("endpoint") == stashdb_url:
                performer_stash_ids.add(stash_id.get("stash_id"))
                performer_names.append(performer.get("name"))
                break

    studio_stash_id = None
    studio_name = None
    studio = scene.get("studio")
    if studio:
        for stash_id in studio.get("stash_ids", []):
            if stash_id.get("endpoint") == stashdb_url:
                studio_stash_id = stash_id.get("stash_id")
                studio_name = studio.get("name")
                break

    if not performer_stash_ids and not studio_stash_id:
        return {
            "error": f"Scene has no performers or studio linked to {stashdb_name}. "
                     "Link at least one performer or studio first using the Tagger."
        }

    log.LogInfo(f"Searching by: {len(performer_stash_ids)} performers, studio: {studio_name or 'None'}")

    # Query StashDB
    all_scenes = {}

    # Query by performers
    if performer_stash_ids:
        performer_scenes = query_stashdb_scenes_by_performers(
            stashdb_url, stashdb_api_key, list(performer_stash_ids)
        )
        for s in performer_scenes:
            all_scenes[s["id"]] = s

    # Query by studio
    if studio_stash_id:
        studio_scenes = query_stashdb_scenes_by_studio(
            stashdb_url, stashdb_api_key, studio_stash_id
        )
        for s in studio_scenes:
            all_scenes[s["id"]] = s

    if not all_scenes:
        return {
            "scene_title": scene.get("title"),
            "search_attributes": {
                "performers": performer_names,
                "studio": studio_name
            },
            "stashdb_name": stashdb_name,
            "stashdb_url": stashdb_url.replace("/graphql", ""),
            "total_results": 0,
            "results": []
        }

    # Get local scene stash_ids to mark which results user already has
    log.LogDebug("Fetching local scene stash_ids...")
    local_stash_ids = get_local_scene_stash_ids(stashdb_url)

    # Score and format results
    results = []
    for stashdb_scene_id, stashdb_scene in all_scenes.items():
        score, matching_performer_count = score_scene(
            stashdb_scene, performer_stash_ids, studio_stash_id
        )

        formatted = format_scene(stashdb_scene, stashdb_scene_id)
        formatted["score"] = score
        formatted["matching_performers"] = matching_performer_count
        formatted["matches_studio"] = (
            studio_stash_id is not None and
            stashdb_scene.get("studio", {}).get("id") == studio_stash_id
        )
        formatted["in_local_stash"] = stashdb_scene_id in local_stash_ids

        results.append(formatted)

    # Sort: not in local stash first, then by score descending, then by date descending
    def sort_key(x):
        # 0 = not in stash (sort first), 1 = in stash (sort second)
        in_stash = 1 if x["in_local_stash"] else 0
        # Negate score so higher scores sort first
        score = -x["score"]
        # Convert date to sortable integer (newer = higher = sort first when negated)
        date_str = x.get("release_date") or ""
        date_int = int(date_str[:10].replace("-", "")) if date_str else 0
        return (in_stash, score, -date_int)

    results.sort(key=sort_key)

    log.LogInfo(f"Returning {len(results)} matching scenes")

    return {
        "scene_title": scene.get("title"),
        "search_attributes": {
            "performers": performer_names,
            "studio": studio_name
        },
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_results": len(results),
        "results": results
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
        if operation == "find_matches":
            scene_id = args.get("scene_id", "")
            if not scene_id:
                output = {"error": "scene_id is required"}
            else:
                output = find_matching_scenes(scene_id, plugin_settings)

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
