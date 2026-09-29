"""Stash-box failures and truncation are reported, never shown as "you have everything"."""

import email.message
import email.utils
import io
import json
import time
import urllib.error
import urllib.request

import pytest

import missing_scenes as ms
import stashbox_api
import theporndb_api
from tests.test_whisparr import FakeResp
from tests.test_whisparr_flows import run_main

EP = "https://stashdb.org/graphql"
FANSDB = "https://fansdb.cc/graphql"
TPDB = "https://theporndb.net/graphql"
PSID = "perf-stash-id"


# ---- helpers ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Never really sleep; record what was asked for."""
    got = []
    monkeypatch.setattr(time, "sleep", lambda s: got.append(s))
    return got


class Everything(set):
    """A local_ids set that owns every scene, so a fill never completes."""

    def __contains__(self, item):
        return True


def ids(prefix, n):
    return [f"{prefix}{i}" for i in range(n)]


def pg(scene_ids, count, has_more):
    return {"scenes": [{"id": i} for i in scene_ids], "count": count, "page": 0, "has_more": has_more}


def api_error(message="HTTP 502: Bad Gateway", status_code=502, **kw):
    return stashbox_api.StashBoxAPIError(message, status_code=status_code, **kw)


def serve(monkeypatch, pages, target="query_scenes_page", module=stashbox_api):
    """Patch a page function. `pages` maps page -> result or exception (or is a callable).
    Returns the list of pages requested."""
    calls = []

    def fake(*a, page=1, **k):
        calls.append(page)
        r = pages(page) if callable(pages) else pages[page]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(module, target, fake)
    return calls


def setup_box(monkeypatch, endpoint=EP, name="StashDB", owned=(), total_local=0):
    monkeypatch.setattr(ms, "get_stashbox_config",
                        lambda: [{"endpoint": endpoint, "api_key": "k", "name": name}])
    monkeypatch.setattr(ms, "get_local_performer",
                        lambda pid: {"name": "Alice", "stash_ids": [{"endpoint": endpoint, "stash_id": PSID}]})
    monkeypatch.setattr(ms, "get_or_build_cache",
                        lambda ep: owned if isinstance(owned, Everything) else set(owned))
    monkeypatch.setattr(ms, "count_local_scenes_for_entity", lambda *a: total_local)


def find(settings=None, **kw):
    return ms.find_missing_scenes_paginated("performer", "1", settings or {}, **kw)


def browse(settings=None, **kw):
    return ms.browse_stashdb(settings or {}, **kw)


def fill(local_ids, url=EP, **kw):
    return ms.fetch_until_full(url, "k", "performer", PSID, local_ids, **kw)


def cursor(**over):
    state = {"stashdb_page": 2, "offset": 0, "sort": "DATE", "direction": "DESC",
             "entity_type": "performer", "entity_stash_id": PSID, "endpoint": EP}
    state.update(over)
    return ms.encode_cursor(state)


def http_error(code, retry_after=None, body=b""):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(EP, code, "err", headers, io.BytesIO(body))


def serve_http(monkeypatch, responses):
    """Patch urlopen with a queue of bodies (dict/bytes) or exceptions."""
    seen = []

    def fake_urlopen(req, *a, **k):
        seen.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(r)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def gql_page(scene_ids, count):
    return {"data": {"queryScenes": {"count": count, "scenes": [{"id": i} for i in scene_ids]}}}


# ---- a failed page ---------------------------------------------------------------

def test_failed_first_page_is_an_error_not_an_empty_result(monkeypatch):
    serve(monkeypatch, {1: api_error()})
    res = fill(set(), page_size=5)
    assert "502" in res["error"]
    assert res["scenes"] == []
    assert res["next_cursor"] is None
    assert res["is_complete"] is False
    assert res["partial"] is False


def test_failed_first_page_response(monkeypatch):
    setup_box(monkeypatch)
    serve(monkeypatch, {1: api_error()})
    res = find(page_size=5)
    assert "502" in res["error"] and "StashDB" in res["error"]
    assert res["missing_scenes"] == []
    assert res["partial"] is False
    assert res["auth_error"] is False
    assert res["is_complete"] is False
    assert res["has_more"] is False and res["cursor"] is None
    assert "missing_count_estimate" not in res
    assert res["total_on_stashdb"] is None


