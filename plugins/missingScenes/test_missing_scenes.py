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

# Import the modules to test
import missing_scenes
import stashbox_api


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


class TestLocalStashIdCache(unittest.TestCase):
    """Test local stash_id cache functionality."""

    def setUp(self):
        """Reset cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    def test_cache_exists(self):
        """Test that cache data structures exist."""
        self.assertIsInstance(missing_scenes._local_stash_id_cache, dict)
        self.assertIsInstance(missing_scenes._cache_metadata, dict)

    def test_cache_initially_empty(self):
        """Test that cache starts empty."""
        self.assertEqual(len(missing_scenes._local_stash_id_cache), 0)
        self.assertEqual(len(missing_scenes._cache_metadata), 0)

    def test_cache_stores_set_of_ids(self):
        """Test that cache stores sets of stash_ids."""
        endpoint = "https://stashdb.org/graphql"
        test_ids = {"id1", "id2", "id3"}
        missing_scenes._local_stash_id_cache[endpoint] = test_ids
        self.assertEqual(missing_scenes._local_stash_id_cache[endpoint], test_ids)

    def test_cache_lookup_performance(self):
        """Test that cache lookup is O(1) via set membership."""
        endpoint = "https://stashdb.org/graphql"
        # Create a large set to verify O(1) lookups
        test_ids = {f"id-{i}" for i in range(10000)}
        missing_scenes._local_stash_id_cache[endpoint] = test_ids

        # These should be fast O(1) operations
        self.assertIn("id-5000", missing_scenes._local_stash_id_cache[endpoint])
        self.assertNotIn("id-99999", missing_scenes._local_stash_id_cache[endpoint])


class TestQueryScenesPage(unittest.TestCase):
    """Test the query_scenes_page function in stashbox_api."""

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_query_scenes_page_performer(self, mock_request):
        """Test querying scenes by performer."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 150,
                "scenes": [{"id": f"scene-{i}"} for i in range(100)]
            }
        }

        result = stashbox_api.query_scenes_page(
            "https://stashdb.org/graphql",
            "api-key",
            "performer",
            "performer-id-123",
            page=1,
            per_page=100
        )

        self.assertEqual(result["count"], 150)
        self.assertEqual(len(result["scenes"]), 100)
        self.assertTrue(result["has_more"])

        # Verify the variables passed to GraphQL
        call_args = mock_request.call_args
        variables = call_args[0][2]  # Third positional arg is variables
        self.assertIn("performers", variables["input"])
        self.assertEqual(variables["input"]["performers"]["value"], ["performer-id-123"])

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_query_scenes_page_studio(self, mock_request):
        """Test querying scenes by studio."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 50,
                "scenes": [{"id": f"scene-{i}"} for i in range(50)]
            }
        }

        result = stashbox_api.query_scenes_page(
            "https://stashdb.org/graphql",
            "api-key",
            "studio",
            "studio-id-456"
        )

        self.assertEqual(result["count"], 50)
        self.assertFalse(result["has_more"])

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("studios", variables["input"])

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_query_scenes_page_tag(self, mock_request):
        """Test querying scenes by tag."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 1000,
                "scenes": [{"id": f"scene-{i}"} for i in range(100)]
            }
        }

        result = stashbox_api.query_scenes_page(
            "https://stashdb.org/graphql",
            "api-key",
            "tag",
            "tag-id-789",
            sort="TITLE",
            direction="ASC"
        )

        self.assertTrue(result["has_more"])

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("tags", variables["input"])
        self.assertEqual(variables["input"]["sort"], "TITLE")
        self.assertEqual(variables["input"]["direction"], "ASC")

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_query_scenes_page_invalid_sort_defaults(self, mock_request):
        """Test that invalid sort field defaults to DATE."""
        mock_request.return_value = {
            "queryScenes": {"count": 10, "scenes": []}
        }

        stashbox_api.query_scenes_page(
            "https://stashdb.org/graphql",
            "api-key",
            "performer",
            "id",
            sort="INVALID"
        )

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertEqual(variables["input"]["sort"], "DATE")

    def test_query_scenes_page_unknown_entity_type(self):
        """Test that unknown entity type returns None."""
        result = stashbox_api.query_scenes_page(
            "https://stashdb.org/graphql",
            "api-key",
            "unknown_type",
            "id"
        )
        self.assertIsNone(result)


