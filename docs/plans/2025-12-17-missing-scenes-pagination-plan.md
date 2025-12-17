# Missing Scenes Pagination Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace "fetch all scenes upfront" with paginated "Fetch Until Full" approach to eliminate 504 timeouts on large queries.

**Architecture:** Backend fetches StashDB pages incrementally until it has enough missing scenes to fill the requested page size. A cursor tracks position for "Load More" requests. Local stash_ids are cached in-memory for fast filtering.

**Tech Stack:** Python 3 (backend), JavaScript (frontend), GraphQL (Stash/StashDB APIs)

---

## Task 1: Add Cursor Encoding/Decoding Utilities

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py` (add at line ~27, after imports)
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write the failing tests for cursor encoding/decoding**

Add to `test_missing_scenes.py`:

```python
class TestCursorEncoding(unittest.TestCase):
    """Test cursor encoding and decoding."""

    def test_encode_cursor(self):
        """Test that cursor state is encoded to base64 string."""
        state = {
            "stashdb_page": 4,
            "offset": 12,
            "sort": "DATE",
            "direction": "DESC",
            "entity_type": "tag",
            "entity_stash_id": "abc-123",
            "endpoint": "https://stashdb.org/graphql"
        }

        cursor = missing_scenes.encode_cursor(state)

        self.assertIsInstance(cursor, str)
        self.assertTrue(len(cursor) > 0)

    def test_decode_cursor(self):
        """Test that cursor string is decoded back to state dict."""
        state = {
            "stashdb_page": 4,
            "offset": 12,
            "sort": "DATE",
            "direction": "DESC",
            "entity_type": "tag",
            "entity_stash_id": "abc-123",
            "endpoint": "https://stashdb.org/graphql"
        }

        cursor = missing_scenes.encode_cursor(state)
        decoded = missing_scenes.decode_cursor(cursor)

        self.assertEqual(decoded["stashdb_page"], 4)
        self.assertEqual(decoded["offset"], 12)
        self.assertEqual(decoded["sort"], "DATE")
        self.assertEqual(decoded["direction"], "DESC")
        self.assertEqual(decoded["entity_stash_id"], "abc-123")

    def test_decode_invalid_cursor_returns_none(self):
        """Test that invalid cursor returns None."""
        result = missing_scenes.decode_cursor("invalid-base64!")
        self.assertIsNone(result)

    def test_decode_none_cursor_returns_none(self):
        """Test that None cursor returns None."""
        result = missing_scenes.decode_cursor(None)
        self.assertIsNone(result)

    def test_roundtrip_preserves_all_fields(self):
        """Test that encode/decode roundtrip preserves all fields."""
        state = {
            "stashdb_page": 10,
            "offset": 50,
            "sort": "TITLE",
            "direction": "ASC",
            "entity_type": "performer",
            "entity_stash_id": "uuid-here",
            "endpoint": "https://example.com/graphql",
            "total_on_stashdb": 5000
        }

        cursor = missing_scenes.encode_cursor(state)
        decoded = missing_scenes.decode_cursor(cursor)

        self.assertEqual(decoded, state)
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'missing_scenes' has no attribute 'encode_cursor'`

**Step 3: Implement cursor encoding/decoding**

Add to `missing_scenes.py` after the imports (around line 27):

```python
import base64

# ============================================================================
# Cursor Encoding/Decoding for Pagination
# ============================================================================

def encode_cursor(state):
    """Encode pagination state to an opaque cursor string."""
    json_str = json.dumps(state, separators=(',', ':'))
    return base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')


def decode_cursor(cursor):
    """Decode cursor string back to pagination state dict.

    Returns None if cursor is invalid or None.
    """
    if not cursor:
        return None
    try:
        json_str = base64.urlsafe_b64decode(cursor.encode('utf-8')).decode('utf-8')
        return json.loads(json_str)
    except Exception:
        return None
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestCursorEncoding` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add cursor encoding/decoding for pagination"
```

---

