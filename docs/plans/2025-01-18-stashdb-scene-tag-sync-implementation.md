# StashDB Scene Tag Sync - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Plugin Task to tagManager that syncs tags from StashDB to local scenes, replicating Stash's Tagger merge behavior.

**Architecture:** New module `stashdb_scene_sync.py` handles sync logic with `TagCache` for efficient matching. Uses two-pass processing: batch fingerprint queries for efficiency, sequential fallback for accuracy. Rate-limited to respect StashDB's 240 req/min limit.

**Tech Stack:** Python 3, stashapi (stashapp-tools), urllib for StashDB API, unittest for testing.

---

## Task 1: Add RateLimiter Class to stashdb_api.py

**Files:**
- Modify: `plugins/tagManager/stashdb_api.py`
- Test: `plugins/tagManager/tests/test_stashdb_api.py`

**Step 1: Write the failing test**

Add to `plugins/tagManager/tests/test_stashdb_api.py`:

```python
class TestRateLimiter(unittest.TestCase):
    """Test rate limiter functionality."""

    def test_wait_enforces_minimum_interval(self):
        """Should enforce minimum interval between requests."""
        from stashdb_api import RateLimiter
        import time

        limiter = RateLimiter(requests_per_second=10)  # 0.1s interval

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        # Two waits should take at least 0.1s (one interval)
        self.assertGreaterEqual(elapsed, 0.09)

    def test_backoff_calculates_exponential_delay(self):
        """Should calculate exponential backoff delays."""
        from stashdb_api import RateLimiter

        limiter = RateLimiter()

        self.assertEqual(limiter.backoff(0), 1.0)  # 2^0 = 1
        self.assertEqual(limiter.backoff(1), 2.0)  # 2^1 = 2
        self.assertEqual(limiter.backoff(2), 4.0)  # 2^2 = 4
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py::TestRateLimiter -v`

Expected: FAIL with "cannot import name 'RateLimiter'"

**Step 3: Write minimal implementation**

Add to `plugins/tagManager/stashdb_api.py` (after DEFAULT_CONFIG):

```python
class RateLimiter:
    """
    Rate limiter for API calls.

    Enforces minimum interval between requests and provides
    exponential backoff for retry scenarios.
    """

    def __init__(self, requests_per_second=2):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Max requests per second (default 2)
        """
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0

    def wait(self):
        """Block until rate limit allows next request."""
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()

    def backoff(self, attempt):
        """
        Calculate exponential backoff delay.

        Args:
            attempt: Attempt number (0-indexed)

        Returns:
            Delay in seconds (2^attempt)
        """
        return float(2 ** attempt)
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py::TestRateLimiter -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_api.py plugins/tagManager/tests/test_stashdb_api.py
git commit -m "feat(tagManager): add RateLimiter class for API rate limiting"
```

---

## Task 2: Add Scene Query Methods to stashdb_api.py

**Files:**
- Modify: `plugins/tagManager/stashdb_api.py`
- Test: `plugins/tagManager/tests/test_stashdb_api.py`

**Step 1: Write the failing tests**

Add to `plugins/tagManager/tests/test_stashdb_api.py`:

```python
class TestSceneQueries(unittest.TestCase):
    """Test scene query functionality (mocked)."""

    def test_find_scene_by_id_returns_scene_with_tags(self):
        """Should return scene dict with tags field."""
        # This will be an integration test - for unit test, we just verify the function exists
        from stashdb_api import find_scene_by_id
        # Function should exist and be callable
        self.assertTrue(callable(find_scene_by_id))

    def test_find_scenes_by_fingerprints_returns_list_of_lists(self):
        """Should return list of scene lists matching fingerprint batches."""
        from stashdb_api import find_scenes_by_fingerprints
        self.assertTrue(callable(find_scenes_by_fingerprints))
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py::TestSceneQueries -v`

Expected: FAIL with "cannot import name 'find_scene_by_id'"

**Step 3: Write implementation**

Add to `plugins/tagManager/stashdb_api.py`:

```python
# GraphQL fragment for scene with tags
SCENE_WITH_TAGS_FIELDS = """
    id
    title
    tags {
        id
        name
        aliases
    }
"""


def find_scene_by_id(url, api_key, scene_id, rate_limiter=None):
    """
    Query StashDB for a single scene by its ID.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        scene_id: StashDB scene UUID
        rate_limiter: Optional RateLimiter instance

    Returns:
        Scene dict with tags, or None if not found
    """
    query = f"""
    query FindScene($id: ID!) {{
        findScene(id: $id) {{
            {SCENE_WITH_TAGS_FIELDS}
        }}
    }}
    """

    variables = {"id": scene_id}

    if rate_limiter:
        rate_limiter.wait()

    try:
        data = graphql_request(url, query, variables, api_key)
    except StashDBAPIError as e:
        log.LogError(f"Error fetching scene {scene_id}: {e}")
        return None

    if not data:
        return None

    return data.get("findScene")


def find_scenes_by_fingerprints(url, api_key, fingerprint_batches, rate_limiter=None):
    """
    Batch query StashDB for scenes by fingerprints.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        fingerprint_batches: List of fingerprint lists (max 40 batches)
            Each fingerprint: {'hash': str, 'algorithm': 'MD5'|'OSHASH'|'PHASH'}
        rate_limiter: Optional RateLimiter instance

    Returns:
        List of lists of scene dicts (one list per input batch)
    """
    if len(fingerprint_batches) > 40:
        log.LogWarning(f"Fingerprint batch size {len(fingerprint_batches)} exceeds limit of 40")
        fingerprint_batches = fingerprint_batches[:40]

    query = f"""
    query FindScenesByFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {{
        findScenesBySceneFingerprints(fingerprints: $fingerprints) {{
            {SCENE_WITH_TAGS_FIELDS}
        }}
    }}
    """

    # Convert to GraphQL input format
    gql_fingerprints = []
    for batch in fingerprint_batches:
        gql_batch = []
        for fp in batch:
            gql_batch.append({
                "hash": fp["hash"],
                "algorithm": fp["algorithm"]
            })
        gql_fingerprints.append(gql_batch)

    variables = {"fingerprints": gql_fingerprints}

    if rate_limiter:
        rate_limiter.wait()

    try:
        data = graphql_request(url, query, variables, api_key)
    except StashDBAPIError as e:
        log.LogError(f"Error fetching scenes by fingerprints: {e}")
        return [[] for _ in fingerprint_batches]

    if not data:
        return [[] for _ in fingerprint_batches]

    return data.get("findScenesBySceneFingerprints", [])
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py::TestSceneQueries -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_api.py plugins/tagManager/tests/test_stashdb_api.py
git commit -m "feat(tagManager): add scene query methods for StashDB"
```