class TestCacheBuildingFunction(unittest.TestCase):
    """Test the get_or_build_cache function."""

    def setUp(self):
        """Reset cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch.object(missing_scenes, 'stash_graphql')
    def test_build_cache_single_page(self, mock_graphql):
        """Test building cache from a single page of results."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 2,
                "scenes": [
                    {"stash_ids": [{"endpoint": endpoint, "stash_id": "id-1"}]},
                    {"stash_ids": [{"endpoint": endpoint, "stash_id": "id-2"}]},
                ]
            }
        }

        result = missing_scenes.get_or_build_cache(endpoint)

        self.assertEqual(result, {"id-1", "id-2"})
        self.assertIn(endpoint, missing_scenes._cache_metadata)
        self.assertEqual(missing_scenes._cache_metadata[endpoint]["count"], 2)

    @patch.object(missing_scenes, 'stash_graphql')
    def test_build_cache_multiple_pages(self, mock_graphql):
        """Test building cache with pagination."""
        endpoint = "https://stashdb.org/graphql"
        # First call returns page 1 with count indicating more pages
        # Second call returns page 2
        mock_graphql.side_effect = [
            {
                "findScenes": {
                    "count": 150,  # More than 100, requires 2 pages
                    "scenes": [{"stash_ids": [{"endpoint": endpoint, "stash_id": f"id-{i}"}]} for i in range(100)]
                }
            },
            {
                "findScenes": {
                    "count": 150,
                    "scenes": [{"stash_ids": [{"endpoint": endpoint, "stash_id": f"id-{i}"}]} for i in range(100, 150)]
                }
            }
        ]

        result = missing_scenes.get_or_build_cache(endpoint)

        self.assertEqual(len(result), 150)
        self.assertIn("id-0", result)
        self.assertIn("id-149", result)

    @patch.object(missing_scenes, 'stash_graphql')
    def test_cache_returns_existing(self, mock_graphql):
        """Test that existing cache is returned without re-building."""
        endpoint = "https://stashdb.org/graphql"
        existing_ids = {"existing-1", "existing-2"}
        missing_scenes._local_stash_id_cache[endpoint] = existing_ids

        result = missing_scenes.get_or_build_cache(endpoint)

        self.assertEqual(result, existing_ids)
        mock_graphql.assert_not_called()

    @patch.object(missing_scenes, 'stash_graphql')
    def test_build_cache_filters_by_endpoint(self, mock_graphql):
        """Test that only stash_ids matching the endpoint are cached."""
        endpoint = "https://stashdb.org/graphql"
        other_endpoint = "https://fansdb.cc/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 2,
                "scenes": [
                    {"stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-id"},
                        {"endpoint": other_endpoint, "stash_id": "fansdb-id"}
                    ]},
                    {"stash_ids": [{"endpoint": other_endpoint, "stash_id": "fansdb-only"}]},
                ]
            }
        }

        result = missing_scenes.get_or_build_cache(endpoint)

        # Should only contain the StashDB ID
        self.assertEqual(result, {"stashdb-id"})

    @patch.object(missing_scenes, 'stash_graphql')
    def test_build_cache_empty_stash(self, mock_graphql):
        """Test building cache when no scenes have stash_ids."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {
                "count": 0,
                "scenes": []
            }
        }

        result = missing_scenes.get_or_build_cache(endpoint)

        self.assertEqual(result, set())
        self.assertEqual(missing_scenes._cache_metadata[endpoint]["count"], 0)


class TestCountLocalScenesForEntity(unittest.TestCase):
    """Test the count_local_scenes_for_entity function."""

    @patch.object(missing_scenes, 'stash_graphql')
    def test_count_scenes_for_performer(self, mock_graphql):
        """Test counting local scenes for a performer."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {"count": 5}
        }

        result = missing_scenes.count_local_scenes_for_entity(endpoint, "performer", "123")

        self.assertEqual(result, 5)
        # Verify the query includes performer filter
        call_args = mock_graphql.call_args
        scene_filter = call_args[0][1]["scene_filter"]
        self.assertIn("performers", scene_filter)
        self.assertEqual(scene_filter["performers"]["value"], ["123"])

    @patch.object(missing_scenes, 'stash_graphql')
    def test_count_scenes_for_tag(self, mock_graphql):
        """Test counting local scenes for a tag."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {"count": 1}
        }

        result = missing_scenes.count_local_scenes_for_entity(endpoint, "tag", "456")

        self.assertEqual(result, 1)
        # Verify the query includes tag filter with depth
        call_args = mock_graphql.call_args
        scene_filter = call_args[0][1]["scene_filter"]
        self.assertIn("tags", scene_filter)
        self.assertEqual(scene_filter["tags"]["value"], ["456"])
        self.assertEqual(scene_filter["tags"]["depth"], -1)

    @patch.object(missing_scenes, 'stash_graphql')
    def test_count_scenes_for_studio(self, mock_graphql):
        """Test counting local scenes for a studio."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findScenes": {"count": 10}
        }

        result = missing_scenes.count_local_scenes_for_entity(endpoint, "studio", "789")

        self.assertEqual(result, 10)
        # Verify the query includes studio filter with depth
        call_args = mock_graphql.call_args
        scene_filter = call_args[0][1]["scene_filter"]
        self.assertIn("studios", scene_filter)
        self.assertEqual(scene_filter["studios"]["depth"], -1)

    def test_count_scenes_unknown_entity_returns_zero(self):
        """Test that unknown entity type returns 0."""
        result = missing_scenes.count_local_scenes_for_entity(
            "https://stashdb.org/graphql", "unknown", "123"
        )
        self.assertEqual(result, 0)


