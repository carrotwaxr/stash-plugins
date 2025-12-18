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