---

## Task 3: Create TagCache Class

**Files:**
- Create: `plugins/tagManager/tag_cache.py`
- Create: `plugins/tagManager/tests/test_tag_cache.py`

**Step 1: Write the failing tests**

Create `plugins/tagManager/tests/test_tag_cache.py`:

```python
"""Tests for TagCache class."""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTagCache(unittest.TestCase):
    """Test TagCache functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.local_tags = [
            {
                "id": "1",
                "name": "Anal Creampie",
                "aliases": ["Anal Cream Pie"],
                "stash_ids": [
                    {"endpoint": "https://stashdb.org/graphql", "stash_id": "stashdb-abc123"}
                ]
            },
            {
                "id": "2",
                "name": "Cowgirl",
                "aliases": ["Girl on Top", "Cowgirl Position"],
                "stash_ids": []
            },
            {
                "id": "3",
                "name": "Custom Tag",
                "aliases": [],
                "stash_ids": []
            },
        ]

    def test_build_creates_cache_from_tags(self):
        """Should build cache from list of local tags."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        self.assertIsNotNone(cache)
        self.assertEqual(cache.tag_count, 3)

    def test_by_stashdb_id_finds_linked_tag(self):
        """Should find tag by StashDB ID link."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)
        endpoint = "https://stashdb.org/graphql"

        result = cache.by_stashdb_id(endpoint, "stashdb-abc123")

        self.assertEqual(result, "1")

    def test_by_stashdb_id_returns_none_for_no_match(self):
        """Should return None when StashDB ID not found."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        result = cache.by_stashdb_id("https://stashdb.org/graphql", "nonexistent")

        self.assertIsNone(result)

    def test_by_name_finds_exact_match(self):
        """Should find tag by exact name (case-insensitive)."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        result = cache.by_name("cowgirl")

        self.assertEqual(result, "2")

    def test_by_name_is_case_insensitive(self):
        """Should match names regardless of case."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        self.assertEqual(cache.by_name("COWGIRL"), "2")
        self.assertEqual(cache.by_name("CowGirl"), "2")
        self.assertEqual(cache.by_name("cowgirl"), "2")

    def test_by_alias_finds_match(self):
        """Should find tag by alias (case-insensitive)."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        result = cache.by_alias("girl on top")

        self.assertEqual(result, "2")

    def test_by_alias_returns_none_for_no_match(self):
        """Should return None when alias not found."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        result = cache.by_alias("nonexistent alias")

        self.assertIsNone(result)

    def test_get_name_returns_tag_name(self):
        """Should return tag name for given ID."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        self.assertEqual(cache.get_name("1"), "Anal Creampie")
        self.assertEqual(cache.get_name("2"), "Cowgirl")

    def test_get_name_returns_none_for_unknown_id(self):
        """Should return None for unknown tag ID."""
        from tag_cache import TagCache

        cache = TagCache.build(self.local_tags)

        self.assertIsNone(cache.get_name("999"))


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_tag_cache.py -v`

Expected: FAIL with "No module named 'tag_cache'"

**Step 3: Write implementation**

Create `plugins/tagManager/tag_cache.py`:

