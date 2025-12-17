# tagManager Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a Stash plugin that matches local tags to StashDB tags, with a paginated list UI, layered search (exact/fuzzy/synonym), and a diff dialog for merging fields.

**Architecture:** Hybrid Python backend + JavaScript UI. Python handles StashDB API calls and matching logic (thefuzz for fuzzy matching). JS registers a custom route `/plugin/tag-manager` with a paginated list view. One-click accept for high-confidence matches, expandable modal for manual search.

**Tech Stack:** Python 3.11+, thefuzz library, vanilla JavaScript (using PluginApi), StashDB GraphQL API

---

## Task 1: Create Plugin Directory Structure

**Files:**
- Create: `plugins/tagManager/tagManager.yml`
- Create: `plugins/tagManager/log.py`
- Create: `plugins/tagManager/tag_manager.py`
- Create: `plugins/tagManager/tag-manager.js`
- Create: `plugins/tagManager/tag-manager.css`
- Create: `plugins/tagManager/synonyms.json`

**Step 1: Create plugin manifest**

```yaml
# plugins/tagManager/tagManager.yml
name: Tag Manager
description: Match and sync local tags with StashDB. Features layered search (exact, fuzzy, synonym) and field-by-field merge dialog.
version: 0.1.0
url: https://github.com/carrotwaxr/stash-plugins

ui:
  javascript:
    - tag-manager.js
  css:
    - tag-manager.css

settings:
  stashdbEndpoint:
    displayName: StashDB Endpoint
    description: StashDB GraphQL endpoint URL. Default is https://stashdb.org/graphql
    type: STRING
  stashdbApiKey:
    displayName: StashDB API Key
    description: Your StashDB API key for authentication
    type: STRING
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

exec:
  - python
  - "{pluginDir}/tag_manager.py"
interface: raw
```

**Step 2: Create log.py (copy from existing plugin)**

```python
# plugins/tagManager/log.py
"""
Stash plugin logging module.
Log messages are transmitted via stderr with special character encoding.
"""
import sys


def __prefix(level_char):
    start_level_char = b'\x01'
    end_level_char = b'\x02'
    ret = start_level_char + level_char + end_level_char
    return ret.decode()


def __log(level_char, s):
    if level_char == "":
        return
    print(__prefix(level_char) + s + "\n", file=sys.stderr, flush=True)


def LogTrace(s):
    __log(b't', s)


def LogDebug(s):
    __log(b'd', s)


def LogInfo(s):
    __log(b'i', s)


def LogWarning(s):
    __log(b'w', s)


def LogError(s):
    __log(b'e', s)


def LogProgress(p):
    """Log progress (0.0 to 1.0)"""
    progress = min(max(0, p), 1)
    __log(b'p', str(progress))
```

**Step 3: Create empty synonyms.json**

```json
{
  "_comment": "Custom synonym mappings for tag matching. Keys are local tag names, values are arrays of StashDB equivalents.",
  "synonyms": {}
}
```

**Step 4: Create placeholder files**

Create empty `tag_manager.py`, `tag-manager.js`, and `tag-manager.css` files (will be populated in subsequent tasks).

**Step 5: Verify directory structure**

Run: `ls -la plugins/tagManager/`

Expected: All 6 files present

**Step 6: Commit**

```bash
git add plugins/tagManager/
git commit -m "feat(tagManager): scaffold plugin directory structure"
```

---

## Task 2: Implement StashDB Tag Fetching

**Files:**
- Create: `plugins/tagManager/stashdb_api.py`
- Test: `plugins/tagManager/tests/test_stashdb_api.py`

**Step 1: Write the failing test for StashDB tag query**

```python
# plugins/tagManager/tests/test_stashdb_api.py
"""Tests for StashDB API module."""
import json
import unittest
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stashdb_api import query_all_tags, search_tags_by_name


class TestQueryAllTags(unittest.TestCase):
    """Test fetching all tags from StashDB."""

    @patch('stashdb_api.urllib.request.urlopen')
    def test_query_all_tags_returns_list(self, mock_urlopen):
        """Should return a list of tags with expected fields."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": {
                "queryTags": {
                    "count": 2,
                    "tags": [
                        {
                            "id": "abc123",
                            "name": "Anal",
                            "description": "Anal sex",
                            "aliases": ["Anal Sex"],
                            "category": {"id": "cat1", "name": "Action", "group": "ACTION"}
                        },
                        {
                            "id": "def456",
                            "name": "Blowjob",
                            "description": "Oral sex on male",
                            "aliases": ["BJ", "Oral"],
                            "category": {"id": "cat1", "name": "Action", "group": "ACTION"}
                        }
                    ]
                }
            }
        }).encode('utf-8')
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        tags = query_all_tags("https://stashdb.org/graphql", "fake-api-key")

        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]["name"], "Anal")
        self.assertEqual(tags[1]["aliases"], ["BJ", "Oral"])


class TestSearchTagsByName(unittest.TestCase):
    """Test searching tags by name."""

    @patch('stashdb_api.urllib.request.urlopen')
    def test_search_finds_exact_match(self, mock_urlopen):
        """Should find tag by exact name match."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": {
                "queryTags": {
                    "count": 1,
                    "tags": [
                        {
                            "id": "abc123",
                            "name": "Ankle Bracelet",
                            "description": "Jewelry worn on ankle",
                            "aliases": ["Anklet", "Anklets"],
                            "category": {"id": "cat2", "name": "Clothing", "group": "SCENE"}
                        }
                    ]
                }
            }
        }).encode('utf-8')
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        tags = search_tags_by_name("https://stashdb.org/graphql", "fake-api-key", "Anklet")

        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]["name"], "Ankle Bracelet")
        self.assertIn("Anklet", tags[0]["aliases"])


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py -v`

Expected: FAIL with "No module named 'stashdb_api'"

**Step 3: Write minimal implementation**

