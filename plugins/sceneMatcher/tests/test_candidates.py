#!/usr/bin/env python3
"""Offline tests: candidate queries prefer AND, honor page caps, and report truncation."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scene_matcher
import stashbox_api
from test_errors import BOXES, EP, MatchBase, make_scene

URL = EP


def sc(i, **kw):
    s = {"id": i, "title": "Some Title Here", "studio": None, "performers": [], "images": []}
    s.update(kw)
    return s


class FakePaginated:
    """Stands in for paginated_query: records the input and page cap of each call."""

    def __init__(self, responses):
        self.responses = list(responses)  # each (items, total)
        self.calls = []

    def __call__(self, url, api_key, query, build_fn, extract_fn, plugin_settings=None,
                 operation_name=None, max_pages=None):
        self.calls.append({"input": build_fn(1, 100)["input"], "max_pages": max_pages,
                           "settings": plugin_settings})
        items, total = self.responses.pop(0)
        return items, total, None


def run(fn, responses, *args, settings=None):
    fake = FakePaginated(responses)
    with mock.patch.object(stashbox_api, "paginated_query", fake):
        out = fn(URL, "k", *args, plugin_settings=settings)
    return out, fake


class TestModifierOrder(unittest.TestCase):
    def test_two_performers_and_first_enough_no_second_query(self):
        many = [sc(str(i)) for i in range(10)]
        (scenes, err), fake = run(stashbox_api.query_scenes_by_performers,
                                  [(many, 10)], ["p1", "p2"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["input"]["performers"]["modifier"], "INCLUDES_ALL")
        self.assertEqual(len(scenes), 10)
        self.assertIsNone(err)

    def test_two_performers_few_adds_includes_and_dedupes(self):
        (scenes, _), fake = run(stashbox_api.query_scenes_by_performers,
                                [([sc("a")], 1), ([sc("a"), sc("b")], 2)], ["p1", "p2"])
        mods = [c["input"]["performers"]["modifier"] for c in fake.calls]
        self.assertEqual(mods, ["INCLUDES_ALL", "INCLUDES"])
        self.assertEqual(sorted(s["id"] for s in scenes), ["a", "b"])

    def test_one_performer_uses_includes_only(self):
        _, fake = run(stashbox_api.query_scenes_by_performers, [([sc("a")], 1)], ["p1"])
        self.assertEqual([c["input"]["performers"]["modifier"] for c in fake.calls], ["INCLUDES"])

    def test_combined_same_rule(self):
        (scenes, _), fake = run(stashbox_api.query_scenes_combined,
                                [([sc("a")], 1), ([sc("a"), sc("b")], 2)], ["p1", "p2"], "s1")
        self.assertEqual([c["input"]["performers"]["modifier"] for c in fake.calls],
                         ["INCLUDES_ALL", "INCLUDES"])
        for c in fake.calls:
            self.assertEqual(c["input"]["studios"]["value"], ["s1"])
        self.assertEqual(len(scenes), 2)

    def test_combined_one_performer(self):
        _, fake = run(stashbox_api.query_scenes_combined, [([sc("a")], 1)], ["p1"], "s1")
        self.assertEqual([c["input"]["performers"]["modifier"] for c in fake.calls], ["INCLUDES"])


class TestPageCaps(unittest.TestCase):
    def test_defaults(self):
        _, f = run(stashbox_api.query_scenes_by_performers, [([], 0)], ["p1"])
        self.assertEqual(f.calls[0]["max_pages"], 10)
        _, f = run(stashbox_api.query_scenes_combined, [([], 0)], ["p1"], "s")
        self.assertEqual(f.calls[0]["max_pages"], 10)
        _, f = run(stashbox_api.query_scenes_by_studio, [([], 0)], "s")
        self.assertEqual(f.calls[0]["max_pages"], 25)

    def test_settings_override(self):
        st = {"stashbox_max_pages_performer": 3, "stashbox_max_pages_studio": 7}
        _, f = run(stashbox_api.query_scenes_by_performers, [([], 0)], ["p1"], settings=st)
        self.assertEqual(f.calls[0]["max_pages"], 3)
        _, f = run(stashbox_api.query_scenes_combined, [([], 0)], ["p1"], "s", settings=st)
        self.assertEqual(f.calls[0]["max_pages"], 3)
        _, f = run(stashbox_api.query_scenes_by_studio, [([], 0)], "s", settings=st)
        self.assertEqual(f.calls[0]["max_pages"], 7)

    def test_wrappers_do_not_hardcode(self):
        seen = {}

        def rec(name):
            def f(*a, **kw):
                seen[name] = kw
                return [], None
            return f
        with mock.patch.object(stashbox_api, "query_scenes_combined", rec("c")), \
             mock.patch.object(stashbox_api, "query_scenes_by_performers", rec("p")), \
             mock.patch.object(stashbox_api, "query_scenes_by_studio", rec("s")):
            scene_matcher.query_stashdb_scenes_combined(URL, "k", ["p"], "s")
            scene_matcher.query_stashdb_scenes_by_performers(URL, "k", ["p"])
            scene_matcher.query_stashdb_scenes_by_studio(URL, "k", "s")
        for kw in seen.values():
            self.assertNotIn("max_pages", kw)

    def test_max_retries_zero_allowed_others_clamped(self):
        self.assertEqual(stashbox_api.get_config({"stashbox_max_retries": 0}, "max_retries"), 0)
        self.assertEqual(stashbox_api.get_config({"stashbox_per_page": 0}, "per_page"), 1)
        self.assertEqual(stashbox_api.get_config({"stashbox_max_pages_studio": 0}, "max_pages_studio"), 1)

    def test_truncated_marker(self):
        (scenes, _), _ = run(stashbox_api.query_scenes_by_studio, [([sc("a")], 500)], "s")
        self.assertTrue(scenes.truncated)
        self.assertEqual(scenes.total, 500)
        (scenes, _), _ = run(stashbox_api.query_scenes_by_studio, [([sc("a")], 1)], "s")
        self.assertFalse(scenes.truncated)


class TestThoroughTruncation(MatchBase):
    def _patch(self, combined, performers, studio):
        mocks = {}
        for name, val in (("query_stashdb_scenes_combined", combined),
                          ("query_stashdb_scenes_by_performers", performers),
                          ("query_stashdb_scenes_by_studio", studio)):
            p = mock.patch.object(scene_matcher, name, side_effect=val)
            mocks[name] = p.start()
            self.addCleanup(p.stop)
        return mocks

    def test_max_results_cuts_and_reports(self):
        scenes = [sc(str(i)) for i in range(30)]
        self._patch([(scenes, None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {"maxResults": 10})
        self.assertEqual(len(out["results"]), 10)
        self.assertEqual(out["total_results"], 10)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["total_candidates"], 30)

    def test_no_truncation_no_fields(self):
        self._patch([([sc("a")], None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertNotIn("truncated", out)
        self.assertNotIn("total_candidates", out)

    def test_max_results_clamping(self):
        f = scene_matcher.get_max_results
        self.assertEqual(f({}), 50)
        self.assertEqual(f({"maxResults": 3}), 10)
        self.assertEqual(f({"maxResults": 9999}), 500)
        self.assertEqual(f({"maxResults": "abc"}), 50)
        self.assertEqual(f({"maxResults": None}), 50)
        self.assertEqual(f({"maxResults": 120}), 120)

    def test_default_cut_is_50(self):
        scenes = [sc(str(i)) for i in range(60)]
        self._patch([(scenes, None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertEqual(len(out["results"]), 50)
        self.assertEqual(out["total_candidates"], 60)

    def test_page_cap_uses_server_total(self):
        capped = stashbox_api.ScenesList([sc("a"), sc("b")])
        capped.total = 1234
        capped.truncated = True
        self._patch([(capped, None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertTrue(out["truncated"])
        self.assertEqual(out["total_candidates"], 1234)
        self.assertEqual(out["total_results"], 2)

    def test_fallback_threshold_on_raw_count(self):
        # 10 raw results, all excluded as phase-1 ids: fallbacks must NOT run.
        ids = [str(i) for i in range(10)]
        m = self._patch([([sc(i) for i in ids], None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {}, exclude_ids=ids)
        m["query_stashdb_scenes_by_performers"].assert_not_called()
        m["query_stashdb_scenes_by_studio"].assert_not_called()
        self.assertEqual(out["total_results"], 0)

    def test_fallback_runs_when_raw_count_low(self):
        m = self._patch([([sc("a")], None)], [([], None)], [([], None)])
        scene_matcher.find_matches_thorough("1", {})
        m["query_stashdb_scenes_by_performers"].assert_called_once()
        m["query_stashdb_scenes_by_studio"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
