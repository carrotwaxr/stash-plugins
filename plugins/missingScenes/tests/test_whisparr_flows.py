"""Whisparr add, the auto-cleanup hook and the cleanup task: StashDB only, never the wrong entry."""

import json
import urllib.parse

import pytest

import missing_scenes as ms
import plugin_data
from tests.test_whisparr import http_error, install

KEY = "SECRET-KEY-123"
URL = "http://h:6969"
SID = "11111111-1111-1111-1111-111111111111"
SID2 = "33333333-3333-3333-3333-333333333333"
OTHER = "22222222-2222-2222-2222-222222222222"

STASHDB = "https://stashdb.org/graphql"
TPDB = "https://theporndb.net/graphql"
FANSDB = "https://fansdb.cc/graphql"

ADD_SETTINGS = {"whisparrUrl": URL, "whisparrApiKey": KEY, "whisparrRootFolder": "/data"}
HOOK_SETTINGS = {"enableAutoCleanup": True, "whisparrUrl": URL, "whisparrApiKey": KEY}
BOXES = [{"endpoint": TPDB, "name": "ThePornDB", "api_key": "t"},
         {"endpoint": STASHDB, "name": "StashDB", "api_key": "s"}]


_QUEUE = object()


def whisparr(movies=(), lookup=(), queue=(), fail=None, queue_reply=_QUEUE):
    """A fake Whisparr v3. `movie?stashId=` returns `movies` unfiltered (a Whisparr that
    ignores the filter, the worst case). `fail(method, path)` may return an exception.
    `queue_reply` replaces the queue reply: a value, or a function of the request path."""
    by_id = {m.get("id"): m for m in movies}

    def handler(req):
        method = req.get_method()
        path = req.full_url.split("/api/v3/", 1)[1]
        if fail:
            err = fail(method, path)
            if err is not None:
                return err
        if method == "GET" and path.startswith("movie?stashId="):
            return list(movies)
        if method == "GET" and path == "movie":
            return list(movies)
        if method == "GET" and path.startswith("lookup/scene"):
            return list(lookup)
        if method == "GET" and path.startswith("queue"):
            if queue_reply is not _QUEUE:
                reply = queue_reply(path) if callable(queue_reply) else queue_reply
                return b"" if reply is None else reply
            return {"records": list(queue)}
        if method == "POST" and path == "movie":
            body = json.loads(req.data)
            return {"id": 42, "title": body.get("title"), "foreignId": body.get("foreignId")}
        if method == "POST" and path == "command":
            return {"id": 1, "name": "MoviesSearch"}
        if path.startswith("movie/"):
            movie_id = int(path.split("/", 1)[1].split("?", 1)[0])
            if method == "GET":
                return dict(by_id[movie_id]) if movie_id in by_id else http_error(404)
            if method == "PUT":
                return json.loads(req.data)
            if method == "DELETE":
                return b""
        return http_error(404)
    return handler


def calls(seen):
    return [f"{r.get_method()} {urllib.parse.unquote(r.full_url.split('/api/v3/', 1)[1])}" for r in seen]


def writes(seen):
    return [c for c in calls(seen) if not c.startswith("GET ")]


# ---- is_stashdb_endpoint -----------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://stashdb.org/graphql", True),
    ("https://StashDB.org/graphql/", True),
    ("  https://stashdb.org/graphql  ", True),
    ("https://stashdb.org", True),
    ("https://stashdb.org:443/graphql", True),
    (TPDB, False),
    (FANSDB, False),
    ("https://stashdb.org.example.com/graphql", False),
    ("https://notstashdb.org/graphql", False),
    ("https://example.com/stashdb.org", False),
    ("", False),
    (None, False),
])
def test_is_stashdb_endpoint(url, expected):
    assert ms.is_stashdb_endpoint(url) is expected


# ---- add_to_whisparr ----------------------------------------------------------

@pytest.mark.parametrize("endpoint", [TPDB, FANSDB, "https://FansDB.cc/graphql/"])
def test_add_refuses_non_stashdb_endpoint(monkeypatch, endpoint):
    seen = install(monkeypatch, whisparr())
    res = ms.add_to_whisparr(SID, "T", ADD_SETTINGS, endpoint=endpoint)
    assert res["success"] is False
    assert "StashDB" in res["error"]
    assert seen == []