## Task 2: Add Local Stash_ID Cache

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py` (add after cursor functions)
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write failing tests for cache functions**

Add to `test_missing_scenes.py`:

```python
class TestLocalStashIdCache(unittest.TestCase):
    """Test local stash_id caching."""

    def setUp(self):
        """Clear cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    def test_cache_starts_empty(self):
        """Test that cache is initially empty."""
        self.assertEqual(len(missing_scenes._local_stash_id_cache), 0)

    def test_is_cache_valid_empty(self):
        """Test that empty cache is not valid."""
        result = missing_scenes.is_cache_valid("https://stashdb.org/graphql")
        self.assertFalse(result)

    def test_is_cache_valid_after_set(self):
        """Test that cache is valid after being set."""
        endpoint = "https://stashdb.org/graphql"
        missing_scenes._local_stash_id_cache[endpoint] = {"id1", "id2"}
        missing_scenes._cache_metadata[endpoint] = {
            "count": 2,
            "built_at": "2025-12-17T10:00:00"
        }

        result = missing_scenes.is_cache_valid(endpoint)
        self.assertTrue(result)

    def test_get_cached_stash_ids(self):
        """Test retrieving cached stash_ids."""
        endpoint = "https://stashdb.org/graphql"
        expected_ids = {"id1", "id2", "id3"}
        missing_scenes._local_stash_id_cache[endpoint] = expected_ids
        missing_scenes._cache_metadata[endpoint] = {"count": 3, "built_at": "2025-12-17T10:00:00"}

        result = missing_scenes.get_cached_stash_ids(endpoint)

        self.assertEqual(result, expected_ids)

    def test_get_cached_stash_ids_returns_empty_set_if_not_cached(self):
        """Test that missing cache returns empty set."""
        result = missing_scenes.get_cached_stash_ids("https://notcached.org/graphql")
        self.assertEqual(result, set())

    def test_get_cache_count(self):
        """Test getting cached count."""
        endpoint = "https://stashdb.org/graphql"
        missing_scenes._local_stash_id_cache[endpoint] = {"id1", "id2"}
        missing_scenes._cache_metadata[endpoint] = {"count": 2, "built_at": "2025-12-17T10:00:00"}

        result = missing_scenes.get_cache_count(endpoint)

        self.assertEqual(result, 2)
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'missing_scenes' has no attribute '_local_stash_id_cache'`

**Step 3: Implement cache data structures and helper functions**

Add to `missing_scenes.py` after cursor functions:

```python
from datetime import datetime

# ============================================================================
# Local Stash_ID Cache
# ============================================================================

# In-memory cache: endpoint -> set of stash_ids
_local_stash_id_cache = {}

# Cache metadata: endpoint -> {count, built_at}
_cache_metadata = {}


def is_cache_valid(endpoint):
    """Check if cache exists for the given endpoint."""
    return endpoint in _local_stash_id_cache and endpoint in _cache_metadata


def get_cached_stash_ids(endpoint):
    """Get cached stash_ids for endpoint, or empty set if not cached."""
    return _local_stash_id_cache.get(endpoint, set())


def get_cache_count(endpoint):
    """Get the count of cached stash_ids for endpoint."""
    metadata = _cache_metadata.get(endpoint, {})
    return metadata.get("count", 0)


def set_cache(endpoint, stash_ids):
    """Store stash_ids in cache for endpoint."""
    _local_stash_id_cache[endpoint] = stash_ids
    _cache_metadata[endpoint] = {
        "count": len(stash_ids),
        "built_at": datetime.now().isoformat()
    }


def clear_cache(endpoint=None):
    """Clear cache for specific endpoint or all endpoints."""
    if endpoint:
        _local_stash_id_cache.pop(endpoint, None)
        _cache_metadata.pop(endpoint, None)
    else:
        _local_stash_id_cache.clear()
        _cache_metadata.clear()
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestLocalStashIdCache` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add local stash_id cache data structures"
```

---

## Task 3: Implement Cache Building Function

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write failing test for build_local_stash_id_cache**

Add to `test_missing_scenes.py`:

```python
class TestBuildCache(unittest.TestCase):
    """Test cache building function."""

    def setUp(self):
        """Clear cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch('missing_scenes.stash_graphql')
    def test_build_cache_single_page(self, mock_graphql):
        """Test building cache with single page of results."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 2,
                "scenes": [
                    {"stash_ids": [{"endpoint": endpoint, "stash_id": "id1"}]},
                    {"stash_ids": [{"endpoint": endpoint, "stash_id": "id2"}]},
                ]
            }
        }

        result = missing_scenes.build_local_stash_id_cache(endpoint)

        self.assertEqual(result, {"id1", "id2"})
        self.assertTrue(missing_scenes.is_cache_valid(endpoint))
        self.assertEqual(missing_scenes.get_cache_count(endpoint), 2)

    @patch('missing_scenes.stash_graphql')
    def test_build_cache_filters_by_endpoint(self, mock_graphql):
        """Test that cache only includes stash_ids for target endpoint."""
        target_endpoint = "https://stashdb.org/graphql"
        other_endpoint = "https://other.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 2,
                "scenes": [
                    {"stash_ids": [
                        {"endpoint": target_endpoint, "stash_id": "id1"},
                        {"endpoint": other_endpoint, "stash_id": "other-id"}
                    ]},
                    {"stash_ids": [{"endpoint": other_endpoint, "stash_id": "id2"}]},
                ]
            }
        }

        result = missing_scenes.build_local_stash_id_cache(target_endpoint)

        self.assertEqual(result, {"id1"})
        self.assertNotIn("other-id", result)
        self.assertNotIn("id2", result)

    @patch('missing_scenes.stash_graphql')
    def test_build_cache_empty_results(self, mock_graphql):
        """Test building cache when no scenes have stash_ids."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 0,
                "scenes": []
            }
        }

        result = missing_scenes.build_local_stash_id_cache(endpoint)

        self.assertEqual(result, set())
        self.assertTrue(missing_scenes.is_cache_valid(endpoint))
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'missing_scenes' has no attribute 'build_local_stash_id_cache'`

**Step 3: Implement build_local_stash_id_cache**

Add to `missing_scenes.py` after the cache helper functions:

```python
def build_local_stash_id_cache(endpoint):
    """Build cache of local scene stash_ids for the given endpoint.

    Queries local Stash for all scenes linked to this endpoint,
    extracts their stash_ids, and caches them for fast lookup.

    Args:
        endpoint: StashDB GraphQL endpoint URL

    Returns:
        Set of stash_ids for scenes linked to this endpoint
    """
    log.LogInfo(f"Building local stash_id cache for {endpoint}...")

    stash_ids = set()
    page = 1
    per_page = 100

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

        if not result or "findScenes" not in result:
            break

        scenes = result["findScenes"].get("scenes", [])
        total = result["findScenes"].get("count", 0)

        for scene in scenes:
            for sid in scene.get("stash_ids", []):
                if sid.get("endpoint") == endpoint:
                    stash_ids.add(sid.get("stash_id"))

        log.LogDebug(f"Cache build: page {page}, collected {len(stash_ids)} IDs (total scenes: {total})")

        if page * per_page >= total:
            break
        page += 1

    set_cache(endpoint, stash_ids)
    log.LogInfo(f"Cache built: {len(stash_ids)} local scenes linked to {endpoint}")

    return stash_ids
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestBuildCache` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): implement cache building function"
```

---

## Task 4: Add Paginated StashDB Query Function to stashbox_api.py

**Files:**
- Modify: `plugins/missingScenes/stashbox_api.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write failing test for single-page StashDB query**

Add to `test_missing_scenes.py`:

```python
# Import stashbox_api at the top
import stashbox_api

