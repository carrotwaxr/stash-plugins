#!/usr/bin/env python3
"""Offline tests for the studio-less result fix and the local-ID cache."""

import glob
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugin_data
import scene_matcher

EP = "https://stashdb.org/graphql"


def page(ids, count, endpoint=EP, extra=None):
    scenes = [{"id": str(i), "stash_ids": [{"endpoint": endpoint, "stash_id": s}] + (extra or [])}
              for i, s in enumerate(ids)]
    return {"findScenes": {"count": count, "scenes": scenes}}


class TestNullStudio(unittest.TestCase):
    def test_format_results_studio_null(self):
        context = {
            "performer_stash_ids": set(), "studio_stash_id": "studio-1",
            "local_title": "t", "local_filename": "", "local_duration": None,
        }
        scenes = {"s1": {"id": "s1", "title": "t", "studio": None, "performers": []}}
        results = scene_matcher.format_results(scenes, context, set())
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["matches_studio"])
        self.assertIsNone(results[0]["studio"])


class CacheBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(plugin_data, "_current_dir", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)


class TestLocalIds(CacheBase):
    def test_query_shape_and_filtering(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page(
                ["a", "b"], 2,
                extra=[{"endpoint": "https://other.example/graphql", "stash_id": "zzz"}])
            ids = scene_matcher.local_stash_ids(EP)
        self.assertEqual(ids, {"a", "b"})
        query, variables = g.call_args[0]
        self.assertEqual(variables["scene_filter"], {
            "stash_id_endpoint": {"endpoint": EP, "modifier": "NOT_NULL"}})
        self.assertEqual(variables["filter"], {"per_page": 1000, "page": 1})

    def test_other_endpoint_ids_excluded_and_normalized(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page(["a"], 1, endpoint="HTTPS://StashDB.org/graphql/")
            self.assertEqual(scene_matcher.local_stash_ids(EP), {"a"})

    def test_pagination(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.side_effect = [page(["a"], 1001), page(["b"], 1001)]
            self.assertEqual(scene_matcher.local_stash_ids(EP), {"a", "b"})
            self.assertEqual(g.call_args[0][1]["filter"]["page"], 2)

    def test_second_call_hits_cache(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page(["a"], 1)
            scene_matcher.local_stash_ids(EP)
            again = scene_matcher.local_stash_ids("https://stashdb.org/graphql/")
        self.assertEqual(again, {"a"})
        self.assertEqual(g.call_count, 1)

    def test_empty_set_is_a_hit(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page([], 0)
            self.assertEqual(scene_matcher.local_stash_ids(EP), set())
            self.assertEqual(scene_matcher.local_stash_ids(EP), set())
        self.assertEqual(g.call_count, 1)

    def test_expired_rebuilds(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page(["a"], 1)
            scene_matcher.local_stash_ids(EP)
            with mock.patch.object(scene_matcher.time, "time",
                                   return_value=scene_matcher.time.time() + 301):
                scene_matcher.local_stash_ids(EP)
        self.assertEqual(g.call_count, 2)

    def test_corrupt_file_rebuilds(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.return_value = page(["a"], 1)
            scene_matcher.local_stash_ids(EP)
            files = glob.glob(os.path.join(self.tmp.name, "*"))
            self.assertEqual(len(files), 1)
            with open(files[0], "w") as f:
                f.write("{not json")
            self.assertEqual(scene_matcher.local_stash_ids(EP), {"a"})
        self.assertEqual(g.call_count, 2)

    def test_keyed_by_endpoint(self):
        with mock.patch.object(scene_matcher, "stash_graphql") as g:
            g.side_effect = [page(["a"], 1), page(["b"], 1, endpoint="https://tpdb.example/graphql")]
            self.assertEqual(scene_matcher.local_stash_ids(EP), {"a"})
            self.assertEqual(scene_matcher.local_stash_ids("https://tpdb.example/graphql"), {"b"})

    def test_none_raises(self):
        with mock.patch.object(scene_matcher, "stash_graphql", return_value=None):
            with self.assertRaises(RuntimeError):
                scene_matcher.local_stash_ids(EP)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_no_temp_files_left(self):
        with mock.patch.object(scene_matcher, "stash_graphql", return_value=page(["a"], 1)):
            scene_matcher.local_stash_ids(EP)
        self.assertEqual(len(os.listdir(self.tmp.name)), 1)


class TestOps(CacheBase):
    def _patch(self):
        box = {"name": "StashDB", "endpoint": EP, "api_key": "k"}
        scene = {"id": "1", "title": "t", "stash_ids": [], "files": [],
                 "performers": [], "studio": None}
        ps = [
            mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]),
            mock.patch.object(scene_matcher, "get_local_scene", return_value=scene),
            mock.patch.object(scene_matcher, "query_stashdb_by_text", return_value=[]),
            mock.patch.object(scene_matcher, "local_stash_ids", return_value={"a"}),
        ]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def test_ops_do_not_return_ids(self):
        self._patch()
        self.assertNotIn("local_stash_ids", scene_matcher.find_matches_fast("1", {}))
        self.assertNotIn("local_stash_ids", scene_matcher.find_matches_thorough("1", {}))

    def test_main_ignores_old_cache_args(self):
        import io, json
        self._patch()
        args = {"operation": "find_matches_fast", "scene_id": "1",
                "cached_local_stash_ids": ["x"], "cache_endpoint": "https://stashdb.org"}
        stdin = io.StringIO(json.dumps({"server_connection": {}, "args": args}))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(scene_matcher, "stash_graphql", return_value=None), \
             mock.patch.object(scene_matcher, "_input_data", None), \
             mock.patch("sys.stdout", out):
            scene_matcher.main()
        reply = json.loads(out.getvalue())
        self.assertNotIn("error", reply)
        self.assertNotIn("local_stash_ids", reply["output"])


class TestMainConfigures(unittest.TestCase):
    def test_main_configures_plugin_data_first(self):
        import io, json
        calls = []
        sc = {"Dir": "/x"}
        stdin = io.StringIO(json.dumps({"server_connection": sc, "args": {}}))
        with mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(plugin_data, "configure", side_effect=lambda s: calls.append(s)), \
             mock.patch.object(scene_matcher, "stash_graphql", return_value=None), \
             mock.patch.object(scene_matcher, "_input_data", None), \
             mock.patch("sys.stdout", io.StringIO()):
            scene_matcher.main()
        self.assertEqual(calls, [sc])


if __name__ == "__main__":
    unittest.main()
