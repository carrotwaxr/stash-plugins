"""Tests for the Whisparr helpers: URL normalization, typed errors, id-checked lookup."""

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import missing_scenes as ms

KEY = "SECRET-KEY-123"
URL = "http://h:6969"
SID = "11111111-1111-1111-1111-111111111111"


class FakeResp:
    def __init__(self, body):
        self._b = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b"", reason="err"):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return urllib.error.HTTPError("http://h/api/v3/x", code, reason, {}, io.BytesIO(body))


def install(monkeypatch, handler):
    """handler(req) -> body | Exception. Returns the list of requests seen."""
    seen = []

    def fake_urlopen(req, *a, **kw):
        seen.append(req)
        r = handler(req)
        if isinstance(r, Exception):
            raise r
        return FakeResp(r)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


# ---- normalize_whisparr_url -------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("  http://h:6969  ", "http://h:6969"),
    ("h:6969", "http://h:6969"),
    ("http://h:6969/", "http://h:6969"),
    ("http://h:6969/api", "http://h:6969"),
    ("http://h:6969/api/", "http://h:6969"),
    ("http://h:6969/api/v3", "http://h:6969"),
    ("http://h:6969/api/v3/movie?x=1", "http://h:6969"),
    ("http://h:6969/#/settings", "http://h:6969"),
    ("https://h/whisparr", "https://h/whisparr"),
    ("http://h:6969/whisparr/", "http://h:6969/whisparr"),
    ("http://h:6969/whisparr/api/v3", "http://h:6969/whisparr"),
])
def test_normalize(raw, expected):
    assert ms.normalize_whisparr_url(raw) == expected


def test_requests_use_normalized_url(monkeypatch):
    seen = install(monkeypatch, lambda r: [])
    ms.whisparr_request("h:6969/api/v3/", KEY, "movie")
    assert seen[0].full_url == "http://h:6969/api/v3/movie"


# ---- WhisparrError ----------------------------------------------------------

def test_error_is_exception_with_fields(monkeypatch):
    install(monkeypatch, lambda r: http_error(500, b"x" * 1000))
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_request(URL, KEY, "movie")
    err = ei.value
    assert isinstance(err, Exception)
    assert err.status == 500
    assert err.url == "http://h:6969/api/v3/movie"
    assert len(err.body) == 300
    assert KEY not in err.url and KEY not in str(err)


def test_400_exposes_validation_messages(monkeypatch):
    body = [{"propertyName": "RootFolderPath", "errorMessage": "Path is invalid"},
            {"propertyName": "X", "errorMessage": "Second problem"}]
    install(monkeypatch, lambda r: http_error(400, body))
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_request(URL, KEY, "movie", "POST", {"a": 1})
    assert ei.value.status == 400
    assert "Path is invalid" in str(ei.value)
    assert "Second problem" in str(ei.value)


def test_400_message_object(monkeypatch):
    install(monkeypatch, lambda r: http_error(400, {"message": "Bad thing"}))
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_request(URL, KEY, "movie")
    assert "Bad thing" in str(ei.value)


@pytest.mark.parametrize("exc", [urllib.error.URLError("refused"), TimeoutError("timed out")])
def test_connection_errors(monkeypatch, exc):
    install(monkeypatch, lambda r: exc)
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_request(URL, KEY, "movie")
    assert ei.value.status is None
    assert "reach" in str(ei.value).lower()
    assert KEY not in str(ei.value)


def test_non_json_body_is_an_error(monkeypatch):
    install(monkeypatch, lambda r: b"<html>login</html>")
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_request(URL, KEY, "movie")


def test_api_key_only_in_header(monkeypatch):
    seen = install(monkeypatch, lambda r: [])
    ms.whisparr_request(URL, KEY, "movie")
    assert seen[0].get_header("X-api-key") == KEY
    assert KEY not in seen[0].full_url


# ---- helpers raise ---------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: ms.whisparr_get_scene_by_stash_id(URL, KEY, SID),
    lambda: ms.whisparr_lookup_scene(URL, KEY, SID),
    lambda: ms.whisparr_trigger_search(URL, KEY, 5),
    lambda: ms.whisparr_get_all_scenes(URL, KEY),
    lambda: ms.whisparr_get_queue(URL, KEY),
])
def test_helpers_raise_on_failure(monkeypatch, call):
    install(monkeypatch, lambda r: http_error(500, b"boom"))
    with pytest.raises(ms.WhisparrError):
        call()


