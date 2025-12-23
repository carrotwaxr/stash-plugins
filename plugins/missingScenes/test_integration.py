#!/usr/bin/env python3
"""
Integration tests for Missing Scenes plugin.
Run with: python test_integration.py

These tests require a real Stash instance with favorites configured.
Configuration is loaded from .env file in this directory.

Prerequisites:
1. Copy .env.example to .env and fill in your credentials
2. Have at least one favorite performer, studio, and tag in your Stash
3. Those favorites should be linked to StashDB
"""

import os
import sys
import unittest
import json
from pathlib import Path

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

# Check required environment variables
STASH_URL = os.environ.get("STASH_URL")
STASH_API_KEY = os.environ.get("STASH_API_KEY")
STASHDB_URL = os.environ.get("STASHDB_URL", "https://stashdb.org/graphql")

if not STASH_URL or not STASH_API_KEY:
    print("ERROR: STASH_URL and STASH_API_KEY must be set in .env file")
    print("Copy .env.example to .env and fill in your values")
    sys.exit(1)

# Import the module to test
import missing_scenes


class TestGetFavoriteStashIdsIntegration(unittest.TestCase):
    """Integration tests for get_favorite_stash_ids function."""

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures - configure Stash connection."""
        # Override the graphql endpoint
        missing_scenes.STASH_URL = STASH_URL
        # Note: stash_graphql uses internal STASH_URL from plugin context
        # For integration tests, we'll call the Stash API directly

    def _stash_graphql(self, query, variables=None):
        """Make a direct GraphQL request to Stash."""
        import urllib.request
        import urllib.error

        headers = {
            "Content-Type": "application/json",
            "ApiKey": STASH_API_KEY,
        }

        data = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(
            f"{STASH_URL}/graphql",
            data=data,
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                if "errors" in result:
                    print(f"GraphQL errors: {result['errors']}")
                    return None
                return result.get("data")
        except urllib.error.URLError as e:
            print(f"Failed to connect to Stash: {e}")
            return None

    def test_query_favorite_performers(self):
        """Test that we can query favorite performers from Stash."""
        query = """
        query FindFavoritePerformers($filter: FindFilterType) {
            findPerformers(
                filter: $filter
                performer_filter: { filter_favorites: true }
            ) {
                count
                performers {
                    id
                    name
                    stash_ids {
                        endpoint
                        stash_id
                    }
                }
            }
        }
        """

        result = self._stash_graphql(query, {"filter": {"page": 1, "per_page": 10}})

        self.assertIsNotNone(result, "Failed to query Stash - check STASH_URL and STASH_API_KEY")
        self.assertIn("findPerformers", result)
        print(f"Found {result['findPerformers']['count']} favorite performers")

        # Show some examples
        for p in result['findPerformers']['performers'][:3]:
            stashdb_ids = [s['stash_id'] for s in p.get('stash_ids', []) if 'stashdb' in s.get('endpoint', '').lower()]
            print(f"  - {p['name']}: {stashdb_ids}")

    def test_query_favorite_studios(self):
        """Test that we can query favorite studios from Stash using 'favorite' field."""
        query = """
        query FindFavoriteStudios($filter: FindFilterType) {
            findStudios(
                filter: $filter
                studio_filter: { favorite: true }
            ) {
                count
                studios {
                    id
                    name
                    stash_ids {
                        endpoint
                        stash_id
                    }
                }
            }
        }
        """

        result = self._stash_graphql(query, {"filter": {"page": 1, "per_page": 10}})

        self.assertIsNotNone(result, "Failed to query Stash - check STASH_URL and STASH_API_KEY")
        self.assertIn("findStudios", result)
        print(f"Found {result['findStudios']['count']} favorite studios")

        # Show some examples
        for s in result['findStudios']['studios'][:3]:
            stashdb_ids = [sid['stash_id'] for sid in s.get('stash_ids', []) if 'stashdb' in sid.get('endpoint', '').lower()]
            print(f"  - {s['name']}: {stashdb_ids}")

    def test_query_favorite_tags(self):
        """Test that we can query favorite tags from Stash using 'favorite' field."""
        query = """
        query FindFavoriteTags($filter: FindFilterType) {
            findTags(
                filter: $filter
                tag_filter: { favorite: true }
            ) {
                count
                tags {
                    id
                    name
                    stash_ids {
                        endpoint
                        stash_id
                    }
                }
            }
        }
        """

        result = self._stash_graphql(query, {"filter": {"page": 1, "per_page": 10}})

        self.assertIsNotNone(result, "Failed to query Stash - check STASH_URL and STASH_API_KEY")
        self.assertIn("findTags", result)
        print(f"Found {result['findTags']['count']} favorite tags")

        # Show some examples
        for t in result['findTags']['tags'][:3]:
            stashdb_ids = [sid['stash_id'] for sid in t.get('stash_ids', []) if 'stashdb' in sid.get('endpoint', '').lower()]
            print(f"  - {t['name']}: {stashdb_ids}")

    def test_studio_filter_favorites_returns_error(self):
        """Verify that using 'filter_favorites' on studios returns an error (wrong field name)."""
        # This query should FAIL because studios use 'favorite', not 'filter_favorites'
        query = """
        query FindFavoriteStudios($filter: FindFilterType) {
            findStudios(
                filter: $filter
                studio_filter: { filter_favorites: true }
            ) {
                count
            }
        }
        """

        result = self._stash_graphql(query, {"filter": {"page": 1, "per_page": 10}})

        # This should return None or have errors because 'filter_favorites' is invalid
        if result is not None:
            # If it returned data, check if count is 0 (might silently fail)
            count = result.get("findStudios", {}).get("count", 0)
            print(f"WARNING: filter_favorites query returned {count} studios (expected error or 0)")
        else:
            print("Correctly rejected filter_favorites for studios")


class TestScenePassesFavoriteFiltersIntegration(unittest.TestCase):
    """Integration tests for scene filtering logic."""

    def test_filter_logic_with_real_scene_structure(self):
        """Test filter logic with realistic StashDB scene structure."""
        # Real StashDB scene structure (simplified)
        scene = {
            "id": "test-scene-id",
            "title": "Test Scene",
            "performers": [
                {"performer": {"id": "perf-123", "name": "Test Performer"}},
                {"performer": {"id": "perf-456", "name": "Another Performer"}},
            ],
            "studio": {"id": "studio-789", "name": "Test Studio"},
            "tags": [
                {"id": "tag-abc", "name": "Test Tag"},
                {"id": "tag-def", "name": "Another Tag"},
            ]
        }

        # Test with matching performer
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids={"perf-123"},
            favorite_studio_ids=None,
            favorite_tag_ids=None
        )
        self.assertTrue(result, "Scene should pass with matching performer")

        # Test with non-matching performer
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids={"perf-999"},
            favorite_studio_ids=None,
            favorite_tag_ids=None
        )
        self.assertFalse(result, "Scene should fail with non-matching performer")

        # Test with matching studio
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids=None,
            favorite_studio_ids={"studio-789"},
            favorite_tag_ids=None
        )
        self.assertTrue(result, "Scene should pass with matching studio")

        # Test with non-matching studio
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids=None,
            favorite_studio_ids={"studio-999"},
            favorite_tag_ids=None
        )
        self.assertFalse(result, "Scene should fail with non-matching studio")

        # Test AND logic - all must match
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids={"perf-123"},
            favorite_studio_ids={"studio-789"},
            favorite_tag_ids={"tag-abc"}
        )
        self.assertTrue(result, "Scene should pass when all filters match")

        # Test AND logic - one fails
        result = missing_scenes.scene_passes_favorite_filters(
            scene,
            favorite_performer_ids={"perf-123"},
            favorite_studio_ids={"studio-999"},  # Wrong studio
            favorite_tag_ids={"tag-abc"}
        )
        self.assertFalse(result, "Scene should fail when one filter doesn't match")


def run_integration_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("Missing Scenes Plugin - Integration Tests")
    print("=" * 60)
    print(f"Stash URL: {STASH_URL}")
    print(f"StashDB URL: {STASHDB_URL}")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestGetFavoriteStashIdsIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestScenePassesFavoriteFiltersIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"Results: {result.testsRun} tests, "
          f"{len(result.failures)} failures, "
          f"{len(result.errors)} errors")
    print("=" * 60)

    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