@pytest.mark.parametrize("kwargs", [{}, {"endpoint": None}, {"endpoint": ""}, {"endpoint": STASHDB},
                                    {"endpoint": "https://StashDB.org/graphql/"}])
def test_add_stashdb_or_missing_endpoint_adds(monkeypatch, kwargs):
    # A missing endpoint means StashDB: older UI builds don't send one
    seen = install(monkeypatch, whisparr(lookup=[{"movie": {"title": "T", "foreignId": "f1", "stashId": SID}}]))
    res = ms.add_to_whisparr(SID, "T", ADD_SETTINGS, **kwargs)
    assert res["success"] is True
    assert "POST movie" in calls(seen)


@pytest.mark.parametrize("search_on_add", [True, False])
def test_add_reports_search_triggered(monkeypatch, search_on_add):
    seen = install(monkeypatch, whisparr(lookup=[{"movie": {"title": "T", "foreignId": "f1", "stashId": SID}}]))
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrSearchOnAdd=search_on_add), endpoint=STASHDB)
    assert res["success"] is True
    assert res["search_triggered"] is search_on_add
    assert res["scene"]["id"] == 42
    assert "error" not in res and "search_error" not in res
    post = next(r for r in seen if r.get_method() == "POST")
    assert json.loads(post.data)["addOptions"]["searchForMovie"] is search_on_add


def test_existing_scene_search_triggered(monkeypatch):
    seen = install(monkeypatch, whisparr(movies=[{"id": 7, "stashId": SID, "hasFile": False}]))
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrSearchOnAdd=True), endpoint=STASHDB)
    assert res["success"] is True and res["already_exists"] is True
    assert res["search_triggered"] is True
    assert "POST command" in calls(seen)


def test_failed_search_trigger_is_reported_and_add_still_succeeds(monkeypatch):
    fail = lambda m, p: http_error(500, {"message": "indexers down"}) if p == "command" else None
    install(monkeypatch, whisparr(movies=[{"id": 7, "stashId": SID, "hasFile": False}], fail=fail))
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrSearchOnAdd=True), endpoint=STASHDB)
    assert res["success"] is True and res["already_exists"] is True
    assert res["search_triggered"] is False
    assert "indexers down" in res["search_error"]
    assert "error" not in res  # main() would turn the whole reply into a failure


def test_existing_scene_with_file_triggers_nothing(monkeypatch):
    seen = install(monkeypatch, whisparr(movies=[{"id": 7, "stashId": SID, "hasFile": True}]))
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrSearchOnAdd=True), endpoint=STASHDB)
    assert res["success"] is True and res["already_exists"] is True
    assert res["search_triggered"] is False
    assert writes(seen) == []


def test_lookup_error_returns_whisparr_text(monkeypatch):
    fail = lambda m, p: http_error(503, {"message": "TPDB is unreachable"}) if p.startswith("lookup") else None
    seen = install(monkeypatch, whisparr(fail=fail))
    res = ms.add_to_whisparr(SID, "T", ADD_SETTINGS, endpoint=STASHDB)
    assert res["success"] is False
    assert "TPDB is unreachable" in res["error"]
    assert "not found" not in res["error"].lower()
    assert writes(seen) == []


def test_lookup_with_no_match_is_not_found(monkeypatch):
    seen = install(monkeypatch, whisparr(lookup=[]))
    res = ms.add_to_whisparr(SID, "T", ADD_SETTINGS, endpoint=STASHDB)
    assert res["success"] is False
    assert "not found" in res["error"].lower()
    assert writes(seen) == []


# ---- main() dispatch ------------------------------------------------------------

def run_main(monkeypatch, tmp_path, capsys, args, settings=None):
    monkeypatch.setattr(plugin_data, "_current_dir", None)
    monkeypatch.setattr(ms, "_input_data", {"server_connection": {"Dir": str(tmp_path)}, "args": args})
    plugins = {"missingScenes": settings or {}}
    monkeypatch.setattr(ms, "stash_graphql", lambda q, v=None: {"configuration": {"plugins": plugins}})
    ms.main()
    return json.loads(capsys.readouterr().out)


def test_main_add_passes_endpoint(monkeypatch, tmp_path, capsys):
    seen = install(monkeypatch, whisparr())
    out = run_main(monkeypatch, tmp_path, capsys,
                   {"operation": "add_to_whisparr", "stash_id": SID, "title": "T", "endpoint": TPDB},
                   ADD_SETTINGS)
    assert "StashDB" in out["error"]
    assert seen == []


