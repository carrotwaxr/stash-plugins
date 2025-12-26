# Missing Scenes General Search Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a general StashDB browse page to the Missing Scenes plugin with favorite filters and excluded tags.

**Architecture:** Extends existing Missing Scenes plugin with a new `browse_stashdb` backend operation and a full-page browse UI. Reuses existing pagination, caching, and scene card components. Server-side filtering via StashDB's INCLUDES/EXCLUDES modifiers.

**Tech Stack:** Python 3 backend (no pip deps), vanilla JavaScript frontend, Stash plugin YAML config.

---

## Task 1: Add New Plugin Settings

**Files:**
- Modify: `plugins/missingScenes/missingScenes.yml:57-74`

**Step 1: Add excludedTags and favoriteLimit settings**

Add these settings after line 74 (after `stashbox_max_pages_studio`):

```yaml
  # Content filtering
  excludedTags:
    displayName: Excluded Tags
    description: Comma-separated list of StashDB Tag UUIDs to exclude from all Missing Scenes results. Find tag UUIDs on StashDB tag pages (e.g., https://stashdb.org/tags/uuid).
    type: STRING
  favoriteLimit:
    displayName: Favorite Limit
    description: Maximum number of favorites to use per filter type (default 100). Favorites are sorted by engagement (performers by last activity, studios/tags by scene count).
    type: NUMBER
```

**Step 2: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('plugins/missingScenes/missingScenes.yml'))"`

Expected: No output (success) or syntax error details.

**Step 3: Commit**

```bash
git add plugins/missingScenes/missingScenes.yml
git commit -m "feat(missingScenes): add excludedTags and favoriteLimit settings"
```

---