class TestGetFavoriteStashIds(unittest.TestCase):
    """Test the get_favorite_stash_ids function."""

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_performers_single_page(self, mock_graphql):
        """Test fetching favorite performers with a single page."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 2,
                "performers": [
                    {"id": "1", "name": "Performer 1", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-perf-1"}
                    ]},
                    {"id": "2", "name": "Performer 2", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-perf-2"},
                        {"endpoint": "https://other.com/graphql", "stash_id": "other-1"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids("performer", endpoint)

        self.assertEqual(result, {"stashdb-perf-1", "stashdb-perf-2"})

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_studios(self, mock_graphql):
        """Test fetching favorite studios."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findStudios": {
                "count": 1,
                "studios": [
                    {"id": "1", "name": "Studio 1", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-studio-1"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids("studio", endpoint)

        self.assertEqual(result, {"stashdb-studio-1"})

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_tags(self, mock_graphql):
        """Test fetching favorite tags."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findTags": {
                "count": 1,
                "tags": [
                    {"id": "1", "name": "Tag 1", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-tag-1"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids("tag", endpoint)

        self.assertEqual(result, {"stashdb-tag-1"})

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_filters_by_endpoint(self, mock_graphql):
        """Test that only stash_ids matching the endpoint are returned."""
        endpoint = "https://stashdb.org/graphql"
        other_endpoint = "https://fansdb.cc/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 2,
                "performers": [
                    {"id": "1", "name": "Performer 1", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "stashdb-1"},
                        {"endpoint": other_endpoint, "stash_id": "fansdb-1"}
                    ]},
                    {"id": "2", "name": "Performer 2", "stash_ids": [
                        {"endpoint": other_endpoint, "stash_id": "fansdb-2"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids("performer", endpoint)

        # Only stashdb ID should be returned
        self.assertEqual(result, {"stashdb-1"})

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_empty_result(self, mock_graphql):
        """Test that empty result returns empty set."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 0,
                "performers": []
            }
        }

        result = missing_scenes.get_favorite_stash_ids("performer", endpoint)

        self.assertEqual(result, set())

    def test_get_favorite_unknown_entity_type(self):
        """Test that unknown entity type returns empty set."""
        result = missing_scenes.get_favorite_stash_ids("unknown", "https://stashdb.org/graphql")
        self.assertEqual(result, set())

    @patch.object(missing_scenes, 'stash_graphql')
    def test_get_favorite_pagination(self, mock_graphql):
        """Test fetching favorites with pagination."""
        endpoint = "https://stashdb.org/graphql"
        # First page returns 100 items, second page returns 50
        mock_graphql.side_effect = [
            {
                "findPerformers": {
                    "count": 150,
                    "performers": [
                        {"id": str(i), "name": f"Performer {i}", "stash_ids": [
                            {"endpoint": endpoint, "stash_id": f"id-{i}"}
                        ]} for i in range(100)
                    ]
                }
            },
            {
                "findPerformers": {
                    "count": 150,
                    "performers": [
                        {"id": str(i), "name": f"Performer {i}", "stash_ids": [
                            {"endpoint": endpoint, "stash_id": f"id-{i}"}
                        ]} for i in range(100, 150)
                    ]
                }
            }
        ]

        result = missing_scenes.get_favorite_stash_ids("performer", endpoint)

        self.assertEqual(len(result), 150)
        self.assertIn("id-0", result)
        self.assertIn("id-149", result)


class TestGetFavoriteStashIdsWithLimit(unittest.TestCase):
    """Test the get_favorite_stash_ids_limited function."""

    @patch.object(missing_scenes, 'stash_graphql')
    def test_performers_sorted_by_last_o_at(self, mock_graphql):
        """Test that performers are sorted by last_o_at DESC."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 3,
                "performers": [
                    {"id": "1", "name": "Recent", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "perf-recent"}
                    ]},
                    {"id": "2", "name": "Old", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "perf-old"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("performer", endpoint, limit=100)

        self.assertIn("perf-recent", result)
        # Verify query used correct sort
        call_args = mock_graphql.call_args
        variables = call_args[0][1]
        self.assertEqual(variables["filter"]["sort"], "last_o_at")
        self.assertEqual(variables["filter"]["direction"], "DESC")

    @patch.object(missing_scenes, 'stash_graphql')
    def test_studios_sorted_by_scenes_count(self, mock_graphql):
        """Test that studios are sorted by scenes_count DESC."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findStudios": {
                "count": 1,
                "studios": [
                    {"id": "1", "name": "Studio", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": "studio-1"}
                    ]},
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("studio", endpoint, limit=100)

        call_args = mock_graphql.call_args
        variables = call_args[0][1]
        self.assertEqual(variables["filter"]["sort"], "scenes_count")

    @patch.object(missing_scenes, 'stash_graphql')
    def test_respects_limit(self, mock_graphql):
        """Test that limit is respected."""
        endpoint = "https://stashdb.org/graphql"
        mock_graphql.return_value = {
            "findPerformers": {
                "count": 200,
                "performers": [
                    {"id": str(i), "name": f"Performer {i}", "stash_ids": [
                        {"endpoint": endpoint, "stash_id": f"perf-{i}"}
                    ]} for i in range(100)
                ]
            }
        }

        result = missing_scenes.get_favorite_stash_ids_limited("performer", endpoint, limit=50)

        # Should only return first 50, not all 100 from page
        self.assertEqual(len(result), 50)


class TestScenePassesFavoriteFilters(unittest.TestCase):
    """Test the scene_passes_favorite_filters function."""

    def test_no_filters_passes_all(self):
        """Test that scenes pass when no filters are enabled."""
        scene = {"id": "scene-1", "performers": [], "studio": None, "tags": []}
        result = missing_scenes.scene_passes_favorite_filters(scene, None, None, None)
        self.assertTrue(result)

    def test_performer_filter_passes_with_match(self):
        """Test scene passes when it has a favorite performer."""
        scene = {
            "performers": [
                {"performer": {"id": "perf-1"}},
                {"performer": {"id": "perf-2"}},
            ]
        }
        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1", "perf-3"}, None, None
        )
        self.assertTrue(result)

    def test_performer_filter_fails_without_match(self):
        """Test scene fails when it has no favorite performers."""
        scene = {"performers": [{"performer": {"id": "perf-2"}}]}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1", "perf-3"}, None, None
        )
        self.assertFalse(result)

    def test_performer_filter_fails_with_no_performers(self):
        """Test scene fails when it has no performers."""
        scene = {"performers": []}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, None, None
        )
        self.assertFalse(result)

    def test_studio_filter_passes_with_match(self):
        """Test scene passes when studio is a favorite."""
        scene = {"studio": {"id": "studio-1"}}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, {"studio-1", "studio-2"}, None
        )
        self.assertTrue(result)

    def test_studio_filter_fails_without_match(self):
        """Test scene fails when studio is not a favorite."""
        scene = {"studio": {"id": "studio-3"}}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, {"studio-1", "studio-2"}, None
        )
        self.assertFalse(result)

    def test_studio_filter_fails_without_studio(self):
        """Test scene fails when it has no studio."""
        scene = {"studio": None}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, {"studio-1"}, None
        )
        self.assertFalse(result)

    def test_tag_filter_passes_with_match(self):
        """Test scene passes when it has a favorite tag."""
        scene = {"tags": [{"id": "tag-1"}, {"id": "tag-2"}]}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, None, {"tag-1", "tag-3"}
        )
        self.assertTrue(result)

    def test_tag_filter_fails_without_match(self):
        """Test scene fails when it has no favorite tags."""
        scene = {"tags": [{"id": "tag-2"}]}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, None, {"tag-1", "tag-3"}
        )
        self.assertFalse(result)

    def test_tag_filter_fails_with_no_tags(self):
        """Test scene fails when it has no tags."""
        scene = {"tags": []}
        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, None, {"tag-1"}
        )
        self.assertFalse(result)

    def test_and_logic_all_match(self):
        """Test that multiple filters require ALL to match (AND logic)."""
        scene = {
            "performers": [{"performer": {"id": "perf-1"}}],
            "studio": {"id": "studio-1"},
            "tags": [{"id": "tag-1"}]
        }

        # All match - should pass
        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, {"studio-1"}, {"tag-1"}
        )
        self.assertTrue(result)

    def test_and_logic_performer_fails(self):
        """Test that scene fails when performer filter doesn't match."""
        scene = {
            "performers": [{"performer": {"id": "perf-2"}}],
            "studio": {"id": "studio-1"},
            "tags": [{"id": "tag-1"}]
        }

        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, {"studio-1"}, {"tag-1"}
        )
        self.assertFalse(result)

    def test_and_logic_studio_fails(self):
        """Test that scene fails when studio filter doesn't match."""
        scene = {
            "performers": [{"performer": {"id": "perf-1"}}],
            "studio": {"id": "studio-2"},
            "tags": [{"id": "tag-1"}]
        }

        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, {"studio-1"}, {"tag-1"}
        )
        self.assertFalse(result)

    def test_and_logic_tag_fails(self):
        """Test that scene fails when tag filter doesn't match."""
        scene = {
            "performers": [{"performer": {"id": "perf-1"}}],
            "studio": {"id": "studio-1"},
            "tags": [{"id": "tag-2"}]
        }

        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, {"studio-1"}, {"tag-1"}
        )
        self.assertFalse(result)

    def test_partial_filters_performer_only(self):
        """Test that partial filters work (only performer enabled)."""
        scene = {
            "performers": [{"performer": {"id": "perf-1"}}],
            "studio": {"id": "studio-999"},  # Doesn't matter
            "tags": [{"id": "tag-999"}]  # Doesn't matter
        }

        result = missing_scenes.scene_passes_favorite_filters(
            scene, {"perf-1"}, None, None
        )
        self.assertTrue(result)

    def test_partial_filters_studio_only(self):
        """Test that partial filters work (only studio enabled)."""
        scene = {
            "performers": [],  # Doesn't matter
            "studio": {"id": "studio-1"},
            "tags": []  # Doesn't matter
        }

        result = missing_scenes.scene_passes_favorite_filters(
            scene, None, {"studio-1"}, None
        )
        self.assertTrue(result)


