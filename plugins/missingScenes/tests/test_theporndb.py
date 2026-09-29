"""ThePornDB REST responses vary in shape; none of the variants may raise."""

import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import missing_scenes as ms
import stashbox_api
import theporndb_api as tp
from tests.test_fetch_errors import (TPDB, browse, ids, serve, setup_browse)


def rest(**over):
    scene = {"id": "s1", "title": "T", "description": "d", "date": "2024-01-01",
             "posters": [{"id": "p", "url": "http://x/p.jpg", "width": 10, "height": 5}],
             "performers": [{"as": "A", "parent": {"id": "pf1", "name": "Ann", "gender": "FEMALE"}}],
             "site": {"uuid": "st1", "name": "Site"},
             "tags": [{"uuid": "t1", "name": "Tag"}], "url": "http://x/s"}
    scene.update(over)
    return scene


def test_baseline_scene_transforms():
    s = tp.transform_scene(rest())
    assert s["studio"] == {"id": "st1", "name": "Site"}
    assert s["performers"][0]["performer"]["name"] == "Ann"
    assert s["images"][0]["url"] == "http://x/p.jpg"


@pytest.mark.parametrize("over,check", [
    ({"posters": "http://x/a.jpg"}, lambda s: s["images"][0]["url"] == "http://x/a.jpg"),
    ({"posters": ["http://x/a.jpg", "http://x/b.jpg"]}, lambda s: s["images"][0]["url"] == "http://x/a.jpg"),
    ({"posters": [{"url": "http://x/a.jpg", "width": None, "height": None}]},
     lambda s: s["images"][0]["width"] == 0 and s["images"][0]["height"] == 0),
    ({"posters": None, "poster": "http://x/one.jpg"}, lambda s: s["images"][0]["url"] == "http://x/one.jpg"),
    ({"posters": [None, 5, {"url": "http://x/a.jpg"}]}, lambda s: len(s["images"]) == 1),
    ({"performers": None}, lambda s: s["performers"] == []),
    ({"performers": ["Some Name"]}, lambda s: s["performers"][0]["performer"]["name"] == "Some Name"),
    ({"performers": [None, {"parent": "Named"}]}, lambda s: s["performers"][-1]["performer"]["name"] == "Named"),
    ({"performers": [{"as": "X", "parent": None, "id": "i", "name": "N"}]},
     lambda s: s["performers"][0]["performer"]["id"] == "i"),
    ({"site": "Some Site"}, lambda s: s["studio"] == {"id": None, "name": "Some Site"}),
    ({"site": None}, lambda s: s["studio"] is None),
    ({"tags": ["Anal", "Big"]}, lambda s: [t["name"] for t in s["tags"]] == ["Anal", "Big"]),
    ({"tags": None}, lambda s: s["tags"] == []),
    ({"tags": [None, {"uuid": "u", "name": "N"}]}, lambda s: len(s["tags"]) == 1),
    ({"directors": [{"id": 1, "name": "Dee Rector"}]}, lambda s: s["director"] == "Dee Rector"),
    ({"directors": ["Plain"]}, lambda s: s["director"] == "Plain"),
    ({"directors": "Single"}, lambda s: s["director"] == "Single"),
    ({"directors": None}, lambda s: s["director"] is None),
    ({"directors": [{"id": 1}]}, lambda s: s["director"] is None),
], ids=lambda v: None if callable(v) else str(v)[:40])
def test_shape_variants_transform_and_format(over, check):
    scene = tp.transform_scene(rest(**over))
    assert check(scene)
    assert isinstance(ms.format_scene(scene, "s1"), dict)  # never raises
    json.dumps(ms.format_scene(scene, "s1"))
    assert scene["director"] is None or isinstance(scene["director"], str)


def test_format_scene_tolerates_null_width_and_null_performer():
    out = ms.format_scene({"images": [{"url": "u", "width": None, "height": 3},
                                      {"url": "v", "width": 9, "height": None}],
                           "performers": [{"performer": None, "as": None}]}, "x")
    assert out["thumbnail"] == "v"
    assert out["performers"][0]["id"] is None


# ---- _fetch_scenes ----

def patch_rest(monkeypatch, response):
    monkeypatch.setattr(tp, "rest_request", lambda *a, **k: response)


def fetch():
    return tp._fetch_scenes("k", "/scenes", {}, 1, 100, None, "test")


