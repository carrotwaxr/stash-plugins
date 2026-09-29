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


@pytest.mark.parametrize("resp", [{"data": None, "meta": None}, {"data": None}, {}, {"data": [], "meta": None},
                                  {"data": [], "meta": "x"}, {"data": "oops", "meta": {}}])
def test_null_data_or_meta_is_an_empty_page(monkeypatch, resp):
    patch_rest(monkeypatch, resp)
    res = fetch()
    assert res["scenes"] == [] and res["has_more"] is False and res["count"] == 0


@pytest.mark.parametrize("resp", [["a"], "text", 5])
def test_non_dict_response_is_a_failed_page(monkeypatch, resp):
    patch_rest(monkeypatch, resp)
    assert fetch() is None


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


def test_rest_request_other_errors_still_return_none(monkeypatch):
    def boom(*a, **k):
        raise http_error(404)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert tp.rest_request("k", "/scenes") is None


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
        return None if params.get("performer") == "p2" else {"data": [], "meta": {}}
    monkeypatch.setattr(tp, "rest_request", fake)
    assert tp.query_scenes_browse("k", performer_ids=["p1", "p2"]) is None


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