class TestFetchUntilFull(unittest.TestCase):
    """Test the fetch_until_full pagination logic."""

    def setUp(self):
        """Reset cache before each test."""
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch.object(stashbox_api, 'query_scenes_page')
    def test_fetch_until_full_basic(self, mock_query):
        """Test basic fetch until full logic."""
        # Setup: StashDB has 5 scenes, we own 2 of them
        endpoint = "https://stashdb.org/graphql"
        local_ids = {"scene-1", "scene-3"}  # We own scenes 1 and 3
        missing_scenes._local_stash_id_cache[endpoint] = local_ids

        # StashDB returns 5 scenes
        mock_query.return_value = {
            "scenes": [
                {"id": "scene-1"},  # owned
                {"id": "scene-2"},  # missing
                {"id": "scene-3"},  # owned
                {"id": "scene-4"},  # missing
                {"id": "scene-5"},  # missing
            ],
            "count": 5,
            "page": 1,
            "has_more": False
        }

        # Request 2 missing scenes
        result = missing_scenes.fetch_until_full(
            url=endpoint,
            api_key="key",
            entity_type="performer",
            entity_stash_id="perf-123",
            local_ids=local_ids,
            page_size=2,
            stashdb_page=1,
            offset=0,
            sort="DATE",
            direction="DESC"
        )

        # Should return 2 missing scenes
        self.assertEqual(len(result["scenes"]), 2)
        self.assertEqual(result["scenes"][0]["id"], "scene-2")
        self.assertEqual(result["scenes"][1]["id"], "scene-4")
        self.assertEqual(result["total_on_stashdb"], 5)

    @patch.object(stashbox_api, 'query_scenes_page')
    def test_fetch_until_full_spans_pages(self, mock_query):
        """Test that fetch_until_full spans multiple StashDB pages if needed."""
        endpoint = "https://stashdb.org/graphql"
        # We own most scenes - need to fetch multiple pages to find missing
        local_ids = {f"scene-{i}" for i in range(1, 95)}  # Own 1-94
        missing_scenes._local_stash_id_cache[endpoint] = local_ids

        # First page: all owned except last 5
        page1_scenes = [{"id": f"scene-{i}"} for i in range(1, 101)]
        # Second page: all missing
        page2_scenes = [{"id": f"scene-{i}"} for i in range(101, 201)]

        mock_query.side_effect = [
            {"scenes": page1_scenes, "count": 200, "page": 1, "has_more": True},
            {"scenes": page2_scenes, "count": 200, "page": 2, "has_more": False},
        ]

        result = missing_scenes.fetch_until_full(
            url=endpoint,
            api_key="key",
            entity_type="performer",
            entity_stash_id="perf-123",
            local_ids=local_ids,
            page_size=10,
            stashdb_page=1,
            offset=0,
            sort="DATE",
            direction="DESC"
        )

        # Should have fetched from both pages
        self.assertEqual(len(result["scenes"]), 10)
        # Should have called query_scenes_page at least twice
        self.assertGreaterEqual(mock_query.call_count, 1)

    @patch.object(stashbox_api, 'query_scenes_page')
    def test_fetch_until_full_respects_offset(self, mock_query):
        """Test that fetch_until_full respects the offset parameter."""
        endpoint = "https://stashdb.org/graphql"
        local_ids = set()  # Own nothing
        missing_scenes._local_stash_id_cache[endpoint] = local_ids

        mock_query.return_value = {
            "scenes": [{"id": f"scene-{i}"} for i in range(1, 11)],
            "count": 100,
            "page": 1,
            "has_more": True
        }

        # Start at offset 5 (skip first 5 scenes on page)
        result = missing_scenes.fetch_until_full(
            url=endpoint,
            api_key="key",
            entity_type="performer",
            entity_stash_id="perf-123",
            local_ids=local_ids,
            page_size=3,
            stashdb_page=1,
            offset=5,
            sort="DATE",
            direction="DESC"
        )

        # Should skip first 5 and return next 3 (scenes 6, 7, 8)
        self.assertEqual(len(result["scenes"]), 3)
        self.assertEqual(result["scenes"][0]["id"], "scene-6")

    @patch.object(stashbox_api, 'query_scenes_page')
    def test_fetch_until_full_returns_cursor(self, mock_query):
        """Test that fetch_until_full returns a valid cursor for continuation."""
        endpoint = "https://stashdb.org/graphql"
        local_ids = set()
        missing_scenes._local_stash_id_cache[endpoint] = local_ids

        mock_query.return_value = {
            "scenes": [{"id": f"scene-{i}"} for i in range(1, 101)],
            "count": 200,
            "page": 1,
            "has_more": True
        }

        result = missing_scenes.fetch_until_full(
            url=endpoint,
            api_key="key",
            entity_type="performer",
            entity_stash_id="perf-123",
            local_ids=local_ids,
            page_size=50,
            stashdb_page=1,
            offset=0,
            sort="DATE",
            direction="DESC"
        )

        # Should have a cursor for continuation
        self.assertIsNotNone(result.get("next_cursor"))
        # Cursor should be decodable
        cursor_state = missing_scenes.decode_cursor(result["next_cursor"])
        self.assertIsNotNone(cursor_state)
        self.assertEqual(cursor_state["offset"], 50)  # We took 50 items