class TestPaginatedStashDBQuery(unittest.TestCase):
    """Test paginated StashDB query function."""

    @patch('stashbox_api.graphql_request_with_retry')
    def test_query_single_page(self, mock_request):
        """Test querying a single page from StashDB."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 50,
                "scenes": [{"id": f"scene{i}"} for i in range(50)]
            }
        }

        scenes, total, has_more = stashbox_api.query_stashdb_scenes_page(
            url="https://stashdb.org/graphql",
            api_key="test-key",
            entity_type="tag",
            entity_stash_id="tag-uuid",
            page=1,
            per_page=100,
            sort="DATE",
            direction="DESC"
        )

        self.assertEqual(len(scenes), 50)
        self.assertEqual(total, 50)
        self.assertFalse(has_more)

    @patch('stashbox_api.graphql_request_with_retry')
    def test_query_has_more_pages(self, mock_request):
        """Test that has_more is True when more pages exist."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 250,
                "scenes": [{"id": f"scene{i}"} for i in range(100)]
            }
        }

        scenes, total, has_more = stashbox_api.query_stashdb_scenes_page(
            url="https://stashdb.org/graphql",
            api_key="test-key",
            entity_type="performer",
            entity_stash_id="performer-uuid",
            page=1,
            per_page=100,
            sort="DATE",
            direction="DESC"
        )

        self.assertEqual(len(scenes), 100)
        self.assertEqual(total, 250)
        self.assertTrue(has_more)
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'stashbox_api' has no attribute 'query_stashdb_scenes_page'`

**Step 3: Implement query_stashdb_scenes_page**

Add to `stashbox_api.py` after the existing query functions (around line 550):

```python
def query_stashdb_scenes_page(url, api_key, entity_type, entity_stash_id,
                               page=1, per_page=100, sort="DATE", direction="DESC",
                               plugin_settings=None):
    """Query a single page of scenes from StashDB for an entity.

    Args:
        url: StashDB GraphQL endpoint URL
        api_key: StashDB API key
        entity_type: "performer", "studio", or "tag"
        entity_stash_id: StashDB UUID of the entity
        page: Page number (1-indexed)
        per_page: Results per page (max 100)
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT"
        direction: Sort direction - "ASC" or "DESC"
        plugin_settings: Optional plugin settings for retry config

    Returns:
        Tuple of (scenes_list, total_count, has_more_pages)
    """
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

    # Build filter based on entity type
    if entity_type == "performer":
        entity_filter = {
            "performers": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    elif entity_type == "studio":
        entity_filter = {
            "studios": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    elif entity_type == "tag":
        entity_filter = {
            "tags": {
                "value": [entity_stash_id],
                "modifier": "INCLUDES"
            }
        }
    else:
        raise ValueError(f"Unknown entity type: {entity_type}")

    variables = {
        "input": {
            **entity_filter,
            "page": page,
            "per_page": per_page,
            "sort": sort,
            "direction": direction
        }
    }

    try:
        data = graphql_request_with_retry(
            url, query, variables, api_key,
            plugin_settings=plugin_settings,
            operation_name=f"scenes page {page} for {entity_type}"
        )
    except StashBoxAPIError:
        return [], 0, False

    if not data or "queryScenes" not in data:
        return [], 0, False

    query_result = data["queryScenes"]
    scenes = query_result.get("scenes", [])
    total = query_result.get("count", 0)
    has_more = page * per_page < total

    return scenes, total, has_more
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestPaginatedStashDBQuery` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/stashbox_api.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add single-page StashDB query function"
```

---

## Task 5: Implement "Fetch Until Full" Core Logic

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write failing tests for fetch_until_full**

Add to `test_missing_scenes.py`:

```python
class TestFetchUntilFull(unittest.TestCase):
    """Test the fetch_until_full pagination logic."""

    def setUp(self):
        """Clear cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch('missing_scenes.stashbox_api.query_stashdb_scenes_page')
    def test_fetch_returns_requested_count(self, mock_query):
        """Test that fetch returns exactly page_size results when available."""
        # Setup: StashDB has 100 scenes, user owns 20
        all_scenes = [{"id": f"scene{i}"} for i in range(100)]
        owned_ids = {f"scene{i}" for i in range(20)}  # Own first 20

        mock_query.return_value = (all_scenes, 100, False)
        missing_scenes.set_cache("https://stashdb.org/graphql", owned_ids)

        result = missing_scenes.fetch_until_full(
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="key",
            entity_type="tag",
            entity_stash_id="tag-uuid",
            local_stash_ids=owned_ids,
            page_size=50,
            sort="DATE",
            direction="DESC",
            stashdb_page=1,
            offset=0
        )

        self.assertEqual(len(result["scenes"]), 50)

    @patch('missing_scenes.stashbox_api.query_stashdb_scenes_page')
    def test_fetch_skips_owned_scenes(self, mock_query):
        """Test that owned scenes are filtered out."""
        scenes = [{"id": "owned1"}, {"id": "missing1"}, {"id": "owned2"}, {"id": "missing2"}]
        owned_ids = {"owned1", "owned2"}

        mock_query.return_value = (scenes, 4, False)

        result = missing_scenes.fetch_until_full(
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="key",
            entity_type="tag",
            entity_stash_id="tag-uuid",
            local_stash_ids=owned_ids,
            page_size=10,
            sort="DATE",
            direction="DESC",
            stashdb_page=1,
            offset=0
        )

        scene_ids = [s["id"] for s in result["scenes"]]
        self.assertNotIn("owned1", scene_ids)
        self.assertNotIn("owned2", scene_ids)
        self.assertIn("missing1", scene_ids)
        self.assertIn("missing2", scene_ids)

    @patch('missing_scenes.stashbox_api.query_stashdb_scenes_page')
    def test_fetch_returns_cursor_for_more(self, mock_query):
        """Test that cursor is returned when more results available."""
        # 200 scenes total, none owned, request 50
        mock_query.return_value = ([{"id": f"s{i}"} for i in range(100)], 200, True)

        result = missing_scenes.fetch_until_full(
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="key",
            entity_type="tag",
            entity_stash_id="tag-uuid",
            local_stash_ids=set(),
            page_size=50,
            sort="DATE",
            direction="DESC",
            stashdb_page=1,
            offset=0
        )

        self.assertTrue(result["has_more"])
        self.assertIsNotNone(result["cursor"])
        self.assertFalse(result["is_complete"])

    @patch('missing_scenes.stashbox_api.query_stashdb_scenes_page')
    def test_fetch_is_complete_when_exhausted(self, mock_query):
        """Test that is_complete=True when all StashDB scenes checked."""
        mock_query.return_value = ([{"id": "s1"}, {"id": "s2"}], 2, False)

        result = missing_scenes.fetch_until_full(
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="key",
            entity_type="tag",
            entity_stash_id="tag-uuid",
            local_stash_ids=set(),
            page_size=50,
            sort="DATE",
            direction="DESC",
            stashdb_page=1,
            offset=0
        )

        self.assertEqual(len(result["scenes"]), 2)
        self.assertFalse(result["has_more"])
        self.assertTrue(result["is_complete"])
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'missing_scenes' has no attribute 'fetch_until_full'`

**Step 3: Implement fetch_until_full**

Add to `missing_scenes.py` after the cache functions:

```python
# Safety limits
MAX_PAGES_PER_REQUEST = 50  # Max StashDB pages to fetch in one request
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100


def fetch_until_full(stashdb_url, stashdb_api_key, entity_type, entity_stash_id,
                     local_stash_ids, page_size, sort, direction,
                     stashdb_page=1, offset=0, plugin_settings=None):
    """Fetch StashDB pages until we have page_size missing scenes.

    Args:
        stashdb_url: StashDB GraphQL endpoint
        stashdb_api_key: StashDB API key
        entity_type: "performer", "studio", or "tag"
        entity_stash_id: StashDB UUID of the entity
        local_stash_ids: Set of stash_ids user already owns
        page_size: Number of missing scenes to collect
        sort: Sort field for StashDB query
        direction: Sort direction (ASC/DESC)
        stashdb_page: Starting page number (1-indexed)
        offset: Scenes to skip from current page (for resume)
        plugin_settings: Optional plugin settings

    Returns:
        Dict with:
            scenes: List of missing scenes (raw from StashDB)
            cursor: Encoded cursor for next page, or None if complete
            has_more: Boolean - more results may be available
            is_complete: Boolean - all StashDB scenes have been checked
            total_on_stashdb: Total scenes on StashDB for this entity
            stashdb_pages_fetched: Number of pages fetched this request
    """
    collected = []
    pages_fetched = 0
    total_on_stashdb = 0
    is_complete = False
    current_page = stashdb_page
    current_offset = offset

    while len(collected) < page_size and pages_fetched < MAX_PAGES_PER_REQUEST:
        scenes, total, has_more_pages = stashbox_api.query_stashdb_scenes_page(
            url=stashdb_url,
            api_key=stashdb_api_key,
            entity_type=entity_type,
            entity_stash_id=entity_stash_id,
            page=current_page,
            per_page=100,
            sort=sort,
            direction=direction,
            plugin_settings=plugin_settings
        )

        pages_fetched += 1
        total_on_stashdb = total

        if not scenes:
            is_complete = True
            break

        # Process scenes starting from offset
        for i, scene in enumerate(scenes):
            if i < current_offset:
                continue

            scene_id = scene.get("id")
            if scene_id not in local_stash_ids:
                collected.append(scene)

                if len(collected) >= page_size:
                    # Save position for cursor
                    current_offset = i + 1
                    if current_offset >= len(scenes):
                        current_page += 1
                        current_offset = 0
                    break
        else:
            # Finished this page, move to next
            if not has_more_pages:
                is_complete = True
                break
            current_page += 1
            current_offset = 0

    # Build cursor if more results available
    cursor = None
    has_more = False

    if not is_complete and (len(collected) >= page_size or pages_fetched >= MAX_PAGES_PER_REQUEST):
        has_more = True
        cursor_state = {
            "stashdb_page": current_page,
            "offset": current_offset,
            "sort": sort,
            "direction": direction,
            "entity_type": entity_type,
            "entity_stash_id": entity_stash_id,
            "endpoint": stashdb_url,
            "total_on_stashdb": total_on_stashdb
        }
        cursor = encode_cursor(cursor_state)

    return {
        "scenes": collected,
        "cursor": cursor,
        "has_more": has_more,
        "is_complete": is_complete,
        "total_on_stashdb": total_on_stashdb,
        "stashdb_pages_fetched": pages_fetched
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestFetchUntilFull` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): implement fetch_until_full pagination logic"
```

---

## Task 6: Create New Paginated find_missing_scenes_paginated Function

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py`
- Test: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Write failing test for paginated find_missing**

Add to `test_missing_scenes.py`:

```python
class TestFindMissingScenesPaginated(unittest.TestCase):
    """Test the paginated find_missing_scenes function."""

    def setUp(self):
        """Clear cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch('missing_scenes.get_local_tag')
    @patch('missing_scenes.get_stashbox_config')
    @patch('missing_scenes.build_local_stash_id_cache')
    @patch('missing_scenes.fetch_until_full')
    @patch('missing_scenes.whisparr_get_status_map')
    def test_first_page_request(self, mock_whisparr, mock_fetch, mock_build_cache,
                                 mock_stashbox_config, mock_get_tag):
        """Test first page request (no cursor)."""
        mock_stashbox_config.return_value = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key", "name": "StashDB"}
        ]
        mock_get_tag.return_value = {
            "id": "123",
            "name": "Test Tag",
            "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": "tag-uuid"}]
        }
        mock_build_cache.return_value = {"owned1", "owned2"}
        mock_fetch.return_value = {
            "scenes": [{"id": "missing1", "title": "Scene 1"}],
            "cursor": "encoded-cursor",
            "has_more": True,
            "is_complete": False,
            "total_on_stashdb": 100,
            "stashdb_pages_fetched": 1
        }
        mock_whisparr.return_value = {}

        result = missing_scenes.find_missing_scenes_paginated(
            entity_type="tag",
            entity_id="123",
            plugin_settings={},
            page_size=50,
            cursor=None,
            sort="DATE",
            direction="DESC"
        )

        self.assertEqual(result["entity_name"], "Test Tag")
        self.assertEqual(result["total_on_stashdb"], 100)
        self.assertEqual(result["total_local"], 2)
        self.assertTrue(result["has_more"])
        self.assertIsNotNone(result["cursor"])
        self.assertEqual(len(result["missing_scenes"]), 1)
```

**Step 2: Run tests to verify they fail**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: FAIL with `AttributeError: module 'missing_scenes' has no attribute 'find_missing_scenes_paginated'`

**Step 3: Implement find_missing_scenes_paginated**

Add to `missing_scenes.py` after fetch_until_full:

```python
def find_missing_scenes_paginated(entity_type, entity_id, plugin_settings,
                                   page_size=PAGE_SIZE_DEFAULT, cursor=None,
                                   sort="DATE", direction="DESC",
                                   endpoint_override=None):
    """Find missing scenes with pagination support.

    Args:
        entity_type: "performer", "studio", or "tag"
        entity_id: Local Stash ID of the entity
        plugin_settings: Plugin configuration from Stash
        page_size: Number of missing scenes to return (default 50, max 100)
        cursor: Pagination cursor from previous request, or None for first page
        sort: Sort field - "DATE", "TITLE", "CREATED_AT", "UPDATED_AT"
        direction: Sort direction - "ASC" or "DESC"
        endpoint_override: Optional stash-box endpoint URL

    Returns:
        Dict with missing scenes and pagination metadata
    """
    # Validate and clamp page_size
    page_size = max(1, min(page_size or PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX))

    # Validate sort/direction
    valid_sorts = {"DATE", "TITLE", "CREATED_AT", "UPDATED_AT"}
    valid_directions = {"ASC", "DESC"}
    sort = sort.upper() if sort and sort.upper() in valid_sorts else "DATE"
    direction = direction.upper() if direction and direction.upper() in valid_directions else "DESC"

    # Check for cursor (resuming pagination)
    cursor_state = decode_cursor(cursor) if cursor else None

    # Get stash-box configuration
    stashbox_configs = get_stashbox_config()
    if not stashbox_configs:
        return {"error": "No stash-box endpoints configured in Stash settings"}

    # Determine endpoint
    target_endpoint = endpoint_override or plugin_settings.get("stashBoxEndpoint", "").strip()

    if cursor_state:
        # Resuming: use endpoint from cursor
        target_endpoint = cursor_state.get("endpoint", target_endpoint)

    # Find matching stash-box config
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

    # Get entity and its stash_id
    if cursor_state:
        # Resuming: use entity info from cursor
        entity_stash_id = cursor_state.get("entity_stash_id")
        entity_name = cursor_state.get("entity_name", "Unknown")
        # Still need to validate sort/direction match
        if cursor_state.get("sort") != sort or cursor_state.get("direction") != direction:
            # Sort changed, start over
            cursor_state = None

    if not cursor_state:
        # First request: get entity from local Stash
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

        entity_name = entity.get("name")

        # Find stash_id for this endpoint
        entity_stash_id = None
        for sid in entity.get("stash_ids", []):
            if sid.get("endpoint") == stashdb_url:
                entity_stash_id = sid.get("stash_id")
                break

        if not entity_stash_id:
            return {
                "error": f"{entity_type.title()} '{entity_name}' is not linked to {stashdb_name}. "
                         f"Please use the Tagger to link this {entity_type} first."
            }

    log.LogInfo(f"Finding missing scenes for {entity_type} '{entity_name}' (page_size={page_size}, sort={sort} {direction})")

    # Build or get cached local stash_ids
    if not is_cache_valid(stashdb_url):
        build_local_stash_id_cache(stashdb_url)
    local_stash_ids = get_cached_stash_ids(stashdb_url)

    # Determine starting position
    stashdb_page = cursor_state.get("stashdb_page", 1) if cursor_state else 1
    offset = cursor_state.get("offset", 0) if cursor_state else 0

    # Fetch missing scenes
    fetch_result = fetch_until_full(
        stashdb_url=stashdb_url,
        stashdb_api_key=stashdb_api_key,
        entity_type=entity_type,
        entity_stash_id=entity_stash_id,
        local_stash_ids=local_stash_ids,
        page_size=page_size,
        sort=sort,
        direction=direction,
        stashdb_page=stashdb_page,
        offset=offset,
        plugin_settings=plugin_settings
    )

    # Get Whisparr status if configured
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

    # Format scenes and add Whisparr status
    missing_scenes = []
    for scene in fetch_result["scenes"]:
        scene_stash_id = scene.get("id")
        formatted = format_scene(scene, scene_stash_id)

        if scene_stash_id in whisparr_status_map:
            formatted["whisparr_status"] = whisparr_status_map[scene_stash_id]
        else:
            formatted["whisparr_status"] = None
        formatted["in_whisparr"] = scene_stash_id in whisparr_status_map

        missing_scenes.append(formatted)

    # Update cursor with entity name for display
    cursor_out = fetch_result["cursor"]
    if cursor_out:
        cursor_state_out = decode_cursor(cursor_out)
        if cursor_state_out:
            cursor_state_out["entity_name"] = entity_name
            cursor_out = encode_cursor(cursor_state_out)

    # Calculate estimate
    total_on_stashdb = fetch_result["total_on_stashdb"]
    total_local = get_cache_count(stashdb_url)
    missing_estimate = max(0, total_on_stashdb - total_local) if not fetch_result["is_complete"] else None

    return {
        "entity_name": entity_name,
        "entity_type": entity_type,
        "stashdb_name": stashdb_name,
        "stashdb_url": stashdb_url.replace("/graphql", ""),
        "total_on_stashdb": total_on_stashdb,
        "total_local": total_local,
        "missing_count_estimate": missing_estimate,
        "missing_count_loaded": len(missing_scenes),
        "cursor": cursor_out,
        "has_more": fetch_result["has_more"],
        "is_complete": fetch_result["is_complete"],
        "missing_scenes": missing_scenes,
        "whisparr_configured": whisparr_configured
    }
```

**Step 4: Run tests to verify they pass**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All `TestFindMissingScenesPaginated` tests PASS

**Step 5: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py plugins/missingScenes/test_missing_scenes.py
git commit -m "feat(missingScenes): add paginated find_missing_scenes function"
```

---

## Task 7: Update Plugin Entry Point to Support Pagination

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py` (main function, around line 1350)

**Step 1: Read current main() implementation**

Review the current `main()` function to understand the entry point.

**Step 2: Update main() to handle pagination parameters**

Modify the `find_missing` operation handler in `main()`:

```python
        if operation == "find_missing":
            entity_type = args.get("entity_type", "performer")
            entity_id = args.get("entity_id", "")
            endpoint = args.get("endpoint")  # Optional endpoint override

            # NEW: Pagination parameters
            page_size = args.get("page_size")
            cursor = args.get("cursor")
            sort = args.get("sort", "DATE")
            direction = args.get("direction", "DESC")

            if not entity_id:
                output = {"error": "entity_id is required"}
            elif page_size is not None or cursor is not None:
                # Use new paginated function
                output = find_missing_scenes_paginated(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    plugin_settings=plugin_settings,
                    page_size=page_size,
                    cursor=cursor,
                    sort=sort,
                    direction=direction,
                    endpoint_override=endpoint
                )
            else:
                # Backwards compatibility: use old function
                log.LogWarning("find_missing called without pagination - using legacy mode")
                output = find_missing_scenes(entity_type, entity_id, plugin_settings, endpoint_override=endpoint)
```

**Step 3: Test manually**

The plugin should still work with existing frontend (legacy mode).

**Step 4: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): update main() to support pagination parameters"
```

---

## Task 8: Update Frontend - Add State Variables and Sort Controls

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js`