def test_failed_later_page_returns_scenes_so_far_and_a_retry_cursor(monkeypatch):
    setup_box(monkeypatch, owned=ids("a", 95))
    calls = serve(monkeypatch, {1: pg(ids("a", 100), 300, True), 2: api_error("HTTP 503", 503)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ids("a", 100)[95:]
    assert res["partial"] is True
    assert "503" in res["error"]
    assert res["has_more"] is True
    assert res["is_complete"] is False
    assert res["total_on_stashdb"] == 300
    state = ms.decode_cursor(res["cursor"])
    assert (state["stashdb_page"], state["offset"]) == (2, 0)
    assert calls == [1, 2]

    # Retrying from the returned cursor starts at the page that failed
    calls = serve(monkeypatch, {2: pg(ids("b", 100), 300, True)})
    res2 = find(page_size=10, cursor=res["cursor"])
    assert calls == [2]
    assert "error" not in res2
    assert [s["stash_id"] for s in res2["missing_scenes"]] == ids("b", 10)


def test_failure_after_pages_with_nothing_missing_is_still_partial(monkeypatch):
    serve(monkeypatch, {1: pg(ids("a", 100), 300, True), 2: api_error()})
    res = fill(Everything(), page_size=5)
    assert res["scenes"] == []
    assert res["partial"] is True
    state = ms.decode_cursor(res["next_cursor"])
    assert (state["stashdb_page"], state["offset"]) == (2, 0)


def test_successful_response_has_no_error_fields(monkeypatch):
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(ids("a", 3), 3, False)})
    res = find(page_size=10)
    assert "error" not in res and "partial" not in res
    assert res["is_complete"] is True


# ---- truncation ---------------------------------------------------------------------

def test_page_cap_returns_a_cursor(monkeypatch):
    setup_box(monkeypatch, owned=Everything())
    calls = serve(monkeypatch, lambda p: pg([f"p{p}-{i}" for i in range(100)], 100000, True))
    res = find(page_size=10)
    assert len(calls) == ms.MAX_PAGES_PER_REQUEST
    assert res["missing_scenes"] == []
    assert res["has_more"] is True
    assert res["is_complete"] is False
    state = ms.decode_cursor(res["cursor"])
    assert (state["stashdb_page"], state["offset"]) == (ms.MAX_PAGES_PER_REQUEST + 1, 0)


def test_fill_exactly_at_the_last_page_emits_no_cursor(monkeypatch):
    serve(monkeypatch, {1: pg(ids("s", 5), 5, False)})
    res = fill({"s0", "s1"}, page_size=3)
    assert [s["id"] for s in res["scenes"]] == ["s2", "s3", "s4"]
    assert res["next_cursor"] is None
    assert res["is_complete"] is True


def test_fill_with_only_owned_scenes_left_on_the_last_page_emits_no_cursor(monkeypatch):
    serve(monkeypatch, {1: pg(ids("s", 5), 5, False)})
    res = fill({"s3", "s4"}, page_size=3)
    assert [s["id"] for s in res["scenes"]] == ["s0", "s1", "s2"]
    assert res["next_cursor"] is None
    assert res["is_complete"] is True


def test_fill_at_page_end_with_more_pages_points_at_the_next_page(monkeypatch):
    serve(monkeypatch, {1: pg(ids("s", 5), 10, True)})
    res = fill(set(), page_size=5)
    state = ms.decode_cursor(res["next_cursor"])
    assert (state["stashdb_page"], state["offset"]) == (2, 0)
    assert res["is_complete"] is False


def test_fill_mid_page_points_inside_the_page(monkeypatch):
    serve(monkeypatch, {1: pg(ids("s", 5), 5, False)})
    res = fill(set(), page_size=2)
    state = ms.decode_cursor(res["next_cursor"])
    assert (state["stashdb_page"], state["offset"]) == (1, 2)


# ---- request_delay ----------------------------------------------------------------

def three_pages(p):
    return pg([f"p{p}-{i}" for i in range(100)], 300, p < 3)


