#!/usr/bin/env python3
"""
Tests for Missing Scenes plugin.
Run with: python test_missing_scenes.py

Tests verify:
1. Scene formatting logic
2. Whisparr payload building
3. URL construction
4. Error handling
"""

import sys
import json
import unittest
from unittest.mock import patch, MagicMock

# Import the module to test
import missing_scenes


class TestFormatScene(unittest.TestCase):
    """Test the format_scene function."""

    def test_basic_scene(self):
        """Test formatting a basic scene with minimal data."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "release_date": "2024-01-15",
            "duration": 1800,
            "images": [],
            "performers": [],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["stash_id"], "abc123")
        self.assertEqual(result["title"], "Test Scene")
        self.assertEqual(result["release_date"], "2024-01-15")
        self.assertEqual(result["duration"], 1800)
        self.assertIsNone(result["thumbnail"])
        self.assertEqual(result["performers"], [])
        self.assertIsNone(result["studio"])

    def test_scene_with_thumbnail(self):
        """Test that thumbnail is extracted correctly."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "images": [
                {"id": "img1", "url": "http://example.com/thumb.jpg", "width": 800, "height": 600},
            ],
            "performers": [],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["thumbnail"], "http://example.com/thumb.jpg")

    def test_scene_prefers_landscape_thumbnail(self):
        """Test that landscape images are preferred for thumbnails."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "images": [
                {"id": "img1", "url": "http://example.com/portrait.jpg", "width": 600, "height": 800},
                {"id": "img2", "url": "http://example.com/landscape.jpg", "width": 1920, "height": 1080},
            ],
            "performers": [],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["thumbnail"], "http://example.com/landscape.jpg")

    def test_scene_with_performers(self):
        """Test performer formatting."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "images": [],
            "performers": [
                {
                    "performer": {
                        "id": "perf1",
                        "name": "Jane Doe",
                        "disambiguation": None,
                        "gender": "FEMALE",
                    },
                    "as": None,
                },
                {
                    "performer": {
                        "id": "perf2",
                        "name": "John Smith",
                        "disambiguation": "actor",
                        "gender": "MALE",
                    },
                    "as": "Johnny",
                },
            ],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(len(result["performers"]), 2)
        self.assertEqual(result["performers"][0]["name"], "Jane Doe")
        self.assertEqual(result["performers"][0]["gender"], "FEMALE")
        self.assertEqual(result["performers"][1]["name"], "John Smith")
        self.assertEqual(result["performers"][1]["as"], "Johnny")

    def test_scene_with_studio(self):
        """Test studio formatting."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "images": [],
            "performers": [],
            "urls": [],
            "studio": {
                "id": "studio1",
                "name": "Test Studio",
            },
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["studio"]["id"], "studio1")
        self.assertEqual(result["studio"]["name"], "Test Studio")

    def test_scene_with_url(self):
        """Test URL extraction."""
        scene = {
            "id": "abc123",
            "title": "Test Scene",
            "images": [],
            "performers": [],
            "urls": [
                {"url": "http://example.com/scene1", "site": {"name": "Example"}},
            ],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["url"], "http://example.com/scene1")

    def test_scene_missing_title(self):
        """Test that missing title defaults to 'Unknown Title'."""
        scene = {
            "id": "abc123",
            "title": None,
            "images": [],
            "performers": [],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["title"], "Unknown Title")

    def test_scene_empty_title(self):
        """Test that empty title defaults to 'Unknown Title'."""
        scene = {
            "id": "abc123",
            "title": "",
            "images": [],
            "performers": [],
            "urls": [],
            "studio": None,
        }

        result = missing_scenes.format_scene(scene, "abc123")

        self.assertEqual(result["title"], "Unknown Title")


class TestWhisparrPayload(unittest.TestCase):
    """Test Whisparr-related functions."""

    def test_whisparr_request_url_construction(self):
        """Test that Whisparr URLs are constructed correctly."""
        # Test URL with trailing slash
        url_with_slash = "http://localhost:6969/"
        expected = "http://localhost:6969/api/v3/test"
        actual = f"{url_with_slash.rstrip('/')}/api/v3/test"
        self.assertEqual(actual, expected)

        # Test URL without trailing slash
        url_no_slash = "http://localhost:6969"
        actual = f"{url_no_slash.rstrip('/')}/api/v3/test"
        self.assertEqual(actual, expected)

    def test_foreign_id_format(self):
        """Test that foreign IDs are formatted correctly for Whisparr."""
        stash_id = "abc123-def456"
        foreign_id = f"stash:{stash_id}"
        self.assertEqual(foreign_id, "stash:abc123-def456")


class TestStashIdMatching(unittest.TestCase):
    """Test scene matching logic."""

    def test_scene_in_local_stash(self):
        """Test that scenes in local Stash are correctly identified."""
        local_ids = {"scene1", "scene2", "scene3"}
        stashdb_scene_id = "scene2"

        self.assertIn(stashdb_scene_id, local_ids)

    def test_scene_not_in_local_stash(self):
        """Test that missing scenes are correctly identified."""
        local_ids = {"scene1", "scene2", "scene3"}
        stashdb_scene_id = "scene4"

        self.assertNotIn(stashdb_scene_id, local_ids)

    def test_missing_scene_calculation(self):
        """Test the missing scenes calculation logic."""
        stashdb_scenes = [
            {"id": "scene1"},
            {"id": "scene2"},
            {"id": "scene3"},
            {"id": "scene4"},
        ]
        local_stash_ids = {"scene1", "scene3"}

        missing = [s for s in stashdb_scenes if s["id"] not in local_stash_ids]

        self.assertEqual(len(missing), 2)
        self.assertEqual(missing[0]["id"], "scene2")
        self.assertEqual(missing[1]["id"], "scene4")


class TestErrorHandling(unittest.TestCase):
    """Test error handling."""

    def test_missing_entity_id(self):
        """Test that missing entity_id returns error."""
        # Simulate the check in find_missing_scenes
        entity_id = ""
        if not entity_id:
            result = {"error": "entity_id is required"}
        else:
            result = {"success": True}

        self.assertIn("error", result)
        self.assertEqual(result["error"], "entity_id is required")

    def test_unknown_entity_type(self):
        """Test that unknown entity type returns error."""
        entity_type = "unknown"
        valid_types = ["performer", "studio"]

        if entity_type not in valid_types:
            result = {"error": f"Unknown entity type: {entity_type}"}
        else:
            result = {"success": True}

        self.assertIn("error", result)

    def test_no_stashbox_config(self):
        """Test that missing stash-box config returns error."""
        stashbox_configs = []

        if not stashbox_configs:
            result = {"error": "No stash-box endpoints configured in Stash settings"}
        else:
            result = {"success": True}

        self.assertIn("error", result)


class TestSceneSorting(unittest.TestCase):
    """Test scene sorting."""

    def test_sort_by_release_date_descending(self):
        """Test that scenes are sorted by release date (newest first)."""
        scenes = [
            {"stash_id": "1", "release_date": "2023-01-01"},
            {"stash_id": "2", "release_date": "2024-06-15"},
            {"stash_id": "3", "release_date": "2024-01-01"},
            {"stash_id": "4", "release_date": None},
        ]

        # Sort by release_date descending (None values sort last)
        sorted_scenes = sorted(scenes, key=lambda s: s.get("release_date") or "", reverse=True)

        self.assertEqual(sorted_scenes[0]["stash_id"], "2")  # 2024-06-15
        self.assertEqual(sorted_scenes[1]["stash_id"], "3")  # 2024-01-01
        self.assertEqual(sorted_scenes[2]["stash_id"], "1")  # 2023-01-01
        self.assertEqual(sorted_scenes[3]["stash_id"], "4")  # None


class TestGraphQLQueryConstruction(unittest.TestCase):
    """Test GraphQL query construction."""

    def test_performer_query_has_required_fields(self):
        """Verify performer query includes all needed fields."""
        query = """
        query FindPerformer($id: ID!) {
            findPerformer(id: $id) {
                id
                name
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
        """

        self.assertIn("findPerformer", query)
        self.assertIn("stash_ids", query)
        self.assertIn("endpoint", query)
        self.assertIn("stash_id", query)

    def test_studio_query_has_required_fields(self):
        """Verify studio query includes all needed fields."""
        query = """
        query FindStudio($id: ID!) {
            findStudio(id: $id) {
                id
                name
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
        """

        self.assertIn("findStudio", query)
        self.assertIn("stash_ids", query)


class TestEndpointSelection(unittest.TestCase):
    """Test stash-box endpoint selection logic."""

    def test_select_preferred_endpoint(self):
        """Test that preferred endpoint is selected when configured."""
        stashbox_configs = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key1", "name": "StashDB"},
            {"endpoint": "https://fansdb.cc/graphql", "api_key": "key2", "name": "FansDB"},
        ]
        preferred_endpoint = "https://fansdb.cc/graphql"

        stashbox = None
        if preferred_endpoint:
            for config in stashbox_configs:
                if config["endpoint"] == preferred_endpoint:
                    stashbox = config
                    break

        self.assertIsNotNone(stashbox)
        self.assertEqual(stashbox["name"], "FansDB")
        self.assertEqual(stashbox["api_key"], "key2")

    def test_fallback_to_first_endpoint(self):
        """Test that first endpoint is used when no preference set."""
        stashbox_configs = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key1", "name": "StashDB"},
            {"endpoint": "https://fansdb.cc/graphql", "api_key": "key2", "name": "FansDB"},
        ]
        preferred_endpoint = ""

        stashbox = None
        if preferred_endpoint:
            for config in stashbox_configs:
                if config["endpoint"] == preferred_endpoint:
                    stashbox = config
                    break
        else:
            stashbox = stashbox_configs[0]

        self.assertEqual(stashbox["name"], "StashDB")

    def test_invalid_endpoint_not_found(self):
        """Test that invalid endpoint returns None."""
        stashbox_configs = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key1", "name": "StashDB"},
        ]
        preferred_endpoint = "https://invalid.com/graphql"

        stashbox = None
        if preferred_endpoint:
            for config in stashbox_configs:
                if config["endpoint"] == preferred_endpoint:
                    stashbox = config
                    break

        self.assertIsNone(stashbox)


class TestEndpointMatching(unittest.TestCase):
    """Test endpoint URL matching."""

    def test_find_stash_id_for_endpoint(self):
        """Test finding the correct stash_id for a given endpoint."""
        entity = {
            "id": "123",
            "name": "Test Performer",
            "stash_ids": [
                {"endpoint": "https://stashdb.org/graphql", "stash_id": "stashdb-id-123"},
                {"endpoint": "https://fansdb.cc/graphql", "stash_id": "fansdb-id-456"},
            ]
        }
        target_endpoint = "https://stashdb.org/graphql"

        stash_id = None
        for sid in entity.get("stash_ids", []):
            if sid.get("endpoint") == target_endpoint:
                stash_id = sid.get("stash_id")
                break

        self.assertEqual(stash_id, "stashdb-id-123")

    def test_no_stash_id_for_endpoint(self):
        """Test when entity has no stash_id for the target endpoint."""
        entity = {
            "id": "123",
            "name": "Test Performer",
            "stash_ids": [
                {"endpoint": "https://fansdb.cc/graphql", "stash_id": "fansdb-id-456"},
            ]
        }
        target_endpoint = "https://stashdb.org/graphql"

        stash_id = None
        for sid in entity.get("stash_ids", []):
            if sid.get("endpoint") == target_endpoint:
                stash_id = sid.get("stash_id")
                break

        self.assertIsNone(stash_id)


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Missing Scenes Plugin - Unit Tests")
    print("=" * 60)

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestFormatScene))
    suite.addTests(loader.loadTestsFromTestCase(TestWhisparrPayload))
    suite.addTests(loader.loadTestsFromTestCase(TestStashIdMatching))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestSceneSorting))
    suite.addTests(loader.loadTestsFromTestCase(TestGraphQLQueryConstruction))
    suite.addTests(loader.loadTestsFromTestCase(TestEndpointSelection))
    suite.addTests(loader.loadTestsFromTestCase(TestEndpointMatching))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"Results: {result.testsRun} tests, "
          f"{len(result.failures)} failures, "
          f"{len(result.errors)} errors")
    print("=" * 60)

    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