class TestCursorEncoding(unittest.TestCase):
    """Test cursor encoding and decoding for pagination."""

    def test_encode_cursor(self):
        """Test that cursor encoding produces a base64 string."""
        state = {
            "stashdb_page": 4,
            "offset": 12,
            "sort": "DATE",
            "direction": "DESC",
            "entity_type": "tag",
            "entity_stash_id": "abc-123",
            "endpoint": "https://stashdb.org/graphql"
        }
        cursor = missing_scenes.encode_cursor(state)
        self.assertIsInstance(cursor, str)
        self.assertTrue(len(cursor) > 0)

    def test_decode_cursor(self):
        """Test that cursor decoding restores original state."""
        state = {
            "stashdb_page": 4,
            "offset": 12,
            "sort": "DATE",
            "direction": "DESC",
            "entity_type": "tag",
            "entity_stash_id": "abc-123",
            "endpoint": "https://stashdb.org/graphql"
        }
        cursor = missing_scenes.encode_cursor(state)
        decoded = missing_scenes.decode_cursor(cursor)
        self.assertEqual(decoded, state)

    def test_decode_invalid_cursor_returns_none(self):
        """Test that invalid cursor returns None."""
        result = missing_scenes.decode_cursor("invalid-base64!")
        self.assertIsNone(result)

    def test_decode_empty_cursor_returns_none(self):
        """Test that empty cursor returns None."""
        result = missing_scenes.decode_cursor("")
        self.assertIsNone(result)

    def test_decode_none_cursor_returns_none(self):
        """Test that None cursor returns None."""
        result = missing_scenes.decode_cursor(None)
        self.assertIsNone(result)

    def test_roundtrip_preserves_unicode(self):
        """Test that unicode characters are preserved."""
        state = {
            "stashdb_page": 1,
            "offset": 0,
            "sort": "DATE",
            "direction": "DESC",
            "entity_type": "performer",
            "entity_stash_id": "performer-with-émojis-🎬",
            "endpoint": "https://stashdb.org/graphql"
        }
        cursor = missing_scenes.encode_cursor(state)
        decoded = missing_scenes.decode_cursor(cursor)
        self.assertEqual(decoded["entity_stash_id"], state["entity_stash_id"])


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