def test_request_delay_sleeps_between_pages(monkeypatch, sleeps):
    serve(monkeypatch, three_pages)
    res = fill(Everything(), page_size=5, plugin_settings={})
    assert res["is_complete"] is True
    assert sleeps == [0.5, 0.5]


def test_request_delay_setting(monkeypatch, sleeps):
    serve(monkeypatch, three_pages)
    fill(Everything(), page_size=5, plugin_settings={"stashbox_request_delay": 2})
    assert sleeps == [2.0, 2.0]


def test_request_delay_zero_never_sleeps(monkeypatch, sleeps):
    serve(monkeypatch, three_pages)
    fill(Everything(), page_size=5, plugin_settings={"stashbox_request_delay": 0})
    assert sleeps == []


def test_single_page_does_not_sleep(monkeypatch, sleeps):
    serve(monkeypatch, {1: pg(ids("s", 5), 5, False)})
    fill(set(), page_size=50)
    assert sleeps == []


def test_find_missing_passes_the_delay_setting(monkeypatch, sleeps):
    setup_box(monkeypatch, owned=Everything())
    serve(monkeypatch, three_pages)
    find({"stashbox_request_delay": 1.5}, page_size=5)
    assert sleeps == [1.5, 1.5]


@pytest.mark.parametrize("value,expected", [
    (2, 2.0), ("2", 2.0), (0, 0.0), (-1, 0.0), (None, 0.5), ("", 0.5), ("inf", 0.5), ("nan", 0.5), ("x", 0.5),
])
def test_get_config_request_delay(value, expected):
    assert stashbox_api.get_config({"stashbox_request_delay": value}, "request_delay") == expected


def test_get_config_budget_default():
    assert stashbox_api.get_config({}, "retry_budget") == 60.0


# ---- 429 and Retry-After -----------------------------------------------------------

def test_retry_after_seconds_is_honoured(monkeypatch, sleeps):
    serve_http(monkeypatch, [http_error(429, retry_after=5), {"data": {"x": 1}}])
    assert stashbox_api.graphql_request_with_retry(EP, "q") == {"x": 1}
    assert sleeps == [5.0]


def test_retry_after_over_the_budget_raises_without_sleeping(monkeypatch, sleeps):
    serve_http(monkeypatch, [http_error(429, retry_after=120)])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        stashbox_api.graphql_request_with_retry(EP, "q")
    assert ei.value.is_rate_limited
    assert ei.value.status_code == 429
    assert ei.value.retry_after == 120
    assert sleeps == []


def test_retry_after_budget_is_per_request_and_cumulative(monkeypatch, sleeps):
    serve_http(monkeypatch, [http_error(429, retry_after=40), http_error(429, retry_after=40)])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        stashbox_api.graphql_request_with_retry(EP, "q")
    assert ei.value.is_rate_limited
    assert sleeps == [40.0]


def test_retry_after_http_date(monkeypatch, sleeps):
    when = email.utils.formatdate(time.time() + 30, usegmt=True)
    serve_http(monkeypatch, [http_error(429, retry_after=when), {"data": {"x": 1}}])
    stashbox_api.graphql_request_with_retry(EP, "q")
    assert len(sleeps) == 1 and 27 <= sleeps[0] <= 30


def test_429_without_retry_after_pauses_within_the_budget(monkeypatch, sleeps):
    serve_http(monkeypatch, [http_error(429), {"data": {"x": 1}}])
    stashbox_api.graphql_request_with_retry(EP, "q")
    pause = stashbox_api.DEFAULT_CONFIG["rate_limit_pause"]
    assert sleeps == [pause]
    assert pause <= stashbox_api.DEFAULT_CONFIG["retry_budget"]


def test_5xx_is_retried_with_backoff(monkeypatch, sleeps):
    serve_http(monkeypatch, [http_error(502), http_error(502), {"data": {"x": 1}}])
    assert stashbox_api.graphql_request_with_retry(EP, "q") == {"x": 1}
    assert sleeps == [1.0, 2.0]


