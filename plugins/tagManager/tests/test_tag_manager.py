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