def test_main_add_without_endpoint(monkeypatch, tmp_path, capsys):
    got = []
    monkeypatch.setattr(ms, "add_to_whisparr",
                        lambda sid, title, s, endpoint=None: got.append(endpoint) or {"success": True})
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "add_to_whisparr", "stash_id": SID, "title": "T"})
    assert out == {"output": {"success": True}}
    assert got == [None]


def test_main_passes_hook_context(monkeypatch, tmp_path, capsys):
    got = []
    ctx = {"id": 5, "type": "Scene.Update.Post", "input": {"id": "5"}, "inputFields": ["id", "stash_ids"]}
    monkeypatch.setattr(ms, "handle_scene_update_hook", lambda c, s: got.append(c) or {"success": True})
    out = run_main(monkeypatch, tmp_path, capsys, {"hookContext": ctx})
    assert got == [ctx]
    assert out == {"output": {"success": True}}


# ---- the Scene.Update.Post hook -------------------------------------------------

def hook_ctx(fields=("id", "stash_ids"), scene_id=5, hook_input=None):
    return {"id": scene_id, "type": "Scene.Update.Post",
            "input": {"id": str(scene_id)} if hook_input is None else hook_input,
            "inputFields": list(fields)}


def stash(monkeypatch, boxes=BOXES, stash_ids=None, title="T"):
    """Fake local Stash. Returns the list of GraphQL queries made."""
    if stash_ids is None:
        stash_ids = [{"endpoint": TPDB, "stash_id": "tpdb-1"}, {"endpoint": STASHDB, "stash_id": SID}]
    queries = []

    def gql(query, variables=None):
        queries.append(query)
        if "findScene(" in query:
            return {"findScene": {"id": "5", "title": title, "stash_ids": stash_ids}}
        raise AssertionError(f"unexpected Stash query: {query}")

    def boxes_fn():
        queries.append("stashBoxes")
        return boxes

    monkeypatch.setattr(ms, "stash_graphql", gql)
    monkeypatch.setattr(ms, "get_stashbox_config", boxes_fn)
    return queries


def logs(monkeypatch):
    lines = []
    for level in ("LogDebug", "LogInfo", "LogWarning", "LogError"):
        monkeypatch.setattr(ms.log, level, lambda s, level=level: lines.append((level, s)))
    return lines


def test_hook_uses_the_stashdb_box_not_the_first(monkeypatch):
    stash(monkeypatch)  # ThePornDB is configured first
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}], queue=[{"movieId": 3}]))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is True
    assert f"GET movie?stashId={SID}" in calls(seen)
    assert not any("tpdb-1" in c for c in calls(seen))
    assert writes(seen) == ["DELETE movie/9?deleteFiles=false"]


def test_hook_without_stashdb_box_does_nothing(monkeypatch):
    stash(monkeypatch, boxes=[BOXES[0]], stash_ids=[{"endpoint": TPDB, "stash_id": "tpdb-1"}])
    lines = logs(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": "tpdb-1"}]))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is True
    assert seen == []
    assert any(level == "LogDebug" and "StashDB" in s for level, s in lines)


@pytest.mark.parametrize("ctx", [
    hook_ctx(fields=("id", "title", "rating100")),
    {"id": 5, "type": "Scene.Update.Post", "input": None},  # a scan: no inputFields at all
])
def test_hook_ignores_updates_that_did_not_set_stash_ids(monkeypatch, ctx):
    queries = stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}]))
    res = ms.handle_scene_update_hook(ctx, HOOK_SETTINGS)
    assert res["success"] is True
    assert seen == [] and queries == []


def test_hook_uses_the_hook_id_when_input_is_a_list(monkeypatch):
    # scenesUpdate passes the whole list of inputs as `input`
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}]))
    res = ms.handle_scene_update_hook(hook_ctx(hook_input=[{"id": "4"}, {"id": "5"}]), HOOK_SETTINGS)
    assert res["success"] is True
    assert writes(seen) == ["DELETE movie/9?deleteFiles=false"]