## Task 2: Add Sorted Favorite Fetching with Limits

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:346-461`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write the failing test for sorted favorites**

Add to `test_missing_scenes.py` after `TestGetFavoriteStashIds` class:

```python
class TestGetFavoriteStashIdsWithLimit(unittest.TestCase):
    """Test the get_favorite_stash_ids_limited function."""

    @patch.object(missing_scenes, 'stash_graphql')
    def test_performers_sorted_by_last_o_at(self, mock_graphql):
        """Test that performers are sorted by last_o_at DESC."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 3,
                "performers": [
                    {"id": "1", "name": "Recent", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "perf-recent"}
                    ]},
                    {"id": "2", "name": "Old", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "perf-old"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("performer", endpoint, limit=100)

        self.assertIn("perf-recent", result)
        # Verify query used correct sort
        call_args = mock_graphql.call_args
        variables = call_args[0][1]
        self.assertEqual(variables["filter"]["sort"], "last_o_at")
        self.assertEqual(variables["filter"]["direction"], "DESC")

    @patch.object(missing_scenes, 'stash_graphql')
    def test_studios_sorted_by_scenes_count(self, mock_graphql):
        """Test that studios are sorted by scenes_count DESC."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findStudios": {
                "count": 1,
                "studios": [
                    {"id": "1", "name": "Studio", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "studio-1"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("studio", endpoint, limit=100)

        call_args = mock_graphql.call_args
        variables = call_args[0][1]
        self.assertEqual(variables["filter"]["sort"], "scenes_count")

    @patch.object(missing_scenes, 'stash_graphql')
    def test_respects_limit(self, mock_graphql):
        """Test that limit is respected."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 200,
                "performers": [
                    {"id": str(i), "name": f"Performer {i}", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": f"perf-{i}"}
                    ]} for i in range(100)
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("performer", endpoint, limit=50)

        # Should only return first 50, not all 100 from page
        self.assertEqual(len(result), 50)
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestGetFavoriteStashIdsWithLimit -v`

Expected: FAIL with "AttributeError: module 'missing_scenes' has no attribute 'get_favorite_stash_ids_limited'"

**Step 3: Implement get_favorite_stash_ids_limited**

Add after `get_favorite_stash_ids` function (around line 462):

```python
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
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestGetFavoriteStashIdsWithLimit -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add get_favorite_stash_ids_limited with sorting"
```

---

## Task 3: Add query_scenes_browse to stashbox_api

**Files:**
- Modify: `plugins/missingScenes/stashbox_api.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write the failing test**

Add to `test_missing_scenes.py`:

```python
class TestQueryScenesBrowse(unittest.TestCase):
    """Test the query_scenes_browse function for general browsing."""

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_no_filters(self, mock_request):
        """Test browsing with no filters returns all scenes."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 1000,
                "scenes": [{"id": f"scene-{i}"} for i in range(100)]
            }
        }

        result = stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            page=1,
            per_page=100
        )

        self.assertEqual(result["count"], 1000)
        self.assertEqual(len(result["scenes"]), 100)

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_with_excluded_tags(self, mock_request):
        """Test that excluded tags are passed to query."""
        mock_request.return_value = {
            "queryScenes": {"count": 500, "scenes": []}
        }

        stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            excluded_tag_ids=["tag-1", "tag-2"]
        )

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("tags", variables["input"])
        self.assertEqual(variables["input"]["tags"]["modifier"], "EXCLUDES")
        self.assertEqual(variables["input"]["tags"]["value"], ["tag-1", "tag-2"])

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_with_performer_filter(self, mock_request):
        """Test filtering by performer IDs."""
        mock_request.return_value = {
            "queryScenes": {"count": 50, "scenes": []}
        }

        stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            performer_ids=["perf-1", "perf-2"]
        )

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("performers", variables["input"])
        self.assertEqual(variables["input"]["performers"]["modifier"], "INCLUDES")
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestQueryScenesBrowse -v`

Expected: FAIL with "AttributeError: module 'stashbox_api' has no attribute 'query_scenes_browse'"

**Step 3: Implement query_scenes_browse**

Add to end of `stashbox_api.py`:

```python
def query_scenes_browse(url, api_key, page=1, per_page=100, sort="DATE", direction="DESC",
                        performer_ids=None, studio_ids=None, tag_ids=None,
                        excluded_tag_ids=None, plugin_settings=None):
    """
    Browse all scenes on StashDB with optional filters.

    Unlike entity-specific queries, this allows querying without a specific
    performer/studio/tag context.

    Args:
        url: StashDB GraphQL endpoint URL
        api_key: API key for authentication
        page: Page number (1-indexed)
        per_page: Results per page
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"
        direction: Sort direction - "ASC" or "DESC"
        performer_ids: List of performer StashDB IDs to filter by (INCLUDES)
        studio_ids: List of studio StashDB IDs to filter by (INCLUDES)
        tag_ids: List of tag StashDB IDs to filter by (INCLUDES)
        excluded_tag_ids: List of tag StashDB IDs to exclude (EXCLUDES)
        plugin_settings: Plugin configuration

    Returns:
        dict with scenes, count, page, has_more
    """
    valid_sorts = {"DATE", "TITLE", "CREATED_AT", "UPDATED_AT", "TRENDING"}
    if sort not in valid_sorts:
        log.LogWarning(f"Invalid sort field '{sort}', using DATE")
        sort = "DATE"

    if direction not in {"ASC", "DESC"}:
        log.LogWarning(f"Invalid direction '{direction}', using DESC")
        direction = "DESC"

    # Build filter input
    filter_input = {
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "direction": direction
    }

    # Add performer filter
    if performer_ids:
        filter_input["performers"] = {
            "value": list(performer_ids),
            "modifier": "INCLUDES"
        }

    # Add studio filter
    if studio_ids:
        filter_input["studios"] = {
            "value": list(studio_ids),
            "modifier": "INCLUDES"
        }

    # Add tag filters (INCLUDES for positive, EXCLUDES for negative)
    # Note: StashDB doesn't support multiple tag filters in one query,
    # so we prioritize excludes if both are provided
    if excluded_tag_ids:
        filter_input["tags"] = {
            "value": list(excluded_tag_ids),
            "modifier": "EXCLUDES"
        }
    elif tag_ids:
        filter_input["tags"] = {
            "value": list(tag_ids),
            "modifier": "INCLUDES"
        }

    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    try:
        data = graphql_request_with_retry(
            url, query, {"input": filter_input}, api_key,
            plugin_settings=plugin_settings,
            operation_name=f"browse scenes page {page}"
        )

        if not data:
            return None

        query_data = data.get("queryScenes", {})
        scenes = query_data.get("scenes", [])
        count = query_data.get("count", 0)

        return {
            "scenes": scenes,
            "count": count,
            "page": page,
            "has_more": page * per_page < count
        }

    except StashBoxAPIError as e:
        log.LogError(f"Error browsing scenes page {page}: {e}")
        return None
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestQueryScenesBrowse -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/stashbox_api.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add query_scenes_browse for general browsing"
```

---

## Task 4: Add browse_stashdb Backend Operation

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write the failing test**

Add to `test_missing_scenes.py`:

```python
class TestBrowseStashdb(unittest.TestCase):
    """Test the browse_stashdb operation."""

    def setUp(self):
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch.object(missing_scenes, 'get_stashbox_config')
    @patch.object(missing_scenes, 'get_or_build_cache')
    @patch.object(stashbox_api, 'query_scenes_browse')
    @patch.object(missing_scenes, 'whisparr_get_status_map')
    def test_browse_basic(self, mock_whisparr, mock_browse, mock_cache, mock_config):
        """Test basic browse without filters."""
        mock_config.return_value = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key", "name": "StashDB"}
        ]
        mock_cache.return_value = {"owned-1", "owned-2"}
        mock_browse.return_value = {
            "scenes": [
                {"id": "scene-1"},
                {"id": "owned-1"},  # Should be filtered out
                {"id": "scene-2"},
            ],
            "count": 1000,
            "page": 1,
            "has_more": True
        }
        mock_whisparr.return_value = {}

        result = missing_scenes.browse_stashdb(
            plugin_settings={},
            page_size=50
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["stashdb_name"], "StashDB")
        # owned-1 should be filtered out
        scene_ids = [s["stash_id"] for s in result["missing_scenes"]]
        self.assertIn("scene-1", scene_ids)
        self.assertNotIn("owned-1", scene_ids)

    @patch.object(missing_scenes, 'get_stashbox_config')
    def test_browse_no_stashbox_config(self, mock_config):
        """Test error when no stash-box configured."""
        mock_config.return_value = []

        result = missing_scenes.browse_stashdb(plugin_settings={})

        self.assertIn("error", result)
        self.assertIn("No stash-box", result["error"])
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestBrowseStashdb -v`

Expected: FAIL with "AttributeError: module 'missing_scenes' has no attribute 'browse_stashdb'"

**Step 3: Implement browse_stashdb function**

Add after `find_missing_scenes_paginated` function (around line 1564):

```python
def browse_stashdb(plugin_settings, page_size=50, cursor=None, sort="DATE", direction="DESC",
                   filter_favorite_performers=False, filter_favorite_studios=False,
                   filter_favorite_tags=False):
    """
    Browse all StashDB scenes without entity context.

    Args:
        plugin_settings: Plugin configuration
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

    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

    # Use configured or first endpoint
    target_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()
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
    truncated_filters = []

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

    while len(collected) < page_size and pages_fetched < MAX_PAGES_PER_REQUEST:
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

        # Filter out owned scenes
        for i, scene in enumerate(scenes):
            if i < current_offset:
                continue

            scene_id = scene.get("id")
            if scene_id and scene_id not in local_ids:
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

    if whisparr_url and whisparr_api_key:
        whisparr_configured = True
        try:
            whisparr_status_map = whisparr_get_status_map(whisparr_url, whisparr_api_key)
        except Exception as e:
            log.LogWarning(f"Could not fetch Whisparr status: {e}")

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
        "filters_active": filters_active,
        "excluded_tags_applied": len(excluded_tag_ids) > 0
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
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py::TestBrowseStashdb -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add browse_stashdb operation"
```

---

## Task 5: Wire browse_stashdb to Plugin Entry Point

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:2059-2097`

**Step 1: Add browse_stashdb operation handler**

In the `main()` function, add a new elif block after the `find_missing` handling (around line 2097):

```python
        elif operation == "browse_stashdb":
            page_size = args.get("page_size", PAGE_SIZE_DEFAULT)
            cursor = args.get("cursor")
            sort = args.get("sort", "DATE")
            direction = args.get("direction", "DESC")
            filter_favorite_performers = args.get("filter_favorite_performers", False)
            filter_favorite_studios = args.get("filter_favorite_studios", False)
            filter_favorite_tags = args.get("filter_favorite_tags", False)

            output = browse_stashdb(
                plugin_settings=plugin_settings,
                page_size=page_size,
                cursor=cursor,
                sort=sort,
                direction=direction,
                filter_favorite_performers=filter_favorite_performers,
                filter_favorite_studios=filter_favorite_studios,
                filter_favorite_tags=filter_favorite_tags
            )
```

**Step 2: Run existing tests to ensure no regressions**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py -v`

Expected: All tests PASS

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): wire browse_stashdb to plugin entry point"
```

---

## Task 6: Create Browse Page JavaScript Structure

**Files:**
- Create: `plugins/missingScenes/missing-scenes-browse.js`
- Modify: `plugins/missingScenes/missingScenes.yml`

**Step 1: Add browse JS to plugin manifest**

In `missingScenes.yml`, update the `ui.javascript` section:

```yaml
ui:
  javascript:
    - missing-scenes.js
    - missing-scenes-browse.js
  css:
    - missing-scenes.css
```

**Step 2: Create browse page JavaScript skeleton**

Create `missing-scenes-browse.js`:

```javascript
(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";
  const BROWSE_PATH = "/plugin/missingScenes/browse";

  // State
  let browsePageRoot = null;
  let missingScenes = [];
  let isLoading = false;
  let currentCursor = null;
  let hasMore = true;
  let sortField = "DATE";
  let sortDirection = "DESC";
  let filterFavoritePerformers = false;
  let filterFavoriteStudios = false;
  let filterFavoriteTags = false;
  let whisparrConfigured = false;
  let stashdbUrl = "";

  /**
   * Get the GraphQL endpoint URL
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request
   */
  async function graphqlRequest(query, variables = {}) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });

    if (!response.ok) {
      throw new Error(`GraphQL request failed: ${response.status}`);
    }

    const result = await response.json();
    if (result.errors && result.errors.length > 0) {
      throw new Error(result.errors[0].message);
    }

    return result.data;
  }

  /**
   * Run a plugin operation
   */
  async function runPluginOperation(args) {
    const query = `
      mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
        runPluginOperation(plugin_id: $plugin_id, args: $args)
      }
    `;

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: args,
    });

    const rawOutput = data?.runPluginOperation;
    if (!rawOutput) throw new Error("No response from plugin");

    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      throw new Error("Invalid response from plugin");
    }

    if (output.error) throw new Error(output.error);
    return output;
  }

  /**
   * Browse StashDB for missing scenes
   */
  async function browseStashdb(options = {}) {
    return runPluginOperation({
      operation: "browse_stashdb",
      page_size: options.pageSize || 50,
      cursor: options.cursor || null,
      sort: options.sort || "DATE",
      direction: options.direction || "DESC",
      filter_favorite_performers: options.filterFavoritePerformers || false,
      filter_favorite_studios: options.filterFavoriteStudios || false,
      filter_favorite_tags: options.filterFavoriteTags || false,
    });
  }

  /**
   * Check if we're on the browse page
   */
  function isOnBrowsePage() {
    return window.location.pathname === BROWSE_PATH;
  }

  /**
   * Initialize browse page if on correct route
   */
  function initBrowsePage() {
    if (!isOnBrowsePage()) return;
    if (browsePageRoot) return; // Already initialized

    // Create the browse page
    createBrowsePage();

    // Load initial results
    performSearch(true);
  }

  /**
   * Create the browse page structure
   */
  function createBrowsePage() {
    // Clear existing content
    const mainContainer = document.querySelector(".main > div") ||
                          document.querySelector("#root > div > div");

    if (!mainContainer) {
      console.error("[MissingScenes] Could not find main container");
      return;
    }

    // Create browse page container
    const page = document.createElement("div");
    page.className = "ms-browse-page";
    page.innerHTML = `
      <div class="ms-browse-header">
        <h1>Missing Scenes</h1>
        <p>Browse StashDB scenes you don't have locally</p>
      </div>

      <div class="ms-browse-controls">
        <div class="ms-filter-controls">
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-performers">
            <span>Favorite Performers</span>
          </label>
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-studios">
            <span>Favorite Studios</span>
          </label>
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-tags">
            <span>Favorite Tags</span>
          </label>
        </div>

        <div class="ms-sort-controls">
          <label>Sort by:</label>
          <select id="ms-sort-field" class="ms-sort-select">
            <option value="DATE">Release Date</option>
            <option value="TITLE">Title</option>
            <option value="CREATED_AT">Added to StashDB</option>
            <option value="UPDATED_AT">Last Updated</option>
            <option value="TRENDING">Trending</option>
          </select>
          <select id="ms-sort-direction" class="ms-sort-select">
            <option value="DESC">Newest First</option>
            <option value="ASC">Oldest First</option>
          </select>
        </div>
      </div>

      <div class="ms-browse-stats" id="ms-browse-stats"></div>

      <div class="ms-browse-results" id="ms-browse-results">
        <div class="ms-placeholder">Loading...</div>
      </div>

      <div class="ms-browse-footer">
        <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: none;">
          Load More
        </button>
      </div>
    `;

    // Replace content
    mainContainer.innerHTML = "";
    mainContainer.appendChild(page);
    browsePageRoot = page;

    // Add event listeners
    setupEventListeners();
  }

  /**
   * Setup event listeners for controls
   */
  function setupEventListeners() {
    // Filter checkboxes
    document.getElementById("ms-filter-performers")?.addEventListener("change", (e) => {
      filterFavoritePerformers = e.target.checked;
      performSearch(true);
    });

    document.getElementById("ms-filter-studios")?.addEventListener("change", (e) => {
      filterFavoriteStudios = e.target.checked;
      performSearch(true);
    });

    document.getElementById("ms-filter-tags")?.addEventListener("change", (e) => {
      filterFavoriteTags = e.target.checked;
      performSearch(true);
    });

    // Sort controls
    document.getElementById("ms-sort-field")?.addEventListener("change", (e) => {
      sortField = e.target.value;
      performSearch(true);
    });

    document.getElementById("ms-sort-direction")?.addEventListener("change", (e) => {
      sortDirection = e.target.value;
      performSearch(true);
    });

    // Load more button
    document.getElementById("ms-load-more-btn")?.addEventListener("click", () => {
      performSearch(false);
    });
  }

  /**
   * Perform search/browse
   */
  async function performSearch(reset = true) {
    if (isLoading) return;

    if (reset) {
      currentCursor = null;
      missingScenes = [];
      hasMore = true;
    }

    isLoading = true;
    updateLoadingState(true);

    try {
      const result = await browseStashdb({
        pageSize: 50,
        cursor: currentCursor,
        sort: sortField,
        direction: sortDirection,
        filterFavoritePerformers,
        filterFavoriteStudios,
        filterFavoriteTags,
      });

      missingScenes = reset ? result.missing_scenes : [...missingScenes, ...result.missing_scenes];
      currentCursor = result.cursor;
      hasMore = result.has_more;
      whisparrConfigured = result.whisparr_configured;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";

      updateStats(result);
      renderResults();
    } catch (error) {
      console.error("[MissingScenes] Browse failed:", error);
      showError(error.message);
    } finally {
      isLoading = false;
      updateLoadingState(false);
    }
  }

  /**
   * Update stats display
   */
  function updateStats(result) {
    const statsEl = document.getElementById("ms-browse-stats");
    if (!statsEl) return;

    const loaded = missingScenes.length;
    const total = result.total_on_stashdb;
    const filtersActive = result.filters_active;
    const excludedApplied = result.excluded_tags_applied;

    let text = `Showing ${loaded}`;
    if (!result.is_complete) {
      text += ` of ~${total}`;
    }
    text += " missing scenes";

    if (filtersActive) text += " (filtered)";
    if (excludedApplied) text += " (content filtered)";

    statsEl.textContent = text;
  }

  /**
   * Render scene results
   */
  function renderResults() {
    const container = document.getElementById("ms-browse-results");
    if (!container) return;

    if (missingScenes.length === 0) {
      container.innerHTML = `
        <div class="ms-placeholder ms-success">
          <div class="ms-success-icon">&#10003;</div>
          <div>No missing scenes found!</div>
        </div>
      `;
      updateLoadMoreButton();
      return;
    }

    // Create grid - reuse scene card structure from main plugin
    const grid = document.createElement("div");
    grid.className = "ms-results-grid";

    for (const scene of missingScenes) {
      const card = createSceneCard(scene);
      grid.appendChild(card);
    }

    container.innerHTML = "";
    container.appendChild(grid);
    updateLoadMoreButton();
  }

  /**
   * Create a scene card (matches existing modal style)
   */
  function createSceneCard(scene) {
    const card = document.createElement("div");
    card.className = "ms-scene-card";
    card.dataset.stashId = scene.stash_id;

    // Build card HTML
    const thumbUrl = scene.thumbnail || "";
    const title = scene.title || "Unknown";
    const studio = scene.studio?.name || "";
    const date = scene.release_date ? formatDate(scene.release_date) : "";
    const performers = (scene.performers || []).slice(0, 3).map(p => p.name).join(", ");

    card.innerHTML = `
      <div class="ms-scene-thumb ${thumbUrl ? '' : 'ms-no-image'}">
        ${thumbUrl ? `<img src="${thumbUrl}" alt="${title}" loading="lazy">` : '<span class="ms-no-image-icon">&#128247;</span>'}
      </div>
      <div class="ms-scene-info">
        <div class="ms-scene-title" title="${title}">${title}</div>
        <div class="ms-scene-meta">${[studio, date].filter(Boolean).join(" - ")}</div>
        <div class="ms-scene-performers">${performers}</div>
      </div>
      <div class="ms-scene-actions">
        <a class="ms-btn ms-btn-small" href="${stashdbUrl}/scenes/${scene.stash_id}" target="_blank">View</a>
      </div>
    `;

    card.onclick = () => {
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank");
    };

    return card;
  }

  /**
   * Format date for display
   */
  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      const [year, month, day] = dateStr.split("-").map(Number);
      const date = new Date(year, month - 1, day);
      return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch {
      return dateStr;
    }
  }

  /**
   * Update load more button
   */
  function updateLoadMoreButton() {
    const btn = document.getElementById("ms-load-more-btn");
    if (!btn) return;

    if (hasMore && missingScenes.length > 0) {
      btn.style.display = "inline-block";
      btn.disabled = isLoading;
    } else {
      btn.style.display = "none";
    }
  }

  /**
   * Update loading state
   */
  function updateLoadingState(loading) {
    const btn = document.getElementById("ms-load-more-btn");
    if (btn) btn.disabled = loading;
  }

  /**
   * Show error message
   */
  function showError(message) {
    const container = document.getElementById("ms-browse-results");
    if (container) {
      container.innerHTML = `
        <div class="ms-placeholder ms-error">
          <div class="ms-error-icon">!</div>
          <div>${message}</div>
        </div>
      `;
    }
  }

  /**
   * Add "Missing Scenes" button to Scenes page
   */
  function addScenesPageButton() {
    if (!window.location.pathname.startsWith("/scenes")) return;
    if (document.querySelector(".ms-browse-button")) return;

    // Find the header area
    const header = document.querySelector(".scenes-header") ||
                   document.querySelector('[class*="ListHeader"]') ||
                   document.querySelector(".content-header");

    if (!header) return;

    const btn = document.createElement("button");
    btn.className = "ms-browse-button btn btn-secondary";
    btn.type = "button";
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      Missing Scenes
    `;
    btn.onclick = () => {
      window.location.href = BROWSE_PATH;
    };

    header.appendChild(btn);
  }

  /**
   * Watch for navigation changes
   */
  function watchNavigation() {
    // Check on page load
    initBrowsePage();
    addScenesPageButton();

    // Watch for SPA navigation
    const observer = new MutationObserver(() => {
      setTimeout(() => {
        initBrowsePage();
        addScenesPageButton();
      }, 100);
    });

    observer.observe(document.body, { childList: true, subtree: true });

    window.addEventListener("popstate", () => {
      setTimeout(() => {
        initBrowsePage();
        addScenesPageButton();
      }, 100);
    });
  }

  /**
   * Initialize
   */
  function init() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", watchNavigation);
    } else {
      watchNavigation();
    }
  }

  init();
})();
```

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing-scenes-browse.js plugins/missingScenes/missingScenes.yml
git commit -m "feat(missingScenes): add browse page JavaScript"
```

---

## Task 7: Add Browse Page CSS Styles

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.css`

**Step 1: Add browse page styles**

Append to `missing-scenes.css`:

```css
/* ============================================
   Browse Page Styles
   ============================================ */

.ms-browse-page {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.ms-browse-header {
  margin-bottom: 20px;
}

.ms-browse-header h1 {
  margin: 0 0 8px 0;
  font-size: 24px;
}

.ms-browse-header p {
  margin: 0;
  color: #999;
}

.ms-browse-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-bottom: 20px;
  padding: 15px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
}

.ms-browse-stats {
  margin-bottom: 15px;
  padding: 10px 15px;
  background: rgba(0, 123, 255, 0.1);
  border-radius: 6px;
  font-size: 14px;
}

.ms-browse-results {
  min-height: 200px;
}

.ms-browse-footer {
  text-align: center;
  margin-top: 20px;
  padding: 20px;
}

/* Browse button on Scenes page */
.ms-browse-button {
  display: inline-flex;
  align-items: center;
  margin-left: 10px;
}
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.css
git commit -m "feat(missingScenes): add browse page CSS styles"
```

---

## Task 8: Run Full Test Suite and Verify

**Files:**
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Run all tests**

Run: `cd plugins/missingScenes && python -m pytest test_missing_scenes.py -v`

Expected: All tests PASS

**Step 2: Manual verification checklist**

- [ ] Plugin loads without errors in Stash
- [ ] New settings appear in Plugin Settings
- [ ] "Missing Scenes" button appears on Scenes page
- [ ] Browse page loads at `/plugin/missingScenes/browse`
- [ ] Initial load shows scenes (no filters)
- [ ] Sort controls work
- [ ] Filter checkboxes trigger new searches
- [ ] Load More pagination works
- [ ] Scene cards link to StashDB

**Step 3: Final commit with version bump**

Update version in `missingScenes.yml` from `1.2.0` to `1.3.0`:

```yaml
version: 1.3.0
```

```bash
git add plugins/missingScenes/missingScenes.yml
git commit -m "chore(missingScenes): bump version to 1.3.0"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add plugin settings | missingScenes.yml |
| 2 | Add sorted favorite fetching | missing_scenes.py, test_missing_scenes.py |
| 3 | Add query_scenes_browse | stashbox_api.py, test_missing_scenes.py |
| 4 | Add browse_stashdb operation | missing_scenes.py, test_missing_scenes.py |
| 5 | Wire to plugin entry point | missing_scenes.py |
| 6 | Create browse page JS | missing-scenes-browse.js, missingScenes.yml |
| 7 | Add browse page CSS | missing-scenes.css |
| 8 | Test and verify | test_missing_scenes.py |

Total: 8 tasks, ~12 commits