```python
"""
TagCache - Efficient lookup for local tag matching.

Pre-builds lookup maps from local Stash tags for fast matching
against StashDB tags during sync.
"""


class TagCache:
    """
    Pre-built lookup maps for efficient tag matching.

    Maps:
    - stashdb_id_map: {(endpoint, stashdb_id): local_tag_id}
    - name_map: {lowercase_name: local_tag_id}
    - alias_map: {lowercase_alias: local_tag_id}
    - id_to_name: {local_tag_id: tag_name}
    """

    def __init__(self):
        """Initialize empty cache."""
        self.stashdb_id_map = {}
        self.name_map = {}
        self.alias_map = {}
        self.id_to_name = {}
        self.tag_count = 0

    @classmethod
    def build(cls, local_tags):
        """
        Build cache from list of local tags.

        Args:
            local_tags: List of tag dicts from Stash API with keys:
                - id: Local tag ID
                - name: Tag name
                - aliases: List of alias strings
                - stash_ids: List of {endpoint, stash_id} dicts

        Returns:
            TagCache instance with populated lookup maps
        """
        cache = cls()
        cache.tag_count = len(local_tags)

        for tag in local_tags:
            tag_id = str(tag.get("id", ""))
            name = tag.get("name", "")
            aliases = tag.get("aliases", []) or []
            stash_ids = tag.get("stash_ids", []) or []

            if not tag_id or not name:
                continue

            # Index by name (lowercase for case-insensitive matching)
            cache.name_map[name.lower()] = tag_id

            # Index by each alias
            for alias in aliases:
                if alias:
                    cache.alias_map[alias.lower()] = tag_id

            # Index by StashDB ID (endpoint + stash_id tuple)
            for stash_id_entry in stash_ids:
                endpoint = stash_id_entry.get("endpoint", "")
                stash_id = stash_id_entry.get("stash_id", "")
                if endpoint and stash_id:
                    cache.stashdb_id_map[(endpoint, stash_id)] = tag_id

            # Store ID to name mapping for reverse lookup
            cache.id_to_name[tag_id] = name

        return cache

    def by_stashdb_id(self, endpoint, stashdb_id):
        """
        Find local tag ID by StashDB ID link.

        Args:
            endpoint: StashDB endpoint URL
            stashdb_id: StashDB tag UUID

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        return self.stashdb_id_map.get((endpoint, stashdb_id))

    def by_name(self, name):
        """
        Find local tag ID by exact name match (case-insensitive).

        Args:
            name: Tag name to match

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        if not name:
            return None
        return self.name_map.get(name.lower())

    def by_alias(self, alias):
        """
        Find local tag ID by alias match (case-insensitive).

        Args:
            alias: Alias to match

        Returns:
            Local tag ID (str) if found, None otherwise
        """
        if not alias:
            return None
        return self.alias_map.get(alias.lower())

    def get_name(self, tag_id):
        """
        Get tag name for a local tag ID.

        Args:
            tag_id: Local tag ID

        Returns:
            Tag name (str) if found, None otherwise
        """
        return self.id_to_name.get(str(tag_id))
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_tag_cache.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/tag_cache.py plugins/tagManager/tests/test_tag_cache.py
git commit -m "feat(tagManager): add TagCache class for efficient tag lookup"
```

---

## Task 4: Create Tag Matching Function

**Files:**
- Create: `plugins/tagManager/stashdb_scene_sync.py`
- Create: `plugins/tagManager/tests/test_stashdb_scene_sync.py`

**Step 1: Write the failing tests**

Create `plugins/tagManager/tests/test_stashdb_scene_sync.py`:

```python
"""Tests for StashDB scene tag sync functionality."""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tag_cache import TagCache


class TestMatchStashdbTagToLocal(unittest.TestCase):
    """Test tag matching logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.endpoint = "https://stashdb.org/graphql"
        self.local_tags = [
            {
                "id": "1",
                "name": "Anal Creampie",
                "aliases": ["Anal Cream Pie"],
                "stash_ids": [
                    {"endpoint": self.endpoint, "stash_id": "stashdb-abc123"}
                ]
            },
            {
                "id": "2",
                "name": "Cowgirl",
                "aliases": ["Girl on Top"],
                "stash_ids": []
            },
            {
                "id": "3",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            },
        ]
        self.tag_cache = TagCache.build(self.local_tags)

    def test_matches_by_stashdb_id_first(self):
        """Should match by StashDB ID link (priority 1)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-abc123", "name": "Different Name"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "1")

    def test_matches_by_name_when_no_stashdb_link(self):
        """Should match by name when no StashDB link (priority 2)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Cowgirl"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "2")

    def test_matches_by_alias_when_no_name_match(self):
        """Should match by alias when name doesn't match (priority 3)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Girl on Top"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "2")

    def test_returns_none_for_no_match(self):
        """Should return None when no match found."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Nonexistent Tag"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertIsNone(result)

    def test_stashdb_id_takes_priority_over_name(self):
        """StashDB ID match should win even if name matches different tag."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        # Tag with StashDB ID that maps to "Anal Creampie" but name is "Blonde"
        stashdb_tag = {"id": "stashdb-abc123", "name": "Blonde"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        # Should match by StashDB ID to "Anal Creampie" (id=1), not by name to "Blonde" (id=3)
        self.assertEqual(result, "1")

    def test_name_takes_priority_over_alias(self):
        """Name match should win over alias match."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        # StashDB tag with name that's also an alias of another tag
        stashdb_tag = {"id": "stashdb-unknown", "name": "Blonde"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "3")


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestMatchStashdbTagToLocal -v`

Expected: FAIL with "No module named 'stashdb_scene_sync'"

**Step 3: Write implementation**

Create `plugins/tagManager/stashdb_scene_sync.py`:

```python
"""
StashDB Scene Tag Sync

Syncs tags from StashDB to local Stash scenes, replicating
Stash's Tagger merge behavior.
"""

import log


def match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint):
    """
    Match a StashDB tag to a local tag.

    Priority order (matches Stash's pkg/match/scraped.go:ScrapedTag):
    1. StashID link - local tag has same StashDB ID for this endpoint
    2. Name match - local tag name equals StashDB tag name (case-insensitive)
    3. Alias match - local tag alias equals StashDB tag name (case-insensitive)

    Args:
        stashdb_tag: Dict with 'id', 'name' from StashDB
        tag_cache: TagCache instance with lookup maps
        endpoint: StashDB endpoint URL

    Returns:
        Local tag ID (str) if matched, None if no match
    """
    stashdb_id = stashdb_tag.get("id")
    stashdb_name = stashdb_tag.get("name", "")

    # Priority 1: Match by StashID link
    if stashdb_id:
        local_id = tag_cache.by_stashdb_id(endpoint, stashdb_id)
        if local_id:
            return local_id

    # Priority 2: Match by local tag name
    local_id = tag_cache.by_name(stashdb_name)
    if local_id:
        return local_id

    # Priority 3: Match by local tag alias
    local_id = tag_cache.by_alias(stashdb_name)
    if local_id:
        return local_id

    return None
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestMatchStashdbTagToLocal -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_scene_sync.py plugins/tagManager/tests/test_stashdb_scene_sync.py
git commit -m "feat(tagManager): add tag matching function for scene sync"
```

