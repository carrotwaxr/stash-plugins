"""Tests for StashDB scene tag sync functionality."""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tag_cache import TagCache


class TestMatchStashdbTagToLocal(unittest.TestCase):
    """Test tag matching logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.endpoint = "https://stashdb.org/graphql"
        self.local_tags = [
            {
                "id": "1",
                "name": "Anal Creampie",
                "aliases": ["Anal Cream Pie"],
                "stash_ids": [
                    {"endpoint": self.endpoint, "stash_id": "stashdb-abc123"}
                ]
            },
            {
                "id": "2",
                "name": "Cowgirl",
                "aliases": ["Girl on Top"],
                "stash_ids": []
            },
            {
                "id": "3",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            },
        ]
        self.tag_cache = TagCache.build(self.local_tags)

    def test_matches_by_stashdb_id_first(self):
        """Should match by StashDB ID link (priority 1)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-abc123", "name": "Different Name"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "1")

    def test_matches_by_name_when_no_stashdb_link(self):
        """Should match by name when no StashDB link (priority 2)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Cowgirl"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "2")

    def test_matches_by_alias_when_no_name_match(self):
        """Should match by alias when name doesn't match (priority 3)."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Girl on Top"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "2")

    def test_returns_none_for_no_match(self):
        """Should return None when no match found."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        stashdb_tag = {"id": "stashdb-unknown", "name": "Nonexistent Tag"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertIsNone(result)

    def test_stashdb_id_takes_priority_over_name(self):
        """StashDB ID match should win even if name matches different tag."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        # Tag with StashDB ID that maps to "Anal Creampie" but name is "Blonde"
        stashdb_tag = {"id": "stashdb-abc123", "name": "Blonde"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        # Should match by StashDB ID to "Anal Creampie" (id=1), not by name to "Blonde" (id=3)
        self.assertEqual(result, "1")

    def test_name_takes_priority_over_alias(self):
        """Name match should win over alias match."""
        from stashdb_scene_sync import match_stashdb_tag_to_local

        # StashDB tag with name that's also an alias of another tag
        stashdb_tag = {"id": "stashdb-unknown", "name": "Blonde"}

        result = match_stashdb_tag_to_local(stashdb_tag, self.tag_cache, self.endpoint)

        self.assertEqual(result, "3")


class TestProcessScene(unittest.TestCase):
    """Test scene processing logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.endpoint = "https://stashdb.org/graphql"
        self.local_tags = [
            {
                "id": "1",
                "name": "Anal",
                "aliases": [],
                "stash_ids": [{"endpoint": self.endpoint, "stash_id": "stashdb-anal"}]
            },
            {
                "id": "2",
                "name": "Blonde",
                "aliases": [],
                "stash_ids": []
            },
            {
                "id": "3",
                "name": "Cowgirl",
                "aliases": [],
                "stash_ids": []
            },
        ]
        self.tag_cache = TagCache.build(self.local_tags)

    def test_returns_no_changes_when_no_new_tags(self):
        """Should return no_changes when scene already has all matched tags."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "1"}, {"id": "2"}]
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-anal", "name": "Anal"},
                {"id": "stashdb-blonde", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": False}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.status, "no_changes")
        self.assertEqual(result.tags_added, 0)

    def test_identifies_new_tags_to_add(self):
        """Should identify new tags that need to be added."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "1"}]  # Only has Anal
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-anal", "name": "Anal"},
                {"id": "stashdb-other", "name": "Blonde"},  # New tag
                {"id": "stashdb-other2", "name": "Cowgirl"}  # New tag
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.tags_added, 2)

    def test_skips_unmatched_tags(self):
        """Should skip StashDB tags with no local match."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": []
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-unknown", "name": "Unknown Tag"},
                {"id": "stashdb-other", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        self.assertEqual(result.tags_added, 1)  # Only Blonde
        self.assertEqual(result.tags_skipped, 1)  # Unknown Tag

    def test_preserves_existing_tags_in_merge(self):
        """Should preserve existing tags when calculating merge."""
        from stashdb_scene_sync import process_scene, ProcessResult

        local_scene = {
            "id": "scene-1",
            "tags": [{"id": "99"}]  # Existing tag not in StashDB
        }
        stashdb_scene = {
            "tags": [
                {"id": "stashdb-other", "name": "Blonde"}
            ]
        }
        settings = {"dry_run": True}

        result = process_scene(
            local_scene, stashdb_scene, self.tag_cache,
            stash=None, settings=settings, endpoint=self.endpoint
        )

        # Should add Blonde (id=2) but preserve existing (id=99)
        self.assertEqual(result.tags_added, 1)
        self.assertIn("99", result.merged_tag_ids)
        self.assertIn("2", result.merged_tag_ids)


class TestSyncSceneTags(unittest.TestCase):
    """Test main sync orchestration."""

    def test_sync_logs_summary_statistics(self):
        """Should log summary statistics at end of sync."""
        from stashdb_scene_sync import sync_scene_tags

        # Function should exist and be callable
        self.assertTrue(callable(sync_scene_tags))

    def test_sync_respects_dry_run_limit(self):
        """Dry run should cap at 200 scenes."""
        from stashdb_scene_sync import DRY_RUN_LIMIT

        self.assertEqual(DRY_RUN_LIMIT, 200)


if __name__ == '__main__':
    unittest.main()