@pytest.mark.parametrize("resp", [{"data": [], "meta": None}, {"data": [], "meta": "x"}, {"data": []}])
def test_empty_data_with_odd_meta_is_an_empty_page(monkeypatch, resp):
    patch_rest(monkeypatch, resp)
    res = fetch()
    assert res["scenes"] == [] and res["has_more"] is False and res["count"] == 0


@pytest.mark.parametrize("resp", [{"data": None, "meta": None}, {"data": None}, {}, {"data": "oops", "meta": {}},
                                  {"data": {"id": "x"}}])
def test_data_that_is_not_a_list_is_a_failed_page(monkeypatch, resp):
    # Not "no scenes": the reply says nothing about the scenes, so it must not read as all found
    patch_rest(monkeypatch, resp)
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        fetch()
    assert "unexpected" in str(ei.value).lower()


@pytest.mark.parametrize("resp", [["a"], "text", 5, None])
def test_non_dict_response_is_a_failed_page(monkeypatch, resp):
    patch_rest(monkeypatch, resp)
    with pytest.raises(stashbox_api.StashBoxAPIError):
        fetch()


def test_meta_with_bad_numbers(monkeypatch):
    patch_rest(monkeypatch, {"data": [], "meta": {"total": None, "last_page": None}})
    res = fetch()
    assert res["count"] == 0 and res["has_more"] is False


def test_one_bad_scene_skips_only_that_scene(monkeypatch):
    warnings = []
    monkeypatch.setattr(tp.log, "LogWarning", warnings.append)
    monkeypatch.setattr(tp, "transform_scene",
                        lambda s: (_ for _ in ()).throw(ValueError("boom")) if s.get("id") == "bad"
                        else {"id": s.get("id")})
    patch_rest(monkeypatch, {"data": [{"id": "a"}, {"id": "bad"}, {"id": "c"}, "junk"],
                             "meta": {"total": 4, "last_page": 1}})
    res = fetch()
    assert [s["id"] for s in res["scenes"]] == ["a", "c"]
    assert any("bad" in w for w in warnings)
    assert len(warnings) == 2  # the bad scene, and the non-object entry


def test_page_with_weird_scenes_survives(monkeypatch):
    patch_rest(monkeypatch, {"data": [rest(id="ok"), None, "x", rest(id="ok2", performers=None, posters="u")],
                             "meta": {"total": 4, "last_page": 1}})
    assert [s["id"] for s in fetch()["scenes"]] == ["ok", "ok2"]


# ---- rest_request errors ----

def http_error(code):
    return urllib.error.HTTPError("https://api.theporndb.net/scenes", code, "msg", {}, io.BytesIO(b""))


@pytest.mark.parametrize("code,auth,limited", [(401, True, False), (403, True, False), (429, False, True)])
def test_rest_request_raises_typed_errors(monkeypatch, code, auth, limited):
    def boom(*a, **k):
        raise http_error(code)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes")
    assert ei.value.status_code == code
    assert ei.value.is_auth_error is auth and ei.value.is_rate_limited is limited


@pytest.mark.parametrize("code", [400, 404, 500, 502])
def test_rest_request_other_errors_raise_with_the_status(monkeypatch, code):
    def boom(*a, **k):
        raise http_error(code)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes")
    assert ei.value.status_code == code and str(code) in str(ei.value)
    assert not ei.value.is_auth_error


@pytest.mark.parametrize("exc", [urllib.error.URLError("refused"), TimeoutError("timed out")])
def test_rest_request_connection_errors_raise(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(stashbox_api.StashBoxAPIError):
        tp.rest_request("k", "/scenes")


def test_rest_request_non_json_raises(monkeypatch):
    from tests.test_whisparr import FakeResp
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp(b"<html>Cloudflare</html>"))
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes")
    assert "json" in str(ei.value).lower()


def test_tpdb_auth_failure_reaches_the_response(monkeypatch):
    setup_browse(monkeypatch, endpoint=TPDB, name="ThePornDB")
    def boom(*a, **k):
        raise http_error(401)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    res = browse(page_size=5)
    assert res["auth_error"] is True and "ThePornDB" in res["error"]


# ---- browse with several favorites ----

class Api:
    """Fake rest_request: records params; per-performer/site pages of scenes."""

    def __init__(self, monkeypatch, by_key, sites=None):
        self.calls = []
        self.by_key = by_key
        self.sites = sites or {}
        monkeypatch.setattr(tp, "rest_request", self)
        tp._site_id_cache.clear()

    def __call__(self, api_key, path, params=None, **kw):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path.startswith("/sites/"):
            return {"data": {"id": self.sites[path.split("/")[2]]}}
        key = (params.get("performer"), params.get("site_id"))
        return {"data": self.by_key.get(key, []), "meta": {"total": len(self.by_key.get(key, [])), "last_page": 1}}


