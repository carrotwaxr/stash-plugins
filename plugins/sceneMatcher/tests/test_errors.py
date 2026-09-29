#!/usr/bin/env python3
"""Offline tests: stash-box failures are reported, not read as "no matches"."""

import io
import json
import os
import socket
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugin_data
import scene_matcher
import stashbox_api
from stashbox_api import StashBoxAPIError

URL = "https://stashdb.org/graphql"
EP = URL
SETTINGS = {"stashbox_rate_limit_pause": 7, "stashbox_max_retries": 3}


def http_error(code, headers=None, body=b""):
    return urllib.error.HTTPError(URL, code, "reason", headers or {}, io.BytesIO(body))


def ok(payload):
    return io.BytesIO(json.dumps(payload).encode())


def call(side_effect, settings=None):
    with mock.patch("urllib.request.urlopen", side_effect=side_effect) as u, \
         mock.patch("time.sleep") as s:
        try:
            result = stashbox_api.graphql_request_with_retry(
                URL, "query{a}", api_key="k", plugin_settings=settings or SETTINGS)
            return result, None, u, s
        except StashBoxAPIError as e:
            return None, e, u, s


class TestRequestErrors(unittest.TestCase):
    def test_401_403_are_auth_errors_without_retry(self):
        for code in (401, 403):
            _, err, u, s = call([http_error(code)])
            self.assertIsNotNone(err)
            self.assertTrue(err.is_auth_error)
            self.assertEqual(u.call_count, 1)
            s.assert_not_called()

    def test_other_http_error_not_auth(self):
        _, err, _, _ = call([http_error(404)])
        self.assertFalse(err.is_auth_error)

    def test_graphql_errors_null_data_raises(self):
        _, err, _, _ = call([ok({"errors": [{"message": "boom"}], "data": None})])
        self.assertIsNotNone(err)
        self.assertIn("boom", str(err))
        self.assertFalse(err.is_auth_error)

    def test_graphql_errors_missing_data_raises(self):
        _, err, _, _ = call([ok({"errors": [{"message": "boom"}]})])
        self.assertIsNotNone(err)

    def test_graphql_not_authorized_is_auth(self):
        for msg in ("not authorized", "Unauthorized", "FORBIDDEN access"):
            _, err, _, _ = call([ok({"errors": [{"message": msg}], "data": None})])
            self.assertTrue(err.is_auth_error, msg)

    def test_graphql_errors_with_data_returns_data(self):
        data, err, _, _ = call([ok({"errors": [{"message": "partial"}], "data": {"x": 1}})])
        self.assertIsNone(err)
        self.assertEqual(data, {"x": 1})

    def test_429_uses_retry_after(self):
        data, err, u, s = call([http_error(429, {"Retry-After": "5"}), ok({"data": {"x": 1}})])
        self.assertIsNone(err)
        self.assertEqual(data, {"x": 1})
        s.assert_called_once_with(5)

    def test_429_without_retry_after_uses_pause(self):
        _, err, _, s = call([http_error(429), ok({"data": {"x": 1}})])
        self.assertIsNone(err)
        s.assert_called_once_with(7)

    def test_429_retry_after_over_60_raises_without_sleep(self):
        _, err, _, s = call([http_error(429, {"Retry-After": "61"})])
        self.assertIsNotNone(err)
        self.assertEqual(err.status_code, 429)
        self.assertFalse(err.is_auth_error)
        s.assert_not_called()

    def test_429_total_wait_over_90_raises(self):
        _, err, u, s = call([http_error(429, {"Retry-After": "50"}),
                             http_error(429, {"Retry-After": "50"}),
                             ok({"data": {}})])
        self.assertIsNotNone(err)
        self.assertEqual(err.status_code, 429)
        self.assertEqual(s.call_count, 1)
        self.assertEqual(u.call_count, 2)

    def test_pause_setting_over_60_is_clamped_not_skipped(self):
        # Our own pause is cut to what one request may wait (90 s), never skipped;
        # the 60 s limit is for a server's Retry-After
        for pause, slept in ((75, 75), (200, 90)):
            with self.subTest(pause=pause):
                data, err, u, s = call([http_error(429), ok({"data": {"x": 1}})],
                                       {"stashbox_rate_limit_pause": pause, "stashbox_max_retries": 3})
                self.assertIsNone(err)
                self.assertEqual(data, {"x": 1})
                s.assert_called_once_with(slept)
                self.assertEqual(u.call_count, 2)

    def test_pause_zero_retries_at_once(self):
        data, err, _, s = call([http_error(429), ok({"data": {"x": 1}})],
                               {"stashbox_rate_limit_pause": 0, "stashbox_max_retries": 3})
        self.assertIsNone(err)
        self.assertEqual(data, {"x": 1})

    def test_pause_clamped_to_what_the_request_has_left(self):
        _, err, _, s = call([http_error(429), http_error(429), ok({"data": {}})],
                            {"stashbox_rate_limit_pause": 75, "stashbox_max_retries": 3})
        self.assertIsNone(err)
        self.assertEqual([c.args[0] for c in s.call_args_list], [75, 15])

    def test_timeout_retried(self):
        for exc in (TimeoutError("t"), socket.timeout("t")):
            data, err, u, s = call([exc, ok({"data": {"x": 1}})])
            self.assertIsNone(err)
            self.assertEqual(data, {"x": 1})
            self.assertEqual(s.call_count, 1)

    def test_timeout_exhausts_retries(self):
        _, err, u, _ = call([TimeoutError("t")] * 10)
        self.assertIsNotNone(err)
        self.assertEqual(u.call_count, 4)

    def test_non_json_body_raises_with_snippet(self):
        _, err, _, _ = call([io.BytesIO(b"<html>Cloudflare Attention Required</html>")])
        self.assertIsNotNone(err)
        self.assertIn("Cloudflare", str(err))

    def test_search_does_not_swallow(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401)]), \
             mock.patch("time.sleep"):
            with self.assertRaises(StashBoxAPIError) as cm:
                stashbox_api.search_scenes_by_text(URL, "k", "some title", plugin_settings=SETTINGS)
        self.assertTrue(cm.exception.is_auth_error)