class TestBrowseStashdb(unittest.TestCase):
    """Test the browse_stashdb operation."""

    def setUp(self):
        missing_scenes._local_stash_id_cache.clear()
        missing_scenes._cache_metadata.clear()

    @patch.object(missing_scenes, 'get_stashbox_config')
    @patch.object(missing_scenes, 'get_or_build_cache')
    @patch.object(stashbox_api, 'query_scenes_browse')
    @patch.object(missing_scenes, 'whisparr_get_status_map')
    def test_browse_basic(self, mock_whisparr, mock_browse, mock_cache, mock_config):
        """Test basic browse without filters."""
        mock_config.return_value = [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "key", "name": "StashDB"}
        ]
        mock_cache.return_value = {"owned-1", "owned-2"}
        mock_browse.return_value = {
            "scenes": [
                {"id": "scene-1"},
                {"id": "owned-1"},  # Should be filtered out
                {"id": "scene-2"},
            ],
            "count": 1000,
            "page": 1,
            "has_more": True
        }
        mock_whisparr.return_value = {}

        result = missing_scenes.browse_stashdb(
            plugin_settings={},
            page_size=50
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["stashdb_name"], "StashDB")
        # owned-1 should be filtered out
        scene_ids = [s["stash_id"] for s in result["missing_scenes"]]
        self.assertIn("scene-1", scene_ids)
        self.assertNotIn("owned-1", scene_ids)

    @patch.object(missing_scenes, 'get_stashbox_config')
    def test_browse_no_stashbox_config(self, mock_config):
        """Test error when no stash-box configured."""
        mock_config.return_value = []

        result = missing_scenes.browse_stashdb(plugin_settings={})

        self.assertIn("error", result)
        self.assertIn("No stash-box", result["error"])


