"""Trending sort and site links: the sort set, ThePornDB mapping, format_scene urls."""

import pytest

import missing_scenes as ms
import stashbox_api
import theporndb_api

EP = "https://stashdb.org/graphql"


def capture(monkeypatch):
    sent = []

    def fake(url, query, variables=None, api_key=None, **kw):
        sent.append(variables)
        return {"queryScenes": {"count": 0, "scenes": []}}

    monkeypatch.setattr(stashbox_api, "graphql_request_with_retry", fake)
    return sent


@pytest.mark.parametrize("entity", ["performer", "studio"])
def test_query_scenes_page_sends_trending(monkeypatch, entity):
    sent = capture(monkeypatch)
    stashbox_api.query_scenes_page(EP, "k", entity, "sid", sort="TRENDING")
    assert sent[0]["input"]["sort"] == "TRENDING"


def test_query_scenes_page_still_rejects_unknown_sort(monkeypatch):
    sent = capture(monkeypatch)
    stashbox_api.query_scenes_page(EP, "k", "performer", "sid", sort="BOGUS")
    assert sent[0]["input"]["sort"] == "DATE"


def test_theporndb_maps_trending():
    assert theporndb_api._map_sort("TRENDING", "DESC")[0] == "trending"


def test_format_scene_keeps_all_urls_with_site_names():
    scene = {"urls": [
        {"url": "https://studio.example/s/1", "site": {"name": "Studio"}},
        {"url": "https://www.other.example/x", "site": None},
        {"url": "https://x.example/y"},
    ]}
    out = ms.format_scene(scene, "id1")
    assert out["url"] == "https://studio.example/s/1"
    assert out["urls"] == [
        {"url": "https://studio.example/s/1", "site": "Studio"},
        {"url": "https://www.other.example/x", "site": "www.other.example"},
        {"url": "https://x.example/y", "site": "x.example"},
    ]


def test_format_scene_without_urls():
    assert ms.format_scene({}, "id1")["urls"] == []
    assert ms.format_scene({"urls": None}, "id1")["url"] is None