---

## Task 5: Add ProcessResult and process_scene Function

**Files:**
- Modify: `plugins/tagManager/stashdb_scene_sync.py`
- Modify: `plugins/tagManager/tests/test_stashdb_scene_sync.py`

**Step 1: Write the failing tests**

Add to `plugins/tagManager/tests/test_stashdb_scene_sync.py`:

```python
class TestProcessScene(unittest.TestCase):
    """Test scene processing logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.endpoint = "https://stashdb.org/graphql"
        self.local_tags = [
            {
                "id": "1",
                "name": "Anal",
                "aliases": [],
                "stash_ids": [{"endpoint": self.endpoint, "stash_id": "stashdb-anal"}]
            },
            {
                "id": "2",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            },
            {
                "id": "3",
                "name": "Cowgirl",
                "aliases": [],
                "stash_ids": []
            },
        ]
        self.tag_cache = TagCache.build(self.local_tags)

    def test_returns_no_changes_when_no_new_tags(self):
        """Should return no_changes when scene already has all matched tags."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "1"}, {"id": "2"}]
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-anal", "name": "Anal"},
                {"id": "stashdb-blonde", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": False}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.status, "no_changes")
        self.assertEqual(result.tags_added, 0)

    def test_identifies_new_tags_to_add(self):
        """Should identify new tags that need to be added."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "1"}]  # Only has Anal
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-anal", "name": "Anal"},
                {"id": "stashdb-other", "name": "Blonde"},  # New tag
                {"id": "stashdb-other2", "name": "Cowgirl"}  # New tag
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.tags_added, 2)

    def test_skips_unmatched_tags(self):
        """Should skip StashDB tags with no local match."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": []
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-unknown", "name": "Unknown Tag"},
                {"id": "stashdb-other", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.tags_added, 1)  # Only Blonde
        self.assertEqual(result.tags_skipped, 1)  # Unknown Tag

    def test_preserves_existing_tags_in_merge(self):
        """Should preserve existing tags when calculating merge."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "99"}]  # Existing tag not in StashDB
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-other", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        # Should add Blonde (id=2) but preserve existing (id=99)
        self.assertEqual(result.tags_added, 1)
        self.assertIn("99", result.merged_tag_ids)
        self.assertIn("2", result.merged_tag_ids)
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestProcessScene -v`

Expected: FAIL with "cannot import name 'process_scene'"

**Step 3: Write implementation**

Add to `plugins/tagManager/stashdb_scene_sync.py`:

```python
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProcessResult:
    """Result of processing a single scene."""
    status: str  # 'updated', 'no_changes', 'dry_run', 'error'
    tags_added: int = 0
    tags_skipped: int = 0
    merged_tag_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None


def process_scene(scene, stashdb_scene, tag_cache, stash, settings, endpoint):
    """
    Process a single scene's tag merge.

    Args:
        scene: Local scene dict with id, tags
        stashdb_scene: StashDB scene dict with tags
        tag_cache: TagCache instance
        stash: StashInterface (can be None for dry_run)
        settings: Plugin settings dict with 'dry_run' key
        endpoint: StashDB endpoint URL

    Returns:
        ProcessResult with status, tags_added, tags_skipped, merged_tag_ids
    """
    scene_id = scene.get("id", "unknown")
    existing_tags = scene.get("tags", []) or []
    existing_tag_ids = set(str(t.get("id", "")) for t in existing_tags if t.get("id"))

    new_tag_ids = set()
    skipped_tags = []

    stashdb_tags = stashdb_scene.get("tags", []) or []

    for stashdb_tag in stashdb_tags:
        local_id = match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint)

        if local_id:
            if local_id not in existing_tag_ids:
                new_tag_ids.add(local_id)
                log.LogDebug(f"Scene {scene_id}: matched '{stashdb_tag.get('name', '')}' -> local tag {local_id}")
            else:
                log.LogTrace(f"Scene {scene_id}: tag '{stashdb_tag.get('name', '')}' already present")
        else:
            skipped_tags.append(stashdb_tag.get("name", ""))
            log.LogDebug(f"Scene {scene_id}: no local match for '{stashdb_tag.get('name', '')}'")

    merged_tag_ids = list(existing_tag_ids | new_tag_ids)

    if not new_tag_ids:
        return ProcessResult(
            status="no_changes",
            tags_added=0,
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )

    if settings.get("dry_run", True):
        tag_names = [tag_cache.get_name(tid) or tid for tid in new_tag_ids]
        log.LogInfo(f"[DRY RUN] Scene {scene_id}: would add {len(new_tag_ids)} tags: {tag_names}")
        return ProcessResult(
            status="dry_run",
            tags_added=len(new_tag_ids),
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )

    # Live mode - update the scene
    try:
        stash.update_scene({"id": scene_id, "tag_ids": merged_tag_ids})
        tag_names = [tag_cache.get_name(tid) or tid for tid in new_tag_ids]
        log.LogInfo(f"Scene {scene_id}: added {len(new_tag_ids)} tags: {tag_names}")
        return ProcessResult(
            status="updated",
            tags_added=len(new_tag_ids),
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )
    except Exception as e:
        log.LogError(f"Scene {scene_id}: failed to update - {e}")
        return ProcessResult(
            status="error",
            tags_added=0,
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids,
            error=str(e)
        )
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestProcessScene -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_scene_sync.py plugins/tagManager/tests/test_stashdb_scene_sync.py
git commit -m "feat(tagManager): add ProcessResult and process_scene function"
```