def sc(i, date):
    return rest(id=i, date=date)


def test_browse_with_several_performers_merges_and_dedupes(monkeypatch):
    api = Api(monkeypatch, {("p1", None): [sc("a", "2024-03-01"), sc("b", "2024-01-01")],
                            ("p2", None): [sc("c", "2024-02-01"), sc("a", "2024-03-01")]})
    res = tp.query_scenes_browse("k", performer_ids={"p2", "p1"}, sort="DATE", direction="DESC")
    assert [s["id"] for s in res["scenes"]] == ["a", "c", "b"]
    assert [c[1]["performer"] for c in api.calls] == ["p1", "p2"]  # stable order
    assert res["favorites_limited"] is False
    res = tp.query_scenes_browse("k", performer_ids={"p2", "p1"}, sort="DATE", direction="ASC")
    assert [s["id"] for s in res["scenes"]] == ["b", "c", "a"]


def test_browse_with_several_studios(monkeypatch):
    api = Api(monkeypatch, {(None, 1): [sc("a", "2024-01-01")], (None, 2): [sc("b", "2024-02-01")]},
              sites={"u1": 1, "u2": 2})
    res = tp.query_scenes_browse("k", studio_ids=["u2", "u1"])
    assert [s["id"] for s in res["scenes"]] == ["b", "a"]


def test_browse_reports_when_favorites_were_capped(monkeypatch):
    many = [f"p{i:02d}" for i in range(tp.MAX_BROWSE_QUERIES + 5)]
    api = Api(monkeypatch, {})
    res = tp.query_scenes_browse("k", performer_ids=set(many))
    assert res["favorites_limited"] is True
    assert len([c for c in api.calls if c[0] == "/scenes"]) == tp.MAX_BROWSE_QUERIES
    assert api.calls[0][1]["performer"] == "p00"


def test_browse_failure_of_one_query_is_a_failed_page(monkeypatch):
    def fake(api_key, path, params=None, **kw):
        if params.get("performer") == "p2":
            raise stashbox_api.StashBoxAPIError("HTTP 502: Bad Gateway", status_code=502)
        return {"data": [], "meta": {}}
    monkeypatch.setattr(tp, "rest_request", fake)
    with pytest.raises(stashbox_api.StashBoxAPIError):
        tp.query_scenes_browse("k", performer_ids=["p1", "p2"])


def test_browse_without_filters_is_one_query(monkeypatch):
    api = Api(monkeypatch, {(None, None): [sc("a", "2024-01-01")]})
    res = tp.query_scenes_browse("k")
    assert len(api.calls) == 1 and res["favorites_limited"] is False


# ---- responses ----

def test_tpdb_browse_response_flags(monkeypatch):
    setup_browse(monkeypatch, endpoint=TPDB, name="ThePornDB")
    monkeypatch.setattr(ms, "get_favorite_stash_ids_limited", lambda *a, **k: {"p1", "p2"})
    serve(monkeypatch, {1: {"scenes": [{"id": "n1"}], "count": 1, "has_more": False, "favorites_limited": True}},
          target="query_scenes_browse", module=tp)
    res = browse({"excludedTags": "t1,t2"}, page_size=5, filter_favorite_performers=True)
    assert res["excluded_tags_applied"] is False
    assert res["favorites_limited"] is True


def test_stashbox_browse_still_applies_excluded_tags(monkeypatch):
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: {"scenes": [{"id": "n1"}], "count": 1, "has_more": False}},
          target="query_scenes_browse")
    res = browse({"excludedTags": "t1"}, page_size=5)
    assert res["excluded_tags_applied"] is True
    assert "favorites_limited" not in res


# ---- failures are errors, never "all found" ----------------------------------------------

STUDIO = "0a1b2c3d-studio-uuid"


def tpdb_http(monkeypatch, routes):
    """Fake api.theporndb.net at the urlopen level. routes: path prefix -> body, HTTP code
    (an error) or callable(params). Returns the paths requested."""
    from tests.test_whisparr import FakeResp
    seen = []

    def fake_urlopen(req, *a, **k):
        parts = urllib.parse.urlsplit(req.full_url)
        seen.append(parts.path)
        for prefix, reply in routes.items():
            if parts.path.startswith(prefix):
                if callable(reply):
                    reply = reply(dict(urllib.parse.parse_qsl(parts.query)))
                if isinstance(reply, int):
                    raise http_error(reply)
                return FakeResp(reply)
        raise http_error(404)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    tp._site_id_cache.clear()
    return seen