def test_hook_skips_an_entry_in_the_download_queue(monkeypatch):
    stash(monkeypatch)
    lines = logs(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}],
                                         queue=[{"movieId": 9, "status": "downloading"}]))
    res = ms.handle_scene_update_hook(hook_ctx(), dict(HOOK_SETTINGS, unmonitorOnly=False))
    assert res["success"] is True
    assert writes(seen) == []
    assert any("queue" in s.lower() for _, s in lines)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}], queue=[{"movieId": 9}]))
    ms.handle_scene_update_hook(hook_ctx(), dict(HOOK_SETTINGS, unmonitorOnly=True))
    assert writes(seen) == []


def test_hook_queue_error_does_not_delete(monkeypatch):
    stash(monkeypatch)
    fail = lambda m, p: http_error(500, b"boom") if p.startswith("queue") else None
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}], fail=fail))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is False and "500" in res["error"]
    assert writes(seen) == []


def test_hook_never_deletes_a_mismatched_entry(monkeypatch):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": OTHER}]))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is True
    assert writes(seen) == []


def test_hook_never_deletes_an_entry_without_an_id(monkeypatch):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"stashId": SID}]))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is False
    assert writes(seen) == []


def test_hook_unmonitor_only(monkeypatch):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID, "monitored": True}]))
    res = ms.handle_scene_update_hook(hook_ctx(), dict(HOOK_SETTINGS, unmonitorOnly=True))
    assert res["success"] is True
    assert writes(seen) == ["PUT movie/9"]
    put = next(r for r in seen if r.get_method() == "PUT")
    assert json.loads(put.data)["monitored"] is False


@pytest.mark.parametrize("settings", [HOOK_SETTINGS, {"enableAutoCleanup": False}])
def test_hook_invalidates_the_stashdb_index(monkeypatch, settings):
    stash(monkeypatch)
    install(monkeypatch, whisparr(movies=[]))
    ms._write_cache_to_disk(STASHDB, {"x"})
    assert ms._read_cache_from_disk(STASHDB) == {"x"}
    ms.handle_scene_update_hook(hook_ctx(), settings)
    assert ms._read_cache_from_disk(STASHDB) is None


def test_hook_disabled_makes_no_whisparr_call(monkeypatch):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}]))
    res = ms.handle_scene_update_hook(hook_ctx(), dict(HOOK_SETTINGS, enableAutoCleanup=False))
    assert res["success"] is True
    assert seen == []


# ---- the Cleanup Whisparr task ----------------------------------------------------

def local_index(monkeypatch, ids, boxes=BOXES):
    """Fake the StashDB index; any other Stash query (a full library scan) fails the test."""
    built = []

    def build(endpoint):
        built.append(endpoint)
        if isinstance(ids, Exception):
            raise ids
        return set(ids)

    def gql(query, variables=None):
        raise AssertionError(f"unexpected Stash query: {query}")

    monkeypatch.setattr(ms, "get_or_build_cache", build)
    monkeypatch.setattr(ms, "stash_graphql", gql)
    monkeypatch.setattr(ms, "get_stashbox_config", lambda: boxes)
    return built


MOVIES = [{"id": 9, "stashId": SID, "title": "A"},
          {"id": 10, "stashId": OTHER, "title": "B"},
          {"id": 11, "title": "No StashDB id"}]


def test_cleanup_uses_the_stashdb_index(monkeypatch):
    built = local_index(monkeypatch, {SID})
    seen = install(monkeypatch, whisparr(movies=MOVIES))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert built == [STASHDB]
    assert writes(seen) == ["DELETE movie/9?deleteFiles=false"]
    assert res["success"] is True
    assert res["cleaned"] == 1 and res["skipped_in_queue"] == 0 and res["errors"] == []


def test_cleanup_skips_queued_items(monkeypatch):
    local_index(monkeypatch, {SID, SID2})
    lines = logs(monkeypatch)
    movies = [{"id": 9, "stashId": SID, "title": "A"}, {"id": 12, "stashId": SID2, "title": "C"}]
    seen = install(monkeypatch, whisparr(movies=movies, queue=[{"movieId": 12}]))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert writes(seen) == ["DELETE movie/9?deleteFiles=false"]
    assert res["success"] is True
    assert res["cleaned"] == 1 and res["skipped_in_queue"] == 1
    assert any("queue" in s.lower() and "C" in s for _, s in lines)


