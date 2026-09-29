"""Tests for scene tag sync from stash-boxes (stashdb_scene_sync + the sync handler).

`FakeStash` stands in for stash_client.LocalStash and `FakeRemote` for the stash-box
lookups, so these run offline. Other test modules import both.

Run with: python -m pytest tests/test_stashdb_scene_sync.py -v
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blacklist import Blacklist
from stash_client import StashError
from stashdb_api import StashDBAPIError
from tag_cache import TagCache

STASHDB = "https://stashdb.org/graphql"
TPDB = "https://theporndb.net/graphql"

STASHDB_BOX = {"endpoint": STASHDB, "api_key": "sdb-key", "name": "StashDB", "max_requests_per_minute": 240}
TPDB_BOX = {"endpoint": TPDB, "api_key": "tpdb-key", "name": "ThePornDB", "max_requests_per_minute": 240}


class FakeStash:
    """In-memory stand-in for stash_client.LocalStash."""

    def __init__(self, tags=None, scenes=None, config=None, fail_writes=False):
        self.tags = tags or []
        self.scenes = scenes or []
        self.config = config or {}
        self.fail_writes = fail_writes
        self.yielded = 0  # scenes yielded by iter_scenes_with_stash_id, across calls
        self.iter_calls = []  # (endpoint, limit)
        self.add_calls = []  # (scene_id, tag_ids)

    def configuration(self):
        return copy.deepcopy(self.config)

    def find_all_tags(self, per_page=1000):
        return copy.deepcopy(self.tags)

    def iter_scenes_with_stash_id(self, endpoint, per_page=100, limit=None):
        self.iter_calls.append((endpoint, limit))
        count = 0
        for scene in self.scenes:
            if limit is not None and count >= limit:
                return
            if any(s.get("endpoint") == endpoint for s in scene.get("stash_ids") or []):
                count += 1
                self.yielded += 1
                yield copy.deepcopy(scene)

    def add_scene_tags(self, scene_id, tag_ids):
        self.add_calls.append((scene_id, list(tag_ids)))
        if self.fail_writes:
            raise StashError("Stash returned HTTP 500")
        scene = next(s for s in self.scenes if s["id"] == scene_id)
        present = {t["id"] for t in scene["tags"]}
        scene["tags"].extend({"id": t} for t in tag_ids if t not in present)


class FakeRemote:
    """Stash-box scenes per endpoint. A scene's oshash is its remote id, so fingerprints match."""

    def __init__(self, scenes=None):
        self.scenes = scenes or {}  # {endpoint: {remote_id: {"id", "tags"}}}
        self.fingerprint_errors = {}  # {endpoint: [exception or None, ...]} consumed per call
        self.by_id_errors = {}  # {endpoint: exception}
        self.fingerprint_calls = []  # (endpoint, api_key, batch_count, rate_limiter)
        self.by_id_calls = []  # (endpoint, api_key, remote_id, rate_limiter)

    def add(self, endpoint, remote_id, tags):
        self.scenes.setdefault(endpoint, {})[remote_id] = {"id": remote_id, "tags": list(tags)}

    def find_scenes_by_fingerprints(self, url, api_key, fingerprint_batches, rate_limiter=None):
        self.fingerprint_calls.append((url, api_key, len(fingerprint_batches), rate_limiter))
        errors = self.fingerprint_errors.get(url) or []
        if errors:
            error = errors.pop(0)
            if error is not None:
                raise error
        known = self.scenes.get(url, {})
        return [
            [copy.deepcopy(known[fp["hash"]]) for fp in batch if fp["hash"] in known]
            for batch in fingerprint_batches
        ]

    def find_scene_by_id(self, url, api_key, scene_id, rate_limiter=None):
        self.by_id_calls.append((url, api_key, scene_id, rate_limiter))
        if url in self.by_id_errors:
            raise self.by_id_errors[url]
        return copy.deepcopy(self.scenes.get(url, {}).get(scene_id))

    @contextmanager
    def patched(self):
        with patch("stashdb_scene_sync.find_scenes_by_fingerprints", self.find_scenes_by_fingerprints), \
             patch("stashdb_scene_sync.find_scene_by_id", self.find_scene_by_id):
            yield self