```python
# plugins/tagManager/stashdb_api.py
"""
StashDB API utilities for tag queries.

Features:
- Fetch all tags with pagination
- Search tags by name (uses StashDB's names filter which searches name + aliases)
- Retry with exponential backoff for transient errors
"""

import json
import ssl
import time
import urllib.request
import urllib.error

import log

# SSL context for HTTPS requests
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Default configuration
DEFAULT_CONFIG = {
    "max_retries": 3,
    "initial_retry_delay": 1.0,
    "max_retry_delay": 30.0,
    "retry_backoff_multiplier": 2.0,
    "request_delay": 0.3,
    "request_timeout": 30,
    "per_page": 100,
}

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class StashDBAPIError(Exception):
    """Exception for StashDB API errors."""

    def __init__(self, message, status_code=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def graphql_request(url, query, variables=None, api_key=None, timeout=30):
    """
    Make a GraphQL request to StashDB.

    Args:
        url: GraphQL endpoint URL
        query: GraphQL query string
        variables: Query variables dict
        api_key: API key for authentication
        timeout: Request timeout in seconds

    Returns:
        Response data dict

    Raises:
        StashDBAPIError: On request failure
    """
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

    max_retries = DEFAULT_CONFIG["max_retries"]
    delay = DEFAULT_CONFIG["initial_retry_delay"]

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as response:
                result = json.loads(response.read().decode("utf-8"))

                if "errors" in result:
                    error_messages = [e.get("message", str(e)) for e in result["errors"]]
                    log.LogWarning(f"GraphQL errors: {error_messages}")

                return result.get("data")

        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                log.LogWarning(f"HTTP {e.code}, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            raise StashDBAPIError(f"HTTP {e.code}: {e.reason}", status_code=e.code)

        except urllib.error.URLError as e:
            if attempt < max_retries:
                log.LogWarning(f"Connection error: {e.reason}, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * DEFAULT_CONFIG["retry_backoff_multiplier"], DEFAULT_CONFIG["max_retry_delay"])
                continue

            raise StashDBAPIError(f"Connection failed: {e.reason}")

    raise StashDBAPIError("Max retries exceeded")


# GraphQL query fragments
TAG_FIELDS = """
    id
    name
    description
    aliases
    category {
        id
        name
        group
    }
"""


def query_all_tags(url, api_key, per_page=100):
    """
    Fetch all tags from StashDB with pagination.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        per_page: Results per page (default 100)

    Returns:
        List of all tags
    """
    query = f"""
    query QueryTags($input: TagQueryInput!) {{
        queryTags(input: $input) {{
            count
            tags {{
                {TAG_FIELDS}
            }}
        }}
    }}
    """

    all_tags = []
    page = 1

    while True:
        variables = {
            "input": {
                "page": page,
                "per_page": per_page,
                "sort": "NAME",
                "direction": "ASC"
            }
        }

        try:
            data = graphql_request(url, query, variables, api_key)
        except StashDBAPIError as e:
            log.LogWarning(f"Error fetching tags page {page}: {e}")
            break

        if not data:
            break

        query_data = data.get("queryTags", {})
        tags = query_data.get("tags", [])
        total = query_data.get("count", 0)

        if not tags:
            break

        all_tags.extend(tags)

        log.LogDebug(f"Tags: page {page}, got {len(tags)} (total: {total}, collected: {len(all_tags)})")

        if len(all_tags) >= total:
            break

        page += 1
        time.sleep(DEFAULT_CONFIG["request_delay"])

    log.LogInfo(f"StashDB: Fetched {len(all_tags)} tags total")
    return all_tags


def search_tags_by_name(url, api_key, search_term, limit=50):
    """
    Search StashDB tags by name.

    Uses the 'names' filter which searches both tag names and aliases.
    e.g., searching "Anklet" will find "Ankle Bracelet" because "Anklet" is an alias.

    Args:
        url: StashDB GraphQL endpoint
        api_key: StashDB API key
        search_term: Search term
        limit: Maximum results to return

    Returns:
        List of matching tags
    """
    query = f"""
    query QueryTags($input: TagQueryInput!) {{
        queryTags(input: $input) {{
            count
            tags {{
                {TAG_FIELDS}
            }}
        }}
    }}
    """

    variables = {
        "input": {
            "names": search_term,
            "page": 1,
            "per_page": limit,
            "sort": "NAME",
            "direction": "ASC"
        }
    }

    try:
        data = graphql_request(url, query, variables, api_key)
    except StashDBAPIError as e:
        log.LogWarning(f"Error searching tags for '{search_term}': {e}")
        return []

    if not data:
        return []

    tags = data.get("queryTags", {}).get("tags", [])
    log.LogDebug(f"Search '{search_term}': found {len(tags)} tags")
    return tags
```

**Step 4: Create tests directory and __init__.py**

```bash
mkdir -p plugins/tagManager/tests
touch plugins/tagManager/tests/__init__.py
```

**Step 5: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_stashdb_api.py -v`

Expected: PASS (2 tests)

**Step 6: Commit**

```bash
git add plugins/tagManager/stashdb_api.py plugins/tagManager/tests/
git commit -m "feat(tagManager): implement StashDB tag API client"
```

---

## Task 3: Implement Fuzzy Matching Logic

**Files:**
- Create: `plugins/tagManager/matcher.py`
- Test: `plugins/tagManager/tests/test_matcher.py`

**Step 1: Write the failing test for matching logic**

```python
# plugins/tagManager/tests/test_matcher.py
"""Tests for tag matching logic."""
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matcher import TagMatcher


class TestTagMatcher(unittest.TestCase):
    """Test tag matching functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.stashdb_tags = [
            {
                "id": "abc123",
                "name": "Anal Creampie",
                "description": "Scene ends with creampie in anus",
                "aliases": ["Anal Cream Pie", "Creampie Anal"],
                "category": {"name": "Action"}
            },
            {
                "id": "def456",
                "name": "Ankle Bracelet",
                "description": "Jewelry worn on ankle",
                "aliases": ["Anklet", "Anklets"],
                "category": {"name": "Clothing"}
            },
            {
                "id": "ghi789",
                "name": "Cowgirl",
                "description": "Sex position with woman on top",
                "aliases": ["Cowgirl Position", "Girl on Top"],
                "category": {"name": "Position"}
            },
        ]
        self.matcher = TagMatcher(self.stashdb_tags)

    def test_exact_name_match(self):
        """Should find exact name match with high confidence."""
        matches = self.matcher.find_matches("Anal Creampie")

        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["tag"]["name"], "Anal Creampie")
        self.assertEqual(matches[0]["match_type"], "exact")
        self.assertEqual(matches[0]["score"], 100)

    def test_alias_match(self):
        """Should find match via alias."""
        matches = self.matcher.find_matches("Anklet")

        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["tag"]["name"], "Ankle Bracelet")
        self.assertEqual(matches[0]["match_type"], "alias")
        self.assertEqual(matches[0]["score"], 100)

    def test_fuzzy_match(self):
        """Should find fuzzy match for close variations."""
        matches = self.matcher.find_matches("Anal Creampies")  # plural

        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["tag"]["name"], "Anal Creampie")
        self.assertEqual(matches[0]["match_type"], "fuzzy")
        self.assertGreater(matches[0]["score"], 80)

    def test_no_match(self):
        """Should return empty list for no match."""
        matches = self.matcher.find_matches("Nonexistent Tag XYZ")

        self.assertEqual(len(matches), 0)

    def test_case_insensitive(self):
        """Should match regardless of case."""
        matches = self.matcher.find_matches("COWGIRL")

        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]["tag"]["name"], "Cowgirl")

    def test_synonym_match(self):
        """Should find match via custom synonym mapping."""
        synonyms = {"Girl on Top": ["Cowgirl"]}
        matcher = TagMatcher(self.stashdb_tags, synonyms=synonyms)

        matches = matcher.find_matches("Girl on Top")

        self.assertGreater(len(matches), 0)
        # Could match via alias OR synonym - just verify we get Cowgirl
        self.assertEqual(matches[0]["tag"]["name"], "Cowgirl")


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_matcher.py -v`

Expected: FAIL with "No module named 'matcher'"

**Step 3: Write minimal implementation**

```python
# plugins/tagManager/matcher.py
"""
Tag matching logic with layered search strategy.