**Step 1: Add new state variables at the top of the file**

After the existing state variables (around line 15), add:

```javascript
  // Pagination state
  let currentCursor = null;
  let hasMore = false;
  let isComplete = false;
  let totalOnStashdb = 0;
  let totalLocal = 0;
  let missingCountEstimate = null;
  let sortField = "DATE";
  let sortDirection = "DESC";
```

**Step 2: Update findMissingScenes function to accept pagination params**

Replace the existing `findMissingScenes` function:

```javascript
  /**
   * Find missing scenes for the current entity
   */
  async function findMissingScenes(entityType, entityId, endpoint = null, options = {}) {
    const args = {
      operation: "find_missing",
      entity_type: entityType,
      entity_id: entityId,
    };
    if (endpoint) {
      args.endpoint = endpoint;
    }
    // Add pagination parameters
    if (options.page_size !== undefined) {
      args.page_size = options.page_size;
    }
    if (options.cursor !== undefined) {
      args.cursor = options.cursor;
    }
    if (options.sort !== undefined) {
      args.sort = options.sort;
    }
    if (options.direction !== undefined) {
      args.direction = options.direction;
    }
    return runPluginOperation(args);
  }
```

**Step 3: Add sort control creation function**

Add after the `createEndpointSelector` function:

```javascript
  /**
   * Create the sort controls
   */
  function createSortControls() {
    const container = document.createElement("div");
    container.className = "ms-sort-controls";
    container.id = "ms-sort-controls";

    // Sort field dropdown
    const fieldLabel = document.createElement("label");
    fieldLabel.textContent = "Sort by: ";
    fieldLabel.htmlFor = "ms-sort-field";

    const fieldSelect = document.createElement("select");
    fieldSelect.id = "ms-sort-field";
    fieldSelect.className = "ms-sort-dropdown";

    const sortOptions = [
      { value: "DATE", label: "Release Date" },
      { value: "TITLE", label: "Title" },
      { value: "CREATED_AT", label: "Added to StashDB" },
      { value: "UPDATED_AT", label: "Last Updated" },
    ];

    for (const opt of sortOptions) {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      if (opt.value === sortField) {
        option.selected = true;
      }
      fieldSelect.appendChild(option);
    }

    // Direction dropdown
    const dirLabel = document.createElement("label");
    dirLabel.textContent = " ";
    dirLabel.htmlFor = "ms-sort-direction";

    const dirSelect = document.createElement("select");
    dirSelect.id = "ms-sort-direction";
    dirSelect.className = "ms-sort-dropdown";

    const dirOptions = [
      { value: "DESC", label: "Newest First" },
      { value: "ASC", label: "Oldest First" },
    ];

    for (const opt of dirOptions) {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      if (opt.value === sortDirection) {
        option.selected = true;
      }
      dirSelect.appendChild(option);
    }

    // Change handlers
    fieldSelect.onchange = () => {
      sortField = fieldSelect.value;
      handleSortChange();
    };

    dirSelect.onchange = () => {
      sortDirection = dirSelect.value;
      handleSortChange();
    };

    container.appendChild(fieldLabel);
    container.appendChild(fieldSelect);
    container.appendChild(dirLabel);
    container.appendChild(dirSelect);

    return container;
  }

  /**
   * Handle sort change - reset and reload
   */
  function handleSortChange() {
    currentCursor = null;
    hasMore = false;
    isComplete = false;
    missingScenes = [];
    performSearch();
  }
```