def test_rate_limit_mid_fill_is_partial(monkeypatch, sleeps):
    setup_box(monkeypatch, owned=ids("a", 97))
    serve_http(monkeypatch, [gql_page(ids("a", 100), 300), http_error(429, retry_after=120)])
    res = find(page_size=10)
    assert res["partial"] is True
    assert res["rate_limited"] is True
    assert res["retry_after"] == 120
    assert "rate" in res["error"].lower() and "StashDB" in res["error"]
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["a97", "a98", "a99"]
    assert sleeps == [0.5]  # the page delay, never the 120s


# ---- auth errors -------------------------------------------------------------------

@pytest.mark.parametrize("code", [401, 403])
def test_http_auth_error_is_flagged_and_not_retried(monkeypatch, sleeps, code):
    seen = serve_http(monkeypatch, [http_error(code)])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        stashbox_api.graphql_request_with_retry(EP, "q")
    assert ei.value.is_auth_error
    assert ei.value.status_code == code
    assert len(seen) == 1 and sleeps == []


@pytest.mark.parametrize("message", ["not authorized", "Unauthorized", "forbidden"])
def test_graphql_auth_error_with_null_data_is_flagged(monkeypatch, message):
    serve_http(monkeypatch, [{"errors": [{"message": message}], "data": None}])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        stashbox_api.graphql_request_with_retry(EP, "q")
    assert ei.value.is_auth_error
    assert message in str(ei.value)


def test_graphql_error_with_null_data_raises(monkeypatch):
    serve_http(monkeypatch, [{"errors": [{"message": "boom"}], "data": None}])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        stashbox_api.graphql_request_with_retry(EP, "q")
    assert not ei.value.is_auth_error
    assert "boom" in str(ei.value)


def test_graphql_errors_alongside_data_return_the_data(monkeypatch):
    serve_http(monkeypatch, [{"errors": [{"message": "minor"}], "data": {"queryScenes": {"count": 0, "scenes": []}}}])
    assert stashbox_api.graphql_request_with_retry(EP, "q") == {"queryScenes": {"count": 0, "scenes": []}}


def test_non_json_response_raises(monkeypatch):
    serve_http(monkeypatch, [b"<html>proxy error</html>"])
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.graphql_request_with_retry(EP, "q")


def test_auth_error_response_names_the_box(monkeypatch):
    setup_box(monkeypatch, endpoint=FANSDB, name="FansDB")
    serve(monkeypatch, {1: api_error("HTTP 401: Unauthorized", 401)})
    res = find(page_size=5)
    assert res["auth_error"] is True
    assert "FansDB" in res["error"] and "API key" in res["error"]
    assert res["missing_scenes"] == []


def test_graphql_auth_error_end_to_end(monkeypatch):
    setup_box(monkeypatch)
    serve_http(monkeypatch, [{"errors": [{"message": "not authorized"}], "data": None}])
    res = find(page_size=5)
    assert res["auth_error"] is True and "StashDB" in res["error"]


# ---- queryScenes: null -------------------------------------------------------------

def test_query_scenes_null_is_an_error(monkeypatch):
    serve_http(monkeypatch, [{"data": {"queryScenes": None}}])
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.query_scenes_page(EP, "k", "performer", PSID)


def test_browse_query_scenes_null_is_an_error(monkeypatch):
    serve_http(monkeypatch, [{"data": {"queryScenes": None}}])
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.query_scenes_browse(EP, "k")


def test_query_scenes_page_raises_instead_of_returning_none(monkeypatch):
    serve_http(monkeypatch, [http_error(500)] * 4)
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.query_scenes_page(EP, "k", "performer", PSID)


def test_query_scenes_null_end_to_end(monkeypatch):
    setup_box(monkeypatch)
    serve_http(monkeypatch, [{"data": {"queryScenes": None}}])
    res = find(page_size=5)
    assert res["error"] and res["missing_scenes"] == []


# ---- cursor validation -------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    cursor(entity_stash_id="someone-else"),
    cursor(entity_type="studio"),
    cursor(endpoint=FANSDB),
    cursor(stashdb_page="2"),
    cursor(stashdb_page=True),
    cursor(stashdb_page=0),
    cursor(offset=1.5),
    cursor(offset=-1),
    cursor(offset=None),
    ms.encode_cursor(["not", "a", "dict"]),
    "not-a-cursor!!",
], ids=["other-entity", "other-entity-type", "other-endpoint", "page-string", "page-bool", "page-zero",
        "offset-float", "offset-negative", "offset-null", "not-a-dict", "undecodable"])