Search order:
1. Exact name match (case-insensitive)
2. Alias match (case-insensitive)
3. Synonym match (from custom mapping)
4. Fuzzy match (using thefuzz library)

Each match includes:
- tag: The matched StashDB tag
- match_type: "exact", "alias", "synonym", or "fuzzy"
- score: Confidence score (0-100)
- matched_on: What string was matched (for display)
"""

import log

# Try to import thefuzz, fall back to basic matching if not available
try:
    from thefuzz import fuzz
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    log.LogWarning("thefuzz not installed - fuzzy matching disabled. Install with: pip install thefuzz")


class TagMatcher:
    """
    Matches local tag names to StashDB tags using layered search.
    """

    def __init__(self, stashdb_tags, synonyms=None, fuzzy_threshold=80):
        """
        Initialize matcher with StashDB tags.

        Args:
            stashdb_tags: List of StashDB tag dicts
            synonyms: Dict mapping local names to StashDB tag names
            fuzzy_threshold: Minimum score (0-100) for fuzzy matches
        """
        self.stashdb_tags = stashdb_tags
        self.synonyms = synonyms or {}
        self.fuzzy_threshold = fuzzy_threshold

        # Build lookup indexes for fast matching
        self._build_indexes()

    def _build_indexes(self):
        """Build indexes for fast exact and alias matching."""
        # Index by lowercase name
        self.name_index = {}
        # Index by lowercase alias
        self.alias_index = {}

        for tag in self.stashdb_tags:
            name_lower = tag["name"].lower()
            self.name_index[name_lower] = tag

            for alias in tag.get("aliases", []):
                alias_lower = alias.lower()
                # Don't overwrite if already exists (first tag wins)
                if alias_lower not in self.alias_index:
                    self.alias_index[alias_lower] = tag

    def find_matches(self, local_tag_name, enable_fuzzy=True, enable_synonyms=True, limit=10):
        """
        Find matching StashDB tags for a local tag name.

        Args:
            local_tag_name: The local tag name to match
            enable_fuzzy: Whether to use fuzzy matching
            enable_synonyms: Whether to use synonym mapping
            limit: Maximum matches to return

        Returns:
            List of match dicts sorted by score (highest first):
            [
                {
                    "tag": {...},  # StashDB tag
                    "match_type": "exact|alias|synonym|fuzzy",
                    "score": 0-100,
                    "matched_on": "string that matched"
                },
                ...
            ]
        """
        matches = []
        search_term = local_tag_name.strip()
        search_lower = search_term.lower()

        # 1. Exact name match
        if search_lower in self.name_index:
            tag = self.name_index[search_lower]
            matches.append({
                "tag": tag,
                "match_type": "exact",
                "score": 100,
                "matched_on": tag["name"]
            })
            # For exact match, we could return early but let's still check
            # for other high-quality matches in case user wants alternatives

        # 2. Alias match
        if search_lower in self.alias_index:
            tag = self.alias_index[search_lower]
            # Don't add if already matched by exact name
            if not any(m["tag"]["id"] == tag["id"] for m in matches):
                matches.append({
                    "tag": tag,
                    "match_type": "alias",
                    "score": 100,
                    "matched_on": search_term
                })

        # 3. Synonym match
        if enable_synonyms and search_term in self.synonyms:
            synonym_targets = self.synonyms[search_term]
            if isinstance(synonym_targets, str):
                synonym_targets = [synonym_targets]

            for target in synonym_targets:
                target_lower = target.lower()
                if target_lower in self.name_index:
                    tag = self.name_index[target_lower]
                    if not any(m["tag"]["id"] == tag["id"] for m in matches):
                        matches.append({
                            "tag": tag,
                            "match_type": "synonym",
                            "score": 95,  # Slightly lower than exact/alias
                            "matched_on": target
                        })

        # 4. Fuzzy match (only if no exact/alias matches found)
        if enable_fuzzy and FUZZY_AVAILABLE and len(matches) == 0:
            fuzzy_matches = self._fuzzy_search(search_term, limit=limit)
            matches.extend(fuzzy_matches)

        # Sort by score descending
        matches.sort(key=lambda m: m["score"], reverse=True)

        return matches[:limit]

    def _fuzzy_search(self, search_term, limit=10):
        """
        Perform fuzzy matching against all tag names and aliases.

        Args:
            search_term: Term to search for
            limit: Maximum matches to return

        Returns:
            List of fuzzy match dicts
        """
        if not FUZZY_AVAILABLE:
            return []

        candidates = []

        for tag in self.stashdb_tags:
            # Check name
            name_score = fuzz.ratio(search_term.lower(), tag["name"].lower())
            if name_score >= self.fuzzy_threshold:
                candidates.append({
                    "tag": tag,
                    "match_type": "fuzzy",
                    "score": name_score,
                    "matched_on": tag["name"]
                })
                continue  # Don't also check aliases for same tag

            # Check aliases
            best_alias_score = 0
            best_alias = None
            for alias in tag.get("aliases", []):
                alias_score = fuzz.ratio(search_term.lower(), alias.lower())
                if alias_score > best_alias_score:
                    best_alias_score = alias_score
                    best_alias = alias

            if best_alias_score >= self.fuzzy_threshold:
                candidates.append({
                    "tag": tag,
                    "match_type": "fuzzy",
                    "score": best_alias_score,
                    "matched_on": best_alias
                })

        # Sort by score descending and limit
        candidates.sort(key=lambda m: m["score"], reverse=True)
        return candidates[:limit]


def load_synonyms(filepath):
    """
    Load synonym mappings from JSON file.

    Args:
        filepath: Path to synonyms.json

    Returns:
        Dict of synonym mappings
    """
    import json
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("synonyms", {})
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        log.LogWarning(f"Error parsing synonyms.json: {e}")
        return {}
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_matcher.py -v`

Expected: PASS (6 tests) - Note: synonym test may need thefuzz installed

**Step 5: Commit**

```bash
git add plugins/tagManager/matcher.py plugins/tagManager/tests/test_matcher.py
git commit -m "feat(tagManager): implement tag matching logic with fuzzy support"
```

---

## Task 4: Implement Python Backend Entry Point

**Files:**
- Modify: `plugins/tagManager/tag_manager.py`
- Test: `plugins/tagManager/tests/test_tag_manager.py`

**Step 1: Write the failing test for the main entry point**

```python
# plugins/tagManager/tests/test_tag_manager.py
"""Tests for main tag_manager module."""
import json
import unittest
from unittest.mock import patch, MagicMock
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTagManagerModes(unittest.TestCase):
    """Test different plugin operation modes."""

    @patch('stashdb_api.graphql_request')
    def test_search_mode_returns_matches(self, mock_graphql):
        """search mode should return matching StashDB tags."""
        # Mock StashDB response
        mock_graphql.return_value = {
            "queryTags": {
                "count": 1,
                "tags": [{
                    "id": "abc123",
                    "name": "Anal Creampie",
                    "description": "Scene ends with...",
                    "aliases": ["Anal Cream Pie"],
                    "category": {"name": "Action", "group": "ACTION"}
                }]
            }
        }

        # Import after mocking
        from tag_manager import handle_search

        result = handle_search(
            tag_name="Anal Creampie",
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="fake-key",
            settings={}
        )

        self.assertIn("matches", result)
        self.assertGreater(len(result["matches"]), 0)
        self.assertEqual(result["matches"][0]["tag"]["name"], "Anal Creampie")


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `cd plugins/tagManager && python -m pytest tests/test_tag_manager.py -v`

Expected: FAIL with "cannot import name 'handle_search'"

**Step 3: Write minimal implementation**

```python
# plugins/tagManager/tag_manager.py
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
```

**Step 4: Run test to verify it passes**

Run: `cd plugins/tagManager && python -m pytest tests/test_tag_manager.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/tag_manager.py plugins/tagManager/tests/test_tag_manager.py
git commit -m "feat(tagManager): implement main plugin entry point with search and fetch modes"
```

---

## Task 5: Implement JavaScript UI - Route Registration and Basic Page

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Implement basic JS with route registration**

```javascript
// plugins/tagManager/tag-manager.js
(function () {
  "use strict";

  const PLUGIN_ID = "tagManager";
  const ROUTE_PATH = "/plugin/tag-manager";

  // Default settings
  const DEFAULTS = {
    stashdbEndpoint: "https://stashdb.org/graphql",
    stashdbApiKey: "",
    enableFuzzySearch: true,
    enableSynonymSearch: true,
    fuzzyThreshold: 80,
    pageSize: 25,
  };

  // State
  let settings = { ...DEFAULTS };
  let stashdbTags = null; // Cached StashDB tags
  let localTags = []; // Local Stash tags
  let currentPage = 1;
  let isLoading = false;
  let matchResults = {}; // Cache of tag_id -> matches

  /**
   * Get the GraphQL endpoint URL for local Stash
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request to local Stash
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
    if (result.errors?.length > 0) {
      throw new Error(result.errors[0].message);
    }

    return result.data;
  }

  /**
   * Get plugin settings from Stash configuration
   */
  async function loadSettings() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID] || {};

      settings = {
        stashdbEndpoint: pluginConfig.stashdbEndpoint || DEFAULTS.stashdbEndpoint,
        stashdbApiKey: pluginConfig.stashdbApiKey || DEFAULTS.stashdbApiKey,
        enableFuzzySearch: pluginConfig.enableFuzzySearch !== false,
        enableSynonymSearch: pluginConfig.enableSynonymSearch !== false,
        fuzzyThreshold: parseInt(pluginConfig.fuzzyThreshold) || DEFAULTS.fuzzyThreshold,
        pageSize: parseInt(pluginConfig.pageSize) || DEFAULTS.pageSize,
      };

      console.debug("[tagManager] Settings loaded:", settings);
    } catch (e) {
      console.error("[tagManager] Failed to load settings:", e);
    }
  }

  /**
   * Fetch local tags from Stash
   */
  async function fetchLocalTags() {
    const query = `
      query FindTags {
        findTags(filter: { per_page: -1 }) {
          count
          tags {
            id
            name
            description
            aliases
            stash_ids {
              endpoint
              stash_id
            }
          }
        }
      }
    `;

    const data = await graphqlRequest(query);
    return data?.findTags?.tags || [];
  }

  /**
   * Call Python backend via runPluginOperation
   */
  async function callBackend(mode, args = {}) {
    const query = `
      mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
        runPluginOperation(plugin_id: $plugin_id, args: $args)
      }
    `;

    const fullArgs = {
      mode,
      stashdb_url: settings.stashdbEndpoint,
      stashdb_api_key: settings.stashdbApiKey,
      settings: settings,
      ...args,
    };

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: fullArgs,
    });

    const output = data?.runPluginOperation;
    if (output?.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /**
   * Render the main page content
   */
  function renderPage(container) {
    const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
    const matchedTags = localTags.filter(t => t.stash_ids && t.stash_ids.length > 0);

    const totalPages = Math.ceil(unmatchedTags.length / settings.pageSize);
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = unmatchedTags.slice(startIdx, startIdx + settings.pageSize);

    container.innerHTML = `
      <div class="tag-manager">
        <div class="tag-manager-header">
          <h2>Tag Manager</h2>
          <div class="tag-manager-stats">
            <span class="stat stat-unmatched">${unmatchedTags.length} unmatched</span>
            <span class="stat stat-matched">${matchedTags.length} matched</span>
          </div>
          <button class="btn btn-secondary" id="tm-settings-btn">Settings</button>
        </div>

        <div class="tag-manager-filters">
          <select id="tm-filter" class="form-control">
            <option value="unmatched">Show Unmatched</option>
            <option value="matched">Show Matched</option>
            <option value="all">Show All</option>
          </select>
          <button class="btn btn-primary" id="tm-search-all-btn" ${isLoading ? 'disabled' : ''}>
            ${isLoading ? 'Searching...' : 'Find Matches for Page'}
          </button>
        </div>

        <div class="tag-manager-list" id="tm-tag-list">
          ${pageTags.length === 0
            ? '<div class="tm-empty">No unmatched tags found</div>'
            : pageTags.map(tag => renderTagRow(tag)).join('')
          }
        </div>

        <div class="tag-manager-pagination">
          <button class="btn btn-secondary" id="tm-prev" ${currentPage <= 1 ? 'disabled' : ''}>Previous</button>
          <span>Page ${currentPage} of ${totalPages || 1}</span>
          <button class="btn btn-secondary" id="tm-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next</button>
        </div>

        <div id="tm-status" class="tag-manager-status"></div>
      </div>
    `;

    // Attach event handlers
    attachEventHandlers(container);
  }

  /**
   * Render a single tag row
   */
  function renderTagRow(tag) {
    const matches = matchResults[tag.id];
    const hasMatches = matches && matches.length > 0;
    const bestMatch = hasMatches ? matches[0] : null;

    let matchContent = '';
    if (isLoading) {
      matchContent = '<span class="tm-loading">Searching...</span>';
    } else if (hasMatches) {
      const matchTypeClass = bestMatch.match_type === 'exact' ? 'exact' :
                             bestMatch.match_type === 'alias' ? 'alias' : 'fuzzy';
      matchContent = `
        <div class="tm-match tm-match-${matchTypeClass}">
          <span class="tm-match-name">${escapeHtml(bestMatch.tag.name)}</span>
          <span class="tm-match-type">${bestMatch.match_type} (${bestMatch.score}%)</span>
          <span class="tm-match-category">${escapeHtml(bestMatch.tag.category?.name || '')}</span>
        </div>
        <div class="tm-actions">
          <button class="btn btn-success btn-sm tm-accept" data-tag-id="${tag.id}">✓</button>
          <button class="btn btn-secondary btn-sm tm-more" data-tag-id="${tag.id}">More</button>
        </div>
      `;
    } else if (matches !== undefined) {
      matchContent = `
        <span class="tm-no-match">No matches found</span>
        <button class="btn btn-secondary btn-sm tm-search" data-tag-id="${tag.id}">Search</button>
      `;
    } else {
      matchContent = `
        <button class="btn btn-primary btn-sm tm-search" data-tag-id="${tag.id}">Find Match</button>
      `;
    }

    return `
      <div class="tm-tag-row" data-tag-id="${tag.id}">
        <div class="tm-tag-info">
          <span class="tm-tag-name">${escapeHtml(tag.name)}</span>
          ${tag.aliases?.length ? `<span class="tm-tag-aliases">${escapeHtml(tag.aliases.join(', '))}</span>` : ''}
        </div>
        <div class="tm-tag-match">
          ${matchContent}
        </div>
      </div>
    `;
  }

  /**
   * Attach event handlers to rendered elements
   */
  function attachEventHandlers(container) {
    // Pagination
    container.querySelector('#tm-prev')?.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        renderPage(container);
      }
    });

    container.querySelector('#tm-next')?.addEventListener('click', () => {
      const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
      const totalPages = Math.ceil(unmatchedTags.length / settings.pageSize);
      if (currentPage < totalPages) {
        currentPage++;
        renderPage(container);
      }
    });

    // Search all on page
    container.querySelector('#tm-search-all-btn')?.addEventListener('click', () => {
      searchAllOnPage(container);
    });

    // Individual search buttons
    container.querySelectorAll('.tm-search').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        searchSingleTag(tagId, container);
      });
    });

    // Accept buttons
    container.querySelectorAll('.tm-accept').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        showDiffDialog(tagId, container);
      });
    });

    // More buttons
    container.querySelectorAll('.tm-more').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        showMatchesModal(tagId, container);
      });
    });
  }

  /**
   * Search for matches for all tags on current page
   */
  async function searchAllOnPage(container) {
    const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = unmatchedTags.slice(startIdx, startIdx + settings.pageSize);

    isLoading = true;
    renderPage(container);

    for (const tag of pageTags) {
      try {
        const result = await callBackend('search', {
          tag_name: tag.name,
          stashdb_tags: stashdbTags,
        });
        matchResults[tag.id] = result.matches || [];
      } catch (e) {
        console.error(`[tagManager] Error searching for ${tag.name}:`, e);
        matchResults[tag.id] = [];
      }
    }

    isLoading = false;
    renderPage(container);
  }

  /**
   * Search for a single tag
   */
  async function searchSingleTag(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    if (!tag) return;

    try {
      const result = await callBackend('search', {
        tag_name: tag.name,
        stashdb_tags: stashdbTags,
      });
      matchResults[tagId] = result.matches || [];
      renderPage(container);
    } catch (e) {
      console.error(`[tagManager] Error searching for ${tag.name}:`, e);
      showStatus(`Error: ${e.message}`, 'error');
    }
  }

  /**
   * Show diff dialog for accepting a match
   */
  function showDiffDialog(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    const match = matches[0];
    // TODO: Implement full diff dialog (Task 6)
    console.log('Show diff dialog for', tag.name, 'with match', match.tag.name);
    showStatus('Diff dialog coming in next task...', 'info');
  }

  /**
   * Show modal with all matches
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    // TODO: Implement matches modal (Task 6)
    console.log('Show matches modal for', tag.name, matches);
    showStatus('Matches modal coming in next task...', 'info');
  }

  /**
   * Show status message
   */
  function showStatus(message, type = 'info') {
    const statusEl = document.getElementById('tm-status');
    if (statusEl) {
      statusEl.textContent = message;
      statusEl.className = `tag-manager-status tm-status-${type}`;
    }
  }

  /**
   * Main page component
   */
  function TagManagerPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);
    const [initialized, setInitialized] = React.useState(false);

    React.useEffect(() => {
      async function init() {
        if (!containerRef.current) return;

        await loadSettings();

        // Check if API key is configured
        if (!settings.stashdbApiKey) {
          containerRef.current.innerHTML = `
            <div class="tag-manager">
              <div class="tag-manager-error">
                <h3>StashDB API Key Required</h3>
                <p>Please configure your StashDB API key in Settings → Plugins → Tag Manager</p>
              </div>
            </div>
          `;
          return;
        }

        // Fetch local tags
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading tags...</div></div>';
        try {
          localTags = await fetchLocalTags();
        } catch (e) {
          containerRef.current.innerHTML = `<div class="tag-manager"><div class="tag-manager-error">Error loading tags: ${escapeHtml(e.message)}</div></div>`;
          return;
        }

        // Optionally fetch all StashDB tags for fuzzy matching
        // (Can be done in background for performance)
        if (settings.enableFuzzySearch) {
          try {
            const result = await callBackend('fetch_all');
            stashdbTags = result.tags || [];
            console.debug(`[tagManager] Cached ${stashdbTags.length} StashDB tags`);
          } catch (e) {
            console.warn('[tagManager] Could not cache StashDB tags:', e);
          }
        }

        setInitialized(true);
        renderPage(containerRef.current);
      }

      init();
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'tag-manager-container'
    });
  }

  /**
   * Register the route
   */
  function registerRoute() {
    PluginApi.register.route(ROUTE_PATH, TagManagerPage);
    console.log('[tagManager] Route registered:', ROUTE_PATH);
  }

  // Initialize
  registerRoute();
  console.log('[tagManager] Plugin loaded');
})();
```

**Step 2: Create basic CSS**

```css
/* plugins/tagManager/tag-manager.css */

.tag-manager-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.tag-manager {
  background: var(--bs-body-bg, #1a1a2e);
  border-radius: 8px;
  padding: 20px;
}

.tag-manager-header {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.tag-manager-header h2 {
  margin: 0;
  flex: 1;
}

.tag-manager-stats {
  display: flex;
  gap: 15px;
}

.tag-manager-stats .stat {
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 0.9em;
}

.stat-unmatched {
  background: var(--bs-warning, #ffc107);
  color: #000;
}

.stat-matched {
  background: var(--bs-success, #28a745);
  color: #fff;
}

.tag-manager-filters {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.tag-manager-filters select {
  max-width: 200px;
}

.tag-manager-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tm-tag-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--bs-secondary-bg, #2d2d44);
  border-radius: 6px;
  gap: 20px;
}

.tm-tag-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 200px;
}

.tm-tag-name {
  font-weight: 500;
  font-size: 1.1em;
}

.tm-tag-aliases {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
}

.tm-tag-match {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.tm-match {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-radius: 4px;
  background: var(--bs-tertiary-bg, #3d3d5c);
}

.tm-match-exact {
  border-left: 3px solid var(--bs-success, #28a745);
}

.tm-match-alias {
  border-left: 3px solid var(--bs-info, #17a2b8);
}

.tm-match-fuzzy {
  border-left: 3px solid var(--bs-warning, #ffc107);
}

.tm-match-name {
  font-weight: 500;
}

.tm-match-type {
  font-size: 0.8em;
  color: var(--bs-secondary-color, #888);
}

.tm-match-category {
  font-size: 0.8em;
  padding: 2px 6px;
  background: var(--bs-primary, #0d6efd);
  color: #fff;
  border-radius: 3px;
}

.tm-actions {
  display: flex;
  gap: 6px;
}

.tm-no-match {
  color: var(--bs-secondary-color, #888);
  font-style: italic;
}

.tm-loading {
  color: var(--bs-secondary-color, #888);
}

.tm-empty {
  text-align: center;
  padding: 40px;
  color: var(--bs-secondary-color, #888);
}

.tag-manager-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 20px;
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--bs-border-color, #444);
}

.tag-manager-status {
  margin-top: 15px;
  padding: 10px;
  border-radius: 4px;
  text-align: center;
}

.tm-status-info {
  background: var(--bs-info-bg-subtle, #1a3a4a);
  color: var(--bs-info, #17a2b8);
}

.tm-status-error {
  background: var(--bs-danger-bg-subtle, #4a1a1a);
  color: var(--bs-danger, #dc3545);
}

.tm-status-success {
  background: var(--bs-success-bg-subtle, #1a4a2a);
  color: var(--bs-success, #28a745);
}

.tag-manager-error {
  text-align: center;
  padding: 40px;
}

.tag-manager-error h3 {
  color: var(--bs-warning, #ffc107);
  margin-bottom: 10px;
}
```

**Step 3: Verify plugin loads**

Run: `ls -la plugins/tagManager/`

Expected: All files present with content

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): implement UI route and basic tag list page"
```

---

## Task 6: Implement Diff Dialog and Tag Update

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (add diff dialog)
- Modify: `plugins/tagManager/tag-manager.css` (add dialog styles)

**Step 1: Add diff dialog HTML and logic to JS**

Add the following functions to `tag-manager.js` (replace the placeholder `showDiffDialog` and `showMatchesModal`):

```javascript
  /**
   * Show diff dialog for accepting a match
   */
  function showDiffDialog(tagId, container, matchIndex = 0) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    const match = matches[matchIndex];
    const stashdbTag = match.tag;

    // Determine defaults: use StashDB value if local is empty
    const nameDefault = tag.name ? 'local' : 'stashdb';
    const descDefault = tag.description ? 'local' : 'stashdb';
    const aliasesDefault = tag.aliases?.length ? 'local' : 'stashdb';

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal">
        <div class="tm-modal-header">
          <h3>Match: ${escapeHtml(tag.name)} → ${escapeHtml(stashdbTag.name)}</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <div class="tm-diff-info">
            <span class="tm-match-type-badge tm-match-${match.match_type}">${match.match_type}</span>
            <span>Score: ${match.score}%</span>
            ${stashdbTag.category ? `<span>Category: ${escapeHtml(stashdbTag.category.name)}</span>` : ''}
          </div>

          <table class="tm-diff-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Your Value</th>
                <th>StashDB Value</th>
                <th>Use</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Name</td>
                <td>${escapeHtml(tag.name) || '<em>empty</em>'}</td>
                <td>${escapeHtml(stashdbTag.name)}</td>
                <td>
                  <label><input type="radio" name="tm-name" value="local" ${nameDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-name" value="stashdb" ${nameDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Description</td>
                <td>${escapeHtml(tag.description) || '<em>empty</em>'}</td>
                <td>${escapeHtml(stashdbTag.description) || '<em>empty</em>'}</td>
                <td>
                  <label><input type="radio" name="tm-desc" value="local" ${descDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-desc" value="stashdb" ${descDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Aliases</td>
                <td>${tag.aliases?.length ? escapeHtml(tag.aliases.join(', ')) : '<em>none</em>'}</td>
                <td>${stashdbTag.aliases?.length ? escapeHtml(stashdbTag.aliases.join(', ')) : '<em>none</em>'}</td>
                <td>
                  <label><input type="radio" name="tm-aliases" value="local" ${aliasesDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-aliases" value="stashdb" ${aliasesDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                  <label><input type="radio" name="tm-aliases" value="merge"> Merge</label>
                </td>
              </tr>
            </tbody>
          </table>

          <div class="tm-stashid-note">
            <strong>StashDB ID will be added:</strong> ${escapeHtml(stashdbTag.id)}
          </div>
        </div>
        <div class="tm-modal-footer">
          <button class="btn btn-secondary tm-cancel-btn">Cancel</button>
          <button class="btn btn-primary tm-apply-btn">Apply</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Event handlers
    modal.querySelector('.tm-close-btn').addEventListener('click', () => modal.remove());
    modal.querySelector('.tm-cancel-btn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    modal.querySelector('.tm-apply-btn').addEventListener('click', async () => {
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;
      const aliasesChoice = modal.querySelector('input[name="tm-aliases"]:checked').value;

      // Build update input
      const updateInput = {
        id: tag.id,
        stash_ids: [{
          endpoint: settings.stashdbEndpoint,
          stash_id: stashdbTag.id,
        }],
      };

      if (nameChoice === 'stashdb') {
        updateInput.name = stashdbTag.name;
      }

      if (descChoice === 'stashdb') {
        updateInput.description = stashdbTag.description || '';
      }

      if (aliasesChoice === 'stashdb') {
        updateInput.aliases = stashdbTag.aliases || [];
      } else if (aliasesChoice === 'merge') {
        const merged = new Set([...(tag.aliases || []), ...(stashdbTag.aliases || [])]);
        updateInput.aliases = Array.from(merged);
      }

      try {
        await updateTag(updateInput);
        modal.remove();

        // Update local state
        const idx = localTags.findIndex(t => t.id === tag.id);
        if (idx >= 0) {
          localTags[idx].stash_ids = updateInput.stash_ids;
          if (updateInput.name) localTags[idx].name = updateInput.name;
          if (updateInput.description !== undefined) localTags[idx].description = updateInput.description;
          if (updateInput.aliases) localTags[idx].aliases = updateInput.aliases;
        }
        delete matchResults[tag.id];

        showStatus(`Matched "${tag.name}" to "${stashdbTag.name}"`, 'success');
        renderPage(container);
      } catch (e) {
        showStatus(`Error: ${e.message}`, 'error');
      }
    });
  }

  /**
   * Update a tag via GraphQL
   */
  async function updateTag(input) {
    const query = `
      mutation TagUpdate($input: TagUpdateInput!) {
        tagUpdate(input: $input) {
          id
          name
          stash_ids {
            endpoint
            stash_id
          }
        }
      }
    `;

    const data = await graphqlRequest(query, { input });
    return data?.tagUpdate;
  }

  /**
   * Show modal with all matches for manual selection
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag) return;

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal tm-modal-wide">
        <div class="tm-modal-header">
          <h3>Matches for: ${escapeHtml(tag.name)}</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <div class="tm-search-row">
            <input type="text" id="tm-manual-search" class="form-control" placeholder="Search StashDB..." value="${escapeHtml(tag.name)}">
            <button class="btn btn-primary" id="tm-manual-search-btn">Search</button>
          </div>
          <div class="tm-matches-list" id="tm-matches-list">
            ${matches?.length
              ? matches.map((m, i) => `
                <div class="tm-match-item" data-index="${i}">
                  <div class="tm-match-info">
                    <span class="tm-match-name">${escapeHtml(m.tag.name)}</span>
                    <span class="tm-match-type-badge tm-match-${m.match_type}">${m.match_type} (${m.score}%)</span>
                    ${m.tag.category ? `<span class="tm-match-category">${escapeHtml(m.tag.category.name)}</span>` : ''}
                  </div>
                  <div class="tm-match-desc">${escapeHtml(m.tag.description || '')}</div>
                  <div class="tm-match-aliases">Aliases: ${m.tag.aliases?.join(', ') || 'none'}</div>
                  <button class="btn btn-success btn-sm tm-select-match">Select</button>
                </div>
              `).join('')
              : '<div class="tm-no-matches">No matches found. Try searching manually above.</div>'
            }
          </div>
        </div>
        <div class="tm-modal-footer">
          <button class="btn btn-secondary tm-cancel-btn">Close</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Event handlers
    modal.querySelector('.tm-close-btn').addEventListener('click', () => modal.remove());
    modal.querySelector('.tm-cancel-btn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    // Manual search
    const searchInput = modal.querySelector('#tm-manual-search');
    const searchBtn = modal.querySelector('#tm-manual-search-btn');

    const doSearch = async () => {
      const term = searchInput.value.trim();
      if (!term) return;

      searchBtn.disabled = true;
      searchBtn.textContent = 'Searching...';

      try {
        const result = await callBackend('search', {
          tag_name: term,
          stashdb_tags: stashdbTags,
        });
        matchResults[tagId] = result.matches || [];

        // Re-render matches list
        const listEl = modal.querySelector('#tm-matches-list');
        const newMatches = matchResults[tagId];
        listEl.innerHTML = newMatches?.length
          ? newMatches.map((m, i) => `
            <div class="tm-match-item" data-index="${i}">
              <div class="tm-match-info">
                <span class="tm-match-name">${escapeHtml(m.tag.name)}</span>
                <span class="tm-match-type-badge tm-match-${m.match_type}">${m.match_type} (${m.score}%)</span>
                ${m.tag.category ? `<span class="tm-match-category">${escapeHtml(m.tag.category.name)}</span>` : ''}
              </div>
              <div class="tm-match-desc">${escapeHtml(m.tag.description || '')}</div>
              <div class="tm-match-aliases">Aliases: ${m.tag.aliases?.join(', ') || 'none'}</div>
              <button class="btn btn-success btn-sm tm-select-match">Select</button>
            </div>
          `).join('')
          : '<div class="tm-no-matches">No matches found.</div>';

        // Re-attach select handlers
        attachSelectHandlers();
      } catch (e) {
        showStatus(`Search error: ${e.message}`, 'error');
      } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = 'Search';
      }
    };

    searchBtn.addEventListener('click', doSearch);
    searchInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') doSearch();
    });

    // Select match handlers
    function attachSelectHandlers() {
      modal.querySelectorAll('.tm-select-match').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const idx = parseInt(e.target.closest('.tm-match-item').dataset.index);
          modal.remove();
          showDiffDialog(tagId, container, idx);
        });
      });
    }
    attachSelectHandlers();
  }
```

**Step 2: Add modal CSS to tag-manager.css**

```css
/* Modal styles - append to tag-manager.css */

.tm-modal-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1050;
}

.tm-modal {
  background: var(--bs-body-bg, #1a1a2e);
  border-radius: 8px;
  max-width: 700px;
  width: 90%;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.tm-modal-wide {
  max-width: 900px;
}

.tm-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 15px 20px;
  border-bottom: 1px solid var(--bs-border-color, #444);
}

.tm-modal-header h3 {
  margin: 0;
  font-size: 1.2em;
}

.tm-close-btn {
  background: none;
  border: none;
  font-size: 1.5em;
  color: var(--bs-secondary-color, #888);
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.tm-close-btn:hover {
  color: var(--bs-body-color, #fff);
}

.tm-modal-body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}

.tm-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 15px 20px;
  border-top: 1px solid var(--bs-border-color, #444);
}

.tm-diff-info {
  display: flex;
  gap: 15px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.tm-match-type-badge {
  padding: 3px 8px;
  border-radius: 3px;
  font-size: 0.85em;
  font-weight: 500;
}

.tm-match-exact {
  background: var(--bs-success, #28a745);
  color: #fff;
}

.tm-match-alias {
  background: var(--bs-info, #17a2b8);
  color: #fff;
}

.tm-match-fuzzy {
  background: var(--bs-warning, #ffc107);
  color: #000;
}

.tm-match-synonym {
  background: var(--bs-purple, #6f42c1);
  color: #fff;
}

.tm-diff-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 20px;
}

.tm-diff-table th,
.tm-diff-table td {
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid var(--bs-border-color, #444);
}

.tm-diff-table th {
  background: var(--bs-secondary-bg, #2d2d44);
  font-weight: 500;
}

.tm-diff-table td label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-right: 12px;
  cursor: pointer;
}

.tm-diff-table em {
  color: var(--bs-secondary-color, #888);
}

.tm-stashid-note {
  background: var(--bs-success-bg-subtle, #1a4a2a);
  color: var(--bs-success, #28a745);
  padding: 10px 15px;
  border-radius: 4px;
  font-size: 0.9em;
}

.tm-search-row {
  display: flex;
  gap: 10px;
  margin-bottom: 15px;
}

.tm-search-row input {
  flex: 1;
}

.tm-matches-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tm-match-item {
  background: var(--bs-secondary-bg, #2d2d44);
  border-radius: 6px;
  padding: 12px;
}

.tm-match-info {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.tm-match-desc {
  font-size: 0.9em;
  color: var(--bs-secondary-color, #888);
  margin-bottom: 5px;
}

.tm-match-aliases {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
  margin-bottom: 10px;
}

.tm-no-matches {
  text-align: center;
  padding: 30px;
  color: var(--bs-secondary-color, #888);
}
```

**Step 3: Verify changes compile (no syntax errors)**

Open browser, navigate to Stash, check console for errors.

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): implement diff dialog and matches modal"
```

---

## Task 7: Add Integration Test with Real Data

**Files:**
- Create: `plugins/tagManager/tests/test_integration.py`

**Step 1: Write integration test that uses real APIs**

```python
# plugins/tagManager/tests/test_integration.py
"""
Integration tests for tagManager plugin.