def tag(tag_id, name, aliases=(), stash_ids=()):
    """A local tag as find_all_tags returns it."""
    return {"id": tag_id, "name": name, "aliases": list(aliases),
            "stash_ids": [{"endpoint": e, "stash_id": s} for e, s in stash_ids]}


def remote_tag(tag_id, name):
    return {"id": tag_id, "name": name, "aliases": []}


def scene(scene_id, links, tag_ids=(), fingerprints=True):
    """A local scene. `links` is [(endpoint, remote_id)]; each remote id becomes an oshash."""
    fps = [{"type": "OSHASH", "value": remote_id} for _, remote_id in links] if fingerprints else []
    return {
        "id": scene_id,
        "tags": [{"id": t} for t in tag_ids],
        "stash_ids": [{"endpoint": e, "stash_id": r} for e, r in links],
        "files": [{"fingerprints": fps}],
    }


LOCAL_TAGS = [
    tag("1", "Anal"),
    tag("2", "Blonde"),
    tag("3", "Cowgirl", aliases=["Girl on Top"]),
    tag("4", "Outdoors", stash_ids=[(TPDB, "tp-outdoors")]),
]

LIVE = {"dry_run": False}
DRY = {"dry_run": True}


def run_sync(client, remote, boxes, settings):
    from stashdb_scene_sync import sync_scene_tags
    with remote.patched():
        return sync_scene_tags(client, boxes, settings)


class TestMatchStashdbTagToLocal(unittest.TestCase):
    """Tag matching priority: stash-id link for this endpoint, then name, then alias."""

    def setUp(self):
        self.endpoint = STASHDB
        self.tag_cache = TagCache.build([
            tag("1", "Anal Creampie", aliases=["Anal Cream Pie"], stash_ids=[(STASHDB, "stashdb-abc123")]),
            tag("2", "Cowgirl", aliases=["Girl on Top"]),
            tag("3", "Blonde"),
        ])

    def match(self, remote, endpoint=None):
        from stashdb_scene_sync import match_stashdb_tag_to_local
        return match_stashdb_tag_to_local(remote, self.tag_cache, endpoint or self.endpoint)

    def test_matches_by_stashdb_id_first(self):
        self.assertEqual(self.match({"id": "stashdb-abc123", "name": "Different Name"}), "1")

    def test_matches_by_name_when_no_stashdb_link(self):
        self.assertEqual(self.match({"id": "stashdb-unknown", "name": "Cowgirl"}), "2")

    def test_matches_by_alias_when_no_name_match(self):
        self.assertEqual(self.match({"id": "stashdb-unknown", "name": "Girl on Top"}), "2")

    def test_returns_none_for_no_match(self):
        self.assertIsNone(self.match({"id": "stashdb-unknown", "name": "Nonexistent Tag"}))

    def test_stashdb_id_takes_priority_over_name(self):
        self.assertEqual(self.match({"id": "stashdb-abc123", "name": "Blonde"}), "1")

    def test_name_takes_priority_over_alias(self):
        self.assertEqual(self.match({"id": "stashdb-unknown", "name": "Blonde"}), "3")

    def test_stash_id_link_is_per_endpoint(self):
        self.assertIsNone(self.match({"id": "stashdb-abc123", "name": "Unknown"}, endpoint=TPDB))


