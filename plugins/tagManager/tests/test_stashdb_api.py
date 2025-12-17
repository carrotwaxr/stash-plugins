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