These tests use real API endpoints (StashDB and local Stash).
Configure environment variables:
  - STASHDB_URL (default: https://stashdb.org/graphql)
  - STASHDB_API_KEY (required)
  - STASH_URL (default: http://localhost:9999)
  - STASH_API_KEY (optional)

Run with: python -m pytest tests/test_integration.py -v -s
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stashdb_api import query_all_tags, search_tags_by_name
from matcher import TagMatcher


# Load config from environment
STASHDB_URL = os.environ.get('STASHDB_URL', 'https://stashdb.org/graphql')
STASHDB_API_KEY = os.environ.get('STASHDB_API_KEY', '')


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestStashDBIntegration(unittest.TestCase):
    """Integration tests against real StashDB."""

    def test_fetch_all_tags(self):
        """Should fetch all tags from StashDB."""
        tags = query_all_tags(STASHDB_URL, STASHDB_API_KEY, per_page=100)

        self.assertGreater(len(tags), 1000, "Expected >1000 tags from StashDB")

        # Verify tag structure
        tag = tags[0]
        self.assertIn('id', tag)
        self.assertIn('name', tag)
        self.assertIn('aliases', tag)

        print(f"Fetched {len(tags)} tags from StashDB")

    def test_search_exact_match(self):
        """Should find exact tag match."""
        tags = search_tags_by_name(STASHDB_URL, STASHDB_API_KEY, "Anal Creampie")

        self.assertGreater(len(tags), 0)
        # Should have exact match in results
        names = [t['name'] for t in tags]
        self.assertIn("Anal Creampie", names)

    def test_search_alias_match(self):
        """Should find tag via alias (Anklet -> Ankle Bracelet)."""
        tags = search_tags_by_name(STASHDB_URL, STASHDB_API_KEY, "Anklet")

        self.assertGreater(len(tags), 0)
        # Should find Ankle Bracelet
        names = [t['name'] for t in tags]
        self.assertIn("Ankle Bracelet", names)


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestMatcherIntegration(unittest.TestCase):
    """Integration tests for matcher with real StashDB data."""

    @classmethod
    def setUpClass(cls):
        """Fetch all StashDB tags once for all tests."""
        print("Fetching StashDB tags for matcher tests...")
        cls.stashdb_tags = query_all_tags(STASHDB_URL, STASHDB_API_KEY)
        print(f"Loaded {len(cls.stashdb_tags)} tags")

    def test_matcher_exact_match(self):
        """Matcher should find exact name match."""
        matcher = TagMatcher(self.stashdb_tags)
        matches = matcher.find_matches("Cowgirl")

        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0]['tag']['name'], "Cowgirl")
        self.assertEqual(matches[0]['match_type'], "exact")

    def test_matcher_fuzzy_match(self):
        """Matcher should find fuzzy match for typos."""
        matcher = TagMatcher(self.stashdb_tags, fuzzy_threshold=70)
        matches = matcher.find_matches("Anal Creampies")  # plural

        self.assertGreater(len(matches), 0)
        # Should match "Anal Creampie" (singular)
        self.assertEqual(matches[0]['tag']['name'], "Anal Creampie")

    def test_batch_matching(self):
        """Test matching a batch of common tags."""
        matcher = TagMatcher(self.stashdb_tags)

        test_tags = [
            "Blowjob",
            "Cowgirl",
            "Anal",
            "Creampie",
            "Amateur",
            "69",
        ]

        results = {}
        for tag_name in test_tags:
            matches = matcher.find_matches(tag_name)
            results[tag_name] = matches[0] if matches else None

        # Print results for debugging
        print("\n--- Batch Matching Results ---")
        for tag_name, match in results.items():
            if match:
                print(f"  {tag_name} -> {match['tag']['name']} ({match['match_type']}, {match['score']}%)")
            else:
                print(f"  {tag_name} -> NO MATCH")

        # All common tags should have matches
        for tag_name in test_tags:
            self.assertIsNotNone(results[tag_name], f"Expected match for {tag_name}")


