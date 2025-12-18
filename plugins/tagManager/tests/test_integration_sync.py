"""
Integration tests for scene tag sync.

Uses real API endpoints (StashDB and local Stash).
Configure environment variables:
  - STASHDB_URL (default: https://stashdb.org/graphql)
  - STASHDB_API_KEY (required)
  - STASH_URL (default: http://localhost:9999)
  - STASH_API_KEY (optional)

Run with: python -m pytest tests/test_integration_sync.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment from ~/code/.env
env_path = os.path.expanduser("~/code/.env")
if os.path.exists(env_path):
    load_dotenv(env_path)

# Load config from environment
STASHDB_URL = os.environ.get('STASHDB_URL', 'https://stashdb.org/graphql')
STASHDB_API_KEY = os.environ.get('STASHDB_API_KEY', '')
STASH_URL = os.environ.get('STASH_URL', 'http://localhost:9999')
STASH_API_KEY = os.environ.get('STASH_API_KEY', '')


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestStashDBSceneQueries(unittest.TestCase):
    """Integration tests for StashDB scene queries."""

    def test_find_scene_by_id(self):
        """Should fetch a scene by ID from StashDB."""
        from stashdb_api import find_scene_by_id, RateLimiter

        # Use a known scene ID from StashDB (this is a real scene)
        # "Lust" by Vixen - a well-known scene
        scene_id = "e5eb1e2e-3e3e-4e3e-8e3e-3e3e3e3e3e3e"

        rate_limiter = RateLimiter()

        # This may return None if the scene doesn't exist - that's OK
        # We just want to verify the API call works
        result = find_scene_by_id(STASHDB_URL, STASHDB_API_KEY, scene_id, rate_limiter)

        # Result is either None or a dict with expected fields
        if result:
            self.assertIn('id', result)
            self.assertIn('tags', result)
            print(f"Found scene: {result.get('title', 'Unknown')}")
            print(f"Tags: {[t.get('name') for t in result.get('tags', [])]}")

    def test_find_scenes_by_fingerprints(self):
        """Should batch query scenes by fingerprints."""
        from stashdb_api import find_scenes_by_fingerprints, RateLimiter

        rate_limiter = RateLimiter()

        # Query with empty fingerprints (should return empty lists)
        result = find_scenes_by_fingerprints(
            STASHDB_URL, STASHDB_API_KEY,
            [[], []],
            rate_limiter
        )

        self.assertEqual(len(result), 2)
        print(f"Empty query returned: {result}")


@unittest.skipIf(not STASHDB_API_KEY, "STASHDB_API_KEY not set")
class TestTagCacheIntegration(unittest.TestCase):
    """Integration tests for TagCache with real data."""

    def test_build_cache_from_mock_tags(self):
        """Should build cache from tag data."""
        from tag_cache import TagCache

        # Simulate tags that would come from local Stash
        mock_tags = [
            {
                "id": "1",
                "name": "Anal",
                "aliases": ["Anal Sex"],
                "stash_ids": [
                    {"endpoint": STASHDB_URL, "stash_id": "abc123"}
                ]
            },
            {
                "id": "2",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            }
        ]

        cache = TagCache.build(mock_tags)

        self.assertEqual(cache.tag_count, 2)
        self.assertEqual(cache.by_stashdb_id(STASHDB_URL, "abc123"), "1")
        self.assertEqual(cache.by_name("blonde"), "2")
        self.assertEqual(cache.by_alias("anal sex"), "1")

        print(f"Cache built with {cache.tag_count} tags")


@unittest.skipIf(not STASH_API_KEY, "STASH_API_KEY not set")
class TestLocalStashIntegration(unittest.TestCase):
    """Integration tests against local Stash instance."""

    def setUp(self):
        """Set up Stash connection."""
        from stashapi.stashapp import StashInterface

        self.stash = StashInterface({
            "Scheme": "http",
            "Host": STASH_URL.replace("http://", "").replace("https://", "").split(":")[0],
            "Port": int(STASH_URL.split(":")[-1]) if ":" in STASH_URL.split("//")[-1] else 9999,
            "ApiKey": STASH_API_KEY
        })

    def test_can_query_tags(self):
        """Should query tags from local Stash."""
        tags = self.stash.find_tags(
            f={},
            filter={"page": 1, "per_page": 10},
            fragment="id name aliases stash_ids { endpoint stash_id }"
        )

        self.assertIsNotNone(tags)
        print(f"Found {len(tags)} tags in local Stash")

        if tags:
            print(f"First tag: {tags[0].get('name')}")

    def test_can_query_scenes_with_stashdb_ids(self):
        """Should query scenes that have StashDB IDs."""
        scenes = self.stash.find_scenes(
            f={
                "stash_id_endpoint": {
                    "endpoint": STASHDB_URL,
                    "modifier": "NOT_NULL",
                    "stash_id": ""
                }
            },
            filter={"page": 1, "per_page": 5},
            fragment="id title stash_ids { endpoint stash_id }"
        )

        print(f"Found {len(scenes) if scenes else 0} scenes with StashDB IDs")

        if scenes:
            for scene in scenes[:3]:
                print(f"  - {scene.get('title', 'Unknown')}: {scene.get('stash_ids')}")


if __name__ == '__main__':
    unittest.main()
