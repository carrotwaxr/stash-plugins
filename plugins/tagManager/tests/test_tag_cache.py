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