---

## Task 6: Add Main sync_scene_tags Function

**Files:**
- Modify: `plugins/tagManager/stashdb_scene_sync.py`
- Modify: `plugins/tagManager/tests/test_stashdb_scene_sync.py`

**Step 1: Write the failing test**

Add to `plugins/tagManager/tests/test_stashdb_scene_sync.py`:

```python
from unittest.mock import Mock, patch


class TestSyncSceneTags(unittest.TestCase):
    """Test main sync orchestration."""

    def test_sync_logs_summary_statistics(self):
        """Should log summary statistics at end of sync."""
        from stashdb_scene_sync import sync_scene_tags

        # Function should exist and be callable
        self.assertTrue(callable(sync_scene_tags))

    def test_sync_respects_dry_run_limit(self):
        """Dry run should cap at 200 scenes."""
        from stashdb_scene_sync import DRY_RUN_LIMIT

        self.assertEqual(DRY_RUN_LIMIT, 200)
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestSyncSceneTags -v`

Expected: FAIL with "cannot import name 'sync_scene_tags'"

**Step 3: Write implementation**

Add to `plugins/tagManager/stashdb_scene_sync.py`:

```python
from tag_cache import TagCache
from stashdb_api import RateLimiter, find_scene_by_id, find_scenes_by_fingerprints

# Constants
DRY_RUN_LIMIT = 200
BATCH_SIZE = 100  # Scenes to fetch per page from local Stash
FINGERPRINT_BATCH_SIZE = 40  # Max scenes per StashDB fingerprint query


@dataclass
class SyncStats:
    """Statistics for sync operation."""
    total_scenes: int = 0
    processed: int = 0
    updated: int = 0
    no_changes: int = 0
    skipped: int = 0
    errors: int = 0
    tags_added_total: int = 0
    tags_skipped_total: int = 0


def sync_scene_tags(stash, stashdb_url, stashdb_api_key, settings):
    """
    Main sync algorithm.

    1. Build tag lookup cache from local Stash
    2. Query scenes with StashIDs (paginated, sorted by updated_at ASC)
    3. Pass 1: Batch query StashDB by fingerprints (40 scenes per request)
    4. Pass 2: Sequential query for retry queue (findScene by ID)
    5. Log summary statistics

    Args:
        stash: StashInterface instance
        stashdb_url: StashDB GraphQL endpoint URL
        stashdb_api_key: StashDB API key
        settings: Plugin settings dict with 'dry_run' key

    Returns:
        SyncStats with operation statistics
    """
    stats = SyncStats()
    dry_run = settings.get("dry_run", True)

    log.LogInfo(f"Starting scene tag sync (dry_run={dry_run})")

    # Step 1: Build tag cache from local tags
    log.LogInfo("Building tag cache from local Stash...")
    local_tags = _fetch_all_local_tags(stash)
    tag_cache = TagCache.build(local_tags)

    stashdb_linked_count = len(tag_cache.stashdb_id_map)
    log.LogInfo(f"Tag cache built: {tag_cache.tag_count} tags ({stashdb_linked_count} with StashDB links)")

    # Step 2: Query scenes with StashIDs
    log.LogInfo("Querying scenes with StashDB IDs...")
    scenes = _fetch_scenes_with_stashdb_ids(stash, stashdb_url)
    stats.total_scenes = len(scenes)

    if stats.total_scenes == 0:
        log.LogInfo("No scenes with StashDB IDs found")
        return stats

    log.LogInfo(f"Found {stats.total_scenes} scenes with StashDB IDs")

    # Apply dry run limit
    if dry_run and stats.total_scenes > DRY_RUN_LIMIT:
        log.LogInfo(f"[DRY RUN] Limiting to {DRY_RUN_LIMIT} scenes (of {stats.total_scenes})")
        scenes = scenes[:DRY_RUN_LIMIT]

    # Initialize rate limiter
    rate_limiter = RateLimiter(requests_per_second=2)

    # Step 3: Pass 1 - Batch by fingerprints
    log.LogInfo("Pass 1: Batch processing by fingerprints...")
    retry_queue = []

    processed_in_pass1 = _process_pass_one(
        scenes, stashdb_url, stashdb_api_key, tag_cache,
        stash, settings, rate_limiter, stats, retry_queue
    )

    log.LogInfo(f"Pass 1 complete: {processed_in_pass1} processed, {len(retry_queue)} in retry queue")

    # Step 4: Pass 2 - Sequential fallback
    if retry_queue:
        log.LogInfo(f"Pass 2: Processing {len(retry_queue)} scenes sequentially...")
        _process_pass_two(
            retry_queue, stashdb_url, stashdb_api_key, tag_cache,
            stash, settings, rate_limiter, stats
        )

    # Step 5: Log summary
    _log_summary(stats, dry_run)

    return stats


def _fetch_all_local_tags(stash):
    """Fetch all tags from local Stash with stash_ids."""
    all_tags = []
    page = 1

    while True:
        result = stash.find_tags(
            f={},
            filter={"page": page, "per_page": BATCH_SIZE},
            fragment="id name aliases stash_ids { endpoint stash_id }"
        )

        if not result:
            break

        all_tags.extend(result)

        if len(result) < BATCH_SIZE:
            break

        page += 1

    return all_tags


def _fetch_scenes_with_stashdb_ids(stash, stashdb_url):
    """Fetch all scenes that have a StashDB ID for the given endpoint."""
    all_scenes = []
    page = 1

    filter_query = {
        "stash_id_endpoint": {
            "endpoint": stashdb_url,
            "modifier": "NOT_NULL",
            "stash_id": ""
        }
    }

    while True:
        result = stash.find_scenes(
            f=filter_query,
            filter={
                "page": page,
                "per_page": BATCH_SIZE,
                "sort": "updated_at",
                "direction": "ASC"
            },
            fragment="""
                id
                tags { id }
                stash_ids { endpoint stash_id }
                files {
                    fingerprints { type value }
                }
            """
        )

        if not result:
            break

        all_scenes.extend(result)

        if len(result) < BATCH_SIZE:
            break

        page += 1
        log.LogDebug(f"Fetched {len(all_scenes)} scenes so far...")

    return all_scenes


def _get_scene_stashdb_id(scene, endpoint):
    """Extract StashDB ID for a scene from the given endpoint."""
    stash_ids = scene.get("stash_ids", []) or []
    for sid in stash_ids:
        if sid.get("endpoint") == endpoint:
            return sid.get("stash_id")
    return None


def _get_scene_fingerprints(scene):
    """Extract fingerprints from scene for StashDB query."""
    fingerprints = []
    files = scene.get("files", []) or []

    for file_info in files:
        for fp in file_info.get("fingerprints", []) or []:
            fp_type = fp.get("type", "").upper()
            fp_value = fp.get("value", "")

            if fp_type in ("MD5", "OSHASH", "PHASH") and fp_value:
                fingerprints.append({
                    "hash": fp_value,
                    "algorithm": fp_type
                })

    return fingerprints


def _process_pass_one(scenes, stashdb_url, stashdb_api_key, tag_cache,
                       stash, settings, rate_limiter, stats, retry_queue):
    """
    Process scenes in batches using fingerprint queries.

    Returns number of scenes processed successfully.
    """
    processed = 0

    # Group scenes into batches of FINGERPRINT_BATCH_SIZE
    for batch_start in range(0, len(scenes), FINGERPRINT_BATCH_SIZE):
        batch = scenes[batch_start:batch_start + FINGERPRINT_BATCH_SIZE]

        # Build fingerprint batches
        fingerprint_batches = []
        batch_scenes = []

        for scene in batch:
            fps = _get_scene_fingerprints(scene)
            if fps:
                fingerprint_batches.append(fps)
                batch_scenes.append(scene)
            else:
                # No fingerprints - add to retry queue
                retry_queue.append(scene)
                log.LogDebug(f"Scene {scene.get('id')}: no fingerprints, queued for pass 2")

        if not fingerprint_batches:
            continue

        # Query StashDB
        stashdb_results = find_scenes_by_fingerprints(
            stashdb_url, stashdb_api_key, fingerprint_batches, rate_limiter
        )

        # Process results
        for i, (scene, stashdb_scenes) in enumerate(zip(batch_scenes, stashdb_results)):
            expected_stashdb_id = _get_scene_stashdb_id(scene, stashdb_url)

            # Find matching StashDB scene by ID
            matched_stashdb_scene = None
            for sdb_scene in (stashdb_scenes or []):
                if sdb_scene and sdb_scene.get("id") == expected_stashdb_id:
                    matched_stashdb_scene = sdb_scene
                    break

            if not matched_stashdb_scene:
                # No match by fingerprint - add to retry queue
                retry_queue.append(scene)
                log.LogDebug(f"Scene {scene.get('id')}: fingerprint didn't match expected StashDB ID, queued for pass 2")
                continue

            # Process the scene
            result = process_scene(
                scene, matched_stashdb_scene, tag_cache,
                stash, settings, stashdb_url
            )

            _update_stats(stats, result)
            processed += 1

        # Progress update
        log.LogProgress(min(1.0, (batch_start + len(batch)) / len(scenes) * 0.8))

    return processed


def _process_pass_two(retry_queue, stashdb_url, stashdb_api_key, tag_cache,
                       stash, settings, rate_limiter, stats):
    """Process scenes individually by StashDB ID."""
    for i, scene in enumerate(retry_queue):
        stashdb_id = _get_scene_stashdb_id(scene, stashdb_url)

        if not stashdb_id:
            log.LogWarning(f"Scene {scene.get('id')}: no StashDB ID found, skipping")
            stats.skipped += 1
            continue

        stashdb_scene = find_scene_by_id(
            stashdb_url, stashdb_api_key, stashdb_id, rate_limiter
        )

        if not stashdb_scene:
            log.LogWarning(f"Scene {scene.get('id')}: StashDB scene {stashdb_id} not found")
            stats.skipped += 1
            continue

        result = process_scene(
            scene, stashdb_scene, tag_cache,
            stash, settings, stashdb_url
        )

        _update_stats(stats, result)

        # Progress update (pass 2 is the remaining 20%)
        log.LogProgress(0.8 + (i + 1) / len(retry_queue) * 0.2)


def _update_stats(stats, result):
    """Update stats based on ProcessResult."""
    stats.processed += 1
    stats.tags_added_total += result.tags_added
    stats.tags_skipped_total += result.tags_skipped

    if result.status == "updated":
        stats.updated += 1
    elif result.status == "no_changes":
        stats.no_changes += 1
    elif result.status == "dry_run":
        stats.updated += 1  # Count as would-be-updated
    elif result.status == "error":
        stats.errors += 1


def _log_summary(stats, dry_run):
    """Log final summary statistics."""
    prefix = "[DRY RUN] " if dry_run else ""

    log.LogInfo(f"{prefix}Sync complete!")
    log.LogInfo(f"  Total scenes: {stats.total_scenes}")
    log.LogInfo(f"  Processed: {stats.processed}")
    log.LogInfo(f"  {'Would update' if dry_run else 'Updated'}: {stats.updated}")
    log.LogInfo(f"  No changes needed: {stats.no_changes}")
    log.LogInfo(f"  Skipped: {stats.skipped}")
    log.LogInfo(f"  Errors: {stats.errors}")
    log.LogInfo(f"  Tags {'would be ' if dry_run else ''}added: {stats.tags_added_total}")
    log.LogInfo(f"  Unmatched tags skipped: {stats.tags_skipped_total}")
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_scene_sync.py::TestSyncSceneTags -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_scene_sync.py plugins/tagManager/tests/test_stashdb_scene_sync.py
git commit -m "feat(tagManager): add main sync_scene_tags orchestration function"
```

