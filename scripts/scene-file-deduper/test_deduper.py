"""Offline tests for the delete endpoint's safety checks (no Stash needed)."""

import pytest

import app as app_module
from stash_client import StashClient


class FakeStash(StashClient):
    """StashClient with the GraphQL layer replaced by an in-memory scene."""

    def __init__(self, scene_files):
        super().__init__("http://stash.invalid", "unused")
        self.scene_files = scene_files
        self.calls = []

    def _execute(self, query, variables=None):
        self.calls.append((query.split("(")[0].split()[-1], variables))
        if "findScenes(" in query:
            return {"findScenes": {"scenes": [{
                "id": "10", "title": "Scene", "performers": [], "studio": None, "tags": [],
                "files": [
                    {"id": f, "path": f"/media/{f}.mp4", "basename": f"{f}.mp4", "size": 1 << 30,
                     "duration": 600, "video_codec": "h264", "audio_codec": "aac", "width": 1920,
                     "height": 1080, "frame_rate": 30, "bit_rate": 8000000}
                    for f in self.scene_files
                ],
            }]}}
        if "findScene(" in query:
            return {"findScene": {"files": [{"id": f} for f in self.scene_files]}}
        if "deleteFiles" in query:
            return {"deleteFiles": True}
        if "sceneUpdate" in query:
            return {"sceneUpdate": {"id": variables["id"]}}
        if "allTags" in query:
            return {"allTags": []}
        if "systemStatus" in query:
            return {"systemStatus": {"databaseSchema": 85}}
        raise AssertionError(f"unexpected query: {query}")

    def mutations(self):
        return [name for name, _ in self.calls if name in ("DeleteFiles", "SetPrimaryFile")]


def test_deletes_non_primary_file():
    stash = FakeStash(["1", "2"])
    assert stash.delete_scene_files("10", ["2"], "1")
    assert stash.mutations() == ["DeleteFiles"]


def test_deleting_primary_promotes_kept_file_first():
    stash = FakeStash(["1", "2"])
    stash.delete_scene_files("10", ["1"], "2")
    assert stash.mutations() == ["SetPrimaryFile", "DeleteFiles"]


@pytest.mark.parametrize("to_delete,keep", [
    (["99"], "1"),       # file from another scene
    (["2", "99"], "1"),  # one stray among valid ones
    (["2"], "99"),       # kept file not in scene
    (["1", "2"], "1"),   # kept file also deleted
])
def test_rejects_requests_that_do_not_match_the_scene(to_delete, keep):
    stash = FakeStash(["1", "2"])
    with pytest.raises(ValueError):
        stash.delete_scene_files("10", to_delete, keep)
    assert stash.mutations() == []


@pytest.fixture
def client(monkeypatch):
    stash = FakeStash(["1", "2"])
    monkeypatch.setenv("STASH_URL", "http://stash.invalid")
    monkeypatch.setenv("STASH_API_KEY", "unused")
    monkeypatch.setattr(app_module, "StashClient", lambda url, key: stash)
    flask_app = app_module.create_app()
    return flask_app.test_client(), stash


def _post(test_client, host="127.0.0.1:5001", origin="http://127.0.0.1:5001", body=None):
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    return test_client.post("/api/delete-files", json=body or {
        "scene_id": "10", "file_ids_to_delete": ["2"], "keep_file_id": "1",
    }, headers=headers)


def test_same_origin_post_allowed(client):
    test_client, stash = client
    assert _post(test_client).status_code == 200
    assert _post(test_client, host="localhost:5001", origin="http://localhost:5001").status_code == 200
    assert stash.mutations() == ["DeleteFiles", "DeleteFiles"]


@pytest.mark.parametrize("host,origin", [
    ("127.0.0.1:5001", "http://evil.example"),   # cross-site
    ("127.0.0.1:5001", None),                    # no Origin header
    ("evil.example:5001", "http://evil.example:5001"),  # DNS rebinding
])
def test_foreign_host_or_origin_rejected(client, host, origin):
    test_client, stash = client
    assert _post(test_client, host=host, origin=origin).status_code == 403
    assert stash.mutations() == []


def test_mismatched_files_return_400(client):
    test_client, stash = client
    resp = _post(test_client, body={"scene_id": "10", "file_ids_to_delete": ["99"], "keep_file_id": "1"})
    assert resp.status_code == 400
    assert stash.mutations() == []


def test_page_renders_for_loopback_only(client):
    test_client, _ = client
    page = test_client.get("/", headers={"Host": "127.0.0.1:5001"})
    assert page.status_code == 200
    assert b"confirmDelete" in page.data and b"/media/2.mp4" in page.data
    assert test_client.get("/", headers={"Host": "evil.example:5001"}).status_code == 403
