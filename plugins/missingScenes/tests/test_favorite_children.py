"""Favorite Studios / Favorite Tags with their sub-studios and sub-tags (#179). Offline."""

import pytest

import missing_scenes as ms
from tests.test_fetch_errors import browse, find, serve, setup_box, setup_browse
from tests.test_whisparr_flows import run_main

EP = "https://stashdb.org/graphql"


class Hierarchy:
    """A fake local Stash holding studios or tags with parents.

    `entities` maps a local id to {parents, sid, favorite, scenes}. Answers the favorites
    query (inline `favorite: true`) and the children query (`parents` variable), sorted
    by scenes_count when asked, paginated by the filter. `fail_children` makes the
    children query return no data, as stash_graphql does on a GraphQL error."""

    def __init__(self, kind, entities, fail_children=False):
        self.kind = kind
        self.entities = {k: {"parents": [], "sid": None, "favorite": False, "scenes": 0, **v}
                         for k, v in entities.items()}
        self.fail_children = fail_children
        self.child_calls = []  # the parents of each children query, in order
        self.child_queries = []

    def __call__(self, query, variables=None):
        key, items_key = ("findStudios", "studios") if self.kind == "studio" else ("findTags", "tags")
        assert key in query, query
        v = variables or {}
        if "parents" in v:
            self.child_calls.append(list(v["parents"]))
            self.child_queries.append(query)
            if self.fail_children:
                return None
            parents = set(v["parents"])
            matched = [k for k, e in self.entities.items() if parents & set(e["parents"])]
        else:
            assert "favorite: true" in query, query
            matched = [k for k, e in self.entities.items() if e["favorite"]]
        f = v.get("filter") or {}
        if f.get("sort") == "scenes_count":
            matched.sort(key=lambda k: self.entities[k]["scenes"], reverse=f.get("direction") == "DESC")
        page, per_page = f.get("page", 1), f.get("per_page", 25)
        chunk = matched[(page - 1) * per_page:page * per_page]

        def render(k):
            sid = self.entities[k]["sid"]
            return {"id": k, "name": k,
                    "stash_ids": [{"endpoint": EP, "stash_id": sid}] if sid else []}

        return {key: {"count": len(matched), items_key: [render(k) for k in chunk]}}


def studios(entities, **kw):
    return Hierarchy("studio", entities, **kw)


def ids_of(items):
    return [i["id"] for i in items]


# ---- the descendant walk ----------------------------------------------------------

def test_descendants_are_breadth_first_over_levels(monkeypatch):
    h = studios({
        "R": {},
        "A": {"parents": ["R"], "scenes": 9}, "B": {"parents": ["R"], "scenes": 5},
        "A1": {"parents": ["A"], "scenes": 1}, "B1": {"parents": ["B"], "scenes": 7},
        "A2": {"parents": ["A1"]},
    })
    monkeypatch.setattr(ms, "stash_graphql", h)
    assert ids_of(ms.get_descendants("studio", ["R"])) == ["A", "B", "B1", "A1", "A2"]
    # One query per level, each asking for the children of the whole level
    assert h.child_calls == [["R"], ["A", "B"], ["B1", "A1"], ["A2"]]
    assert all("modifier: INCLUDES" in q for q in h.child_queries)


def test_descendants_stop_on_a_studio_cycle(monkeypatch):
    # A corrupted hierarchy: R -> A -> B -> R
    h = studios({"R": {"parents": ["B"]}, "A": {"parents": ["R"]}, "B": {"parents": ["A"]}})
    monkeypatch.setattr(ms, "stash_graphql", h)
    assert ids_of(ms.get_descendants("studio", ["R"])) == ["A", "B"]
    assert len(h.child_calls) == 3


def test_a_tag_with_two_parents_is_listed_once(monkeypatch):
    h = Hierarchy("tag", {
        "R1": {}, "R2": {},
        "X": {"parents": ["R1", "R2"]},
        "Y": {"parents": ["X", "R1"]},
        "Z": {"parents": ["X", "Y"]},
    })
    monkeypatch.setattr(ms, "stash_graphql", h)
    assert ids_of(ms.get_descendants("tag", ["R1", "R2"])) == ["X", "Y", "Z"]
    # Tags filter by direct parents only; the walk does the depth itself
    assert all("depth: 0" in q for q in h.child_queries)


def test_roots_are_never_listed_as_descendants(monkeypatch):
    # B is a root and also a child of root A
    monkeypatch.setattr(ms, "stash_graphql", studios({"A": {}, "B": {"parents": ["A"]}, "C": {"parents": ["B"]}}))
    assert ids_of(ms.get_descendants("studio", ["A", "B"])) == ["C"]