def test_empty_results_are_not_errors(monkeypatch):
    install(monkeypatch, lambda r: [])
    assert ms.whisparr_get_scene_by_stash_id(URL, KEY, SID) is None
    assert ms.whisparr_lookup_scene(URL, KEY, SID) is None
    assert ms.whisparr_get_all_scenes(URL, KEY) == []
    install(monkeypatch, lambda r: {"records": []})
    assert ms.whisparr_get_queue(URL, KEY) == []


@pytest.mark.parametrize("reply", [[], b"", {}, {"records": None}, {"records": "x"},
                                   {"records": [{"movieId": 1}, 5]},
                                   {"totalRecords": 3, "records": []}])
def test_queue_raises_on_an_unexpected_reply(monkeypatch, reply):
    install(monkeypatch, lambda r: reply)
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_get_queue(URL, KEY)
    assert "queue" in str(ei.value).lower()


def test_queue_pages_until_total_records(monkeypatch):
    first = [{"movieId": i} for i in range(1000)]

    def handler(req):
        page = int(urllib.parse.parse_qs(req.full_url.split("?", 1)[1])["page"][0])
        return {"page": page, "totalRecords": 1001, "records": first if page == 1 else [{"movieId": 5000}]}
    seen = install(monkeypatch, handler)
    records = ms.whisparr_get_queue(URL, KEY)
    assert len(records) == 1001 and records[-1] == {"movieId": 5000}
    pages = [urllib.parse.parse_qs(r.full_url.split("?", 1)[1])["page"] for r in seen]
    assert pages == [["1"], ["2"]]


def test_queue_without_total_stops_at_a_short_page(monkeypatch):
    seen = install(monkeypatch, lambda r: {"records": [{"movieId": 1}]})
    assert ms.whisparr_get_queue(URL, KEY) == [{"movieId": 1}]
    assert len(seen) == 1


def test_queue_that_never_ends_raises(monkeypatch):
    full = [{"movieId": i} for i in range(1000)]
    install(monkeypatch, lambda r: {"totalRecords": 10 ** 9, "records": full})
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_get_queue(URL, KEY)


def test_get_by_stash_id_checks_the_id(monkeypatch):
    other = {"id": 1, "stashId": "22222222-2222-2222-2222-222222222222"}
    mine = {"id": 2, "stashId": SID}
    install(monkeypatch, lambda r: [other])
    assert ms.whisparr_get_scene_by_stash_id(URL, KEY, SID) is None
    install(monkeypatch, lambda r: [other, mine])
    assert ms.whisparr_get_scene_by_stash_id(URL, KEY, SID) == mine


def test_lookup_prefers_matching_id(monkeypatch):
    wrong = {"movie": {"title": "wrong", "stashId": "other"}}
    right = {"movie": {"title": "right", "stashId": SID}}
    install(monkeypatch, lambda r: [wrong, right])
    assert ms.whisparr_lookup_scene(URL, KEY, SID)["title"] == "right"
    install(monkeypatch, lambda r: [wrong])
    assert ms.whisparr_lookup_scene(URL, KEY, SID) is None


def test_delete_404_is_success_other_errors_raise(monkeypatch):
    install(monkeypatch, lambda r: http_error(404, b""))
    assert ms.whisparr_delete_scene(URL, KEY, 5) is True
    install(monkeypatch, lambda r: http_error(500, b""))
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_delete_scene(URL, KEY, 5)


def test_unmonitor_raises_when_scene_missing(monkeypatch):
    install(monkeypatch, lambda r: None if r.get_method() == "GET" else {})
    with pytest.raises(ms.WhisparrError) as ei:
        ms.whisparr_unmonitor_scene(URL, KEY, 5)
    assert "5" in str(ei.value)
    install(monkeypatch, lambda r: http_error(404, b""))
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_unmonitor_scene(URL, KEY, 5)


# ---- status map ---------------------------------------------------------------

def movies_and_queue(queue_records):
    def handler(req):
        if "/queue" in req.full_url:
            return {"records": queue_records}
        return [{"id": 7, "stashId": SID, "hasFile": False}]
    return handler


def test_status_map_tolerates_nulls(monkeypatch):
    item = {"movieId": 7, "status": None, "errorMessage": None, "size": None,
            "sizeleft": None, "trackedDownloadState": None, "timeleft": None}
    install(monkeypatch, movies_and_queue([item]))
    m = ms.whisparr_get_status_map(URL, KEY)
    assert m[SID]["status"] == "queued"


def test_status_map_error_raises(monkeypatch):
    install(monkeypatch, lambda r: http_error(401, b"Unauthorized"))
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_get_status_map(URL, KEY)


