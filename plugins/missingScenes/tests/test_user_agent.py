"""Every outgoing request names the plugin: ThePornDB's Cloudflare answers Python's
default User-Agent with "Error 1010: Access denied" (HTTP 403)."""

import io
import json
import os
import re
import urllib.request

import stashbox_api
import theporndb_api

YML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "missingScenes.yml")


def plugin_version():
    with open(YML, encoding="utf-8") as f:
        return re.search(r"^version:\s*(\S+)", f.read(), re.MULTILINE).group(1)


class _Response(io.BytesIO):
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capture(monkeypatch, body):
    seen = []

    def fake_urlopen(req, *args, **kwargs):
        seen.append(req)
        return _Response(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_stashbox_requests_send_the_plugin_user_agent(monkeypatch):
    seen = capture(monkeypatch, {"data": {"ok": True}})
    stashbox_api.graphql_request_with_retry("https://theporndb.net/graphql", "{ ok }", api_key="k")
    assert seen and seen[0].get_header("User-agent") == f"stash-plugins-missingScenes/{plugin_version()}"


def test_theporndb_rest_requests_send_the_plugin_user_agent(monkeypatch):
    seen = capture(monkeypatch, {"data": []})
    theporndb_api.rest_request("k", "/scenes")
    assert seen and seen[0].get_header("User-agent") == f"stash-plugins-missingScenes/{plugin_version()}"