if __name__ == '__main__':
    unittest.main()
```

**Step 2: Run integration test (requires API key)**

Run: `cd plugins/tagManager && STASHDB_API_KEY=your-key python -m pytest tests/test_integration.py -v -s`

Expected: PASS (if API key is valid)

**Step 3: Commit**

```bash
git add plugins/tagManager/tests/test_integration.py
git commit -m "test(tagManager): add integration tests with real StashDB data"
```

---

## Task 8: Final Cleanup and Documentation

**Files:**
- Modify: `plugins/tagManager/tagManager.yml` (bump version if needed)
- Update: `README.md` (add tagManager section)

**Step 1: Ensure all files have proper content**

Verify `synonyms.json` has the starter structure:
```json
{
  "_comment": "Custom synonym mappings for tag matching. Keys are local tag names, values are arrays of StashDB equivalents.",
  "synonyms": {}
}
```

**Step 2: Run all tests**

Run: `cd plugins/tagManager && python -m pytest tests/ -v`

Expected: All tests pass

**Step 3: Update README.md**

Add tagManager section to main README:

```markdown
## tagManager

Match and sync local tags with StashDB tags.

### Features
- Paginated list of unmatched tags
- Layered search: exact name → alias → fuzzy → synonym
- One-click accept for high-confidence matches
- Field-by-field merge dialog (name, description, aliases)
- Manual search for edge cases

