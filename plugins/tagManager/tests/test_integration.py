"""
Integration tests for tagManager plugin.

These tests use real API endpoints (StashDB and local Stash).
Configure environment variables:
  - STASHDB_URL (default: https://stashdb.org/graphql)
  - STASHDB_API_KEY (required)
  - STASH_URL (default: http://localhost:9999)
  - STASH_API_KEY (optional)

Run with: python -m unittest tests.test_integration -v
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
