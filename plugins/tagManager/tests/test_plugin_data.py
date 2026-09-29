"""Tests for plugin_data and cache safety."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugin_data
import tag_manager
from stashdb_api import StashDBAPIError


class TestPluginData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = plugin_data._current_dir
        plugin_data.configure({"Dir": self.tmp})

    def tearDown(self):
        plugin_data._current_dir = self._saved

    def test_data_dir_under_stash_config(self):
        d = plugin_data.data_dir({"Dir": self.tmp})
        self.assertEqual(d, os.path.join(self.tmp, "plugin_data", "tagManager"))
        self.assertTrue(os.path.isdir(d))

    def test_data_dir_fallback(self):
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(plugin_data.data_dir({}), os.path.join(plugin_dir, "data"))

    def test_cache_filename_windows_safe(self):
        name = os.path.basename(tag_manager.get_cache_file_path("https://stashdb.org:443/graphql"))
        for ch in (":", "\\", "/"):
            self.assertNotIn(ch, name)

    def test_empty_fetch_not_cached(self):
        url = "https://stashdb.org/graphql"
        self.assertFalse(tag_manager.save_tags_to_cache(url, []))
        self.assertFalse(os.path.exists(tag_manager.get_cache_file_path(url)))

    def test_cache_write_atomic(self):
        url = "https://stashdb.org/graphql"
        self.assertTrue(tag_manager.save_tags_to_cache(url, [{"id": "1", "name": "a"}]))
        files = os.listdir(tag_manager.get_cache_dir())
        self.assertEqual(files, [os.path.basename(tag_manager.get_cache_file_path(url))])

    def test_fetch_all_error_returns_error_and_does_not_cache(self):
        url = "https://stashdb.org/graphql"
        with patch("tag_manager.query_all_tags",
                   side_effect=StashDBAPIError("HTTP 403", status_code=403)):
            result = tag_manager.handle_fetch_all(url, "key", force_refresh=True)
        self.assertEqual(result["auth_error"], True)
        self.assertIn("403", result["error"])
        self.assertFalse(os.path.exists(tag_manager.get_cache_file_path(url)))


if __name__ == "__main__":
    unittest.main()
