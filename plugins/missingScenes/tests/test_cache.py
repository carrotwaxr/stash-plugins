"""Tests for the local stash_id cache and the plugin data dir."""

import hashlib
import json
import os
import time

import pytest

import missing_scenes
import plugin_data

EP = "https://stashdb.org/graphql"


@pytest.fixture(autouse=True)
def reset_plugin_data(monkeypatch):
    monkeypatch.setattr(plugin_data, "_current_dir", None)


def fake_graphql(pages, calls):
    def _gql(query, variables=None):
        calls.append(variables)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else pages[-1]
    return _gql


def page(ids, count=None):
    return {"findScenes": {
        "count": len(ids) if count is None else count,
        "scenes": [{"stash_ids": [{"endpoint": EP, "stash_id": i}]} for i in ids],
    }}


def test_data_dir_creates_under_dir(tmp_path):
    p = plugin_data.data_dir({"Dir": str(tmp_path)})
    assert p == os.path.join(str(tmp_path), "plugin_data", "missingScenes")
    assert os.path.isdir(p)


def test_data_dir_without_dir_uses_plugin_dir_data():
    p = plugin_data.data_dir(None)
    assert p == os.path.join(os.path.dirname(os.path.abspath(plugin_data.__file__)), "data")


def test_configure_uses_dir(tmp_path):
    plugin_data.configure({"Dir": str(tmp_path)})
    assert plugin_data.current_dir() == str(tmp_path / "plugin_data" / "missingScenes")


def test_configure_falls_back_when_uncreatable(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    real = plugin_data.data_dir
    monkeypatch.setattr(plugin_data, "data_dir",
                        lambda sc=None: real(sc) if sc else str(tmp_path / "plugindata"))
    (tmp_path / "plugindata").mkdir()
    plugin_data.configure({"Dir": str(blocker)})
    assert plugin_data.current_dir() == str(tmp_path / "plugindata")


def test_configure_never_raises_and_uses_temp(tmp_path, monkeypatch):
    def boom(sc=None):
        raise OSError("nope")
    monkeypatch.setattr(plugin_data, "data_dir", boom)
    plugin_data.configure({"Dir": "/x"})
    assert plugin_data._current_dir


def test_cache_file_under_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], []))
    missing_scenes.get_or_build_cache(EP)
    path = missing_scenes._get_cache_filepath(EP)
    assert os.path.dirname(path) == str(tmp_path)
    assert os.path.exists(path)


def test_expired_cache_rebuilt(monkeypatch):
    calls = []
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], calls))
    missing_scenes.get_or_build_cache(EP)
    path = missing_scenes._get_cache_filepath(EP)
    old = time.time() - missing_scenes.CACHE_TTL_SECONDS - 10
    os.utime(path, (old, old))
    missing_scenes._local_stash_id_cache.clear()
    missing_scenes.get_or_build_cache(EP)
    assert len(calls) == 2


def test_corrupt_cache_rebuilds(monkeypatch):
    calls = []
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], calls))
    with open(missing_scenes._get_cache_filepath(EP), "w") as f:
        f.write("{not json")
    assert missing_scenes.get_or_build_cache(EP) == {"a"}
    assert len(calls) == 1


def test_wrong_shape_cache_rebuilds(monkeypatch):
    calls = []
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], calls))
    with open(missing_scenes._get_cache_filepath(EP), "w") as f:
        json.dump([1, 2], f)
    assert missing_scenes.get_or_build_cache(EP) == {"a"}


def test_unique_temp_files_and_atomic_replace(tmp_path, monkeypatch):
    srcs = []
    real = os.replace

    def spy(src, dst):
        srcs.append(src)
        real(src, dst)
    monkeypatch.setattr(missing_scenes.os, "replace", spy)
    missing_scenes._write_cache_to_disk(EP, {"a"})
    missing_scenes._write_cache_to_disk(EP, {"b"})
    dst = missing_scenes._get_cache_filepath(EP)
    assert len(set(srcs)) == 2
    assert all(s != dst + ".tmp" and os.path.dirname(s) == str(tmp_path) for s in srcs)
    assert sorted(os.listdir(tmp_path)) == [os.path.basename(dst)]
    assert json.load(open(dst))["stash_ids"] == ["b"]


def test_temp_file_cleaned_on_failure(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("fail")
    monkeypatch.setattr(missing_scenes.os, "replace", boom)
    missing_scenes._write_cache_to_disk(EP, {"a"})
    assert os.listdir(tmp_path) == []


def test_new_endpoint_new_key():
    a = missing_scenes._get_cache_filepath(EP)
    b = missing_scenes._get_cache_filepath("https://other.example/graphql")
    assert a != b


def test_build_uses_per_page_1000(monkeypatch):
    calls = []
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], calls))
    missing_scenes.get_or_build_cache(EP)
    assert calls[0]["filter"]["per_page"] == 1000


def test_build_paginates(monkeypatch):
    calls = []
    pages = [page(["a"], count=1500), page(["b"], count=1500)]
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql(pages, calls))
    assert missing_scenes.get_or_build_cache(EP) == {"a", "b"}
    assert [c["filter"]["page"] for c in calls] == [1, 2]


def test_invalidate_cache_removes_file(monkeypatch):
    monkeypatch.setattr(missing_scenes, "stash_graphql", fake_graphql([page(["a"])], []))
    missing_scenes.get_or_build_cache(EP)
    path = missing_scenes._get_cache_filepath(EP)
    assert os.path.exists(path)
    missing_scenes.invalidate_cache(EP)
    assert not os.path.exists(path)
    assert EP not in missing_scenes._local_stash_id_cache
    missing_scenes.invalidate_cache(EP)  # missing file is fine


def test_none_graphql_result_raises_clear_error(monkeypatch):
    monkeypatch.setattr(missing_scenes, "stash_graphql", lambda q, v=None: None)
    with pytest.raises(RuntimeError, match="Stash"):
        missing_scenes.get_or_build_cache(EP)


def test_md5_not_used_for_security(monkeypatch):
    seen = []
    real = hashlib.md5

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(missing_scenes.hashlib, "md5", spy)
    missing_scenes._get_cache_filepath(EP)
    assert seen == [{"usedforsecurity": False}]


def test_single_get_cache_info_definition():
    src = open(missing_scenes.__file__).read()
    assert src.count("def _get_cache_info(") == 1
