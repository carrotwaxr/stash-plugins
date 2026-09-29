#!/usr/bin/env python3
"""Offline tests for endpoint resolution, normalization and the User-Agent."""

import io
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scene_matcher
import stashbox_api
from scene_matcher import normalize_endpoint, resolve_endpoint

STASHDB = {"name": "StashDB", "endpoint": "https://stashdb.org/graphql", "api_key": "k1"}
TPDB = {"name": "ThePornDB", "endpoint": "https://theporndb.net/graphql", "api_key": "k2"}
BOXES = [STASHDB, TPDB]


def make_scene(stash_ids):
    return {
        "id": "1", "title": "t", "stash_ids": stash_ids, "files": [],
        "performers": [{"name": "P", "stash_ids": [
            {"endpoint": "https://STASHDB.org/graphql/", "stash_id": "perf-1"}]}],
        "studio": {"name": "S", "stash_ids": [
            {"endpoint": "https://stashdb.org/graphql", "stash_id": "studio-1"}]},
    }


class TestNormalize(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_endpoint("  HTTPS://StashDB.org/graphql/ "), "https://stashdb.org/graphql")
        self.assertEqual(normalize_endpoint(None), "")
        self.assertEqual(normalize_endpoint(""), "")


class TestResolve(unittest.TestCase):
    def test_arg_wins_over_setting(self):
        box, err = resolve_endpoint("https://THEPORNDB.net/graphql/", BOXES, "https://stashdb.org/graphql")
        self.assertIsNone(err)
        self.assertIs(box, TPDB)

    def test_setting_used_when_no_arg(self):
        box, err = resolve_endpoint(None, BOXES, "https://theporndb.net/graphql/")
        self.assertIs(box, TPDB)

    def test_setting_without_graphql(self):
        box, err = resolve_endpoint("", BOXES, "https://theporndb.net")
        self.assertIs(box, TPDB)

    def test_first_box_fallback(self):
        box, err = resolve_endpoint(None, BOXES, "")
        self.assertIs(box, STASHDB)

    def test_unknown_arg_is_error_naming_boxes(self):
        box, err = resolve_endpoint("https://evil.example/graphql", BOXES, "")
        self.assertIsNone(box)
        self.assertIn("StashDB", err)
        self.assertIn("ThePornDB", err)

    def test_no_boxes(self):
        box, err = resolve_endpoint(None, [], "")
        self.assertIsNone(box)
        self.assertTrue(err)


class TestContext(unittest.TestCase):
    def ctx(self, scene, endpoint=None, setting=""):
        with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=BOXES), \
             mock.patch.object(scene_matcher, "get_local_scene", return_value=scene), \
             mock.patch("urllib.request.urlopen") as urlopen:
            result = scene_matcher.get_scene_context(
                "1", {"stashBoxEndpoint": setting}, endpoint=endpoint)
            self.assertFalse(urlopen.called)
            return result

    def test_linked_only_to_other_box_not_rejected(self):
        scene = make_scene([{"endpoint": "https://theporndb.net/graphql", "stash_id": "x"}])
        context, error = self.ctx(scene, endpoint="https://stashdb.org/graphql")
        self.assertIsNone(error)
        self.assertEqual(context["endpoint"], "https://stashdb.org/graphql")
        self.assertEqual(context["stashdb_url"], "https://stashdb.org")
        self.assertEqual(context["performer_stash_ids"], {"perf-1"})
        self.assertEqual(context["studio_stash_id"], "studio-1")

    def test_linked_to_resolved_box_rejected_despite_case_and_slash(self):
        scene = make_scene([{"endpoint": "HTTPS://stashdb.org/graphql/", "stash_id": "x"}])
        context, error = self.ctx(scene, endpoint="https://stashdb.org/graphql")
        self.assertIsNone(context)
        self.assertIn("already has", error["error"])

    def test_unknown_endpoint_error_no_request(self):
        context, error = self.ctx(make_scene([]), endpoint="https://evil.example/graphql")
        self.assertIsNone(context)
        self.assertIn("StashDB", error["error"])

    def test_stashdb_url_rule(self):
        self.assertEqual(scene_matcher.site_base("https://stashdb.org/graphql"), "https://stashdb.org")
        self.assertEqual(scene_matcher.site_base("https://x.test/api"), "https://x.test/api")


class TestUnnamedBox(unittest.TestCase):
    """A stash-box saved without a name is called by its host, never by an empty string."""

    def test_empty_or_missing_name_falls_back_to_host(self):
        for box in ({"name": "", "endpoint": "https://fansdb.cc/graphql", "api_key": "k"},
                    {"name": None, "endpoint": "https://fansdb.cc/graphql", "api_key": "k"},
                    {"name": "  ", "endpoint": "https://fansdb.cc/graphql", "api_key": "k"},
                    {"endpoint": "https://fansdb.cc/graphql", "api_key": "k"}):
            with self.subTest(name=box.get("name", "<missing>")):
                linked = make_scene([{"endpoint": "https://fansdb.cc/graphql", "stash_id": "x"}])
                with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]), \
                     mock.patch.object(scene_matcher, "get_local_scene", return_value=linked):
                    _, error = scene_matcher.get_scene_context("1", {})
                self.assertEqual(error["error"], "Scene already has a fansdb.cc ID. No matching needed.")
                with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]), \
                     mock.patch.object(scene_matcher, "get_local_scene", return_value=make_scene([])):
                    context, _ = scene_matcher.get_scene_context("1", {})
                self.assertEqual(context["stashdb_name"], "fansdb.cc")

    def test_box_name_helper(self):
        self.assertEqual(scene_matcher.box_name({"name": "StashDB", "endpoint": "https://stashdb.org/graphql"}),
                         "StashDB")
        self.assertEqual(scene_matcher.box_name({"name": "", "endpoint": "not a url"}), "the stash-box")


class TestFindMatches(unittest.TestCase):
    def test_response_has_endpoint_keys_and_uses_arg(self):
        scene = make_scene([])
        seen = []

        def fake_text(url, key, term, **kw):
            seen.append((url, key))
            return []

        with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=BOXES), \
             mock.patch.object(scene_matcher, "get_local_scene", return_value=scene), \
             mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=fake_text), \
             mock.patch.object(scene_matcher, "local_stash_ids", return_value=set()):
            out = scene_matcher.find_matches_fast(
                "1", {"stashBoxEndpoint": "https://stashdb.org/graphql"},
                endpoint="https://theporndb.net/graphql")
        self.assertEqual(out["endpoint"], "https://theporndb.net/graphql")
        self.assertEqual(out["endpoint_name"], "ThePornDB")
        self.assertTrue(all(u == "https://theporndb.net/graphql" and k == "k2" for u, k in seen))


class TestUserAgent(unittest.TestCase):
    def test_header_sent(self):
        version = re.search(r"^version:\s*(\S+)", open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sceneMatcher.yml")
        ).read(), re.M).group(1)
        captured = []

        def fake_urlopen(req, **kw):
            captured.append(req)
            return io.BytesIO(json.dumps({"data": {"ok": 1}}).encode())

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            stashbox_api.graphql_request_with_retry("https://stashdb.org/graphql", "query{a}", api_key="k")
        self.assertEqual(captured[0].get_header("User-agent"), f"stash-plugins-sceneMatcher/{version}")


if __name__ == "__main__":
    unittest.main()
