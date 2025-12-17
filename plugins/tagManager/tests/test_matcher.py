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