def scene_page(ids, count):
    return {"data": {"queryScenes": {"count": count, "scenes": [{"id": i} for i in ids]}}}


PAGED = {"stashbox_per_page": 1, "stashbox_max_retries": 1, "stashbox_request_delay": 0}


class TestPaginated(unittest.TestCase):
    def _run(self, side_effect):
        with mock.patch("urllib.request.urlopen", side_effect=side_effect), \
             mock.patch("time.sleep"):
            return stashbox_api.paginated_query(
                URL, "k", "query", lambda p, pp: {"page": p},
                lambda d: (d["queryScenes"]["scenes"], d["queryScenes"]["count"]),
                plugin_settings=PAGED, operation_name="t", max_pages=5)

    def test_success_shape(self):
        items, total, error = self._run([ok(scene_page(["a"], 2)), ok(scene_page(["b"], 2))])
        self.assertEqual([i["id"] for i in items], ["a", "b"])
        self.assertEqual(total, 2)
        self.assertIsNone(error)

    def test_later_page_failure_keeps_earlier(self):
        items, total, error = self._run([ok(scene_page(["a"], 3)), http_error(400)])
        self.assertEqual([i["id"] for i in items], ["a"])
        self.assertIsInstance(error, StashBoxAPIError)

    def test_first_page_failure_raises(self):
        with self.assertRaises(StashBoxAPIError):
            self._run([http_error(403)])

    def test_query_functions_return_scenes_and_error(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[ok(scene_page(["a"], 3)), http_error(400)]), \
             mock.patch("time.sleep"):
            scenes, error = stashbox_api.query_scenes_by_studio(URL, "k", "s1", plugin_settings=PAGED)
        self.assertEqual([s["id"] for s in scenes], ["a"])
        self.assertIsInstance(error, StashBoxAPIError)
        self.assertEqual(stashbox_api.query_scenes_combined(URL, "k", [], "s"), ([], None))
        self.assertEqual(stashbox_api.query_scenes_by_performers(URL, "k", []), ([], None))


BOXES = [{"name": "StashDB", "endpoint": EP, "api_key": "k"}]


def make_scene(performers=True, studio=True):
    return {
        "id": "1", "title": "Some Title Here", "stash_ids": [], "files": [],
        "performers": [{"name": "Jane", "stash_ids": [{"endpoint": EP, "stash_id": "p1"}]}] if performers else [],
        "studio": {"name": "Studio", "stash_ids": [{"endpoint": EP, "stash_id": "s1"}]} if studio else None,
    }


def sc(i):
    return {"id": i, "title": "Some Title Here", "studio": None, "performers": [], "images": []}