class TestProcessScene(unittest.TestCase):
    """process_scene decides the tags to add for one scene and writes them in ADD mode."""

    def setUp(self):
        self.tag_cache = TagCache.build(LOCAL_TAGS)
        self.client = FakeStash(scenes=[scene("s1", [(STASHDB, "r1")], tag_ids=["1"])])

    def process(self, remote_tags, settings, local_tag_ids=("1",)):
        from stashdb_scene_sync import process_scene
        local = scene("s1", [(STASHDB, "r1")], tag_ids=local_tag_ids)
        return process_scene(local, {"id": "r1", "tags": remote_tags}, self.tag_cache,
                             client=self.client, settings=settings, endpoint=STASHDB,
                             blacklist=Blacklist(""))

    def test_returns_no_changes_when_no_new_tags(self):
        result = self.process([remote_tag("x1", "Anal")], LIVE)
        self.assertEqual(result.status, "no_changes")
        self.assertEqual(result.tags_added, 0)
        self.assertEqual(self.client.add_calls, [])

    def test_dry_run_counts_new_tags_and_writes_nothing(self):
        result = self.process([remote_tag("x1", "Anal"), remote_tag("x2", "Blonde"),
                               remote_tag("x3", "Girl on Top")], DRY)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.tags_added, 2)
        self.assertEqual(self.client.add_calls, [])

    def test_skips_unmatched_tags(self):
        result = self.process([remote_tag("x1", "Unknown Tag"), remote_tag("x2", "Blonde")], LIVE)
        self.assertEqual(result.status, "updated")
        self.assertEqual(result.tags_added, 1)
        self.assertEqual(result.tags_skipped, 1)
        self.assertEqual(self.client.add_calls, [("s1", ["2"])])

    def test_write_failure_is_an_error_result(self):
        self.client.fail_writes = True
        result = self.process([remote_tag("x2", "Blonde")], LIVE)
        self.assertEqual(result.status, "error")
        self.assertIn("500", result.error)


