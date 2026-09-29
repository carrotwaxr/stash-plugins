"""Tests for typed stash-box errors, User-Agent and paging (all mocked)."""
import http.client
import io
import json
import os
import re
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stashdb_api  # noqa: E402
from stashdb_api import StashDBAPIError  # noqa: E402

URL = "https://stashdb.example/graphql"


def ok(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def http_error(code, body=b""):
    return urllib.error.HTTPError(URL, code, "Err", {}, io.BytesIO(body))


def tags_page(n, total):
    return {"data": {"queryTags": {"count": total,
                                   "tags": [{"id": str(i), "name": f"t{i}"} for i in range(n)]}}}


def plugin_version():
    yml = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagManager.yml")
    with open(yml) as f:
        return re.search(r"^version:\s*(\S+)", f.read(), re.M).group(1)


@patch("stashdb_api.time.sleep")
@patch("stashdb_api.urllib.request.urlopen")
class TestErrors(unittest.TestCase):
    def test_user_agent_sent(self, urlopen, _sleep):
        urlopen.return_value = ok({"data": {"x": 1}})
        stashdb_api.graphql_request(URL, "q", api_key="k")
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_header("User-agent"), f"stash-plugins-tagManager/{plugin_version()}")

    def test_timeout_is_retried(self, urlopen, _sleep):
        urlopen.side_effect = [TimeoutError("timed out"), ok({"data": {"x": 1}})]
        self.assertEqual(stashdb_api.graphql_request(URL, "q"), {"x": 1})
        self.assertEqual(urlopen.call_count, 2)

    def test_connection_reset_becomes_retryable_api_error(self, urlopen, _sleep):
        urlopen.side_effect = ConnectionResetError("reset")
        with self.assertRaises(StashDBAPIError) as ctx:
            stashdb_api.graphql_request(URL, "q")
        self.assertTrue(ctx.exception.retryable)

    def test_dropped_connection_becomes_api_error(self, urlopen, _sleep):
        urlopen.side_effect = http.client.RemoteDisconnected("gone")
        with self.assertRaises(StashDBAPIError):
            stashdb_api.graphql_request(URL, "q")
        urlopen.side_effect = http.client.IncompleteRead(b"ab")
        with self.assertRaises(StashDBAPIError):
            stashdb_api.graphql_request(URL, "q")

    def test_401_is_auth_error(self, urlopen, _sleep):
        urlopen.side_effect = http_error(401)
        with self.assertRaises(StashDBAPIError) as cm:
            stashdb_api.graphql_request(URL, "q", api_key="k")
        self.assertEqual(cm.exception.status_code, 401)
        self.assertTrue(cm.exception.is_auth_error)

    def test_403_body_in_message(self, urlopen, _sleep):
        urlopen.side_effect = http_error(403, b"error code: 1010")
        with self.assertRaises(StashDBAPIError) as cm:
            stashdb_api.graphql_request(URL, "q", api_key="k")
        self.assertIn("1010", str(cm.exception))
        self.assertTrue(cm.exception.is_auth_error)

    def test_graphql_errors_without_data_raise(self, urlopen, _sleep):
        urlopen.return_value = ok({"errors": [{"message": "x"}], "data": None})
        with self.assertRaises(StashDBAPIError) as cm:
            stashdb_api.graphql_request(URL, "q", api_key="k")
        self.assertFalse(cm.exception.is_auth_error)

    def test_graphql_errors_with_data_returns_data(self, urlopen, _sleep):
        urlopen.return_value = ok({"errors": [{"message": "x"}], "data": {"a": 1}})
        self.assertEqual(stashdb_api.graphql_request(URL, "q", api_key="k"), {"a": 1})

    def test_unauthorized_graphql_error_is_auth(self, urlopen, _sleep):
        urlopen.return_value = ok({"errors": [{"message": "Not authorized"}], "data": None})
        with self.assertRaises(StashDBAPIError) as cm:
            stashdb_api.graphql_request(URL, "q", api_key="k")
        self.assertTrue(cm.exception.is_auth_error)

    def test_query_all_tags_failed_page_raises(self, urlopen, _sleep):
        urlopen.side_effect = [ok(tags_page(2, 4))] + [http_error(500)] * 10
        with self.assertRaises(StashDBAPIError):
            stashdb_api.query_all_tags(URL, "k", per_page=2)

    def test_query_all_tags_falls_back_to_100(self, urlopen, _sleep):
        urlopen.side_effect = [http_error(422), ok(tags_page(3, 3)), ok(tags_page(0, 0))]
        tags = stashdb_api.query_all_tags(URL, "k")
        self.assertEqual(len(tags), 3)
        sizes = [json.loads(c[0][0].data)["variables"]["input"]["per_page"]
                 for c in urlopen.call_args_list]
        self.assertEqual(sizes, [1000, 100, 100])

    def test_search_raises_on_error(self, urlopen, _sleep):
        urlopen.side_effect = http_error(500)
        with self.assertRaises(StashDBAPIError):
            stashdb_api.search_tags_by_name(URL, "k", "foo")

    def test_find_scene_by_id_none_only_when_missing(self, urlopen, _sleep):
        urlopen.return_value = ok({"data": {"findScene": None}})
        self.assertIsNone(stashdb_api.find_scene_by_id(URL, "k", "abc"))
        urlopen.side_effect = http_error(500)
        with self.assertRaises(StashDBAPIError):
            stashdb_api.find_scene_by_id(URL, "k", "abc")

    def test_find_scenes_by_fingerprints_raises(self, urlopen, _sleep):
        urlopen.side_effect = http_error(500)
        with self.assertRaises(StashDBAPIError):
            stashdb_api.find_scenes_by_fingerprints(
                URL, "k", [[{"hash": "h", "algorithm": "MD5"}]])


if __name__ == "__main__":
    unittest.main()
