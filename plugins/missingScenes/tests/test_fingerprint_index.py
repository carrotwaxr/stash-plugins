"""Scenes matched by fingerprint count as owned (#160).

The fingerprint index maps local scenes with no stash_id for an endpoint to that
endpoint's scenes, by file fingerprint. Offline: Stash (stash_graphql) and the
stash-box request layer (graphql_request_with_retry) are fakes.
"""

import json
import os
import sqlite3
import time

import pytest

import fingerprint_index
import missing_scenes as ms
import stashbox_api
from tests.test_fetch_errors import pg, serve, setup_box, setup_browse
from tests.test_whisparr_flows import run_main

EP = "https://stashdb.org/graphql"
FANSDB = "https://fansdb.cc/graphql"
BOXES = [{"endpoint": EP, "api_key": "k", "name": "StashDB"}]


# ---- fakes ----------------------------------------------------------------------

def local_scene(i, updated="2026-01-01T00:00:00Z", fingerprints=None, duration=1800.0):
    """A local scene as Stash's findScenes returns it."""
    if fingerprints is None:
        fingerprints = [{"type": "phash", "value": f"ph{i}"}, {"type": "oshash", "value": f"os{i}"},
                        {"type": "md5", "value": f"md{i}"}]
    return {"id": str(i), "updated_at": updated,
            "files": [{"duration": duration, "fingerprints": fingerprints}]}


class FakeStash:
    """stash_graphql: pages of local scenes for findScenes, and the stash-box config."""

    def __init__(self, scenes, boxes=BOXES):
        self.scenes = scenes
        self.boxes = boxes
        self.calls = []

    def __call__(self, query, variables=None):
        self.calls.append((query, variables))
        if "findScenes" in query:
            f = variables["filter"]
            start = (f["page"] - 1) * f["per_page"]
            return {"findScenes": {"count": len(self.scenes),
                                   "scenes": self.scenes[start:start + f["per_page"]]}}
        if "stashBoxes" in query:
            return {"configuration": {"general": {"stashBoxes": self.boxes}}}
        return {"configuration": {"plugins": {}}}


class FakeBox:
    """graphql_request_with_retry for findScenesBySceneFingerprints.

    `matches` maps a fingerprint hash to the stash-box scene ids (or (id, duration)) it matches.
    `fail_on` is the 1-based call number that raises instead.
    """

    def __init__(self, matches=None, fail_on=None, error=None):
        self.matches = matches or {}
        self.fail_on = fail_on
        self.error = error or stashbox_api.StashBoxAPIError("HTTP 502: Bad Gateway", status_code=502)
        self.calls = []

    def __call__(self, url, query, variables=None, api_key=None, plugin_settings=None,
                 operation_name=None):
        assert "findScenesBySceneFingerprints" in query
        self.calls.append({"url": url, "query": query, "fingerprints": variables["fingerprints"],
                           "api_key": api_key})
        if self.fail_on == len(self.calls):
            raise self.error
        out = []
        for group in variables["fingerprints"]:
            found = []
            for fp in group:
                for m in self.matches.get(fp["hash"], []):
                    sid, dur = m if isinstance(m, tuple) else (m, None)
                    if sid not in [s["id"] for s in found]:
                        found.append({"id": sid, "duration": dur})
            out.append(found)
        return {"findScenesBySceneFingerprints": out}

    def queried_ids(self, call):
        """The local scene each group of a call came from (by its oshash os<i>)."""
        return [next(fp["hash"][2:] for fp in g if fp["algorithm"] == "OSHASH") for g in call["fingerprints"]]


def install(monkeypatch, scenes, box, boxes=BOXES):
    stash = FakeStash(scenes, boxes)
    monkeypatch.setattr(ms, "stash_graphql", stash)
    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry", box)
    return stash


def owned(endpoint=EP):
    idx = fingerprint_index.read_index(ms.CACHE_DIR, endpoint)
    return None if idx is None else idx["stash_ids"]


# ---- stashbox_api.find_scenes_by_fingerprints ----------------------------------------