def test_status_map_cached_60s(monkeypatch, tmp_path):
    seen = install(monkeypatch, movies_and_queue([]))
    first = ms.whisparr_get_status_map(URL, KEY)
    n = len(seen)
    assert n == 2
    assert ms.whisparr_get_status_map(URL + "/", KEY) == first  # same normalized url
    assert len(seen) == n
    files = [f for f in os.listdir(tmp_path) if "whisparr" in f]
    assert len(files) == 1
    assert KEY not in open(os.path.join(tmp_path, files[0])).read()
    # expire it
    path = os.path.join(tmp_path, files[0])
    old = time.time() - 61
    os.utime(path, (old, old))
    data = json.load(open(path))
    data["ts"] = old
    json.dump(data, open(path, "w"))
    ms.whisparr_get_status_map(URL, KEY)
    assert len(seen) == 2 * n


def test_errors_are_not_cached(monkeypatch, tmp_path):
    install(monkeypatch, lambda r: http_error(500, b""))
    with pytest.raises(ms.WhisparrError):
        ms.whisparr_get_status_map(URL, KEY)
    assert not [f for f in os.listdir(tmp_path) if "whisparr" in f]


def test_status_helper_returns_error_string(monkeypatch):
    install(monkeypatch, lambda r: http_error(401, b"Unauthorized"))
    m, err = ms._whisparr_status_for_response(URL, KEY)
    assert m == {} and isinstance(err, str) and "401" in err


def test_browse_response_carries_whisparr_error(monkeypatch):
    monkeypatch.setattr(ms, "get_stashbox_config",
                        lambda: [{"endpoint": "https://stashdb.org/graphql", "api_key": "k", "name": "StashDB"}])
    monkeypatch.setattr(ms, "get_or_build_cache", lambda *a, **k: set())
    monkeypatch.setattr(ms.stashbox_api, "query_scenes_browse",
                        lambda *a, **k: {"scenes": [{"id": "s1"}], "count": 1, "page": 1, "has_more": False})
    install(monkeypatch, lambda r: http_error(500, b"boom"))
    res = ms.browse_stashdb(plugin_settings={"whisparrUrl": URL, "whisparrApiKey": KEY}, page_size=50)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1"]
    assert isinstance(res["whisparr_error"], str) and "500" in res["whisparr_error"]
    assert res["missing_scenes"][0]["in_whisparr"] is False


# ---- callers ----------------------------------------------------------------

def test_add_to_whisparr_reports_error(monkeypatch):
    install(monkeypatch, lambda r: http_error(400, [{"errorMessage": "Path is invalid"}]))
    res = ms.add_to_whisparr(SID, "T", {"whisparrUrl": URL, "whisparrApiKey": KEY,
                                        "whisparrRootFolder": "/x"})
    assert res["success"] is False
    assert "Path is invalid" in res["error"]
    assert res["whisparr_error"] == res["error"]


STASHDB = "https://stashdb.org/graphql"


def test_hook_reports_lookup_error(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", lambda *a, **k: {"findScene": {
        "id": "1", "title": "T", "stash_ids": [{"endpoint": STASHDB, "stash_id": SID}]}})
    monkeypatch.setattr(ms, "get_stashbox_config", lambda: [{"endpoint": STASHDB}])
    install(monkeypatch, lambda r: http_error(500, b"boom"))
    ctx = {"id": 1, "type": "Scene.Update.Post", "input": {"id": "1"}, "inputFields": ["id", "stash_ids"]}
    res = ms.handle_scene_update_hook(ctx, {"enableAutoCleanup": True, "whisparrUrl": URL,
                                            "whisparrApiKey": KEY})
    assert res["success"] is False
    assert "500" in res["message"]


def test_cleanup_task_reports_error(monkeypatch):
    monkeypatch.setattr(ms, "get_stashbox_config", lambda: [{"endpoint": STASHDB}])
    install(monkeypatch, lambda r: http_error(500, b"boom"))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is False
    assert "500" in res["message"]


# ---- test_whisparr_connection ------------------------------------------------