class TestSyncSceneTags(unittest.TestCase):
    """sync_scene_tags(client, boxes, settings) end to end over FakeStash and FakeRemote."""

    def test_each_scene_synced_against_its_own_endpoint(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[
            scene("s1", [(STASHDB, "sd-1")]),
            scene("s2", [(TPDB, "tp-1")]),
        ])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])
        # "Great Outdoors" only matches through the local tag's ThePornDB stash-id link.
        remote.add(TPDB, "tp-1", [remote_tag("b", "Blonde"), remote_tag("tp-outdoors", "Great Outdoors")])

        stats = run_sync(client, remote, [STASHDB_BOX, TPDB_BOX], LIVE)

        self.assertEqual(client.add_calls, [("s1", ["1"]), ("s2", ["2", "4"])])
        self.assertEqual([(c[0], c[1]) for c in remote.fingerprint_calls],
                         [(STASHDB, "sdb-key"), (TPDB, "tpdb-key")])
        self.assertIsNone(stats.error)
        self.assertEqual(stats.updated, 2)
        self.assertEqual(stats.tags_added_total, 3)
        self.assertEqual(stats.by_endpoint[STASHDB]["name"], "StashDB")
        self.assertEqual(stats.by_endpoint[STASHDB]["tags_added"], 1)
        self.assertEqual(stats.by_endpoint[TPDB]["name"], "ThePornDB")
        self.assertEqual(stats.by_endpoint[TPDB]["tags_added"], 2)

    def test_scene_on_two_boxes_gets_tags_from_each(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1"), (TPDB, "tp-1")])])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])
        remote.add(TPDB, "tp-1", [remote_tag("a2", "Anal"), remote_tag("b", "Blonde")])

        stats = run_sync(client, remote, [STASHDB_BOX, TPDB_BOX], LIVE)

        self.assertEqual(client.add_calls, [("s1", ["1"]), ("s1", ["2"])])
        self.assertEqual(stats.total_scenes, 2)

    def test_add_scene_tags_gets_only_new_ids(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1")], tag_ids=["1", "99"])])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde"),
                                     remote_tag("c", "Cowgirl")])

        stats = run_sync(client, remote, [STASHDB_BOX], LIVE)

        self.assertEqual(client.add_calls, [("s1", ["2", "3"])])
        self.assertEqual(stats.tags_added_total, 2)

    def test_auth_failure_aborts_with_error(self):
        # 90 scenes make three fingerprint batches; a second box follows.
        scenes = [scene(f"s{i}", [(STASHDB, f"sd-{i}")]) for i in range(90)]
        scenes.append(scene("t1", [(TPDB, "tp-1")]))
        client = FakeStash(tags=LOCAL_TAGS, scenes=scenes)
        remote = FakeRemote()
        remote.add(TPDB, "tp-1", [remote_tag("b", "Blonde")])
        remote.fingerprint_errors[STASHDB] = [StashDBAPIError("HTTP 403: Forbidden", status_code=403)]

        stats = run_sync(client, remote, [STASHDB_BOX, TPDB_BOX], LIVE)

        self.assertIn("StashDB", stats.error)
        self.assertIn("403", stats.error)
        self.assertEqual(len(remote.fingerprint_calls), 1)
        self.assertEqual(remote.by_id_calls, [])
        self.assertEqual(client.add_calls, [])

    def test_auth_failure_in_pass_two_aborts(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[
            scene("s1", [(TPDB, "tp-1")], fingerprints=False),
            scene("s2", [(TPDB, "tp-2")], fingerprints=False),
        ])
        remote = FakeRemote()
        remote.by_id_errors[TPDB] = StashDBAPIError("GraphQL error: not authorized", graphql=True)

        stats = run_sync(client, remote, [TPDB_BOX], LIVE)

        self.assertIn("ThePornDB", stats.error)
        self.assertEqual(len(remote.by_id_calls), 1)

    def test_non_auth_batch_error_counts_scenes_and_continues(self):
        scenes = [scene(f"s{i}", [(STASHDB, f"sd-{i}")]) for i in range(50)]
        client = FakeStash(tags=LOCAL_TAGS, scenes=scenes)
        remote = FakeRemote()
        for i in range(50):
            remote.add(STASHDB, f"sd-{i}", [remote_tag("b", "Blonde")])
        remote.fingerprint_errors[STASHDB] = [StashDBAPIError("HTTP 502: Bad Gateway", status_code=502)]

        stats = run_sync(client, remote, [STASHDB_BOX], LIVE)

        self.assertEqual(len(remote.fingerprint_calls), 2)
        self.assertEqual(stats.errors, 40)
        self.assertEqual(stats.updated, 10)
        self.assertIsNone(stats.error)

    def test_non_auth_lookup_error_in_pass_two_counts_scene_and_continues(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[
            scene("s1", [(STASHDB, "sd-1")], fingerprints=False),
            scene("s2", [(STASHDB, "sd-2")], fingerprints=False),
        ])
        remote = FakeRemote()
        remote.by_id_errors[STASHDB] = StashDBAPIError("HTTP 500", status_code=500)

        stats = run_sync(client, remote, [STASHDB_BOX], LIVE)

        self.assertEqual(len(remote.by_id_calls), 2)
        self.assertEqual(stats.errors, 2)

    def test_scene_without_fingerprint_match_uses_lookup_by_id(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1")], fingerprints=False)])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("b", "Blonde")])

        stats = run_sync(client, remote, [STASHDB_BOX], LIVE)

        self.assertEqual([c[2] for c in remote.by_id_calls], ["sd-1"])
        self.assertEqual(client.add_calls, [("s1", ["2"])])
        self.assertEqual(stats.updated, 1)

    def test_dry_run_stops_fetching_at_limit(self):
        scenes = [scene(f"s{i}", [(STASHDB, f"sd-{i}")]) for i in range(500)]
        client = FakeStash(tags=LOCAL_TAGS, scenes=scenes)
        remote = FakeRemote()
        for i in range(500):
            remote.add(STASHDB, f"sd-{i}", [remote_tag("b", "Blonde")])

        stats = run_sync(client, remote, [STASHDB_BOX], DRY)

        self.assertLessEqual(client.yielded, 200)
        self.assertEqual(stats.total_scenes, 200)
        self.assertEqual(stats.updated, 200)
        self.assertEqual(client.add_calls, [])

    def test_dry_run_limit_is_shared_across_boxes(self):
        scenes = [scene(f"s{i}", [(STASHDB, f"sd-{i}")]) for i in range(150)]
        scenes += [scene(f"t{i}", [(TPDB, f"tp-{i}")]) for i in range(150)]
        client = FakeStash(tags=LOCAL_TAGS, scenes=scenes)

        stats = run_sync(client, FakeRemote(), [STASHDB_BOX, TPDB_BOX], DRY)

        self.assertEqual(client.iter_calls, [(STASHDB, 200), (TPDB, 50)])
        self.assertEqual(client.yielded, 200)
        self.assertEqual(stats.total_scenes, 200)

    def test_live_run_fetches_without_limit(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1")])])

        run_sync(client, FakeRemote(), [STASHDB_BOX], LIVE)

        self.assertEqual(client.iter_calls, [(STASHDB, None)])

    def test_blacklist_from_settings(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1")])])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])

        run_sync(client, remote, [STASHDB_BOX], {"dry_run": False, "tag_blacklist": "Blonde"})

        self.assertEqual(client.add_calls, [("s1", ["1"])])

    def test_zero_success_reports_error(self):
        client = FakeStash(tags=LOCAL_TAGS, fail_writes=True, scenes=[
            scene("s1", [(STASHDB, "sd-1")]),
            scene("s2", [(STASHDB, "sd-2")]),
        ])
        remote = FakeRemote()
        remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal")])
        remote.add(STASHDB, "sd-2", [remote_tag("b", "Blonde")])

        stats = run_sync(client, remote, [STASHDB_BOX], LIVE)

        self.assertEqual(stats.processed, 0)
        self.assertEqual(stats.errors, 2)
        self.assertTrue(stats.error)

    def test_rate_limit_from_box(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[
            scene("s1", [(STASHDB, "sd-1")]),
            scene("t1", [(TPDB, "tp-1")]),
        ])
        remote = FakeRemote()
        slow_box = {**STASHDB_BOX, "max_requests_per_minute": 30}
        box_without_limit = {k: v for k, v in TPDB_BOX.items() if k != "max_requests_per_minute"}

        run_sync(client, remote, [slow_box, box_without_limit], DRY)

        limiters = {c[0]: c[3] for c in remote.fingerprint_calls}
        self.assertGreaterEqual(limiters[STASHDB].min_interval, 2.0)
        self.assertAlmostEqual(limiters[TPDB].min_interval, 0.5)

    def test_rate_limit_capped_at_two_per_second(self):
        client = FakeStash(tags=LOCAL_TAGS, scenes=[scene("s1", [(STASHDB, "sd-1")])])
        remote = FakeRemote()

        run_sync(client, remote, [{**STASHDB_BOX, "max_requests_per_minute": 600}], DRY)

        self.assertAlmostEqual(remote.fingerprint_calls[0][3].min_interval, 0.5)

    def test_local_stash_failure_aborts_with_error(self):
        client = FakeStash(tags=LOCAL_TAGS)

        def broken_iter(endpoint, per_page=100, limit=None):
            raise StashError("Stash returned HTTP 502")
            yield  # pragma: no cover

        client.iter_scenes_with_stash_id = broken_iter

        stats = run_sync(client, FakeRemote(), [STASHDB_BOX], LIVE)

        self.assertIn("502", stats.error)


class TestHandleSyncSceneTags(unittest.TestCase):
    """handle_sync_scene_tags reads Stash's config and reports the result to the UI."""

    SERVER = {"Scheme": "http", "Host": "localhost", "Port": 9999}

    def setUp(self):
        self.remote = FakeRemote()
        self.remote.add(STASHDB, "sd-1", [remote_tag("a", "Anal"), remote_tag("b", "Blonde")])
        self.plugin_config = {"syncDryRun": False, "tagBlacklist": "Blonde"}
        self.general = {"apiKey": "stash-key", "stashBoxes": [
            STASHDB_BOX,
            {"endpoint": TPDB, "api_key": "", "name": "ThePornDB", "max_requests_per_minute": 240},
        ]}
        self.client = None
        self.constructed = []

    def fake_local_stash(self, server_connection, api_key=None, timeout=60):
        self.constructed.append(api_key)
        if self.client is None:
            self.client = FakeStash(
                tags=LOCAL_TAGS,
                scenes=[scene("s1", [(STASHDB, "sd-1")]), scene("t1", [(TPDB, "tp-1")])],
                config={"general": self.general, "plugins": {"tagManager": self.plugin_config}},
            )
        return self.client

    def run_handler(self):
        import tag_manager
        with patch("tag_manager.LocalStash", side_effect=self.fake_local_stash), self.remote.patched():
            return tag_manager.handle_sync_scene_tags(self.SERVER)

    def test_syncs_boxes_with_an_api_key_using_plugin_settings(self):
        result = self.run_handler()

        self.assertTrue(result["success"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(self.client.add_calls, [("s1", ["1"])])  # Blonde is blacklisted
        self.assertEqual([c[0] for c in self.remote.fingerprint_calls], [STASHDB])
        self.assertEqual(list(result["by_endpoint"]), [STASHDB])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.constructed[-1], "stash-key")

    def test_uses_session_cookie_when_stash_has_no_api_key(self):
        self.general["apiKey"] = ""

        result = self.run_handler()

        self.assertTrue(result["success"])
        self.assertEqual(set(self.constructed), {None})

    def test_dry_run_defaults_on(self):
        del self.plugin_config["syncDryRun"]

        result = self.run_handler()

        self.assertTrue(result["dry_run"])
        self.assertEqual(self.client.add_calls, [])

    def test_no_box_with_api_key_is_an_error(self):
        self.general["stashBoxes"] = [{"endpoint": TPDB, "api_key": "", "name": "ThePornDB"}]

        result = self.run_handler()

        self.assertTrue(result["error"].startswith("No stash-box endpoints with an API key are configured"))
        self.assertEqual(self.remote.fingerprint_calls, [])

    def test_auth_abort_is_an_error(self):
        self.remote.fingerprint_errors[STASHDB] = [StashDBAPIError("HTTP 401", status_code=401)]

        result = self.run_handler()

        self.assertIn("StashDB", result["error"])
        self.assertNotIn("success", result)

    def test_zero_success_is_an_error(self):
        self.fake_local_stash(self.SERVER)
        self.client.fail_writes = True

        result = self.run_handler()

        self.assertIn("error", result)
        self.assertEqual(result["errors"], 1)

    def test_configuration_failure_is_an_error(self):
        import tag_manager

        class Broken:
            def __init__(self, *args, **kwargs):
                pass

            def configuration(self):
                raise StashError("Stash returned HTTP 401")

        with patch("tag_manager.LocalStash", Broken):
            result = tag_manager.handle_sync_scene_tags(self.SERVER)

        self.assertIn("401", result["error"])

    def test_main_sync_mode(self):
        import plugin_data
        import tag_manager
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(setattr, plugin_data, "_current_dir", None)
        payload = json.dumps({"server_connection": {**self.SERVER, "Dir": tmp},
                              "args": {"mode": "sync_scene_tags"}})
        out = io.StringIO()
        with patch("sys.stdin", io.StringIO(payload)), patch("sys.stdout", out), \
             patch("tag_manager.LocalStash", side_effect=self.fake_local_stash), self.remote.patched():
            tag_manager.main()

        output = json.loads(out.getvalue())["output"]
        self.assertTrue(output["success"])
        self.assertEqual(self.client.add_calls, [("s1", ["1"])])


if __name__ == '__main__':
    unittest.main()