def studio_fill(**kw):
    return ms.fetch_until_full(TPDB, "k", "studio", STUDIO, set(), page_size=5, **kw)


def test_studio_site_lookup_failure_is_an_error_not_all_found(monkeypatch):
    # A 502 on /sites/<uuid> used to come back as {scenes: [], is_complete: True}
    tpdb_http(monkeypatch, {f"/sites/{STUDIO}": 502, "/scenes": {"data": [rest(id="n1")], "meta": {"last_page": 1}}})
    res = studio_fill()
    assert res["is_complete"] is False and res["scenes"] == []
    assert "502" in res["error"]
    assert res["partial"] is False


def test_studio_not_on_tpdb_is_an_explicit_error(monkeypatch):
    seen = tpdb_http(monkeypatch, {f"/sites/{STUDIO}": 404})
    res = studio_fill()
    assert res["is_complete"] is False and res["scenes"] == []
    assert "isn't on ThePornDB" in res["error"]
    assert "/scenes" not in seen


def test_studio_lookup_with_an_odd_reply_is_an_error(monkeypatch):
    tpdb_http(monkeypatch, {f"/sites/{STUDIO}": {"data": None}})
    res = studio_fill()
    assert res["is_complete"] is False and "error" in res


def test_studio_resolves_and_lists(monkeypatch):
    tpdb_http(monkeypatch, {f"/sites/{STUDIO}": {"data": {"id": 77}},
                            "/scenes": lambda q: {"data": [rest(id="n1")] if q.get("site_id") == "77" else [],
                                                  "meta": {"total": 1, "last_page": 1}}})
    res = studio_fill()
    assert [s["id"] for s in res["scenes"]] == ["n1"] and res["is_complete"] is True
    assert "error" not in res


def test_tag_view_on_tpdb_is_an_explicit_error(monkeypatch):
    seen = tpdb_http(monkeypatch, {})
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.query_scenes_page("k", "tag", "tag-uuid")
    assert "can't list scenes by tag" in str(ei.value)

    monkeypatch.setattr(ms, "get_stashbox_config", lambda: [{"endpoint": TPDB, "api_key": "k", "name": "ThePornDB"}])
    monkeypatch.setattr(ms, "get_local_tag",
                        lambda tid: {"name": "Anal", "stash_ids": [{"endpoint": TPDB, "stash_id": "tag-uuid"}]})
    monkeypatch.setattr(ms, "get_or_build_cache", lambda ep: set())
    monkeypatch.setattr(ms, "count_local_scenes_for_entity", lambda *a: 0)
    monkeypatch.setattr(ms, "get_or_build_cache", lambda ep: pytest.fail("no index build for a tag on ThePornDB"))
    res = ms.find_missing_scenes_paginated("tag", "1", {})
    assert res["error"] == tp.TAG_VIEWS_UNSUPPORTED
    assert not res.get("is_complete") and not res.get("missing_scenes")
    assert seen == []


def test_browse_no_favorite_studio_on_tpdb_is_an_error(monkeypatch):
    seen = tpdb_http(monkeypatch, {"/sites/": 404})
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.query_scenes_browse("k", studio_ids=["u1", "u2"])
    assert "ThePornDB" in str(ei.value)
    assert "/scenes" not in seen

    setup_browse(monkeypatch, endpoint=TPDB, name="ThePornDB")
    monkeypatch.setattr(ms, "get_favorite_stash_ids_limited", lambda *a, **k: ["u1", "u2"])
    res = browse(page_size=5, filter_favorite_studios=True)
    assert "error" in res and res["missing_scenes"] == [] and res["is_complete"] is False


def test_browse_favorite_studio_lookup_failure_is_a_failed_page(monkeypatch):
    tpdb_http(monkeypatch, {"/sites/u1": {"data": {"id": 1}}, "/sites/u2": 502,
                            "/scenes": {"data": [rest(id="n1")], "meta": {"total": 1, "last_page": 1}}})
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.query_scenes_browse("k", studio_ids=["u1", "u2"])
    assert ei.value.status_code == 502


def test_browse_skips_favorite_studios_that_are_not_on_tpdb(monkeypatch):
    tpdb_http(monkeypatch, {"/sites/u1": {"data": {"id": 1}}, "/sites/u2": 404,
                            "/scenes": lambda q: {"data": [rest(id="n" + q.get("site_id", "?"))],
                                                  "meta": {"total": 1, "last_page": 1}}})
    res = tp.query_scenes_browse("k", studio_ids=["u1", "u2"])
    assert [s["id"] for s in res["scenes"]] == ["n1"]