def test_find_scenes_by_fingerprints_query_and_result(monkeypatch):
    seen = []

    def fake(url, query, variables=None, api_key=None, plugin_settings=None, operation_name=None):
        seen.append((url, query, variables, api_key))
        return {"findScenesBySceneFingerprints": [[{"id": "x", "duration": 60}, None], None, []]}

    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry", fake)
    groups = [[{"hash": "a", "algorithm": "PHASH"}, {"hash": "b", "algorithm": "OSHASH"}],
              [{"hash": "c", "algorithm": "MD5"}], [{"hash": "d", "algorithm": "OSHASH"}]]
    res = stashbox_api.find_scenes_by_fingerprints(EP, "key", groups)
    assert res == [[{"id": "x", "duration": 60}], [], []]
    url, query, variables, api_key = seen[0]
    assert url == EP and api_key == "key"
    assert "findScenesBySceneFingerprints(fingerprints: $fingerprints)" in query
    assert "[[FingerprintQueryInput!]!]!" in query
    assert variables == {"fingerprints": groups}


def test_find_scenes_by_fingerprints_rejects_more_than_40(monkeypatch):
    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry", lambda *a, **k: pytest.fail("no call"))
    with pytest.raises(ValueError):
        stashbox_api.find_scenes_by_fingerprints(EP, "k", [[{"hash": "h", "algorithm": "MD5"}]] * 41)


def test_find_scenes_by_fingerprints_bad_response_is_an_error(monkeypatch):
    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry",
                        lambda *a, **k: {"findScenesBySceneFingerprints": None})
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.find_scenes_by_fingerprints(EP, "k", [[{"hash": "h", "algorithm": "MD5"}]])
    # A result list that doesn't line up with the input can't be attributed to scenes
    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry",
                        lambda *a, **k: {"findScenesBySceneFingerprints": [[]]})
    with pytest.raises(stashbox_api.StashBoxAPIError):
        stashbox_api.find_scenes_by_fingerprints(EP, "k", [[{"hash": "h", "algorithm": "MD5"}]] * 2)


# ---- building the index ----------------------------------------------------------

def test_build_lists_scenes_without_a_stash_id_for_the_endpoint(monkeypatch):
    stash = install(monkeypatch, [local_scene(1)], FakeBox())
    ms.build_fingerprint_index({})
    query, variables = next(c for c in stash.calls if "findScenes" in c[0])
    assert variables["scene_filter"] == {"stash_id_endpoint": {"endpoint": EP, "modifier": "IS_NULL"}}
    assert variables["filter"]["per_page"] == 1000 and variables["filter"]["sort"] == "id"
    for field in ("updated_at", "files", "duration", "fingerprints", "type", "value"):
        assert field in query


def test_build_batches_40_and_stores_matches(monkeypatch):
    scenes = [local_scene(i) for i in range(85)]
    box = FakeBox({"ph3": ["sdb-3"], "os50": ["sdb-50"], "md84": ["sdb-84", "sdb-84b"]})
    install(monkeypatch, scenes, box)
    res = ms.build_fingerprint_index({})
    assert [len(c["fingerprints"]) for c in box.calls] == [40, 40, 5]
    assert box.calls[0]["fingerprints"][3] == [
        {"hash": "md3", "algorithm": "MD5"}, {"hash": "os3", "algorithm": "OSHASH"},
        {"hash": "ph3", "algorithm": "PHASH"}]
    assert res["scanned"] == 85 and res["queried"] == 85 and res["skipped"] == 0
    assert res["matched"] == 3 and res["partial"] is False and "error" not in res
    assert owned() == {"sdb-3", "sdb-50", "sdb-84", "sdb-84b"}


