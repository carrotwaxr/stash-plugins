"""Tests for scene sync blacklist integration.

These tests verify that the blacklist from settings["tag_blacklist"] filters
stash-box tags before they are matched and added to local scenes.

Run with: python -m pytest plugins/tagManager/tests/test_scene_sync_blacklist.py -v
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_stashdb_scene_sync import (
    FakeStash, FakeRemote, STASHDB, STASHDB_BOX, remote_tag, run_sync, scene, tag,
)


class TestSyncSceneTagsWithBlacklist(unittest.TestCase):
    """Scene sync with blacklist filtering."""

    def setUp(self):
        """Set up test fixtures."""
        self.local_tags = [
            tag("1", "Anal"),
            tag("2", "4K Available"),
            tag("3", "1080p"),
            tag("4", "Blonde"),
        ]
        self.remote = FakeRemote()
        self.remote.add(STASHDB, "sd-1", [
            remote_tag("stashdb-1", "Anal"),
            remote_tag("stashdb-2", "4K Available"),
            remote_tag("stashdb-3", "1080p"),
            remote_tag("stashdb-4", "Blonde"),
        ])

    def sync(self, tag_blacklist, dry_run=False):
        client = FakeStash(tags=self.local_tags, scenes=[scene("scene-1", [(STASHDB, "sd-1")])])
        stats = run_sync(client, self.remote, [STASHDB_BOX],
                         {"dry_run": dry_run, "tag_blacklist": tag_blacklist})
        added = [tag_id for _, tag_ids in client.add_calls for tag_id in tag_ids]
        return stats, added

    def test_filters_blacklisted_tags_literal(self):
        """Should not add tags that match a literal blacklist entry."""
        stats, added = self.sync("4K Available")

        self.assertEqual(added, ["1", "3", "4"])  # Anal, 1080p, Blonde; not 4K Available
        self.assertEqual(stats.tags_added_total, 3)

    def test_filters_blacklisted_tags_regex(self):
        """Should not add tags that match a regex blacklist entry."""
        # Regex to match resolution patterns like 1080p, 720p, etc.
        stats, added = self.sync(r"/^\d+p$")

        self.assertEqual(added, ["1", "2", "4"])  # 1080p is blacklisted

    def test_blacklist_case_insensitive(self):
        """Blacklist literal matching should be case-insensitive."""
        stats, added = self.sync("4k available")  # Lowercase

        self.assertEqual(added, ["1", "3", "4"])  # 4K Available is blacklisted

    def test_empty_blacklist_processes_all_tags(self):
        """Empty blacklist should process all tags normally."""
        stats, added = self.sync("")

        self.assertEqual(added, ["1", "2", "3", "4"])

    def test_multiple_blacklist_patterns(self):
        """Should filter tags matching any blacklist pattern."""
        # Multiple patterns: literal + regex
        stats, added = self.sync("4K Available\n/^\\d+p$")

        self.assertEqual(added, ["1", "4"])  # Anal, Blonde

    def test_blacklist_applies_in_dry_run(self):
        """A dry run counts only the tags the blacklist lets through."""
        stats, added = self.sync("4K Available, 1080p", dry_run=True)

        self.assertEqual(added, [])
        self.assertEqual(stats.tags_added_total, 2)


if __name__ == '__main__':
    unittest.main()