# ---- which favorites ThePornDB browse uses ----------------------------------------------

def test_limited_favorites_keep_the_engagement_order(monkeypatch):
    # Stash answers most engaged first (last_o_at / scenes_count DESC); the order is the point
    order = ["p-zed", "p-alpha", "p-mid", "p-beta"]
    monkeypatch.setattr(ms, "stash_graphql", lambda q, v=None: {"findPerformers": {"count": len(order), "performers": [
        {"id": str(i), "stash_ids": [{"endpoint": TPDB, "stash_id": sid}]} for i, sid in enumerate(order)]}})
    assert ms.get_favorite_stash_ids_limited("performer", TPDB, limit=3) == order[:3]


def test_tpdb_browse_searches_the_most_engaged_favorites_first(monkeypatch):
    engaged = [f"p{i:02d}" for i in range(tp.MAX_BROWSE_QUERIES + 5)][::-1]  # p14, p13, ...
    api = Api(monkeypatch, {})
    res = tp.query_scenes_browse("k", performer_ids=engaged)
    assert res["favorites_limited"] is True
    assert [c[1]["performer"] for c in api.calls] == engaged[:tp.MAX_BROWSE_QUERIES]


def test_browse_sends_favorites_in_order_and_filters_by_membership(monkeypatch):
    setup_browse(monkeypatch, endpoint=TPDB, name="ThePornDB")
    monkeypatch.setattr(ms, "get_favorite_stash_ids_limited", lambda *a, **k: ["p-zed", "p-alpha"])
    got = []

    def fake(api_key, page=1, **kw):
        got.append(kw["performer_ids"])
        scene = lambda i, p: {"id": i, "performers": [{"performer": {"id": p}}]}
        return {"scenes": [scene("n1", "p-alpha"), scene("n2", "other")], "count": 2, "has_more": False,
                "favorites_limited": True}
    monkeypatch.setattr(tp, "query_scenes_browse", fake)
    res = browse(page_size=5, filter_favorite_performers=True)
    assert got == [["p-zed", "p-alpha"]]
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["n1"]
    assert res["favorites_limited"] is True
    assert res["favorites_query_limit"] == tp.MAX_BROWSE_QUERIES


# ---- rest_request: Retry-After and the retry budget, as for the stash-boxes -----------------

@pytest.fixture
def slept(monkeypatch):
    got = []
    monkeypatch.setattr(tp.time, "sleep", lambda s: got.append(s))
    return got


def retry_error(code, retry_after=None):
    from tests.test_fetch_errors import http_error as with_headers
    return with_headers(code, retry_after=retry_after)


def queue_http(monkeypatch, replies):
    from tests.test_fetch_errors import serve_http
    return serve_http(monkeypatch, list(replies))


def test_tpdb_retry_after_is_honoured(monkeypatch, slept):
    queue_http(monkeypatch, [retry_error(429, retry_after=5), {"data": []}])
    assert tp.rest_request("k", "/scenes") == {"data": []}
    assert slept == [5.0]


def test_tpdb_retry_after_over_the_budget_raises_without_sleeping(monkeypatch, slept):
    queue_http(monkeypatch, [retry_error(429, retry_after=120)])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes")
    assert ei.value.is_rate_limited and ei.value.retry_after == 120
    assert slept == []


def test_tpdb_retry_budget_is_cumulative(monkeypatch, slept):
    queue_http(monkeypatch, [retry_error(429, retry_after=40), retry_error(429, retry_after=40)])
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes")
    assert ei.value.is_rate_limited
    assert slept == [40.0]


def test_tpdb_429_without_retry_after_pauses_within_the_budget(monkeypatch, slept):
    queue_http(monkeypatch, [retry_error(429), {"data": []}])
    tp.rest_request("k", "/scenes")
    assert slept == [stashbox_api.DEFAULT_CONFIG["rate_limit_pause"]]


def test_tpdb_backoff_stops_at_the_budget(monkeypatch, slept):
    # Backoff waits count against the same budget: the 1s wait fits in 1.5s, the next 2s doesn't
    queue_http(monkeypatch, [retry_error(502)] * 4)
    with pytest.raises(stashbox_api.StashBoxAPIError) as ei:
        tp.rest_request("k", "/scenes", plugin_settings={"stashbox_retry_budget": 1.5})
    assert ei.value.status_code == 502
    assert slept == [1.0]