### Configuration
Go to Settings → Plugins → Tag Manager:
- **StashDB Endpoint**: GraphQL URL (default: https://stashdb.org/graphql)
- **StashDB API Key**: Your API key (required)
- **Enable Fuzzy Search**: Use fuzzy matching for typos
- **Fuzzy Threshold**: Minimum score (0-100) for fuzzy matches

### Usage
1. Navigate to `/plugin/tag-manager` in your Stash UI
2. Click "Find Matches for Page" to search all visible tags
3. Click ✓ to accept a match, or "More" for alternatives
4. Review the diff dialog and click Apply

### Requirements
- Python 3.11+
- `thefuzz` library: `pip install thefuzz`
```

**Step 4: Final commit**

```bash
git add plugins/tagManager/ README.md
git commit -m "docs(tagManager): add documentation and finalize plugin"
```

---

## Summary

This plan creates the **tagManager** plugin with:

1. **Task 1**: Directory structure and scaffolding
2. **Task 2**: StashDB API client for fetching/searching tags
3. **Task 3**: Matching logic (exact, alias, fuzzy, synonym)
4. **Task 4**: Python backend entry point
5. **Task 5**: JavaScript UI with route registration and paginated list
6. **Task 6**: Diff dialog and tag update functionality
7. **Task 7**: Integration tests with real data
8. **Task 8**: Cleanup and documentation

Each task follows TDD (write failing test, implement, verify pass, commit) and produces small, reviewable commits.