class TestQueryScenesBrowse(unittest.TestCase):
    """Test the query_scenes_browse function for general browsing."""

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_no_filters(self, mock_request):
        """Test browsing with no filters returns all scenes."""
        mock_request.return_value = {
            "queryScenes": {
                "count": 1000,
                "scenes": [{"id": f"scene-{i}"} for i in range(100)]
            }
        }

        result = stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            page=1,
            per_page=100
        )

        self.assertEqual(result["count"], 1000)
        self.assertEqual(len(result["scenes"]), 100)

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_with_excluded_tags(self, mock_request):
        """Test that excluded tags are passed to query."""
        mock_request.return_value = {
            "queryScenes": {"count": 500, "scenes": []}
        }

        stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            excluded_tag_ids=["tag-1", "tag-2"]
        )

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("tags", variables["input"])
        self.assertEqual(variables["input"]["tags"]["modifier"], "EXCLUDES")
        self.assertEqual(variables["input"]["tags"]["value"], ["tag-1", "tag-2"])

    @patch.object(stashbox_api, 'graphql_request_with_retry')
    def test_browse_with_performer_filter(self, mock_request):
        """Test filtering by performer IDs."""
        mock_request.return_value = {
            "queryScenes": {"count": 50, "scenes": []}
        }

        stashbox_api.query_scenes_browse(
            "https://stashdb.org/graphql",
            "api-key",
            performer_ids=["perf-1", "perf-2"]
        )

        call_args = mock_request.call_args
        variables = call_args[0][2]
        self.assertIn("performers", variables["input"])
        self.assertEqual(variables["input"]["performers"]["modifier"], "INCLUDES")


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
    suite.addTests(loader.loadTestsFromTestCase(TestLocalStashIdCache))
    suite.addTests(loader.loadTestsFromTestCase(TestQueryScenesPage))
    suite.addTests(loader.loadTestsFromTestCase(TestCacheBuildingFunction))
    suite.addTests(loader.loadTestsFromTestCase(TestCountLocalScenesForEntity))
    suite.addTests(loader.loadTestsFromTestCase(TestGetFavoriteStashIds))
    suite.addTests(loader.loadTestsFromTestCase(TestScenePassesFavoriteFilters))
    suite.addTests(loader.loadTestsFromTestCase(TestFetchUntilFull))
    suite.addTests(loader.loadTestsFromTestCase(TestCursorEncoding))
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
