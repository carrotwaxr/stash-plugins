"""Tests for the scene sync history (sync_history.SyncHistory) and how scene sync uses it.

The history remembers which stash-box tags each scene was synced with, so a tag the
user removes from a scene is not added back by the next sync.

Run with: python -m pytest tests/test_sync_history.py -v
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugin_data
from sync_history import SyncHistory
from tests.test_stashdb_scene_sync import (
    DRY, LIVE, LOCAL_TAGS, STASHDB, STASHDB_BOX, TPDB, FakeRemote, FakeStash,
    remote_tag, scene, tag,
)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "sync_history.sqlite")

    def open_history(self):
        history = SyncHistory(self.path)
        self.addCleanup(history.close)
        return history


class TestSyncHistory(TempDir):
    """SyncHistory stores, per (endpoint, local scene), the stash-box tag ids last synced."""

    def test_get_missing_returns_none(self):
        history = self.open_history()

        self.assertIsNone(history.get(STASHDB, "s1", "sd-1"))

    def test_record_then_get(self):
        history = self.open_history()

        history.record(STASHDB, "s1", "sd-1", ["b", "a", "c"])
        history.record(STASHDB, "s2", "sd-2", [])

        self.assertEqual(history.get(STASHDB, "s1", "sd-1"), {"a", "b", "c"})
        self.assertEqual(history.get(STASHDB, "s2", "sd-2"), set())  # synced, nothing matched
        self.assertIsNone(history.get(TPDB, "s1", "sd-1"))  # per endpoint
        history.close()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT tag_ids FROM scene_tags WHERE scene_id = 's1'").fetchone()[0],
                             "a b c")

    def test_record_replaces_previous(self):
        history = self.open_history()

        history.record(STASHDB, "s1", "sd-1", ["a", "b"])
        history.record(STASHDB, "s1", "sd-1", ["c"])

        self.assertEqual(history.get(STASHDB, "s1", "sd-1"), {"c"})

    def test_remote_id_change_returns_none(self):
        history = self.open_history()

        history.record(STASHDB, "s1", "sd-1", ["a"])

        self.assertIsNone(history.get(STASHDB, "s1", "sd-other"))

    def test_persists_across_instances(self):
        history = SyncHistory(self.path)
        history.record(STASHDB, "s1", "sd-1", ["a", "b"])
        history.close()

        self.assertEqual(self.open_history().get(STASHDB, "s1", "sd-1"), {"a", "b"})

    def test_commits_every_200_records(self):
        history = self.open_history()

        for i in range(199):
            history.record(STASHDB, f"s{i}", f"sd-{i}", ["a"])
        with sqlite3.connect(self.path) as other:
            self.assertEqual(other.execute("SELECT COUNT(*) FROM scene_tags").fetchone()[0], 0)
        history.record(STASHDB, "s199", "sd-199", ["a"])
        with sqlite3.connect(self.path) as other:
            self.assertEqual(other.execute("SELECT COUNT(*) FROM scene_tags").fetchone()[0], 200)

    def test_reset_returns_count(self):
        history = self.open_history()
        for i in range(3):
            history.record(STASHDB, f"s{i}", f"sd-{i}", ["a"])

        self.assertEqual(history.reset(), 3)
        self.assertIsNone(history.get(STASHDB, "s0", "sd-0"))
        self.assertEqual(history.reset(), 0)

    def test_file_is_wal_with_schema_version(self):
        self.open_history().close()

        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_close_twice_is_harmless(self):
        history = SyncHistory(self.path)
        history.close()
        history.close()


class TestSyncWithHistory(TempDir):
    """sync_scene_tags(..., history=...) over FakeStash: removed tags stay removed."""

    def setUp(self):
        super().setUp()
        self.history = self.open_history()
        self.remote = FakeRemote()
        self.client = FakeStash(tags=list(LOCAL_TAGS), scenes=[scene("s1", [(STASHDB, "sd-1")])])

    def sync(self, settings=LIVE):
        from stashdb_scene_sync import sync_scene_tags
        with self.remote.patched():
            return sync_scene_tags(self.client, [STASHDB_BOX], settings, history=self.history)

    def scene_tag_ids(self):
        return sorted(t["id"] for t in self.client.scenes[0]["tags"])

    def remove_scene_tag(self, tag_id):
        self.client.scenes[0]["tags"] = [t for t in self.client.scenes[0]["tags"] if t["id"] != tag_id]

    def remembered(self, remote_id="sd-1"):
        return self.history.get(STASHDB, "s1", remote_id)

    def test_first_sync_adds_all(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde"),
                                          remote_tag("c", "Girl on Top"), remote_tag("u", "Unknown")])

        stats = self.sync()

        self.assertEqual(self.client.add_calls, [("s1", ["1", "2", "3"])])
        self.assertEqual(stats.updated, 1)
        self.assertEqual(self.remembered(), {"a", "b", "c"})  # not the unmatched one

    def test_already_present_tags_are_remembered(self):
        self.client.scenes[0]["tags"] = [{"id": "1"}]
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])

        stats = self.sync()

        self.assertEqual(stats.no_changes, 1)
        self.assertEqual(self.remembered(), {"a"})

    def test_removed_tag_not_readded(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.sync()
        self.remove_scene_tag("2")

        stats = self.sync()

        self.assertEqual(self.client.add_calls, [("s1", ["1", "2"])])  # sync 1 only
        self.assertEqual(self.scene_tag_ids(), ["1"])
        self.assertEqual(stats.no_changes, 1)
        self.assertEqual(stats.tags_added_total, 0)
        self.assertEqual(self.remembered(), {"a", "b"})

        self.sync()  # still remembered on later runs

        self.assertEqual(self.scene_tag_ids(), ["1"])

    def test_new_stashdb_tag_added(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])
        self.sync()
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])

        stats = self.sync()

        self.assertEqual(self.client.add_calls[-1], ("s1", ["2"]))
        self.assertEqual(stats.tags_added_total, 1)
        self.assertEqual(self.remembered(), {"a", "b"})

    def test_unmatched_tag_added_once_matched(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("r", "Redhead")])
        self.sync()
        self.assertEqual(self.remembered(), {"a"})
        self.client.tags.append(tag("5", "Redhead"))

        self.sync()

        self.assertEqual(self.client.add_calls[-1], ("s1", ["5"]))
        self.assertEqual(self.remembered(), {"a", "r"})

    def test_blacklisted_tag_added_once_unblacklisted(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.sync({"dry_run": False, "tag_blacklist": "Blonde"})
        self.assertEqual(self.remembered(), {"a"})

        self.sync()

        self.assertEqual(self.client.add_calls[-1], ("s1", ["2"]))

    def test_relinked_scene_is_a_first_sync(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.remote.add(STASHDB, "sd-2", [remote_tag("b", "Blonde")])
        self.sync()
        self.remove_scene_tag("2")
        self.client.scenes[0] = scene("s1", [(STASHDB, "sd-2")], tag_ids=["1"])

        self.sync()

        self.assertEqual(self.client.add_calls[-1], ("s1", ["2"]))
        self.assertEqual(self.remembered("sd-2"), {"b"})

    def test_write_error_records_nothing(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])
        self.client.fail_writes = True

        stats = self.sync()

        self.assertEqual(stats.errors, 1)
        self.assertIsNone(self.remembered())

    def test_dry_run_records_nothing(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])

        stats = self.sync(DRY)

        self.assertEqual(stats.tags_added_total, 2)
        self.assertEqual(self.client.add_calls, [])
        self.assertIsNone(self.remembered())

    def test_dry_run_leaves_out_removed_tags(self):
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.sync()
        self.remove_scene_tag("2")

        stats = self.sync(DRY)

        self.assertEqual(stats.updated, 0)
        self.assertEqual(stats.tags_added_total, 0)

    def test_no_history_keeps_readding(self):
        from stashdb_scene_sync import sync_scene_tags
        self.remote.add(STASHDB, "sd-1", [remote_tag("b", "Blonde")])
        with self.remote.patched():
            sync_scene_tags(self.client, [STASHDB_BOX], LIVE)
            self.remove_scene_tag("2")
            sync_scene_tags(self.client, [STASHDB_BOX], LIVE)

        self.assertEqual(self.client.add_calls, [("s1", ["2"]), ("s1", ["2"])])


class TestSyncHistoryModes(unittest.TestCase):
    """The plugin keeps the history in Stash's config dir and can reset it."""

    SERVER = {"Scheme": "http", "Host": "localhost", "Port": 9999}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, plugin_data, "_current_dir", None)
        self.data_dir = os.path.join(self.tmp, "plugin_data", "tagManager")
        self.remote = FakeRemote()
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.client = FakeStash(
            tags=LOCAL_TAGS,
            scenes=[scene("s1", [(STASHDB, "sd-1")])],
            config={"general": {"apiKey": "stash-key", "stashBoxes": [STASHDB_BOX]},
                    "plugins": {"tagManager": {"syncDryRun": False}}},
        )

    def run_main(self, mode, local_stash=None):
        import tag_manager
        payload = json.dumps({"server_connection": {**self.SERVER, "Dir": self.tmp},
                              "args": {"mode": mode}})
        out = io.StringIO()
        local_stash = local_stash or (lambda *args, **kwargs: self.client)
        with patch("sys.stdin", io.StringIO(payload)), patch("sys.stdout", out), \
             patch("tag_manager.LocalStash", side_effect=local_stash), self.remote.patched():
            tag_manager.main()
        return json.loads(out.getvalue())["output"]

    def test_history_file_outside_plugin_dir(self):
        output = self.run_main("sync_scene_tags")

        self.assertTrue(output["success"])
        path = os.path.join(self.data_dir, "sync_history.sqlite")
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.exists(os.path.join(PLUGIN_DIR, "sync_history.sqlite")))
        self.assertFalse(os.path.exists(os.path.join(PLUGIN_DIR, "data", "sync_history.sqlite")))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT endpoint, scene_id, remote_id, tag_ids FROM scene_tags").fetchall(),
                             [(STASHDB, "s1", "sd-1", "a b")])

    def test_second_sync_through_main_keeps_removed_tag_off(self):
        self.run_main("sync_scene_tags")
        self.client.scenes[0]["tags"] = [{"id": "1"}]

        output = self.run_main("sync_scene_tags")

        self.assertEqual(output["tags_added"], 0)
        self.assertEqual(self.client.scenes[0]["tags"], [{"id": "1"}])

    def test_main_reset_mode(self):
        self.run_main("sync_scene_tags")

        def no_stash(*args, **kwargs):
            raise AssertionError("reset_sync_history must not need Stash")

        output = self.run_main("reset_sync_history", local_stash=no_stash)

        self.assertEqual(output, {"success": True, "cleared": 1})
        self.assertEqual(self.run_main("reset_sync_history", local_stash=no_stash),
                         {"success": True, "cleared": 0})

    def write_corrupt_history(self):
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, "sync_history.sqlite")
        with open(path, "wb") as f:
            f.write(b"this is not a sqlite database " * 20)
        return path

    def test_reset_deletes_corrupt_file(self):
        path = self.write_corrupt_history()

        output = self.run_main("reset_sync_history")

        self.assertTrue(output["success"])
        self.assertIsNone(output["cleared"])
        self.assertIn("unreadable", output["note"])
        self.assertFalse(os.path.exists(path))

    def test_reset_removes_file(self):
        self.run_main("sync_scene_tags")
        path = os.path.join(self.data_dir, "sync_history.sqlite")
        for suffix in ("-wal", "-shm"):
            with open(path + suffix, "wb") as f:
                f.write(b"x")

        output = self.run_main("reset_sync_history")

        self.assertEqual(output, {"success": True, "cleared": 1})
        for suffix in ("", "-wal", "-shm"):
            self.assertFalse(os.path.exists(path + suffix))
        self.assertTrue(self.run_main("sync_scene_tags")["success"])
        self.assertTrue(os.path.isfile(path))

    def test_sync_error_mentions_reset_task(self):
        path = self.write_corrupt_history()

        output = self.run_main("sync_scene_tags")

        self.assertIn("Reset Scene Tag Sync History", output["error"])
        self.assertIn(path, output["error"])


if __name__ == "__main__":
    unittest.main()
