"""Tests for scene sync blacklist integration.

These tests verify that the blacklist filtering works correctly
when processing scenes from StashDB.

Run with: python -m pytest plugins/tagManager/tests/test_scene_sync_blacklist.py -v
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tag_cache import TagCache
from blacklist import Blacklist


class TestProcessSceneWithBlacklist(unittest.TestCase):
    """Test scene processing with blacklist filtering."""

    def setUp(self):
        """Set up test fixtures."""
        self.endpoint = "https://stashdb.org/graphql"
        self.local_tags = [
            {"id": "1", "name": "Anal", "aliases": [], "stash_ids": []},
            {"id": "2", "name": "4K Available", "aliases": [], "stash_ids": []},
            {"id": "3", "name": "1080p", "aliases": [], "stash_ids": []},
            {"id": "4", "name": "Blonde", "aliases": [], "stash_ids": []},
        ]
        self.tag_cache = TagCache.build(self.local_tags)

    def test_filters_blacklisted_tags_literal(self):
        """Should not process tags that match literal blacklist."""
        from stashdb_scene_sync import process_scene

        blacklist = Blacklist("4K Available")

        local_scene = {"id": "scene-1", "tags": []}
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-1", "name": "Anal"},
                {"id": "stashdb-2", "name": "4K Available"},  # Blacklisted
                {"id": "stashdb-3", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint,
            blacklist=blacklist
        )

        # Should add Anal and Blonde, but NOT 4K Available
        self.assertEqual(result.tags_added, 2)
        self.assertIn("1", result.merged_tag_ids)  # Anal
        self.assertIn("4", result.merged_tag_ids)  # Blonde
        self.assertNotIn("2", result.merged_tag_ids)  # 4K Available is blacklisted

    def test_filters_blacklisted_tags_regex(self):
        """Should not process tags that match regex blacklist."""
        from stashdb_scene_sync import process_scene

        # Regex to match resolution patterns like 1080p, 720p, etc.
        blacklist = Blacklist(r"/^\d+p$")

        local_scene = {"id": "scene-1", "tags": []}
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-1", "name": "Anal"},
                {"id": "stashdb-2", "name": "1080p"},  # Blacklisted by regex
                {"id": "stashdb-3", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint,
            blacklist=blacklist
        )

        # Should add Anal and Blonde, but NOT 1080p
        self.assertEqual(result.tags_added, 2)
        self.assertIn("1", result.merged_tag_ids)  # Anal
        self.assertIn("4", result.merged_tag_ids)  # Blonde
        self.assertNotIn("3", result.merged_tag_ids)  # 1080p is blacklisted

    def test_blacklist_case_insensitive(self):
        """Blacklist literal matching should be case-insensitive."""
        from stashdb_scene_sync import process_scene

        blacklist = Blacklist("4k available")  # Lowercase

        local_scene = {"id": "scene-1", "tags": []}
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-1", "name": "4K Available"},  # Different case
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint,
            blacklist=blacklist
        )

        # Should NOT add 4K Available (blacklist is case-insensitive)
        self.assertEqual(result.tags_added, 0)

    def test_empty_blacklist_processes_all_tags(self):
        """Empty blacklist should process all tags normally."""
        from stashdb_scene_sync import process_scene

        blacklist = Blacklist("")

        local_scene = {"id": "scene-1", "tags": []}
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-1", "name": "Anal"},
                {"id": "stashdb-2", "name": "4K Available"},
                {"id": "stashdb-3", "name": "1080p"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint,
            blacklist=blacklist
        )

        # Should add all 3 tags
        self.assertEqual(result.tags_added, 3)

    def test_multiple_blacklist_patterns(self):
        """Should filter tags matching any blacklist pattern."""
        from stashdb_scene_sync import process_scene

        # Multiple patterns: literal + regex
        blacklist = Blacklist("4K Available\n/^\\d+p$")

        local_scene = {"id": "scene-1", "tags": []}
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-1", "name": "Anal"},
                {"id": "stashdb-2", "name": "4K Available"},  # Blacklisted (literal)
                {"id": "stashdb-3", "name": "1080p"},         # Blacklisted (regex)
                {"id": "stashdb-4", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint,
            blacklist=blacklist
        )

        # Should only add Anal and Blonde
        self.assertEqual(result.tags_added, 2)
        self.assertIn("1", result.merged_tag_ids)  # Anal
        self.assertIn("4", result.merged_tag_ids)  # Blonde


if __name__ == '__main__':
    unittest.main()
