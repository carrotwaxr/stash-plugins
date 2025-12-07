#!/usr/bin/env python3
"""
Unit tests for Scene Matcher plugin.
Tests scoring, formatting, and sorting logic without requiring network access.
"""

import sys
import os
import unittest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scene_matcher import score_scene, format_scene


class TestScoreScene(unittest.TestCase):
    """Tests for the score_scene function."""

    def test_no_matches(self):
        """Scene with no matching performers or studio scores 0."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [
                {"performer": {"id": "perf-123"}},
                {"performer": {"id": "perf-456"}}
            ]
        }
        performer_ids = {"perf-999"}  # No match
        studio_id = "studio-xyz"  # No match

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 0)
        self.assertEqual(matching_performers, 0)

    def test_studio_match_only(self):
        """Scene matching only studio scores 3."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [
                {"performer": {"id": "perf-123"}}
            ]
        }
        performer_ids = {"perf-999"}  # No match
        studio_id = "studio-abc"  # Match

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 3)
        self.assertEqual(matching_performers, 0)

    def test_one_performer_match(self):
        """Scene matching one performer scores 2."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [
                {"performer": {"id": "perf-123"}},
                {"performer": {"id": "perf-456"}}
            ]
        }
        performer_ids = {"perf-123"}  # One match
        studio_id = None

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 2)
        self.assertEqual(matching_performers, 1)

    def test_multiple_performer_matches(self):
        """Scene matching multiple performers scores 2 per performer."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [
                {"performer": {"id": "perf-123"}},
                {"performer": {"id": "perf-456"}},
                {"performer": {"id": "perf-789"}}
            ]
        }
        performer_ids = {"perf-123", "perf-456"}  # Two matches
        studio_id = None

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 4)  # 2 * 2
        self.assertEqual(matching_performers, 2)

    def test_studio_and_performers_match(self):
        """Scene matching studio and performers scores combined."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [
                {"performer": {"id": "perf-123"}},
                {"performer": {"id": "perf-456"}}
            ]
        }
        performer_ids = {"perf-123", "perf-456"}  # Two matches
        studio_id = "studio-abc"  # Match

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 7)  # 3 (studio) + 4 (2 performers)
        self.assertEqual(matching_performers, 2)

    def test_empty_performers(self):
        """Scene with no performers still scores studio."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": []
        }
        performer_ids = {"perf-123"}
        studio_id = "studio-abc"

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 3)
        self.assertEqual(matching_performers, 0)

    def test_no_studio(self):
        """Scene with no studio only scores performers."""
        scene = {
            "studio": None,
            "performers": [
                {"performer": {"id": "perf-123"}}
            ]
        }
        performer_ids = {"perf-123"}
        studio_id = "studio-abc"

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 2)
        self.assertEqual(matching_performers, 1)

    def test_missing_performer_id(self):
        """Performers without IDs are skipped."""
        scene = {
            "studio": None,
            "performers": [
                {"performer": {"id": "perf-123"}},
                {"performer": {"id": None}},  # No ID
                {"performer": {}}  # Missing ID key
            ]
        }
        performer_ids = {"perf-123"}
        studio_id = None

        score, matching_performers = score_scene(scene, performer_ids, studio_id)

        self.assertEqual(score, 2)
        self.assertEqual(matching_performers, 1)