def test_cleanup_unmonitor_only(monkeypatch):
    local_index(monkeypatch, {SID})
    seen = install(monkeypatch, whisparr(movies=MOVIES))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY, "unmonitorOnly": True})
    assert writes(seen) == ["PUT movie/9"]
    assert res["success"] is True and res["cleaned"] == 1


@pytest.mark.parametrize("broken", ["movie", "queue"])
def test_cleanup_fails_when_whisparr_cannot_be_read(monkeypatch, broken):
    local_index(monkeypatch, {SID})
    fail = lambda m, p: http_error(500, b"boom") if m == "GET" and p.split("?")[0] == broken else None
    seen = install(monkeypatch, whisparr(movies=MOVIES, fail=fail))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is False and "500" in res["error"]
    assert writes(seen) == []


def test_cleanup_without_stashdb_box(monkeypatch):
    local_index(monkeypatch, {SID}, boxes=[BOXES[0]])
    seen = install(monkeypatch, whisparr(movies=MOVIES))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is False and "StashDB" in res["error"]
    assert seen == []


def test_cleanup_fails_when_the_local_index_cannot_be_built(monkeypatch):
    local_index(monkeypatch, RuntimeError("Could not read scenes from Stash"))
    seen = install(monkeypatch, whisparr(movies=MOVIES))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is False and "Could not read scenes" in res["error"]
    assert writes(seen) == []


# ---- reading the download queue fails closed ---------------------------------------

# Replies that don't show the whole queue: nothing may be deleted on the strength of them
BAD_QUEUE_REPLIES = [
    pytest.param([], id="a list"),
    pytest.param(None, id="an empty body"),
    pytest.param({}, id="no records"),
    pytest.param({"records": None}, id="records null"),
    pytest.param({"records": "x"}, id="records a string"),
    pytest.param({"records": [{"movieId": 9}, "junk"]}, id="an unreadable item"),
    pytest.param({"page": 1, "pageSize": 1000, "totalRecords": 5, "records": []}, id="total without records"),
]


def paged_queue(pages, total=None):
    """A queue reply per requested page, like Whisparr's paging resource."""
    def reply(path):
        query = urllib.parse.parse_qs(path.split("?", 1)[1]) if "?" in path else {}
        page = int(query.get("page", ["1"])[0])
        size = int(query.get("pageSize", ["10"])[0])
        records = pages[page - 1] if page <= len(pages) else []
        assert len(records) <= size
        return {"page": page, "pageSize": size, "records": records,
                "totalRecords": sum(map(len, pages)) if total is None else total}
    return reply


# 1000 other downloads on page 1; movie 9 is downloading on page 2
BIG_QUEUE = [[{"movieId": 1000 + i, "status": "downloading"} for i in range(1000)],
             [{"movieId": 9, "status": "downloading"}]]


@pytest.mark.parametrize("reply", BAD_QUEUE_REPLIES)
def test_hook_deletes_nothing_when_the_queue_reply_is_unexpected(monkeypatch, reply):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}], queue_reply=reply))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is False and "queue" in res["error"].lower()
    assert writes(seen) == []


@pytest.mark.parametrize("reply", BAD_QUEUE_REPLIES)
def test_cleanup_deletes_nothing_when_the_queue_reply_is_unexpected(monkeypatch, reply):
    local_index(monkeypatch, {SID})
    seen = install(monkeypatch, whisparr(movies=MOVIES, queue_reply=reply))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is False and "queue" in res["error"].lower()
    assert writes(seen) == []


def test_hook_sees_a_download_past_the_first_queue_page(monkeypatch):
    stash(monkeypatch)
    seen = install(monkeypatch, whisparr(movies=[{"id": 9, "stashId": SID}], queue_reply=paged_queue(BIG_QUEUE)))
    res = ms.handle_scene_update_hook(hook_ctx(), HOOK_SETTINGS)
    assert res["success"] is True and "queue" in res["message"].lower()
    assert writes(seen) == []


def test_cleanup_sees_a_download_past_the_first_queue_page(monkeypatch):
    local_index(monkeypatch, {SID})
    seen = install(monkeypatch, whisparr(movies=MOVIES, queue_reply=paged_queue(BIG_QUEUE)))
    res = ms.task_cleanup_whisparr({"whisparrUrl": URL, "whisparrApiKey": KEY})
    assert res["success"] is True
    assert res["cleaned"] == 0 and res["skipped_in_queue"] == 1
    assert writes(seen) == []