def test_descendants_page_through_large_levels(monkeypatch):
    kids = {f"k{i:03d}": {"parents": ["R"], "scenes": 1000 - i} for i in range(250)}
    monkeypatch.setattr(ms, "stash_graphql", studios({"R": {}, **kids}))
    assert ids_of(ms.get_descendants("studio", ["R"])) == sorted(kids)


def test_a_failed_children_query_raises(monkeypatch):
    # An empty answer would read as "no sub-studios"
    monkeypatch.setattr(ms, "stash_graphql", studios({"R": {}, "A": {"parents": ["R"]}}, fail_children=True))
    with pytest.raises(Exception) as ei:
        ms.get_descendants("studio", ["R"])
    assert "sub-studios" in str(ei.value)


# ---- every favorite (the entity page) -----------------------------------------------

def test_all_favorites_with_children_walk_through_unlinked_children(monkeypatch):
    h = studios({
        "R": {"sid": "r", "favorite": True},
        "N": {"parents": ["R"]},  # not linked to this stash-box
        "C": {"parents": ["N"], "sid": "c"},
        "U": {"favorite": True},  # an unlinked favorite still has its children counted
        "UC": {"parents": ["U"], "sid": "uc"},
        "O": {"sid": "o"},
    })
    monkeypatch.setattr(ms, "stash_graphql", h)
    assert ms.get_favorite_stash_ids("studio", EP, include_children=True) == {"r", "c", "uc"}


def test_all_favorites_without_children_query_no_children(monkeypatch):
    h = studios({"R": {"sid": "r", "favorite": True}, "C": {"parents": ["R"], "sid": "c"}})
    monkeypatch.setattr(ms, "stash_graphql", h)
    assert ms.get_favorite_stash_ids("studio", EP) == {"r"}
    assert h.child_calls == []


# ---- the top favorites (the browse page) --------------------------------------------

def browse_tree():
    # Favorites by scene count: F2 (80), F1 (50). Children: c2 (F2), c1 (F1), then g1 under c1
    return {
        "F1": {"sid": "f1", "favorite": True, "scenes": 50},
        "F2": {"sid": "f2", "favorite": True, "scenes": 80},
        "c1": {"parents": ["F1"], "sid": "c1", "scenes": 10},
        "c2": {"parents": ["F2"], "sid": "c2", "scenes": 20},
        "g1": {"parents": ["c1"], "sid": "g1"},
    }


def test_limited_lists_favorites_first_then_children_breadth_first(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=100) == \
        (["f2", "f1", "c2", "c1", "g1"], False)


def test_limited_caps_the_total_and_says_children_were_cut(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=3) == (["f2", "f1", "c2"], True)


def test_limited_cap_that_fits_every_child_is_not_a_cut(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=5) == \
        (["f2", "f1", "c2", "c1", "g1"], False)


def test_limited_cap_full_of_favorites_still_says_children_were_cut(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=2) == (["f2", "f1"], True)


def test_limited_children_without_a_link_are_not_a_cut(monkeypatch):
    tree = {"F": {"sid": "f", "favorite": True}, "N": {"parents": ["F"]}}
    monkeypatch.setattr(ms, "stash_graphql", studios(tree))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=1) == (["f"], False)


def test_limited_favorite_that_is_a_child_of_another_is_listed_once(monkeypatch):
    tree = {"A": {"sid": "a", "favorite": True, "scenes": 9},
            "B": {"sid": "b", "favorite": True, "parents": ["A"], "scenes": 1}}
    monkeypatch.setattr(ms, "stash_graphql", studios(tree))
    assert ms.get_favorite_stash_ids_limited_with_children("studio", EP, limit=2) == (["a", "b"], False)


def test_limited_without_children_keeps_its_list_return(monkeypatch):
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    assert ms.get_favorite_stash_ids_limited("studio", EP, limit=100) == ["f2", "f1"]


# ---- browse_stashdb ---------------------------------------------------------------

def browse_page(monkeypatch):
    got = []

    def fake(url, api_key, page=1, **kw):
        got.append(kw)
        return {"scenes": [{"id": "s1", "studio": {"id": "g1"}}, {"id": "s2", "studio": {"id": "zz"}}],
                "count": 2, "has_more": False}
    monkeypatch.setattr(ms.stashbox_api, "query_scenes_browse", fake)
    return got


def test_browse_sends_favorites_then_sub_studios_and_warns_when_cut(monkeypatch):
    setup_browse(monkeypatch)
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    got = browse_page(monkeypatch)
    res = browse({"favoriteLimit": 4}, page_size=5, filter_favorite_studios=True,
                 favorite_studios_include_children=True)
    assert got[0]["studio_ids"] == ["f2", "f1", "c2", "c1"]
    assert res["warnings"] == ["Favorite Studios: sub-studios cut at the Favorite Limit of 4; "
                               "raise it in the plugin settings to include them all."]
    assert res["missing_scenes"] == []  # g1 was cut, so its scene doesn't pass


