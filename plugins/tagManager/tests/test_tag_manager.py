"""Tests for main tag_manager module."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTagManagerModes(unittest.TestCase):
    """Test different plugin operation modes."""

    @patch('tag_manager.load_cached_tags', return_value=None)
    @patch('stashdb_api.graphql_request')
    def test_search_mode_returns_matches(self, mock_graphql, _mock_cache):
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

    @patch('tag_manager.load_cached_tags', return_value=None)
    @patch('stashdb_api.graphql_request')
    def test_search_mode_scores_exact_higher_than_alias(self, mock_graphql, _mock_cache):
        """Exact name matches should score higher than alias matches."""
        # Mock StashDB response with two tags:
        # - One where search term matches the name exactly
        # - One where search term matches an alias
        mock_graphql.return_value = {
            "queryTags": {
                "count": 2,
                "tags": [
                    {
                        "id": "alias-match",
                        "name": "Different Name",
                        "description": "",
                        "aliases": ["Test Tag"],
                        "category": None
                    },
                    {
                        "id": "exact-match",
                        "name": "Test Tag",
                        "description": "",
                        "aliases": [],
                        "category": None
                    }
                ]
            }
        }

        from tag_manager import handle_search

        result = handle_search(
            tag_name="Test Tag",
            stashdb_url="https://stashdb.org/graphql",
            stashdb_api_key="fake-key",
            settings={}
        )

        # Exact match should be first (higher score)
        self.assertEqual(result["matches"][0]["tag"]["id"], "exact-match")
        self.assertEqual(result["matches"][0]["match_type"], "exact")
        self.assertEqual(result["matches"][0]["score"], 100)

        # Alias match should be second (lower score)
        self.assertEqual(result["matches"][1]["tag"]["id"], "alias-match")
        self.assertEqual(result["matches"][1]["match_type"], "alias")
        self.assertLess(result["matches"][1]["score"], 100)


class TestCacheFunctions(unittest.TestCase):
    """Test caching functionality."""

    def setUp(self):
        """Create a temporary cache directory."""
        self.temp_dir = tempfile.mkdtemp()
        # Patch get_cache_dir to use temp directory
        self.patcher = patch('tag_manager.get_cache_dir', return_value=self.temp_dir)
        self.patcher.start()

    def tearDown(self):
        """Clean up temporary directory."""
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_cache_file_path_generates_unique_paths(self):
        """Different endpoints should get different cache paths."""
        from tag_manager import get_cache_file_path

        path1 = get_cache_file_path("https://stashdb.org/graphql")
        path2 = get_cache_file_path("https://fansdb.cc/graphql")

        self.assertNotEqual(path1, path2)
        self.assertTrue(path1.endswith('.json'))
        self.assertTrue(path2.endswith('.json'))

    def test_save_and_load_cached_tags(self):
        """Should save tags to cache and load them back."""
        from tag_manager import save_tags_to_cache, load_cached_tags

        endpoint = "https://test.example.org/graphql"
        tags = [
            {"id": "1", "name": "Tag A"},
            {"id": "2", "name": "Tag B"},
        ]

        # Save
        result = save_tags_to_cache(endpoint, tags)
        self.assertTrue(result)

        # Load
        cached = load_cached_tags(endpoint)
        self.assertIsNotNone(cached)
        self.assertEqual(cached['count'], 2)
        self.assertEqual(len(cached['tags']), 2)
        self.assertEqual(cached['tags'][0]['name'], "Tag A")

    def test_load_returns_none_for_missing_cache(self):
        """Should return None when no cache exists."""
        from tag_manager import load_cached_tags

        result = load_cached_tags("https://nonexistent.example.org/graphql")
        self.assertIsNone(result)

    def test_cache_status_for_existing_cache(self):
        """Should return status info for existing cache."""
        from tag_manager import save_tags_to_cache, get_cache_status

        endpoint = "https://test.example.org/graphql"
        tags = [{"id": "1", "name": "Tag A"}]
        save_tags_to_cache(endpoint, tags)

        status = get_cache_status(endpoint)
        self.assertTrue(status['exists'])
        self.assertEqual(status['count'], 1)
        self.assertIn('age_hours', status)
        self.assertFalse(status['expired'])

    def test_cache_status_for_missing_cache(self):
        """Should return exists=False for missing cache."""
        from tag_manager import get_cache_status

        status = get_cache_status("https://nonexistent.example.org/graphql")
        self.assertFalse(status['exists'])

    def test_clear_cache(self):
        """Should clear cache file."""
        from tag_manager import save_tags_to_cache, clear_cache, load_cached_tags

        endpoint = "https://test.example.org/graphql"
        save_tags_to_cache(endpoint, [{"id": "1", "name": "Tag"}])

        # Verify it exists
        self.assertIsNotNone(load_cached_tags(endpoint))

        # Clear
        result = clear_cache(endpoint)
        self.assertTrue(result)

        # Verify it's gone
        self.assertIsNone(load_cached_tags(endpoint))

    @patch('tag_manager.CACHE_MAX_AGE_HOURS', 0)  # Expire immediately
    def test_expired_cache_returns_none(self):
        """Expired cache should return None."""
        from tag_manager import save_tags_to_cache, load_cached_tags

        endpoint = "https://test.example.org/graphql"
        save_tags_to_cache(endpoint, [{"id": "1", "name": "Tag"}])

        # Sleep a tiny bit to ensure time has passed
        time.sleep(0.01)

        # With CACHE_MAX_AGE_HOURS=0, should be expired
        result = load_cached_tags(endpoint)
        self.assertIsNone(result)


class TestHandleFetchAll(unittest.TestCase):
    """Test fetch_all mode with caching."""

    def setUp(self):
        """Create a temporary cache directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.patcher = patch('tag_manager.get_cache_dir', return_value=self.temp_dir)
        self.patcher.start()

    def tearDown(self):
        """Clean up temporary directory."""
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('tag_manager.query_all_tags')
    def test_fetch_all_uses_cache_when_available(self, mock_query):
        """Should use cache instead of fetching when cache is valid."""
        from tag_manager import save_tags_to_cache, handle_fetch_all

        endpoint = "https://test.example.org/graphql"
        cached_tags = [{"id": "1", "name": "Cached Tag"}]
        save_tags_to_cache(endpoint, cached_tags)

        result = handle_fetch_all(endpoint, "fake-key", force_refresh=False)

        # Should not have called the API
        mock_query.assert_not_called()

        # Should return cached data
        self.assertTrue(result['from_cache'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['tags'][0]['name'], "Cached Tag")

    @patch('tag_manager.query_all_tags')
    def test_fetch_all_force_refresh_bypasses_cache(self, mock_query):
        """Should fetch fresh data when force_refresh=True."""
        from tag_manager import save_tags_to_cache, handle_fetch_all

        endpoint = "https://test.example.org/graphql"
        cached_tags = [{"id": "1", "name": "Cached Tag"}]
        save_tags_to_cache(endpoint, cached_tags)

        # Mock fresh data
        mock_query.return_value = [{"id": "2", "name": "Fresh Tag"}]

        result = handle_fetch_all(endpoint, "fake-key", force_refresh=True)

        # Should have called the API
        mock_query.assert_called_once()

        # Should return fresh data
        self.assertFalse(result['from_cache'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['tags'][0]['name'], "Fresh Tag")

    @patch('tag_manager.query_all_tags')
    def test_fetch_all_fetches_when_no_cache(self, mock_query):
        """Should fetch from API when no cache exists."""
        from tag_manager import handle_fetch_all

        endpoint = "https://newtest.example.org/graphql"
        mock_query.return_value = [{"id": "1", "name": "New Tag"}]

        result = handle_fetch_all(endpoint, "fake-key", force_refresh=False)

        # Should have called the API
        mock_query.assert_called_once()

        # Should return fetched data
        self.assertFalse(result['from_cache'])
        self.assertEqual(result['count'], 1)


if __name__ == '__main__':
    unittest.main()


def _tag(tag_id, name, aliases=None):
    return {"id": tag_id, "name": name, "description": "", "aliases": aliases or [], "category": None}


class TestMainSearch(unittest.TestCase):
    """Run tag_manager.main() end to end with a mocked Stash and StashDB."""

    URL = "https://stashdb.org/graphql"

    def setUp(self):
        import io
        import plugin_data
        self._io = io
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, plugin_data, "_current_dir", None)
        self.plugin_config = {"tagBlacklist": "Blonde"}

    def _run(self, args, graphql_side_effect=None, graphql_return=None):
        import tag_manager
        config = {
            "general": {"stashBoxes": [{"endpoint": self.URL, "api_key": "k", "name": "StashDB"}]},
            "plugins": {"tagManager": self.plugin_config},
        }
        if graphql_return is None:
            graphql_return = {"queryTags": {"count": 2, "tags": [
                _tag("b1", "Blonde"), _tag("b2", "Blonde Hair")]}}
        payload = json.dumps({"server_connection": {"Dir": self.tmp}, "args": args})
        out = self._io.StringIO()
        with patch("sys.stdin", self._io.StringIO(payload)), \
             patch("sys.stdout", out), \
             patch("tag_manager.LocalStash") as mock_stash, \
             patch("stashdb_api.graphql_request", side_effect=graphql_side_effect,
                   return_value=graphql_return):
            mock_stash.return_value.configuration.return_value = config
            tag_manager.main()
        return json.loads(out.getvalue())["output"]

    def test_search_uses_server_blacklist(self):
        output = self._run({
            "mode": "search", "tag_name": "Blonde", "stashdb_url": self.URL,
            "settings": {"tagBlacklist": ""},
        })
        names = [m["tag"]["name"] for m in output["matches"]]
        self.assertNotIn("Blonde", names)
        self.assertIn("Blonde Hair", names)

    def test_search_ignores_client_stashdb_tags(self):
        import tag_manager, plugin_data
        plugin_data.configure({"Dir": self.tmp})
        tag_manager.save_tags_to_cache(self.URL, [_tag("h1", "Handcuffs")])
        output = self._run({
            "mode": "search", "tag_name": "Handcuff", "stashdb_url": self.URL,
            "stashdb_tags": [_tag("z1", "Handcuffz")],
        }, graphql_return={"queryTags": {"count": 0, "tags": []}})
        names = [m["tag"]["name"] for m in output["matches"]]
        self.assertIn("Handcuffs", names)
        self.assertNotIn("Handcuffz", names)
        self.assertFalse(output.get("fuzzy_unavailable"))

    def test_search_without_cache_reports_fuzzy_unavailable(self):
        output = self._run({"mode": "search", "tag_name": "Blonde", "stashdb_url": self.URL})
        self.assertTrue(output["fuzzy_unavailable"])

    def test_search_error_is_reported(self):
        from stashdb_api import StashDBAPIError
        output = self._run(
            {"mode": "search", "tag_name": "Blonde", "stashdb_url": self.URL},
            graphql_side_effect=StashDBAPIError("forbidden", status_code=403),
        )
        self.assertIn("error", output)
        self.assertTrue(output["auth_error"])
