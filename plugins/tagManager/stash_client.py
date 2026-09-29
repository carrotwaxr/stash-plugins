"""
Standard-library GraphQL client for the local Stash server.

Used by the plugin backend to read tags and scenes and to write scene tags, without
depending on stashapp-tools. Data methods return data or raise StashError.
"""

import json
import ssl
import urllib.error
import urllib.request


class StashError(Exception):
    """Raised when a request to the local Stash fails (HTTP, network or GraphQL error)."""


# Stash runs on this host. With HTTPS its cert names a public host while we connect
# via localhost, so hostname checks would fail. Don't verify.
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


CONFIGURATION_QUERY = """
query TagManagerConfiguration {
    configuration {
        general { apiKey stashBoxes { endpoint api_key name max_requests_per_minute } }
        plugins
    }
}
"""

FIND_TAGS_QUERY = """
query TagManagerFindTags($filter: FindFilterType) {
    findTags(filter: $filter) {
        count
        tags { id name aliases stash_ids { endpoint stash_id } }
    }
}
"""

FIND_SCENES_QUERY = """
query TagManagerFindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
    findScenes(filter: $filter, scene_filter: $scene_filter) {
        count
        scenes {
            id
            tags { id }
            stash_ids { endpoint stash_id }
            files { fingerprints { type value } }
        }
    }
}
"""

BULK_SCENE_UPDATE_MUTATION = """
mutation TagManagerAddSceneTags($input: BulkSceneUpdateInput!) {
    bulkSceneUpdate(input: $input) { id }
}
"""


class LocalStash:
    """GraphQL client for the Stash server that launched this plugin."""

    def __init__(self, server_connection, api_key=None, timeout=60):
        """
        Args:
            server_connection: `server_connection` object Stash passes to plugins
            api_key: Optional API key; sent instead of the session cookie when set
            timeout: Per-request timeout in seconds
        """
        host = server_connection.get("Host", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        scheme = server_connection.get("Scheme", "http")
        port = server_connection.get("Port", 9999)
        self.url = f"{scheme}://{host}:{port}/graphql"
        self.timeout = timeout

        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        cookie = (server_connection.get("SessionCookie") or {}).get("Value")
        if api_key:
            self.headers["ApiKey"] = api_key
        elif cookie:
            self.headers["Cookie"] = f"session={cookie}"

    def call(self, query, variables=None):
        """Run a GraphQL query or mutation and return its `data` object."""
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL_CONTEXT) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise StashError(f"Stash returned HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise StashError(f"Stash request failed: {e}") from e
        if result.get("errors"):
            raise StashError(f"Stash GraphQL errors: {result['errors']}")
        return result.get("data") or {}

    def configuration(self):
        """Return Stash's `configuration` (API key, stash-boxes, plugin settings)."""
        return self.call(CONFIGURATION_QUERY).get("configuration") or {}

    def find_all_tags(self, per_page=1000):
        """Return every tag (id, name, aliases, stash_ids), paginating on `count`."""
        tags = []
        page = 1
        while True:
            data = self.call(FIND_TAGS_QUERY, {"filter": {"page": page, "per_page": per_page}})
            result = data.get("findTags") or {}
            batch = result.get("tags") or []
            tags.extend(batch)
            if not batch or len(tags) >= result.get("count", 0):
                return tags
            page += 1

    def iter_scenes_with_stash_id(self, endpoint, per_page=100, limit=None):
        """
        Yield scenes that have a stash ID for `endpoint`, oldest update first.

        Args:
            endpoint: Stash-box endpoint URL
            per_page: Scenes fetched per request
            limit: Stop after yielding this many scenes (None for all)
        """
        scene_filter = {"stash_id_endpoint": {"endpoint": endpoint, "modifier": "NOT_NULL", "stash_id": ""}}
        yielded = 0
        page = 1
        while limit is None or yielded < limit:
            data = self.call(FIND_SCENES_QUERY, {
                "filter": {"page": page, "per_page": per_page, "sort": "updated_at", "direction": "ASC"},
                "scene_filter": scene_filter,
            })
            result = data.get("findScenes") or {}
            batch = result.get("scenes") or []
            for scene in batch:
                if limit is not None and yielded >= limit:
                    return
                yielded += 1
                yield scene
            if not batch or page * per_page >= result.get("count", 0):
                return
            page += 1

    def add_scene_tags(self, scene_id, tag_ids):
        """Add tags to a scene, keeping the tags it already has."""
        self.call(BULK_SCENE_UPDATE_MUTATION, {
            "input": {"ids": [scene_id], "tag_ids": {"ids": list(tag_ids), "mode": "ADD"}},
        })