def test_build_sleeps_the_request_delay_between_batches(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    install(monkeypatch, [local_scene(i) for i in range(85)], FakeBox())
    ms.build_fingerprint_index({"stashbox_request_delay": 2})
    assert slept == [2.0, 2.0]


def test_unchanged_scene_is_not_requeried(monkeypatch):
    scenes = [local_scene(i) for i in range(45)]
    box = FakeBox({"ph1": ["sdb-1"]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    assert len(box.calls) == 2
    res = ms.build_fingerprint_index({})
    assert len(box.calls) == 2, "second run made no stash-box call"
    assert res["scanned"] == 45 and res["skipped"] == 45 and res["queried"] == 0
    assert res["matched"] == 1
    assert owned() == {"sdb-1"}


def test_changed_scene_is_requeried(monkeypatch):
    scenes = [local_scene(i) for i in range(5)]
    box = FakeBox({"ph2": ["sdb-2"]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    scenes[2] = local_scene(2, updated="2026-02-01T00:00:00Z")
    box.matches = {"ph2": ["sdb-2-new"]}
    res = ms.build_fingerprint_index({})
    assert box.queried_ids(box.calls[-1]) == ["2"]
    assert res["queried"] == 1 and res["skipped"] == 4
    assert owned() == {"sdb-2-new"}, "the old match is replaced"


def test_a_new_fingerprint_is_requeried_even_when_updated_at_is_the_same(monkeypatch):
    # Stash's Generate task adds a phash to the file, which doesn't touch the scene's updated_at
    scenes = [local_scene(1, fingerprints=[{"type": "oshash", "value": "os1"}])]
    box = FakeBox({"ph1": ["sdb-1"]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    assert owned() == set()
    scenes[0] = local_scene(1)
    res = ms.build_fingerprint_index({})
    assert res["queried"] == 1
    assert owned() == {"sdb-1"}


def test_error_mid_build_keeps_what_was_stored_and_reports_partial(monkeypatch):
    scenes = [local_scene(i) for i in range(85)]
    box = FakeBox({"ph1": ["sdb-1"], "ph60": ["sdb-60"]}, fail_on=2)
    install(monkeypatch, scenes, box)
    res = ms.build_fingerprint_index({})
    assert res["partial"] is True and res["complete"] is False
    assert "502" in res["error"] and "StashDB" in res["error"]
    assert res["queried"] == 40 and res["scanned"] == 85
    assert owned() == {"sdb-1"}
    idx = fingerprint_index.read_index(ms.CACHE_DIR, EP)
    assert idx["complete"] is False

    # The next run looks up only what wasn't stored
    box.fail_on = None
    res = ms.build_fingerprint_index({})
    assert [len(c["fingerprints"]) for c in box.calls[2:]] == [40, 5]
    assert res["partial"] is False and res["complete"] is True and res["skipped"] == 40
    assert owned() == {"sdb-1", "sdb-60"}


def test_auth_error_on_the_first_batch(monkeypatch):
    box = FakeBox(fail_on=1, error=stashbox_api.StashBoxAPIError("HTTP 401: Unauthorized", status_code=401))
    install(monkeypatch, [local_scene(1)], box)
    res = ms.build_fingerprint_index({})
    assert res["auth_error"] is True and res["partial"] is False
    assert "API key" in res["error"]


def test_scenes_without_fingerprints_are_not_sent(monkeypatch):
    scenes = [local_scene(1, fingerprints=[]), local_scene(2),
              local_scene(3, fingerprints=[{"type": "custom", "value": "x"}])]
    box = FakeBox()
    install(monkeypatch, scenes, box)
    res = ms.build_fingerprint_index({})
    assert box.queried_ids(box.calls[0]) == ["2"]
    assert res["scanned"] == 3 and res["queried"] == 1 and res["no_fingerprints"] == 2


def test_a_match_whose_duration_is_far_off_is_not_counted(monkeypatch):
    scenes = [local_scene(1, duration=1800.0), local_scene(2, duration=1800.0),
              local_scene(3, duration=1800.0)]
    box = FakeBox({"ph1": [("near", 1830)], "ph2": [("trailer", 120)], "ph3": [("unknown", None)]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    assert owned() == {"near", "unknown"}


def test_scenes_now_tagged_or_deleted_leave_the_index(monkeypatch):
    scenes = [local_scene(1), local_scene(2)]
    box = FakeBox({"ph1": ["sdb-1"], "ph2": ["sdb-2"]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    assert owned() == {"sdb-1", "sdb-2"}
    del scenes[0]  # tagged with a StashDB ID now, so the IS_NULL query no longer lists it
    res = ms.build_fingerprint_index({})
    assert owned() == {"sdb-2"} and res["scanned"] == 1


def test_a_stash_failure_while_listing_changes_nothing(monkeypatch):
    box = FakeBox({"ph1": ["sdb-1"]})
    install(monkeypatch, [local_scene(1)], box)
    ms.build_fingerprint_index({})
    monkeypatch.setattr(ms, "stash_graphql", lambda q, v=None: {"configuration": {"general": {
        "stashBoxes": BOXES}}} if "stashBoxes" in q else None)
    res = ms.build_fingerprint_index({})
    assert "error" in res and "scanned" not in res
    assert owned() == {"sdb-1"}


def test_the_index_is_per_endpoint(monkeypatch):
    boxes = BOXES + [{"endpoint": FANSDB, "api_key": "f", "name": "FansDB"}]
    box = FakeBox({"ph1": ["m"]})
    install(monkeypatch, [local_scene(1)], box, boxes=boxes)
    ms.build_fingerprint_index({}, endpoint=FANSDB)
    assert box.calls[0]["url"] == FANSDB and box.calls[0]["api_key"] == "f"
    assert owned(FANSDB) == {"m"}
    assert owned(EP) is None


def test_build_for_an_unknown_endpoint_is_an_error(monkeypatch):
    install(monkeypatch, [], FakeBox())
    res = ms.build_fingerprint_index({}, endpoint="https://nope.example/graphql")
    assert "not found" in res["error"]


# ---- the SQLite file ------------------------------------------------------------

def test_the_index_is_a_sqlite_file_in_the_data_dir(monkeypatch):
    install(monkeypatch, [local_scene(1)], FakeBox({"ph1": ["sdb-1"]}))
    ms.build_fingerprint_index({})
    path = fingerprint_index.index_path(ms.CACHE_DIR, EP)
    assert os.path.dirname(path) == ms.CACHE_DIR and os.path.isfile(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT scene_id, updated_at FROM scenes").fetchall() == [
            ("1", "2026-01-01T00:00:00Z")]
        assert conn.execute("SELECT scene_id, stash_id FROM matches").fetchall() == [("1", "sdb-1")]
    finally:
        conn.close()


def test_no_connection_is_left_open(monkeypatch):
    opened, closed = [], []
    real_connect = sqlite3.connect

    class Tracked:
        def __init__(self, conn):
            self._c = conn
            opened.append(self)

        def close(self):
            closed.append(self)
            self._c.close()

        def __getattr__(self, name):
            return getattr(self._c, name)

        def __enter__(self):
            self._c.__enter__()
            return self

        def __exit__(self, *a):
            return self._c.__exit__(*a)

    monkeypatch.setattr(fingerprint_index.sqlite3, "connect", lambda *a, **k: Tracked(real_connect(*a, **k)))
    install(monkeypatch, [local_scene(i) for i in range(45)], FakeBox({"ph1": ["sdb-1"]}))
    ms.build_fingerprint_index({})
    fingerprint_index.read_index(ms.CACHE_DIR, EP)
    assert opened and len(closed) == len(opened)


def test_a_corrupt_file_is_moved_aside_on_read(monkeypatch):
    path = fingerprint_index.index_path(ms.CACHE_DIR, EP)
    with open(path, "wb") as f:
        f.write(b"this is not a database" * 100)
    assert fingerprint_index.read_index(ms.CACHE_DIR, EP) is None
    assert not os.path.exists(path)
    assert os.path.exists(path + ".corrupt")


def test_a_corrupt_file_is_rebuilt_by_the_next_build(monkeypatch):
    path = fingerprint_index.index_path(ms.CACHE_DIR, EP)
    with open(path, "wb") as f:
        f.write(b"garbage" * 1000)
    install(monkeypatch, [local_scene(1)], FakeBox({"ph1": ["sdb-1"]}))
    res = ms.build_fingerprint_index({})
    assert "error" not in res and res["queried"] == 1
    assert owned() == {"sdb-1"}
    assert os.path.exists(path + ".corrupt")


def test_a_file_from_another_schema_version_is_rebuilt(monkeypatch):
    path = fingerprint_index.index_path(ms.CACHE_DIR, EP)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '999')")
    conn.commit()
    conn.close()
    assert fingerprint_index.read_index(ms.CACHE_DIR, EP) is None


def test_a_never_built_index_is_not_used(monkeypatch):
    assert fingerprint_index.read_index(ms.CACHE_DIR, EP) is None
    assert not os.listdir(ms.CACHE_DIR), "reading doesn't create a file"


# ---- the missing and browse views ------------------------------------------------

def build_index_matching(monkeypatch, stash_ids):
    """An index where local scene i matches stash-box scene stash_ids[i]."""
    scenes = [local_scene(i) for i in range(len(stash_ids))]
    install(monkeypatch, scenes, FakeBox({f"ph{i}": [sid] for i, sid in enumerate(stash_ids)}))
    res = ms.build_fingerprint_index({})
    assert "error" not in res


def find(settings=None, **kw):
    return ms.find_missing_scenes_paginated("performer", "1", settings or {}, **kw)


def test_a_fingerprint_match_is_owned_in_the_missing_list(monkeypatch):
    build_index_matching(monkeypatch, ["s2"])
    setup_box(monkeypatch, owned={"s1"})
    serve(monkeypatch, {1: pg(["s1", "s2", "s3"], 3, False)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s3"]
    assert res["owned_by_fingerprint"] == 1
    assert res["fingerprint_index"] is True and res["fingerprint_matching"] is True
    assert res["fingerprint_index_complete"] is True


def test_a_scene_owned_both_ways_is_not_counted_as_owned_by_fingerprint(monkeypatch):
    build_index_matching(monkeypatch, ["s1"])
    setup_box(monkeypatch, owned={"s1"})
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s2"]
    assert res["owned_by_fingerprint"] == 0


def test_owned_by_fingerprint_counts_each_scene_once_across_pages(monkeypatch):
    build_index_matching(monkeypatch, ["f1", "f2"])
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(["a", "f1", "b", "f2", "c"], 5, False)})
    first = find(page_size=2)
    assert [s["stash_id"] for s in first["missing_scenes"]] == ["a", "b"]
    second = find(page_size=2, cursor=first["cursor"])
    assert [s["stash_id"] for s in second["missing_scenes"]] == ["c"]
    assert first["owned_by_fingerprint"] + second["owned_by_fingerprint"] == 2


def test_the_setting_off_means_no_union(monkeypatch):
    build_index_matching(monkeypatch, ["s2"])
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)})
    res = find({"ignoreFingerprintMatches": True}, page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1", "s2"]
    assert res["owned_by_fingerprint"] == 0
    assert res["fingerprint_matching"] is False and res["fingerprint_index"] is False


def test_no_index_means_no_union(monkeypatch):
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1", "s2"]
    assert res["fingerprint_index"] is False and res["fingerprint_matching"] is True
    assert res["owned_by_fingerprint"] == 0


def test_another_endpoints_index_is_not_used(monkeypatch):
    boxes = [{"endpoint": FANSDB, "api_key": "f", "name": "FansDB"}]
    install(monkeypatch, [local_scene(0)], FakeBox({"ph0": ["s2"]}), boxes=boxes)
    ms.build_fingerprint_index({})
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1", "s2"]
    assert res["fingerprint_index"] is False


def test_browse_unions_the_index(monkeypatch):
    build_index_matching(monkeypatch, ["s2"])
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2", "s3"], 3, False)}, target="query_scenes_browse")
    res = ms.browse_stashdb({}, page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1", "s3"]
    assert res["owned_by_fingerprint"] == 1 and res["fingerprint_index"] is True


def test_browse_without_an_index_or_with_the_setting_off(monkeypatch):
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)}, target="query_scenes_browse")
    res = ms.browse_stashdb({}, page_size=10)
    assert res["fingerprint_index"] is False and res["owned_by_fingerprint"] == 0

    build_index_matching(monkeypatch, ["s2"])
    setup_browse(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)}, target="query_scenes_browse")
    res = ms.browse_stashdb({"ignoreFingerprintMatches": True}, page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1", "s2"]
    assert res["fingerprint_matching"] is False


def test_a_partial_index_is_used_and_says_so(monkeypatch):
    scenes = [local_scene(i) for i in range(45)]
    install(monkeypatch, scenes, FakeBox({"ph1": ["s2"]}, fail_on=2))
    assert ms.build_fingerprint_index({})["partial"] is True
    setup_box(monkeypatch)
    serve(monkeypatch, {1: pg(["s1", "s2"], 2, False)})
    res = find(page_size=10)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1"]
    assert res["fingerprint_index"] is True and res["fingerprint_index_complete"] is False


# ---- main(): the operation and the task ------------------------------------------

def test_main_operation_returns_the_counts(monkeypatch, tmp_path, capsys):
    got = []
    monkeypatch.setattr(ms, "build_fingerprint_index",
                        lambda settings, endpoint=None: got.append(endpoint) or {
                            "success": True, "scanned": 3, "queried": 3, "matched": 1, "partial": False})
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "build_fingerprint_index", "endpoint": EP})
    assert out == {"output": {"success": True, "scanned": 3, "queried": 3, "matched": 1, "partial": False}}
    assert got == [EP]


def test_main_operation_sends_a_partial_build_as_output(monkeypatch, tmp_path, capsys):
    partial = {"success": False, "error": "StashDB failed", "scanned": 85, "queried": 40,
               "matched": 1, "partial": True}
    monkeypatch.setattr(ms, "build_fingerprint_index", lambda settings, endpoint=None: partial)
    out = run_main(monkeypatch, tmp_path, capsys, {"operation": "build_fingerprint_index"})
    assert out == {"output": partial}


def test_main_task_builds_every_configured_endpoint(monkeypatch, tmp_path, capsys):
    boxes = BOXES + [{"endpoint": FANSDB, "api_key": "f", "name": "FansDB"}]
    monkeypatch.setattr(ms, "get_stashbox_config", lambda: boxes)
    got = []
    monkeypatch.setattr(ms, "build_fingerprint_index",
                        lambda settings, endpoint=None, progress=None: got.append(endpoint) or {
                            "success": True, "endpoint": endpoint, "scanned": 1, "queried": 1,
                            "matched": 0, "skipped": 0, "partial": False})
    out = run_main(monkeypatch, tmp_path, capsys, {"mode": "build_fingerprint_index"})
    assert got == [EP, FANSDB]
    assert [r["endpoint"] for r in out["output"]["results"]] == [EP, FANSDB]
    assert out["output"]["success"] is True


def test_the_manifest_has_the_task_and_the_setting():
    import yaml
    with open(os.path.join(os.path.dirname(ms.__file__), "missingScenes.yml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    tasks = {t["name"]: t for t in manifest["tasks"]}
    assert tasks["Build Fingerprint Index"]["defaultArgs"] == {"mode": "build_fingerprint_index"}
    assert manifest["settings"]["ignoreFingerprintMatches"]["type"] == "BOOLEAN"


def test_unmatched_scene_is_rechecked_after_30_days_matched_is_not(monkeypatch):
    assert fingerprint_index.RECHECK_UNMATCHED_DAYS == 30
    scenes = [local_scene(1), local_scene(2)]
    box = FakeBox({"ph1": ["sdb-1"]})
    install(monkeypatch, scenes, box)
    ms.build_fingerprint_index({})
    assert len(box.calls) == 1

    def age(days):
        conn = sqlite3.connect(fingerprint_index.index_path(ms.CACHE_DIR, EP))
        conn.execute("UPDATE scenes SET checked_at = ?", (time.time() - days * 86400,))
        conn.commit()
        conn.close()

    age(29)
    res = ms.build_fingerprint_index({})
    assert len(box.calls) == 1 and res["queried"] == 0, "29 days old: not looked up"

    age(31)
    box.matches = {"ph1": ["sdb-1"], "ph2": ["sdb-2"]}
    res = ms.build_fingerprint_index({})
    assert res["queried"] == 1 and res["skipped"] == 1
    assert box.queried_ids(box.calls[-1]) == ["2"], "only the unmatched scene is looked up again"
    assert owned() == {"sdb-1", "sdb-2"}, "the match the box gained is picked up"

    age(31)
    res = ms.build_fingerprint_index({})
    assert res["queried"] == 0, "both have matches now; matched scenes are not re-queried"


# ---- a local scene's matches go with it (tagged or deleted) ------------------------------

TWO_BOXES = [{"endpoint": EP, "api_key": "k", "name": "StashDB"},
             {"endpoint": FANSDB, "api_key": "f", "name": "FansDB"}]


def two_indexes(monkeypatch):
    """StashDB: local scene 0 matches s-a, scene 1 matches s-b. FansDB: scene 0 matches f-a."""
    scenes = [local_scene(0), local_scene(1)]
    install(monkeypatch, scenes, FakeBox({"ph0": ["s-a"], "ph1": ["s-b"]}), boxes=TWO_BOXES)
    assert "error" not in ms.build_fingerprint_index({}, endpoint=EP)
    install(monkeypatch, scenes, FakeBox({"ph0": ["f-a"]}), boxes=TWO_BOXES)
    assert "error" not in ms.build_fingerprint_index({}, endpoint=FANSDB)
    assert owned(EP) == {"s-a", "s-b"} and owned(FANSDB) == {"f-a"}


def hook_stash(monkeypatch, stash_ids=None, found=True, boxes=TWO_BOXES):
    """Stash for the hooks: the configured boxes, and scene 0 with these stash_ids."""
    queries = []

    def gql(query, variables=None):
        queries.append(query)
        if "stashBoxes" in query:
            return {"configuration": {"general": {"stashBoxes": boxes}}}
        if "findScene(" in query:
            scene = {"id": str(variables["id"]), "title": "T", "stash_ids": stash_ids or []}
            return {"findScene": scene if found else None}
        raise AssertionError(f"unexpected Stash query: {query}")

    monkeypatch.setattr(ms, "stash_graphql", gql)
    return queries


def no_whisparr(monkeypatch):
    import urllib.request
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: seen.append(req) or pytest.fail(
        f"unexpected request {req.full_url}"))
    return seen


UPDATE = {"id": 0, "type": "Scene.Update.Post", "input": {"id": "0"}, "inputFields": ["id", "stash_ids"]}
DESTROY = {"id": 0, "type": "Scene.Destroy.Post",
           "input": {"id": "0", "checksum": "", "oshash": "os0", "path": "/data/a.mp4"}}
CLEANUP_ON = {"enableAutoCleanup": True, "whisparrUrl": "http://h:6969", "whisparrApiKey": "K"}


def test_tagging_a_scene_drops_its_matches_from_that_boxes_index(monkeypatch):
    two_indexes(monkeypatch)
    hook_stash(monkeypatch, stash_ids=[{"endpoint": EP, "stash_id": "s-real"}])
    res = ms.handle_scene_update_hook(UPDATE, {})
    assert res["success"] is True
    # Its StashDB ID speaks for it now; s-a no longer counts as owned
    assert owned(EP) == {"s-b"}
    # Still untagged on FansDB, so its FansDB match still counts
    assert owned(FANSDB) == {"f-a"}


def test_an_update_of_a_scene_stash_no_longer_has_drops_it_everywhere(monkeypatch):
    two_indexes(monkeypatch)
    hook_stash(monkeypatch, found=False)
    ms.handle_scene_update_hook(UPDATE, {})
    assert owned(EP) == {"s-b"} and owned(FANSDB) == set()


def test_an_update_that_did_not_set_stash_ids_leaves_the_index(monkeypatch):
    two_indexes(monkeypatch)
    queries = hook_stash(monkeypatch, stash_ids=[{"endpoint": EP, "stash_id": "s-real"}])
    ms.handle_scene_update_hook(dict(UPDATE, inputFields=["id", "title"]), {})
    assert owned(EP) == {"s-a", "s-b"} and queries == []


def test_deleting_a_scene_drops_its_matches_everywhere_and_skips_whisparr(monkeypatch):
    two_indexes(monkeypatch)
    hook_stash(monkeypatch)
    seen = no_whisparr(monkeypatch)
    ms._write_cache_to_disk(EP, {"s-real"})
    res = ms.handle_scene_destroy_hook(DESTROY, CLEANUP_ON)
    assert res["success"] is True
    assert owned(EP) == {"s-b"} and owned(FANSDB) == set()
    assert seen == []
    assert ms._read_cache_from_disk(EP) is None  # its stash_ids no longer count as owned either


def test_deleting_a_scene_without_an_index_creates_nothing(monkeypatch):
    hook_stash(monkeypatch)
    res = ms.handle_scene_destroy_hook(DESTROY, {})
    assert res["success"] is True
    assert not [f for f in os.listdir(ms.CACHE_DIR) if f.startswith("fingerprints_")]


def test_main_dispatches_the_destroy_hook_without_whisparr(monkeypatch, tmp_path, capsys):
    got = []
    monkeypatch.setattr(ms, "handle_scene_update_hook", lambda *a: pytest.fail("not the update hook"))
    monkeypatch.setattr(ms, "handle_scene_destroy_hook", lambda c, s: got.append(c) or {"success": True})
    out = run_main(monkeypatch, tmp_path, capsys, {"hookContext": DESTROY}, CLEANUP_ON)
    assert got == [DESTROY] and out == {"output": {"success": True}}


def test_the_manifest_declares_the_destroy_hook():
    import yaml
    with open(os.path.join(os.path.dirname(ms.__file__), "missingScenes.yml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    triggers = [t for hook in manifest["hooks"] for t in hook["triggeredBy"]]
    assert "Scene.Update.Post" in triggers and "Scene.Destroy.Post" in triggers