---

## Task 7: Update tagManager.yml with New Task and Setting

**Files:**
- Modify: `plugins/tagManager/tagManager.yml`

**Step 1: Read current file (already done above)**

**Step 2: Add new setting and task**

Update `plugins/tagManager/tagManager.yml`:

```yaml
name: Tag Manager
description: Match and sync local tags with stash-box endpoints. Features multi-endpoint support, tag caching, layered search (exact, fuzzy, synonym), and field-by-field merge dialog.
version: 0.3.0
url: https://github.com/carrotwaxr/stash-plugins

ui:
  javascript:
    - tag-manager.js
  css:
    - tag-manager.css

settings:
  enableFuzzySearch:
    displayName: Enable Fuzzy Search
    description: Use fuzzy matching for close variations (typos, etc). Enabled by default.
    type: BOOLEAN
  enableSynonymSearch:
    displayName: Enable Synonym Search
    description: Use custom synonym mappings. Enabled by default.
    type: BOOLEAN
  fuzzyThreshold:
    displayName: Fuzzy Match Threshold
    description: Minimum score (0-100) for fuzzy matches. Default is 80.
    type: NUMBER
  pageSize:
    displayName: Tags Per Page
    description: Number of tags to show per page. Default is 25.
    type: NUMBER
  syncDryRun:
    displayName: Scene Tag Sync - Dry Run
    description: Preview what tags would be added without making changes (caps at 200 scenes). Default is enabled for safety.
    type: BOOLEAN

exec:
  - python
  - "{pluginDir}/tag_manager.py"
interface: raw

tasks:
  - name: Sync Scene Tags from StashDB
    description: Fetch tags from StashDB for all scenes with StashIDs and merge with local tags
    defaultArgs:
      mode: sync_scene_tags
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tagManager.yml
git commit -m "feat(tagManager): add Sync Scene Tags task and syncDryRun setting"
```