class MatchBase(unittest.TestCase):
    def setUp(self):
        self.scene = make_scene()
        for p in (
            mock.patch.object(scene_matcher, "get_stashbox_config", return_value=BOXES),
            mock.patch.object(scene_matcher, "get_local_scene", side_effect=lambda _: self.scene),
            mock.patch.object(scene_matcher, "local_stash_ids", return_value=set()),
        ):
            p.start()
            self.addCleanup(p.stop)


class TestFast(MatchBase):
    def test_one_search_fails_returns_other_with_warning(self):
        side = [StashBoxAPIError("HTTP 500: boom"), [sc("a")]]
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=side):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertNotIn("error", out)
        self.assertEqual(out["total_results"], 1)
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("boom", out["warnings"][0])

    def test_both_fail_returns_error(self):
        side = [StashBoxAPIError("HTTP 500: boom"), StashBoxAPIError("HTTP 500: boom")]
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=side):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertIn("boom", out["error"])
        self.assertNotIn("auth_error", out)

    def test_auth_failure(self):
        e = StashBoxAPIError("HTTP 401: Unauthorized", status_code=401, is_auth_error=True)
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=[e, e]):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertTrue(out["auth_error"])
        self.assertIn("StashDB", out["error"])
        self.assertIn("Settings > Metadata Providers", out["error"])

    def test_single_search_failing_is_total_failure(self):
        self.scene["performers"] = []
        self.scene["studio"] = None
        with mock.patch.object(scene_matcher, "query_stashdb_by_text",
                               side_effect=StashBoxAPIError("nope")):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertIn("nope", out["error"])

    def test_no_warnings_key_when_clean(self):
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", return_value=[sc("a")]):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertNotIn("warnings", out)
        self.assertNotIn("partial", out)