**Step 4: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add frontend sort controls and state"
```

---

## Task 9: Update Frontend - Modal and Stats Bar

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js`

**Step 1: Update createModal to include sort controls**

Modify `createModal()` to insert sort controls:

```javascript
    // After creating stats element, add sort controls
    const sortControls = createSortControls();

    // Assemble modal
    modal.appendChild(header);
    modal.appendChild(sortControls);  // Add sort controls
    modal.appendChild(stats);
    modal.appendChild(body);
    modal.appendChild(footer);
```

**Step 2: Update updateStats to show pagination info**

Replace `updateStats` function:

```javascript
  /**
   * Update the stats bar with pagination info
   */
  function updateStats(data) {
    const statsEl = document.getElementById("ms-stats");
    if (!statsEl) return;

    let entityLabel;
    switch (data.entity_type) {
      case "performer":
        entityLabel = "Performer";
        break;
      case "studio":
        entityLabel = "Studio";
        break;
      case "tag":
        entityLabel = "Tag";
        break;
      default:
        entityLabel = "Entity";
    }

    // Update global state
    totalOnStashdb = data.total_on_stashdb || 0;
    totalLocal = data.total_local || 0;
    missingCountEstimate = data.missing_count_estimate;
    hasMore = data.has_more || false;
    isComplete = data.is_complete || false;

    // Format missing count
    let missingDisplay;
    if (isComplete) {
      missingDisplay = `${missingScenes.length}`;
    } else if (missingCountEstimate !== null) {
      missingDisplay = `~${missingCountEstimate.toLocaleString()} (${missingScenes.length} loaded)`;
    } else {
      missingDisplay = `${missingScenes.length} loaded`;
    }

    statsEl.innerHTML = `
      <div class="ms-stat">
        <span class="ms-stat-label">${entityLabel}:</span>
        <span class="ms-stat-value">${data.entity_name || "Unknown"}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">On ${data.stashdb_name || "StashDB"}:</span>
        <span class="ms-stat-value">${totalOnStashdb.toLocaleString()}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">You Have:</span>
        <span class="ms-stat-value">${totalLocal.toLocaleString()}</span>
      </div>
      <div class="ms-stat ms-stat-highlight">
        <span class="ms-stat-label">Missing:</span>
        <span class="ms-stat-value">${missingDisplay}</span>
      </div>
    `;
  }
```

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): update modal with sort controls and pagination stats"
```

---

## Task 10: Update Frontend - Load More Button and Search Flow

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js`