def test_bad_cursor_is_a_clear_error(monkeypatch, bad):
    setup_box(monkeypatch)
    calls = serve(monkeypatch, lambda p: pg(ids("s", 5), 5, False))
    res = find(page_size=5, cursor=bad)
    assert "cursor" in res["error"].lower()
    assert calls == []


def test_valid_cursor_resumes_at_its_page_and_offset(monkeypatch):
    setup_box(monkeypatch)
    calls = serve(monkeypatch, {3: pg(ids("s", 10), 1000, True)})
    res = find(page_size=2, cursor=cursor(stashdb_page=3, offset=7))
    assert calls == [3]
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s7", "s8"]


# ---- missing_count_estimate -----------------------------------------------------------

def test_estimate_is_clamped_at_zero(monkeypatch):
    setup_box(monkeypatch, total_local=500)
    serve(monkeypatch, {1: pg(ids("s", 100), 150, True)})
    res = find(page_size=5)
    assert res["missing_count_estimate"] == 0


def test_estimate_when_known(monkeypatch):
    setup_box(monkeypatch, total_local=20)
    serve(monkeypatch, {1: pg(ids("s", 100), 150, True)})
    assert find(page_size=5)["missing_count_estimate"] == 130


def test_estimate_on_partial_is_clamped(monkeypatch):
    setup_box(monkeypatch, owned=Everything(), total_local=999)
    serve(monkeypatch, {1: pg(ids("s", 100), 300, True), 2: api_error()})
    res = find(page_size=5)
    assert res["partial"] is True
    assert res["missing_count_estimate"] == 0


# ---- browse -------------------------------------------------------------------------

def setup_browse(monkeypatch, endpoint=EP, name="StashDB", owned=()):
    monkeypatch.setattr(ms, "get_stashbox_config",
                        lambda: [{"endpoint": endpoint, "api_key": "k", "name": name}])
    monkeypatch.setattr(ms, "get_or_build_cache",
                        lambda ep: owned if isinstance(owned, Everything) else set(owned))


def test_browse_failed_first_page_is_an_error(monkeypatch):
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: api_error()}, target="query_scenes_browse")
    res = browse(page_size=5)
    assert "502" in res["error"] and "StashDB" in res["error"]
    assert res["missing_scenes"] == []
    assert res["partial"] is False
    assert res["has_more"] is False and res["cursor"] is None
    assert res["is_complete"] is False
    assert res["total_on_stashdb"] is None


def test_browse_failed_later_page_is_partial(monkeypatch):
    setup_browse(monkeypatch, owned=ids("a", 98))
    serve(monkeypatch, {1: pg(ids("a", 100), 5000, True), 2: api_error()}, target="query_scenes_browse")
    res = browse(page_size=10)
    assert res["partial"] is True and res["error"]
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["a98", "a99"]
    assert res["has_more"] is True
    state = ms.decode_cursor(res["cursor"])
    assert (state["stashdb_page"], state["offset"]) == (2, 0)

    calls = serve(monkeypatch, {2: pg(ids("b", 100), 5000, True)}, target="query_scenes_browse")
    res2 = browse(page_size=10, cursor=res["cursor"])
    assert calls == [2] and "error" not in res2


def test_browse_page_cap_returns_a_cursor(monkeypatch):
    setup_browse(monkeypatch, owned=Everything())
    calls = serve(monkeypatch, lambda p: pg([f"p{p}-{i}" for i in range(100)], 10 ** 6, True),
                  target="query_scenes_browse")
    res = browse(page_size=10)
    assert len(calls) == ms.MAX_PAGES_PER_REQUEST
    assert res["has_more"] is True
    state = ms.decode_cursor(res["cursor"])
    assert state["stashdb_page"] == ms.MAX_PAGES_PER_REQUEST + 1


def test_browse_fill_at_last_page_emits_no_cursor(monkeypatch):
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: pg(ids("s", 5), 5, False)}, target="query_scenes_browse")
    res = browse(page_size=5)
    assert res["cursor"] is None and res["has_more"] is False and res["is_complete"] is True


