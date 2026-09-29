"""Paging tests for query_all_tags: real totals (StashDB) and per-page counts (ThePornDB)."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stashdb_api  # noqa: E402

URL = "https://stashdb.example/graphql"


def ok(payload):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def make_tags(start, n):
    return [{"id": f"id{i}", "name": f"t{i}"} for i in range(start, start + n)]


def serve(handler):
    """urlopen side_effect: handler(page, per_page) -> (count, tags). Records pages."""
    pages = []

    def side_effect(req, *a, **kw):
        inp = json.loads(req.data.decode("utf-8"))["variables"]["input"]
        pages.append(inp["page"])
        count, tags = handler(inp["page"], inp["per_page"])
        return ok({"data": {"queryTags": {"count": count, "tags": tags}}})

    return side_effect, pages


@patch("stashdb_api.time.sleep")
@patch("stashdb_api.urllib.request.urlopen")
class TestQueryAllTagsPaging(unittest.TestCase):
    def test_count_is_page_length_like_tpdb(self, urlopen, _sleep):
        def handler(page, per_page):
            sizes = {1: 100, 2: 100, 3: 100, 4: 40}
            n = sizes.get(page, 0)
            return n, make_tags((page - 1) * 100, n)
        urlopen.side_effect, pages = serve(handler)
        tags = stashdb_api.query_all_tags(URL, "k", per_page=1000)
        self.assertEqual(len(tags), 340)
        self.assertEqual(len({t["id"] for t in tags}), 340)
        self.assertEqual(pages, [1, 2, 3, 4])

    def test_normal_total_count_like_stashdb(self, urlopen, _sleep):
        def handler(page, per_page):
            n = {1: 1000, 2: 1000, 3: 500}.get(page, 0)
            return 2500, make_tags((page - 1) * 1000, n)
        urlopen.side_effect, pages = serve(handler)
        tags = stashdb_api.query_all_tags(URL, "k", per_page=1000)
        self.assertEqual(len(tags), 2500)
        self.assertEqual(len(pages), 3)

    def test_repeated_page_stops(self, urlopen, _sleep):
        urlopen.side_effect, pages = serve(lambda p, pp: (100, make_tags(0, 100)))
        tags = stashdb_api.query_all_tags(URL, "k", per_page=1000)
        self.assertEqual(len(tags), 100)
        self.assertEqual(pages, [1, 2])

    def test_empty_first_page(self, urlopen, _sleep):
        urlopen.side_effect, pages = serve(lambda p, pp: (0, []))
        self.assertEqual(stashdb_api.query_all_tags(URL, "k", per_page=1000), [])
        self.assertEqual(pages, [1])


if __name__ == "__main__":
    unittest.main()