def test_browse_with_every_sub_studio_has_no_warning(monkeypatch):
    setup_browse(monkeypatch)
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    got = browse_page(monkeypatch)
    res = browse({}, page_size=5, filter_favorite_studios=True, favorite_studios_include_children=True)
    assert got[0]["studio_ids"] == ["f2", "f1", "c2", "c1", "g1"]
    assert res["warnings"] == []
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1"]


def test_browse_without_include_children_is_unchanged(monkeypatch):
    setup_browse(monkeypatch)
    h = studios(browse_tree())
    monkeypatch.setattr(ms, "stash_graphql", h)
    got = browse_page(monkeypatch)
    res = browse({"favoriteLimit": 1}, page_size=5, filter_favorite_studios=True)
    assert got[0]["studio_ids"] == ["f2"]
    assert h.child_calls == []
    assert res["warnings"] == []


def test_browse_include_children_needs_the_favorites_filter(monkeypatch):
    setup_browse(monkeypatch)
    h = studios(browse_tree())
    monkeypatch.setattr(ms, "stash_graphql", h)
    got = browse_page(monkeypatch)
    browse({}, page_size=5, favorite_studios_include_children=True)
    assert got[0]["studio_ids"] is None
    assert h.child_calls == []


def test_browse_warns_about_cut_sub_tags(monkeypatch):
    setup_browse(monkeypatch)
    monkeypatch.setattr(ms, "stash_graphql", Hierarchy("tag", browse_tree()))
    got = browse_page(monkeypatch)
    res = browse({"favoriteLimit": 2}, page_size=5, filter_favorite_tags=True,
                 favorite_tags_include_children=True)
    assert got[0]["tag_ids"] == ["f2", "f1"]
    assert res["warnings"] == ["Favorite Tags: sub-tags cut at the Favorite Limit of 2; "
                               "raise it in the plugin settings to include them all."]


# ---- find_missing_scenes_paginated (the entity page) --------------------------------

def test_find_matches_scenes_from_sub_studios(monkeypatch):
    setup_box(monkeypatch)
    monkeypatch.setattr(ms, "stash_graphql", studios(browse_tree()))
    scenes = [{"id": "s1", "studio": {"id": "g1"}}, {"id": "s2", "studio": {"id": "zz"}},
              {"id": "s3", "studio": {"id": "f1"}}]
    serve(monkeypatch, {1: {"scenes": scenes, "count": 3, "page": 1, "has_more": False}})
    on = find(page_size=10, filter_favorite_studios=True, favorite_studios_include_children=True)
    assert [s["stash_id"] for s in on["missing_scenes"]] == ["s1", "s3"]
    off = find(page_size=10, filter_favorite_studios=True)
    assert [s["stash_id"] for s in off["missing_scenes"]] == ["s3"]


def test_find_matches_scenes_with_sub_tags(monkeypatch):
    setup_box(monkeypatch)
    monkeypatch.setattr(ms, "stash_graphql", Hierarchy("tag", browse_tree()))
    scenes = [{"id": "s1", "tags": [{"id": "g1"}]}, {"id": "s2", "tags": [{"id": "zz"}]}]
    serve(monkeypatch, {1: {"scenes": scenes, "count": 2, "page": 1, "has_more": False}})
    res = find(page_size=10, filter_favorite_tags=True, favorite_tags_include_children=True)
    assert [s["stash_id"] for s in res["missing_scenes"]] == ["s1"]


# ---- the RPC ------------------------------------------------------------------------

def recorder(monkeypatch, name):
    got = []
    monkeypatch.setattr(ms, name, lambda *a, **kw: got.append(kw) or {"missing_scenes": []})
    return got


@pytest.mark.parametrize("operation, target, extra", [
    ("find_missing", "find_missing_scenes_paginated", {"entity_type": "performer", "entity_id": "1"}),
    ("browse_stashdb", "browse_stashdb", {}),
])
def test_rpc_passes_include_children(monkeypatch, tmp_path, capsys, operation, target, extra):
    got = recorder(monkeypatch, target)
    run_main(monkeypatch, tmp_path, capsys, {"operation": operation, **extra,
                                             "filter_favorite_studios": True, "filter_favorite_tags": True,
                                             "favorite_studios_include_children": True,
                                             "favorite_tags_include_children": True})
    assert got[0]["favorite_studios_include_children"] is True
    assert got[0]["favorite_tags_include_children"] is True

    got.clear()
    run_main(monkeypatch, tmp_path, capsys, {"operation": operation, **extra})
    assert got[0]["favorite_studios_include_children"] is False
    assert got[0]["favorite_tags_include_children"] is False