**Step 1: Add Load More button to footer**

Update the footer HTML in `createModal()`:

```javascript
    // Footer
    const footer = document.createElement("div");
    footer.className = "ms-modal-footer";
    footer.innerHTML = `
      <div class="ms-status" id="ms-status"></div>
      <div class="ms-footer-actions">
        <button class="ms-btn" id="ms-load-more-btn" style="display: none;">Load More</button>
        <button class="ms-btn" id="ms-add-all-btn" style="display: none;">Add All to Whisparr</button>
      </div>
    `;
```

**Step 2: Add updateLoadMoreButton function**

```javascript
  /**
   * Update the Load More button state
   */
  function updateLoadMoreButton() {
    const btn = document.getElementById("ms-load-more-btn");
    if (!btn) return;

    if (hasMore && !isComplete) {
      btn.style.display = "inline-block";
      btn.disabled = isLoading;
      btn.textContent = isLoading ? "Loading..." : "Load More";
      btn.onclick = loadMoreResults;
    } else {
      btn.style.display = "none";
    }
  }

  /**
   * Load more results (pagination)
   */
  async function loadMoreResults() {
    if (!hasMore || isLoading || isComplete) return;
    await performSearch();
  }
```

**Step 3: Update handleSearch to reset pagination state**

```javascript
  /**
   * Handle the search button click
   */
  async function handleSearch() {
    if (isLoading) return;
    if (!currentEntityId || !currentEntityType) {
      console.error("[MissingScenes] No entity selected");
      return;
    }

    // Reset pagination state for new search
    currentCursor = null;
    hasMore = false;
    isComplete = false;
    missingScenes = [];

    isLoading = true;
    createModal();
    showLoading();
    setStatus("Checking endpoints...", "loading");

    // ... rest of existing endpoint checking logic ...
```