class TestThorough(MatchBase):
    def _patch(self, combined, performers, studio):
        for name, val in (("query_stashdb_scenes_combined", combined),
                          ("query_stashdb_scenes_by_performers", performers),
                          ("query_stashdb_scenes_by_studio", studio)):
            p = mock.patch.object(scene_matcher, name, side_effect=val)
            p.start()
            self.addCleanup(p.stop)

    def test_page_failure_partial(self):
        err = StashBoxAPIError("HTTP 500: later page")
        self._patch([([sc("a")], err)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertNotIn("error", out)
        self.assertTrue(out["partial"])
        self.assertEqual(out["total_results"], 1)
        self.assertIn("later page", out["warnings"][0])

    def test_one_query_raises_others_succeed_is_partial(self):
        self._patch([StashBoxAPIError("combined broke")], [([sc("a")], None)], [([sc("b")], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertTrue(out["partial"])
        self.assertEqual(out["total_results"], 2)
        self.assertIn("combined broke", out["warnings"][0])

    def test_total_failure_error(self):
        e = StashBoxAPIError("down")
        self._patch([e], [e], [e])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertIn("down", out["error"])
        self.assertNotIn("results", out)

    def test_total_auth_failure(self):
        e = StashBoxAPIError("HTTP 403: Forbidden", status_code=403, is_auth_error=True)
        self._patch([e], [e], [e])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertTrue(out["auth_error"])
        self.assertIn("Settings > Metadata Providers", out["error"])

    def test_clean_run_has_no_flags(self):
        self._patch([([sc("a")] * 1, None)], [([], None)], [([], None)])
        out = scene_matcher.find_matches_thorough("1", {})
        self.assertNotIn("partial", out)
        self.assertNotIn("warnings", out)


class TestYearZeroDate(MatchBase):
    def test_a_year_zero_release_date_does_not_fail_the_phase(self):
        dated = [dict(sc("a"), release_date="0000"), dict(sc("b"), release_date="0000-05"),
                 dict(sc("c"), release_date="2024-01-02")]
        with mock.patch.object(scene_matcher, "query_stashdb_scenes_combined", return_value=(dated, None)), \
             mock.patch.object(scene_matcher, "query_stashdb_scenes_by_performers", return_value=([], None)), \
             mock.patch.object(scene_matcher, "query_stashdb_scenes_by_studio", return_value=([], None)):
            out = scene_matcher.find_matches_thorough("1", {})
        self.assertNotIn("error", out)
        self.assertEqual([r["stash_id"] for r in out["results"]], ["c", "a", "b"])


class TestLocalIdsFailure(MatchBase):
    """Failing to list the library's stash IDs costs the In Stash badges, not the results."""

    FAILURES = (RuntimeError("Could not list local scenes from Stash"),
                urllib.error.URLError("connection refused"), socket.timeout("timed out"))

    def check(self, out):
        self.assertNotIn("error", out)
        self.assertEqual(out["total_results"], 1)
        self.assertFalse(out["results"][0]["in_local_stash"])
        self.assertIn("Couldn't read your library's stash IDs; In Stash badges may be missing",
                      out["warnings"])

    def test_phase1_keeps_results(self):
        for exc in self.FAILURES:
            with self.subTest(exc=exc), \
                 mock.patch.object(scene_matcher, "local_stash_ids", side_effect=exc), \
                 mock.patch.object(scene_matcher, "query_stashdb_by_text", return_value=[sc("a")]):
                self.check(scene_matcher.find_matches_fast("1", {}))

    def test_phase2_keeps_results(self):
        for exc in self.FAILURES:
            with self.subTest(exc=exc), \
                 mock.patch.object(scene_matcher, "local_stash_ids", side_effect=exc), \
                 mock.patch.object(scene_matcher, "query_stashdb_scenes_combined", return_value=([sc("a")], None)), \
                 mock.patch.object(scene_matcher, "query_stashdb_scenes_by_performers", return_value=([], None)), \
                 mock.patch.object(scene_matcher, "query_stashdb_scenes_by_studio", return_value=([], None)):
                out = scene_matcher.find_matches_thorough("1", {})
                self.check(out)
                self.assertNotIn("partial", out)  # the results themselves are complete


class FakeClock:
    """Stands in for stashbox_api's `time`: sleep() moves monotonic() on, so waits add up."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def time(self):
        return self.now


def rate_limited_once(ok_payload):
    """urlopen side effect: every request is answered 429 (no Retry-After) once, then OK."""
    state = {"n": 0}

    def urlopen(req, timeout=None, context=None):
        state["n"] += 1
        if state["n"] % 2:
            raise http_error(429)
        return ok(ok_payload(state["n"] // 2))
    return urlopen


def text_page(_n):
    return {"data": {"searchScene": [{"id": "t1", "title": "Some Title Here", "performers": [], "images": []}]}}


def big_page(n):
    # 100 scenes per page of 2000, so paging would go on to the page cap
    return {"data": {"queryScenes": {"count": 2000, "scenes": [
        {"id": f"p{n}-{i}", "title": "x", "performers": [], "images": []} for i in range(100)]}}}


class TestOperationBudget(MatchBase):
    """Each phase shares one time budget, so it ends before the browser's 120 s abort."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        p = mock.patch.object(stashbox_api, "time", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def test_budget_is_below_the_ui_timeout(self):
        self.assertLessEqual(stashbox_api.OPERATION_BUDGET_SECONDS, 100)
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "scene-matcher.js")).read()
        self.assertIn("REQUEST_TIMEOUT_MS = 120000", js)

    def test_phase1_waits_stay_within_the_budget(self):
        with mock.patch("urllib.request.urlopen", side_effect=rate_limited_once(text_page)):
            out = scene_matcher.find_matches_fast("1", {})
        self.assertLessEqual(sum(self.clock.slept), stashbox_api.OPERATION_BUDGET_SECONDS)
        self.assertNotIn("error", out)
        self.assertEqual(out["total_results"], 1)  # the first search's result is kept
        self.assertTrue(out["partial"])
        self.assertTrue(any("stopped after 100 s" in w for w in out["warnings"]), out["warnings"])

    def test_phase2_waits_stay_within_the_budget(self):
        with mock.patch("urllib.request.urlopen", side_effect=rate_limited_once(big_page)):
            out = scene_matcher.find_matches_thorough("1", {})
        self.assertLessEqual(sum(self.clock.slept), stashbox_api.OPERATION_BUDGET_SECONDS)
        self.assertNotIn("error", out)
        self.assertTrue(out["partial"])
        self.assertGreater(out["total_results"], 0)  # pages fetched before the stop are kept
        stops = [w for w in out["warnings"] if "stopped after 100 s" in w]
        self.assertEqual(len(stops), 1, out["warnings"])

    def test_phase1_nothing_before_the_budget_ran_out_is_an_error(self):
        deadline = stashbox_api.Deadline(0)
        with mock.patch.object(stashbox_api, "Deadline", return_value=deadline), \
             mock.patch("urllib.request.urlopen") as u:
            out = scene_matcher.find_matches_fast("1", {})
        u.assert_not_called()
        self.assertIn("stopped after", out["error"])


class TestDeadline(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        p = mock.patch.object(stashbox_api, "time", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def request(self, side_effect, deadline, settings=None):
        with mock.patch("urllib.request.urlopen", side_effect=side_effect) as u:
            try:
                return stashbox_api.graphql_request_with_retry(
                    URL, "query{a}", api_key="k", plugin_settings=settings or SETTINGS,
                    deadline=deadline), None, u
            except StashBoxAPIError as e:
                return None, e, u

    def test_expired_deadline_makes_no_request(self):
        _, err, u = self.request([ok({"data": {}})], stashbox_api.Deadline(0))
        self.assertIsInstance(err, stashbox_api.BudgetExceeded)
        u.assert_not_called()

    def test_retry_after_past_the_deadline_is_not_waited(self):
        deadline = stashbox_api.Deadline(10)
        _, err, u = self.request([http_error(429, {"Retry-After": "30"}), ok({"data": {}})], deadline)
        self.assertIsInstance(err, stashbox_api.BudgetExceeded)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(u.call_count, 1)

    def test_pause_past_the_deadline_is_not_waited(self):
        _, err, _ = self.request([http_error(429), ok({"data": {}})], stashbox_api.Deadline(5))
        self.assertIsInstance(err, stashbox_api.BudgetExceeded)
        self.assertEqual(self.clock.slept, [])

    def test_backoff_past_the_deadline_is_not_waited(self):
        st = {"stashbox_initial_retry_delay": 8, "stashbox_max_retries": 3}
        _, err, _ = self.request([http_error(503), ok({"data": {}})], stashbox_api.Deadline(5), st)
        self.assertIsInstance(err, stashbox_api.BudgetExceeded)
        self.assertEqual(self.clock.slept, [])

    def test_wait_inside_the_deadline_still_happens(self):
        data, err, _ = self.request([http_error(429, {"Retry-After": "5"}), ok({"data": {"x": 1}})],
                                    stashbox_api.Deadline(50))
        self.assertIsNone(err)
        self.assertEqual(data, {"x": 1})
        self.assertEqual(self.clock.slept, [5])

    def test_request_timeout_capped_by_the_budget(self):
        deadline = stashbox_api.Deadline(12)
        _, _, u = self.request([ok({"data": {}})], deadline, {"stashbox_request_timeout": 30})
        self.assertEqual(u.call_args.kwargs["timeout"], 12)

    def test_pagination_stops_at_the_deadline_and_keeps_pages(self):
        deadline = stashbox_api.Deadline(10)

        def urlopen(req, timeout=None, context=None):
            self.clock.now += 6  # each page takes 6 s
            return ok(scene_page(["a"], 5))
        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            items, total, error = stashbox_api.paginated_query(
                URL, "k", "query", lambda p, pp: {"page": p},
                lambda d: (d["queryScenes"]["scenes"], d["queryScenes"]["count"]),
                plugin_settings=PAGED, operation_name="t", max_pages=5, deadline=deadline)
        self.assertEqual(len(items), 2)
        self.assertEqual(total, 5)
        self.assertIsInstance(error, stashbox_api.BudgetExceeded)


class TestMain(MatchBase):
    def _main(self, args):
        stdin = io.StringIO(json.dumps({"server_connection": {}, "args": args}))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(scene_matcher, "stash_graphql", return_value=None), \
             mock.patch.object(scene_matcher, "_input_data", None), \
             mock.patch.object(plugin_data, "configure"), \
             mock.patch("sys.stdout", out):
            scene_matcher.main()
        return json.loads(out.getvalue())

    def test_expected_failure_is_structured_output(self):
        e = StashBoxAPIError("HTTP 401", status_code=401, is_auth_error=True)
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=e):
            reply = self._main({"operation": "find_matches_fast", "scene_id": "1"})
        self.assertNotIn("error", reply)
        self.assertTrue(reply["output"]["auth_error"])
        self.assertIn("error", reply["output"])

    def test_unexpected_exception_is_top_level_error(self):
        with mock.patch.object(scene_matcher, "query_stashdb_by_text", side_effect=ValueError("bug")):
            reply = self._main({"operation": "find_matches_fast", "scene_id": "1"})
        self.assertEqual(reply, {"error": "bug"})

    def test_context_error_stays_top_level(self):
        self.scene = None
        reply = self._main({"operation": "find_matches_fast", "scene_id": "1"})
        self.assertIn("error", reply)
        self.assertNotIn("output", reply)


if __name__ == "__main__":
    unittest.main()