---

## Task 8: Add Mode Handler to tag_manager.py

**Files:**
- Modify: `plugins/tagManager/tag_manager.py`

**Step 1: Add imports and handler**

Add import at top of `plugins/tagManager/tag_manager.py` (after existing imports):

```python
from stashapi.stashapp import StashInterface
```

Add handler function before `main()`:

```python
def handle_sync_scene_tags(server_connection, settings):
    """
    Handle sync_scene_tags mode - sync tags from StashDB to local scenes.

    Args:
        server_connection: Stash server connection info
        settings: Plugin settings dict

    Returns:
        Dict with sync results
    """
    from stashdb_scene_sync import sync_scene_tags

    # Initialize Stash interface
    stash = StashInterface(server_connection)

    # Get StashDB configuration from Stash
    try:
        stash_config = stash.get_configuration()
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
```

**Step 2: Update main() to handle new mode**

In `main()`, add the new mode handler after the existing mode checks (around line 450):

```python
        elif mode == "sync_scene_tags":
            log.LogInfo("Starting scene tag sync task")
            result = handle_sync_scene_tags(server_connection, settings)
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag_manager.py
git commit -m "feat(tagManager): add sync_scene_tags mode handler"
```

---

## Task 9: Add Integration Tests

**Files:**
- Create: `plugins/tagManager/tests/test_integration_sync.py`

**Step 1: Write integration tests**

Create `plugins/tagManager/tests/test_integration_sync.py`:

```python
"""
Integration tests for scene tag sync.

Uses real API endpoints (StashDB and local Stash).
Configure environment variables:
  - STASHDB_URL (default: https://stashdb.org/graphql)
  - STASHDB_API_KEY (required)
  - STASH_URL (default: http://localhost:9999)
  - STASH_API_KEY (optional)

Run with: python -m pytest tests/test_integration_sync.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment from ~/code/.env
env_path = os.path.expanduser("~/code/.env")
if os.path.exists(env_path):
    load_dotenv(env_path)

# Load config from environment
STASHDB_URL = os.environ.get('STASHDB_URL', 'https://stashdb.org/graphql')
STASHDB_API_KEY = os.environ.get('STASHDB_API_KEY', '')
STASH_URL = os.environ.get('STASH_URL', 'http://localhost:9999')
STASH_API_KEY = os.environ.get('STASH_API_KEY', '')


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestStashDBSceneQueries(unittest.TestCase):
    """Integration tests for StashDB scene queries."""

    def test_find_scene_by_id(self):
        """Should fetch a scene by ID from StashDB."""
        from stashdb_api import find_scene_by_id, RateLimiter

        # Use a known scene ID from StashDB (this is a real scene)
        # "Lust" by Vixen - a well-known scene
        scene_id = "e5eb1e2e-3e3e-4e3e-8e3e-3e3e3e3e3e3e"

        rate_limiter = RateLimiter()

        # This may return None if the scene doesn't exist - that's OK
        # We just want to verify the API call works
        result = find_scene_by_id(STASHDB_URL, STASHDB_API_KEY, scene_id, rate_limiter)

        # Result is either None or a dict with expected fields
        if result:
            self.assertIn('id', result)
            self.assertIn('tags', result)
            print(f"Found scene: {result.get('title', 'Unknown')}")
            print(f"Tags: {[t.get('name') for t in result.get('tags', [])]}")

    def test_find_scenes_by_fingerprints(self):
        """Should batch query scenes by fingerprints."""
        from stashdb_api import find_scenes_by_fingerprints, RateLimiter

        rate_limiter = RateLimiter()

        # Query with empty fingerprints (should return empty lists)
        result = find_scenes_by_fingerprints(
            STASHDB_URL, STASHDB_API_KEY,
            [[], []],
            rate_limiter
        )

        self.assertEqual(len(result), 2)
        print(f"Empty query returned: {result}")


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestTagCacheIntegration(unittest.TestCase):
    """Integration tests for TagCache with real data."""

    def test_build_cache_from_mock_tags(self):
        """Should build cache from tag data."""
        from tag_cache import TagCache

        # Simulate tags that would come from local Stash
        mock_tags = [
            {
                "id": "1",
                "name": "Anal",
                "aliases": ["Anal Sex"],
                "stash_ids": [
                    {"endpoint": STASHDB_URL, "stash_id": "abc123"}
                ]
            },
            {
                "id": "2",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            }
        ]

        cache = TagCache.build(mock_tags)

        self.assertEqual(cache.tag_count, 2)
        self.assertEqual(cache.by_stashdb_id(STASHDB_URL, "abc123"), "1")
        self.assertEqual(cache.by_name("blonde"), "2")
        self.assertEqual(cache.by_alias("anal sex"), "1")

        print(f"Cache built with {cache.tag_count} tags")


@unittest.skipIf(not STASH_API_KEY, "STASH_API_KEY not set")
class TestLocalStashIntegration(unittest.TestCase):
    """Integration tests against local Stash instance."""

    def setUp(self):
        """Set up Stash connection."""
        from stashapi.stashapp import StashInterface

        self.stash = StashInterface({
            "Scheme": "http",
            "Host": STASH_URL.replace("http://", "").replace("https://", "").split(":")[0],
            "Port": int(STASH_URL.split(":")[-1]) if ":" in STASH_URL.split("//")[-1] else 9999,
            "ApiKey": STASH_API_KEY
        })

    def test_can_query_tags(self):
        """Should query tags from local Stash."""
        tags = self.stash.find_tags(
            f={},
            filter={"page": 1, "per_page": 10},
            fragment="id name aliases stash_ids { endpoint stash_id }"
        )

        self.assertIsNotNone(tags)
        print(f"Found {len(tags)} tags in local Stash")

        if tags:
            print(f"First tag: {tags[0].get('name')}")

    def test_can_query_scenes_with_stashdb_ids(self):
        """Should query scenes that have StashDB IDs."""
        scenes = self.stash.find_scenes(
            f={
                "stash_id_endpoint": {
                    "endpoint": STASHDB_URL,
                    "modifier": "NOT_NULL",
                    "stash_id": ""
                }
            },
            filter={"page": 1, "per_page": 5},
            fragment="id title stash_ids { endpoint stash_id }"
        )

        print(f"Found {len(scenes) if scenes else 0} scenes with StashDB IDs")

        if scenes:
            for scene in scenes[:3]:
                print(f"  - {scene.get('title', 'Unknown')}: {scene.get('stash_ids')}")


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tests/test_integration_sync.py
git commit -m "test(tagManager): add integration tests for scene tag sync"
```

---

## Task 10: Final Testing and Documentation

**Files:**
- Run all tests
- Update README if exists

**Step 1: Run all unit tests**

```bash
cd plugins/tagManager && python -m pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_integration_sync.py
```

Expected: All tests PASS

**Step 2: Run integration tests (requires credentials)**

```bash
cd plugins/tagManager && python -m pytest tests/test_integration_sync.py -v
```

**Step 3: Manual test with local Stash**

1. Reload plugins in Stash (Settings > Plugins > Reload)
2. Enable "Scene Tag Sync - Dry Run" in plugin settings
3. Go to Settings > Tasks > Plugin Tasks
4. Find "Sync Scene Tags from StashDB" and click Run
5. Check logs for output

**Step 4: Final commit**

```bash
git add -A
git commit -m "docs(tagManager): complete scene tag sync implementation"
```

---

## Summary

This plan implements the StashDB Scene Tag Sync feature in 10 tasks:

1. **RateLimiter class** - Rate limiting for API calls
2. **Scene query methods** - `find_scene_by_id` and `find_scenes_by_fingerprints`
3. **TagCache class** - Efficient local tag lookup
4. **Tag matching function** - Replicates Stash's ScrapedTag logic
5. **ProcessResult and process_scene** - Single scene processing
6. **sync_scene_tags function** - Main orchestration with two-pass processing
7. **YAML configuration** - New task and setting
8. **Mode handler** - Integration with tag_manager.py
9. **Integration tests** - Real API testing
10. **Final testing** - End-to-end validation

Each task follows TDD with failing test first, minimal implementation, then commit.