class TestFormatScene(unittest.TestCase):
    """Tests for the format_scene function."""

    def test_full_scene_formatting(self):
        """Complete scene is formatted correctly."""
        scene = {
            "title": "Test Scene",
            "details": "Scene description",
            "release_date": "2024-01-15",
            "duration": 1800,
            "code": "ABC123",
            "director": "John Doe",
            "urls": [{"url": "https://example.com/scene"}],
            "studio": {"id": "studio-abc", "name": "Test Studio"},
            "images": [
                {"url": "https://example.com/thumb.jpg", "width": 800, "height": 450}
            ],
            "performers": [
                {
                    "performer": {
                        "id": "perf-123",
                        "name": "Jane Doe",
                        "disambiguation": None,
                        "gender": "FEMALE"
                    },
                    "as": None
                }
            ]
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["stash_id"], "stash-id-xyz")
        self.assertEqual(result["title"], "Test Scene")
        self.assertEqual(result["details"], "Scene description")
        self.assertEqual(result["release_date"], "2024-01-15")
        self.assertEqual(result["duration"], 1800)
        self.assertEqual(result["code"], "ABC123")
        self.assertEqual(result["director"], "John Doe")
        self.assertEqual(result["url"], "https://example.com/scene")
        self.assertEqual(result["thumbnail"], "https://example.com/thumb.jpg")
        self.assertEqual(result["studio"]["id"], "studio-abc")
        self.assertEqual(result["studio"]["name"], "Test Studio")
        self.assertEqual(len(result["performers"]), 1)
        self.assertEqual(result["performers"][0]["name"], "Jane Doe")

    def test_missing_title_uses_default(self):
        """Scene without title uses 'Unknown Title'."""
        scene = {
            "title": None,
            "performers": [],
            "images": [],
            "urls": []
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["title"], "Unknown Title")

    def test_empty_title_uses_default(self):
        """Scene with empty title uses 'Unknown Title'."""
        scene = {
            "title": "",
            "performers": [],
            "images": [],
            "urls": []
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["title"], "Unknown Title")

    def test_prefers_landscape_thumbnail(self):
        """Landscape images are preferred for thumbnails."""
        scene = {
            "title": "Test",
            "performers": [],
            "urls": [],
            "images": [
                {"url": "https://example.com/portrait.jpg", "width": 450, "height": 800},
                {"url": "https://example.com/landscape.jpg", "width": 800, "height": 450},
                {"url": "https://example.com/square.jpg", "width": 500, "height": 500}
            ]
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["thumbnail"], "https://example.com/landscape.jpg")

    def test_falls_back_to_first_image(self):
        """Falls back to first image if no landscape available."""
        scene = {
            "title": "Test",
            "performers": [],
            "urls": [],
            "images": [
                {"url": "https://example.com/portrait.jpg", "width": 450, "height": 800},
                {"url": "https://example.com/square.jpg", "width": 500, "height": 500}
            ]
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["thumbnail"], "https://example.com/portrait.jpg")

    def test_no_images_returns_none(self):
        """No images returns None for thumbnail."""
        scene = {
            "title": "Test",
            "performers": [],
            "urls": [],
            "images": []
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertIsNone(result["thumbnail"])

    def test_no_studio_returns_none(self):
        """No studio returns None for studio field."""
        scene = {
            "title": "Test",
            "performers": [],
            "urls": [],
            "images": [],
            "studio": None
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertIsNone(result["studio"])

    def test_no_urls_returns_none(self):
        """No URLs returns None for url field."""
        scene = {
            "title": "Test",
            "performers": [],
            "urls": [],
            "images": []
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertIsNone(result["url"])

    def test_performer_alias_preserved(self):
        """Performer 'as' alias is preserved."""
        scene = {
            "title": "Test",
            "urls": [],
            "images": [],
            "performers": [
                {
                    "performer": {
                        "id": "perf-123",
                        "name": "Jane Doe",
                        "disambiguation": None,
                        "gender": "FEMALE"
                    },
                    "as": "Jane Smith"
                }
            ]
        }

        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["performers"][0]["as"], "Jane Smith")


def sort_key(x):
    """Sort key matching scene_matcher.py implementation."""
    in_stash = 1 if x["in_local_stash"] else 0
    score = -x["score"]
    date_str = x.get("release_date") or ""
    date_int = int(date_str[:10].replace("-", "")) if date_str else 0
    return (in_stash, score, -date_int)


class TestResultSorting(unittest.TestCase):
    """Tests for result sorting logic."""

    def test_sort_by_in_local_stash(self):
        """Scenes not in local stash sort before scenes in local stash."""
        results = [
            {"in_local_stash": True, "score": 5, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 3, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 5, "release_date": "2024-01-01"},
        ]

        results.sort(key=sort_key)

        # Not in stash should come first
        self.assertFalse(results[0]["in_local_stash"])
        self.assertFalse(results[1]["in_local_stash"])
        self.assertTrue(results[2]["in_local_stash"])

    def test_sort_by_score_within_stash_group(self):
        """Higher scores sort before lower scores within same stash group."""
        results = [
            {"in_local_stash": False, "score": 2, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 5, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 3, "release_date": "2024-01-01"},
        ]

        results.sort(key=sort_key)

        self.assertEqual(results[0]["score"], 5)
        self.assertEqual(results[1]["score"], 3)
        self.assertEqual(results[2]["score"], 2)

    def test_sort_by_date_within_score_group(self):
        """Newer dates sort before older dates within same score group."""
        results = [
            {"in_local_stash": False, "score": 5, "release_date": "2023-06-15"},
            {"in_local_stash": False, "score": 5, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 5, "release_date": "2023-12-25"},
        ]

        results.sort(key=sort_key)

        self.assertEqual(results[0]["release_date"], "2024-01-01")
        self.assertEqual(results[1]["release_date"], "2023-12-25")
        self.assertEqual(results[2]["release_date"], "2023-06-15")

    def test_null_dates_sort_last(self):
        """Scenes without dates sort after scenes with dates."""
        results = [
            {"in_local_stash": False, "score": 5, "release_date": None},
            {"in_local_stash": False, "score": 5, "release_date": "2024-01-01"},
            {"in_local_stash": False, "score": 5, "release_date": ""},
        ]

        results.sort(key=sort_key)

        self.assertEqual(results[0]["release_date"], "2024-01-01")
        # Null and empty both sort after


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases and error conditions."""

    def test_score_with_empty_performer_set(self):
        """Scoring works with empty performer set."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [{"performer": {"id": "perf-123"}}]
        }

        score, matching_performers = score_scene(scene, set(), "studio-abc")

        self.assertEqual(score, 3)  # Just studio
        self.assertEqual(matching_performers, 0)

    def test_score_with_none_studio_id(self):
        """Scoring works when studio_id is None."""
        scene = {
            "studio": {"id": "studio-abc"},
            "performers": [{"performer": {"id": "perf-123"}}]
        }

        score, matching_performers = score_scene(scene, {"perf-123"}, None)

        self.assertEqual(score, 2)  # Just performer
        self.assertEqual(matching_performers, 1)

    def test_format_scene_handles_missing_keys(self):
        """format_scene handles scenes with missing optional keys."""
        scene = {
            "title": "Test",
            "performers": [],
            # Missing: details, release_date, duration, code, director, urls, images, studio
        }

        # Should not raise
        result = format_scene(scene, "stash-id-xyz")

        self.assertEqual(result["title"], "Test")
        self.assertIsNone(result.get("details"))
        self.assertIsNone(result.get("release_date"))
        self.assertIsNone(result.get("duration"))
        self.assertIsNone(result.get("thumbnail"))
        self.assertIsNone(result.get("studio"))
        self.assertIsNone(result.get("url"))


if __name__ == "__main__":
    unittest.main()
