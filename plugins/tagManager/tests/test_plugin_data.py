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

    def test_configure_falls_back_when_dir_not_creatable(self):
        blocker = os.path.join(self.tmp, "afile")
        open(blocker, "w").close()

        plugin_data.configure({"Dir": blocker})  # a file used as a dir: makedirs fails

        self.assertTrue(os.path.isdir(plugin_data.current_dir()))
        self.assertNotIn(blocker, plugin_data.current_dir())

    def test_configure_falls_back_to_temp_when_plugin_dir_also_fails(self):
        blocker = os.path.join(self.tmp, "afile")
        open(blocker, "w").close()
        real_makedirs = os.makedirs

        def picky(path, *a, **kw):
            if path.startswith(self.tmp) or os.path.basename(path) == "data":
                raise PermissionError("read-only")
            return real_makedirs(path, *a, **kw)

        with patch("plugin_data.os.makedirs", side_effect=picky):
            plugin_data.configure({"Dir": blocker})

        self.assertTrue(plugin_data.current_dir().startswith(tempfile.gettempdir()))

    def test_main_search_still_replies_when_data_dir_not_creatable(self):
        import io
        import json
        blocker = os.path.join(self.tmp, "afile")
        open(blocker, "w").close()
        stdin = io.StringIO(json.dumps({"args": {"mode": "search", "tag_name": "x"},
                                        "server_connection": {"Dir": blocker}}))
        out = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", out):
            tag_manager.main()

        self.assertIsInstance(json.loads(out.getvalue().strip().splitlines()[-1]), dict)

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