**Step 4: Update performSearch to use pagination**

Replace `performSearch()`:

```javascript
  /**
   * Perform the actual search (called by handleSearch and Load More)
   */
  async function performSearch() {
    if (isLoading && currentCursor !== null) return; // Prevent double-loads

    isLoading = true;

    if (currentCursor === null) {
      showLoading();
    }
    setStatus("Loading...", "loading");

    try {
      const result = await findMissingScenes(
        currentEntityType,
        currentEntityId,
        selectedEndpoint,
        {
          page_size: 50,
          cursor: currentCursor,
          sort: sortField,
          direction: sortDirection
        }
      );

      // Append new results (don't replace)
      if (result.missing_scenes) {
        missingScenes = [...missingScenes, ...result.missing_scenes];
      }

      // Update pagination state
      currentCursor = result.cursor;
      hasMore = result.has_more || false;
      isComplete = result.is_complete || false;
      whisparrConfigured = result.whisparr_configured || false;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";

      updateStats(result);
      renderResults();
      updateLoadMoreButton();
      updateAddAllButton();

      if (missingScenes.length > 0) {
        const loadedText = isComplete ? "" : ` (${missingScenes.length} loaded)`;
        setStatus(`Found missing scenes${loadedText}`, "success");
      } else {
        setStatus("You have all available scenes!", "success");
      }
    } catch (error) {
      console.error("[MissingScenes] Search failed:", error);
      showError(error.message || "Failed to search for missing scenes");
      setStatus(error.message || "Search failed", "error");
    } finally {
      isLoading = false;
      updateLoadMoreButton();
    }
  }
```