GOOD_STATUS = {"appName": "Whisparr", "version": "3.0.0.100"}
ROOTS = [{"path": "/data/a"}, {"path": "/data/b"}]
PROFILES = [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD"}]


def conn_handler(status=GOOD_STATUS, roots=ROOTS, profiles=PROFILES):
    def h(req):
        u = req.full_url
        if u.endswith("/system/status"):
            return status
        if u.endswith("/rootfolder"):
            return roots
        if u.endswith("/qualityprofile"):
            return profiles
        return http_error(404)
    return h


def settings(**kw):
    s = {"whisparrUrl": URL, "whisparrApiKey": KEY}
    s.update(kw)
    return s


def test_connection_ok(monkeypatch):
    seen = install(monkeypatch, conn_handler())
    r = ms.test_whisparr_connection(settings(whisparrUrl=" h:6969/api/v3/ "))
    assert r["ok"] is True and r["problems"] == []
    assert r["app"] == "Whisparr" and r["version"] == "3.0.0.100"
    assert r["url"] == "http://h:6969"
    assert r["root_folders"] == ["/data/a", "/data/b"]
    assert r["quality_profiles"] == [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD"}]
    assert [q.full_url.rsplit("/", 1)[1] for q in seen] == ["status", "rootfolder", "qualityprofile"]
    assert KEY not in json.dumps(r)


@pytest.mark.parametrize("app", ["Radarr", "Sonarr"])
def test_connection_wrong_app(monkeypatch, app):
    seen = install(monkeypatch, conn_handler(status={"appName": app, "version": "5.0"}))
    r = ms.test_whisparr_connection(settings())
    assert not r["ok"] and len(r["problems"]) == 1
    assert "wrong service" in r["problems"][0] and app in r["problems"][0]
    assert len(seen) == 1


def test_connection_html_is_wrong_service(monkeypatch):
    seen = install(monkeypatch, lambda req: b"<!doctype html><html></html>")
    r = ms.test_whisparr_connection(settings())
    assert not r["ok"] and "wrong service" in r["problems"][0]
    assert len(seen) == 1


def test_connection_v2(monkeypatch):
    install(monkeypatch, conn_handler(status={"appName": "Whisparr", "version": "2.2.0.108"}))
    r = ms.test_whisparr_connection(settings())
    assert not r["ok"]
    assert any("Whisparr v3 is required" in p for p in r["problems"])


def test_connection_404(monkeypatch):
    seen = install(monkeypatch, lambda req: http_error(404))
    r = ms.test_whisparr_connection(settings())
    assert r["problems"] == [r["problems"][0]] and "check URL Base" in r["problems"][0]
    assert len(seen) == 1


def test_connection_401(monkeypatch):
    seen = install(monkeypatch, lambda req: http_error(401))
    r = ms.test_whisparr_connection(settings())
    assert len(r["problems"]) == 1 and "API key rejected" in r["problems"][0]
    assert len(seen) == 1


@pytest.mark.parametrize("exc", [urllib.error.URLError("refused"), TimeoutError("timed out")])
def test_connection_unreachable(monkeypatch, exc):
    seen = install(monkeypatch, lambda req: exc)
    r = ms.test_whisparr_connection(settings())
    assert len(r["problems"]) == 1 and "can't reach" in r["problems"][0].lower()
    assert len(seen) == 1
    assert KEY not in json.dumps(r)


def test_connection_bad_root_folder(monkeypatch):
    install(monkeypatch, conn_handler())
    r = ms.test_whisparr_connection(settings(whisparrRootFolder="  /nope  "))
    assert not r["ok"] and len(r["problems"]) == 1
    p = r["problems"][0]
    assert "/nope" in p and "/data/a" in p and "/data/b" in p


def test_connection_good_root_folder(monkeypatch):
    install(monkeypatch, conn_handler())
    assert ms.test_whisparr_connection(settings(whisparrRootFolder=" /data/b "))["ok"]


def test_connection_bad_quality_profile(monkeypatch):
    install(monkeypatch, conn_handler())
    r = ms.test_whisparr_connection(settings(whisparrQualityProfile=9))
    assert not r["ok"] and len(r["problems"]) == 1
    p = r["problems"][0]
    assert "9" in p and "1: Any" in p and "4: HD" in p


def test_connection_good_quality_profile(monkeypatch):
    install(monkeypatch, conn_handler())
    assert ms.test_whisparr_connection(settings(whisparrQualityProfile=4))["ok"]


@pytest.mark.parametrize("s,name", [
    ({"whisparrApiKey": KEY}, "Whisparr URL"),
    ({"whisparrUrl": "  ", "whisparrApiKey": KEY}, "Whisparr URL"),
    ({"whisparrUrl": URL}, "Whisparr API Key"),
])
def test_connection_missing_settings(monkeypatch, s, name):
    seen = install(monkeypatch, conn_handler())
    r = ms.test_whisparr_connection(s)
    assert not r["ok"] and any(name in p and "empty" in p for p in r["problems"])
    assert seen == []


def test_task_test_whisparr_logs_and_returns(monkeypatch):
    install(monkeypatch, conn_handler())
    r = ms.task_test_whisparr(settings(whisparrRootFolder="/nope"))
    assert r["ok"] is False and r["problems"]
