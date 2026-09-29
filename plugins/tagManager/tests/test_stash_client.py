"""Tests for the standard-library client for the local Stash."""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stash_client
from stash_client import LocalStash, StashError

CONN = {"Scheme": "http", "Host": "0.0.0.0", "Port": 9999, "SessionCookie": {"Value": "abc"}}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    """Records each request and returns queued JSON responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None, context=None):
        self.requests.append({
            "url": req.full_url,
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": json.loads(req.data.decode("utf-8")),
        })
        return FakeResponse(json.dumps(self.responses.pop(0)).encode("utf-8"))


def run_with(responses, fn):
    fake = FakeUrlopen(responses)
    with patch.object(stash_client.urllib.request, "urlopen", fake):
        result = fn()
    return fake, result


class TestLocalStash(unittest.TestCase):
    def test_url_and_cookie_from_server_connection(self):
        fake, _ = run_with([{"data": {}}], lambda: LocalStash(CONN).call("query { x }"))
        self.assertEqual(fake.requests[0]["url"], "http://localhost:9999/graphql")
        self.assertEqual(fake.requests[0]["headers"]["cookie"], "session=abc")

    def test_api_key_header_preferred(self):
        fake, _ = run_with([{"data": {}}], lambda: LocalStash(CONN, api_key="k").call("query { x }"))
        headers = fake.requests[0]["headers"]
        self.assertEqual(headers["apikey"], "k")
        self.assertNotIn("cookie", headers)

    def test_graphql_errors_raise(self):
        with self.assertRaises(StashError):
            run_with([{"errors": [{"message": "boom"}]}], lambda: LocalStash(CONN).call("query { x }"))

    def test_find_all_tags_paginates(self):
        def page(n):
            return {"data": {"findTags": {"count": 2500, "tags": [{"id": str(i)} for i in range(n)]}}}

        fake, tags = run_with([page(1000), page(1000), page(500)],
                              lambda: LocalStash(CONN).find_all_tags(per_page=1000))
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(len(tags), 2500)

    def test_iter_scenes_respects_limit(self):
        def page(n):
            return {"data": {"findScenes": {"count": 1000, "scenes": [{"id": str(i)} for i in range(n)]}}}

        fake, scenes = run_with([page(100), page(100)],
                                lambda: list(LocalStash(CONN).iter_scenes_with_stash_id(
                                    "https://stashdb.org/graphql", per_page=100, limit=150)))
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(len(scenes), 150)

    def test_add_scene_tags_uses_add_mode(self):
        fake, _ = run_with([{"data": {"bulkSceneUpdate": []}}],
                           lambda: LocalStash(CONN).add_scene_tags("7", ["1", "2"]))
        body = fake.requests[0]["body"]
        self.assertIn("bulkSceneUpdate", body["query"])
        self.assertEqual(body["variables"]["input"],
                         {"ids": ["7"], "tag_ids": {"ids": ["1", "2"], "mode": "ADD"}})


if __name__ == "__main__":
    unittest.main()