**Step 5: Update Add All button to show count**

Add function:

```javascript
  /**
   * Update Add All to Whisparr button
   */
  function updateAddAllButton() {
    const addAllBtn = document.getElementById("ms-add-all-btn");
    if (!addAllBtn) return;

    if (whisparrConfigured) {
      const notInWhisparr = missingScenes.filter((s) => !s.in_whisparr);
      if (notInWhisparr.length > 0) {
        addAllBtn.style.display = "inline-block";
        addAllBtn.textContent = `Add All to Whisparr (${notInWhisparr.length})`;
        addAllBtn.onclick = () => handleAddAll(notInWhisparr);
      } else {
        addAllBtn.style.display = "none";
      }
    } else {
      addAllBtn.style.display = "none";
    }
  }
```

**Step 6: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add Load More button and paginated search flow"
```

---

## Task 11: Add CSS Styles for New Elements

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.css`

**Step 1: Add styles for sort controls and load more button**

Add to `missing-scenes.css`:

```css
/* Sort Controls */
.ms-sort-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  background: var(--ms-bg-secondary, #1a1a1a);
  border-bottom: 1px solid var(--ms-border, #333);
  font-size: 0.85rem;
}

.ms-sort-controls label {
  color: var(--ms-text-secondary, #888);
}

.ms-sort-dropdown {
  background: var(--ms-bg-primary, #0d0d0d);
  color: var(--ms-text-primary, #fff);
  border: 1px solid var(--ms-border, #333);
  border-radius: 4px;
  padding: 0.25rem 0.5rem;
  font-size: 0.85rem;
  cursor: pointer;
}

.ms-sort-dropdown:hover {
  border-color: var(--ms-accent, #007bff);
}

.ms-sort-dropdown:focus {
  outline: none;
  border-color: var(--ms-accent, #007bff);
  box-shadow: 0 0 0 2px rgba(0, 123, 255, 0.25);
}

/* Load More Button */
#ms-load-more-btn {
  margin-right: 0.5rem;
}

#ms-load-more-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.css
git commit -m "feat(missingScenes): add CSS for sort controls and load more button"
```

---

## Task 12: Update Tests and Run Full Test Suite

**Files:**
- Modify: `plugins/missingScenes/test_missing_scenes.py`

**Step 1: Add test suite runner for new test classes**

Update `run_all_tests()` to include new test classes:

```python
def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Missing Scenes Plugin - Unit Tests")
    print("=" * 60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestFormatScene))
    suite.addTests(loader.loadTestsFromTestCase(TestWhisparrPayload))
    suite.addTests(loader.loadTestsFromTestCase(TestStashIdMatching))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestSceneSorting))
    suite.addTests(loader.loadTestsFromTestCase(TestGraphQLQueryConstruction))
    suite.addTests(loader.loadTestsFromTestCase(TestEndpointSelection))
    suite.addTests(loader.loadTestsFromTestCase(TestEndpointMatching))
    # NEW test classes
    suite.addTests(loader.loadTestsFromTestCase(TestCursorEncoding))
    suite.addTests(loader.loadTestsFromTestCase(TestLocalStashIdCache))
    suite.addTests(loader.loadTestsFromTestCase(TestBuildCache))
    suite.addTests(loader.loadTestsFromTestCase(TestPaginatedStashDBQuery))
    suite.addTests(loader.loadTestsFromTestCase(TestFetchUntilFull))
    suite.addTests(loader.loadTestsFromTestCase(TestFindMissingScenesPaginated))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"Results: {result.testsRun} tests, "
          f"{len(result.failures)} failures, "
          f"{len(result.errors)} errors")
    print("=" * 60)

    return len(result.failures) == 0 and len(result.errors) == 0
```

**Step 2: Run full test suite**

```bash
cd plugins/missingScenes && python test_missing_scenes.py
```

Expected: All tests PASS

**Step 3: Commit**

```bash
git add plugins/missingScenes/test_missing_scenes.py
git commit -m "test(missingScenes): add pagination tests to test suite"
```

---

## Task 13: Manual Testing

**Step 1: Test with small entity**

1. Open Stash, navigate to a performer with < 50 scenes on StashDB
2. Click "Missing Scenes" button
3. Verify results load quickly
4. Verify no "Load More" button (all results in one page)
5. Verify sort controls work

**Step 2: Test with large entity (tag)**

1. Navigate to a tag with many scenes (e.g., "Christmas" with 2000+ scenes)
2. Click "Missing Scenes"
3. Verify first results appear within seconds (no 504)
4. Verify "Load More" button appears
5. Click "Load More" and verify more results append
6. Test changing sort field - should reset and reload

**Step 3: Test Whisparr integration**

1. Verify "Add to Whisparr" buttons still work
2. Verify "Add All to Whisparr" shows correct count of loaded scenes

**Step 4: Document any issues found**

---

## Task 14: Final Commit and Cleanup

**Step 1: Review all changes**

```bash
git diff main
git log --oneline main..HEAD
```

**Step 2: Create summary commit if needed**

If there are uncommitted changes:

```bash
git add -A
git commit -m "chore(missingScenes): final cleanup for pagination feature"
```

**Step 3: Verify branch is ready**

```bash
git status
python plugins/missingScenes/test_missing_scenes.py
```

---

## Summary

This plan implements:

1. **Cursor-based pagination** - Resume fetching from where we left off
2. **Local stash_id caching** - Fast filtering without repeated Stash queries
3. **"Fetch Until Full" algorithm** - Always return full pages, no empty results
4. **Sort controls** - User can sort by DATE, TITLE, CREATED_AT, UPDATED_AT
5. **Load More button** - Progressive loading instead of all-at-once
6. **Backwards compatibility** - Old frontend still works (legacy mode)

Total: 14 tasks, each with TDD approach (write test, verify fail, implement, verify pass, commit).