def test_browse_auth_error_names_the_box(monkeypatch):
    setup_browse(monkeypatch, endpoint=FANSDB, name="FansDB")
    serve(monkeypatch, {1: api_error("HTTP 403: Forbidden", 403)}, target="query_scenes_browse")
    res = browse(page_size=5)
    assert res["auth_error"] is True and "FansDB" in res["error"]


def test_browse_request_delay(monkeypatch, sleeps):
    setup_browse(monkeypatch, owned=Everything())
    serve(monkeypatch, three_pages, target="query_scenes_browse")
    browse({"stashbox_request_delay": 1}, page_size=5)
    assert sleeps == [1.0, 1.0]


def browse_cursor(**over):
    state = {"stashdb_page": 2, "offset": 0, "sort": "DATE", "direction": "DESC", "endpoint": EP}
    state.update(over)
    return ms.encode_cursor(state)


@pytest.mark.parametrize("bad", [
    browse_cursor(endpoint=FANSDB),
    browse_cursor(stashdb_page="2"),
    browse_cursor(offset=2.5),
    browse_cursor(sort="TITLE"),
    "garbage!!",
], ids=["other-endpoint", "page-string", "offset-float", "other-sort", "undecodable"])
def test_browse_bad_cursor_is_a_clear_error(monkeypatch, bad):
    setup_browse(monkeypatch)
    calls = serve(monkeypatch, lambda p: pg(ids("s", 5), 5, False), target="query_scenes_browse")
    res = browse(page_size=5, cursor=bad)
    assert "cursor" in res["error"].lower()
    assert calls == []


def test_browse_cursor_carries_the_endpoint(monkeypatch):
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: pg(ids("s", 100), 5000, True)}, target="query_scenes_browse")
    res = browse(page_size=5)
    assert ms.decode_cursor(res["cursor"])["endpoint"] == EP


# ---- ThePornDB (its functions return None on errors) ------------------------------------

def test_tpdb_failed_first_page_is_an_error(monkeypatch):
    setup_box(monkeypatch, endpoint=TPDB, name="ThePornDB")
    serve(monkeypatch, {1: None}, module=theporndb_api)
    res = find(page_size=5)
    assert "ThePornDB" in res["error"]
    assert res["missing_scenes"] == [] and res["partial"] is False
    assert "missing_count_estimate" not in res


def test_tpdb_failed_later_page_is_partial(monkeypatch):
    setup_box(monkeypatch, endpoint=TPDB, name="ThePornDB", owned=ids("a", 99))
    serve(monkeypatch, {1: pg(ids("a", 100), 300, True), 2: None}, module=theporndb_api)
    res = find(page_size=5)
    assert res["partial"] is True
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["a99"]
    assert ms.decode_cursor(res["cursor"])["stashdb_page"] == 2


def test_tpdb_browse_failure_is_an_error(monkeypatch):
    setup_browse(monkeypatch, endpoint=TPDB, name="ThePornDB")
    serve(monkeypatch, {1: None}, target="query_scenes_browse", module=theporndb_api)
    res = browse(page_size=5)
    assert "ThePornDB" in res["error"] and res["missing_scenes"] == []


# ---- main() delivers the failure details -------------------------------------------------

def test_main_sends_failure_responses_as_output(monkeypatch, tmp_path, capsys):
    partial = {"error": "page 2 failed", "partial": True, "auth_error": False,
               "missing_scenes": [{"stash_id": "s1"}], "cursor": "c", "has_more": True}
    monkeypatch.setattr(ms, "find_missing_scenes_paginated", lambda *a, **k: partial)
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "find_missing", "entity_id": "1"})
    assert out == {"output": partial}


def test_main_sends_browse_failures_as_output(monkeypatch, tmp_path, capsys):
    failed = {"error": "StashDB rejected the API key", "partial": False, "auth_error": True,
              "missing_scenes": []}
    monkeypatch.setattr(ms, "browse_stashdb", lambda **k: failed)
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "browse_stashdb"})
    assert out == {"output": failed}


def test_main_keeps_plain_errors_top_level(monkeypatch, tmp_path, capsys):
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "find_missing"})
    assert out == {"error": "entity_id is required"}
