"""Offline tests for the fetch seam, per-source status and each source's parser.

The fixtures in tests/fixtures/ are synthetic: hand-written copies of the markup each
site uses, trimmed to what the parsers read, with a made-up performer (Jane Example)
and made-up image paths on each site's real image hosts.
"""

import ast
import email.message
import http.server
import io
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

import image_search  # noqa: E402
from image_search import (  # noqa: E402
    SourceBlocked,
    SourceError,
    SourceHTTPError,
    SourceNotFound,
    SourceResult,
    SourceTimeout,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NAME = "Jane Example"

BABEPEDIA_URL = "https://www.babepedia.com/babe/Jane_Example"
FREEONES_LIST_URL = "https://www.freeones.com/jane-example/photos"
FREEONES_GALLERY_1 = "https://www.freeones.com/jane-example/photos/jane-example-in-the-garden"
FREEONES_GALLERY_2 = "https://www.freeones.com/jane-example/photos/jane-example-by-the-pool"
PORNPICS_INDEX_URL = "https://www.pornpics.com/pornstars/jane-example/"
ELITEBABES_INDEX_URL = "https://www.elitebabes.com/model/jane-example/"
BOOBPEDIA_URL = "https://www.boobpedia.com/boobs/Jane_Example"
JAVDATABASE_URL = "https://www.javdatabase.com/idols/jane-example/"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def pairs(results):
    """(image, thumbnail) for each result, in order."""
    return [(r["image"], r["thumbnail"]) for r in results]


def photos(results):
    """(image, thumbnail, width, height) for each result, in order."""
    return [(r["image"], r["thumbnail"], r["width"], r["height"]) for r in results]


def all_urls(results):
    return [url for r in results for url in (r["image"], r["thumbnail"])]


class FakeWeb:
    """Stands in for image_search._fetch: serves pages by URL and records each request.

    A route's value is the page text, an exception to raise, or a callable returning
    the page. Unknown URLs answer 404, as a real site would.
    """

    def __init__(self):
        self.routes = {}
        self.fallback = None  # callable(url) -> page or None
        self.requests = []
        self._lock = threading.Lock()

    def __call__(self, url, deadline, headers=None):
        with self._lock:
            self.requests.append((url, headers))
        page = self.routes.get(url)
        if page is None and self.fallback is not None:
            page = self.fallback(url)
        if page is None:
            raise SourceNotFound(url)
        if isinstance(page, BaseException):
            raise page
        if callable(page):
            return page()
        return page

    def urls(self):
        return [url for url, _ in self.requests]


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(image_search, "_fetch", fake)
    return fake


# --- PornPics helpers: build pages for any gallery ids from the fixtures ---

def pornpics_gallery_url(gid):
    return f"https://www.pornpics.com/galleries/{gid}/"


def pornpics_gallery(gid):
    return fixture("pornpics_gallery.html").replace("30000001", gid)


def pornpics_index(ids):
    """A PornPics performer page listing these gallery ids, in this order."""
    tile = (
        "<li class='thumbwook'><a class='rel-link' href='https://www.pornpics.com/galleries/"
        "jane-example-set-{gid}/' data-gid='{gid}'><img src='https://static.pornpics.com/style/img/1px.png'"
        " data-src='https://cdni.pornpics.com/460/1/2/{gid}/{gid}_001_ab12.jpg' /></a></li>\n"
    )
    return "<ul id='tiles'>\n" + "".join(tile.format(gid=gid) for gid in ids) + "</ul>\n"


def serve_pornpics(web, ids):
    web.routes[PORNPICS_INDEX_URL] = pornpics_index(ids)
    for gid in ids:
        web.routes[pornpics_gallery_url(gid)] = pornpics_gallery(gid)


def elitebabes_gallery(url):
    """The EliteBabes gallery fixture, re-labelled for a gallery URL.

    <slug>-<n>/ gets content id 5<n> (zero-padded to 6 digits); a slug without a
    number (the fixture's on-the-beach gallery) gets 540002.
    """
    number = re.search(r"-(\d+)/$", url)
    gid = number.group(1) if number else "40002"
    return fixture("elitebabes_gallery.html").replace("500001", "5" + gid.zfill(5))


# --- FreeOnes helpers ---

def freeones_gallery_2():
    """The FreeOnes gallery fixture with its photo paths moved, so it reads as another gallery."""
    page = fixture("freeones_gallery.html")
    return page.replace("/gg/hh/", "/jj/kk/").replace("\\/gg\\/hh\\/", "\\/jj\\/kk\\/")  # JSON-LD escapes "/"


def serve_freeones(web):
    web.routes[FREEONES_LIST_URL] = fixture("freeones_list.html")
    web.routes[FREEONES_GALLERY_1] = fixture("freeones_gallery.html")
    web.routes[FREEONES_GALLERY_2] = freeones_gallery_2()


def freeones_photo(n, width, height, folder="gg/hh"):
    """What search_freeones returns for photo n of the gallery fixture."""
    path = f"{folder}/fakePath0001{n}/photo-000{n}.jpg"
    return (
        f"https://thumbs.freeones.com/photo/fakeSigFull0{n}/1440x0/filters:quality(85)/{path}",
        f"https://thumbs.freeones.com/photo/fakeSigFit00{n}/fit-in/0x230/center/top/filters:upscale():quality(85)/{path}",
        width,
        height,
    )


# ---------------------------------------------------------------------------
# _fetch: the one place requests are made
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body=b"<html>ok</html>", status=200):
        self.status = status
        self.headers = email.message.Message()
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"", headers=None):
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://www.example.com/page", code, "error", message, io.BytesIO(body))


class FakeUrlopen:
    """Replaces urllib.request.urlopen and records (request, timeout).

    outcome is a response, an exception to raise, or a callable(url) returning either.
    """

    def __init__(self):
        self.calls = []
        self.outcome = FakeResponse()

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        outcome = self.outcome(request.full_url) if callable(self.outcome) else self.outcome
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def urlopen(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(image_search.urllib.request, "urlopen", fake)
    monkeypatch.setattr(image_search, "_now", lambda: 100.0)
    return fake


def test_fetch_is_the_only_place_requests_are_made():
    path = os.path.join(PLUGIN_DIR, "image_search.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    fetch = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_fetch"
    )
    lines = source.splitlines()
    outside = lines[:fetch.lineno - 1] + lines[fetch.end_lineno:]
    hits = [line for line in outside if re.search(r"\burlopen\(|\bRequest\(", line)]
    assert hits == []


def test_fetch_sends_the_shared_headers_with_a_current_browser_user_agent(urlopen):
    image_search._fetch("https://www.example.com/page", 150.0)
    request, _ = urlopen.calls[0]
    user_agent = request.get_header("User-agent")
    assert user_agent == image_search.HEADERS["User-Agent"]
    chrome = re.search(r"Chrome/(\d+)\.", user_agent)
    assert chrome and int(chrome.group(1)) >= 140, user_agent
    assert user_agent.startswith("Mozilla/5.0 (")
    assert request.get_header("Accept") == image_search.HEADERS["Accept"]
    assert request.get_header("Accept-language") == image_search.HEADERS["Accept-Language"]


def test_fetch_adds_extra_headers(urlopen):
    image_search._fetch("https://www.example.com/page", 150.0, headers={"Referer": "https://www.example.com/"})
    request, _ = urlopen.calls[0]
    assert request.get_header("Referer") == "https://www.example.com/"
    assert request.get_header("User-agent") == image_search.USER_AGENT


def test_fetch_returns_the_body_as_text(urlopen):
    urlopen.outcome = FakeResponse("<p>Jane Example ü</p>".encode("utf-8"))
    assert image_search._fetch("https://www.example.com/page", 150.0) == "<p>Jane Example ü</p>"


@pytest.mark.parametrize("deadline,expected", [(103.5, 3.5), (110.0, 10), (500.0, 10)])
def test_fetch_socket_timeout_is_the_smaller_of_10s_and_the_time_left(urlopen, deadline, expected):
    image_search._fetch("https://www.example.com/page", deadline)
    _, timeout = urlopen.calls[0]
    assert timeout == pytest.approx(expected)


@pytest.mark.parametrize("deadline", [100.0, 99.0])
def test_fetch_raises_timeout_once_the_deadline_has_passed(urlopen, deadline):
    with pytest.raises(SourceTimeout):
        image_search._fetch("https://www.example.com/page", deadline)
    assert urlopen.calls == []


@pytest.mark.parametrize("error", [
    socket.timeout("timed out"),
    TimeoutError("timed out"),
    urllib.error.URLError(socket.timeout("timed out")),
])
def test_fetch_turns_socket_timeouts_into_source_timeout(urlopen, error):
    urlopen.outcome = error
    with pytest.raises(SourceTimeout):
        image_search._fetch("https://www.example.com/page", 150.0)


def test_fetch_times_out_when_the_deadline_passes_mid_download(urlopen, monkeypatch):
    calls = []

    def clock():  # the deadline check before the request passes; every later one fails
        calls.append(1)
        return 100.0 if len(calls) == 1 else 200.0

    monkeypatch.setattr(image_search, "_now", clock)
    urlopen.outcome = FakeResponse(b"x" * 200000)
    with pytest.raises(SourceTimeout):
        image_search._fetch("https://www.example.com/page", 150.0)


class TrickleHandler(http.server.BaseHTTPRequestHandler):
    """Serves /slow at 1 KB every 0.2 s (40 KB in all) and /fast (200 KB) at once."""

    def do_GET(self):
        slow = self.path == "/slow"
        size = 40 * 1024 if slow else 200 * 1024
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        try:
            if not slow:
                self.wfile.write(b"x" * size)
                return
            for _ in range(size // 1024):
                self.wfile.write(b"x" * 1024)
                self.wfile.flush()
                time.sleep(0.2)
        except OSError:  # the client hung up
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TrickleHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_stops_a_slow_download_at_the_deadline(local_server):
    # read() would wait for a full buffer (64 KB), long past the deadline
    started = time.monotonic()
    with pytest.raises(SourceTimeout, match="did not finish sending in time"):
        image_search._fetch(local_server + "/slow", image_search._now() + 1.0)
    assert time.monotonic() - started < 2.5


def test_fetch_reads_a_whole_body_in_chunks(local_server):
    assert image_search._fetch(local_server + "/fast", image_search._now() + 10) == "x" * 200 * 1024


@pytest.mark.parametrize("code", [403, 429])
def test_fetch_raises_blocked_on_403_and_429(urlopen, code):
    urlopen.outcome = http_error(code)
    with pytest.raises(SourceBlocked) as caught:
        image_search._fetch("https://www.example.com/page", 150.0)
    assert str(code) in str(caught.value)


# A trimmed Cloudflare "checking your browser" page, as sent with HTTP 403 or 503
CLOUDFLARE_CHALLENGE = (
    b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    b"<script>window._cf_chl_opt={cType:'managed'};</script>"
    b"<script src='/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=0000'></script></body></html>"
)


@pytest.mark.parametrize("outcome", [
    http_error(503, CLOUDFLARE_CHALLENGE),
    http_error(403, CLOUDFLARE_CHALLENGE),
    http_error(403, b"", headers={"cf-mitigated": "challenge"}),
    http_error(503, b"", headers={"cf-mitigated": "challenge"}),
])
def test_fetch_raises_blocked_on_a_cloudflare_challenge_page(urlopen, outcome):
    urlopen.outcome = outcome
    with pytest.raises(SourceBlocked) as caught:
        image_search._fetch("https://www.example.com/page", 150.0)
    assert "Cloudflare" in str(caught.value)
    assert str(outcome.code) in str(caught.value)


@pytest.mark.parametrize("body", [
    b"<html><head><title>Just a moment...</title></head><body>Loading the gallery</body></html>",
    b"<p>Her catchphrase is \"Just a moment...\"</p><a href='/wiki/cf-chl'>cf-chl</a>",
    CLOUDFLARE_CHALLENGE,
])
def test_fetch_returns_a_200_page_whatever_words_it_contains(urlopen, body):
    # Cloudflare sends its challenge with HTTP 403 or 503, never 200
    urlopen.outcome = FakeResponse(body)
    assert image_search._fetch("https://www.example.com/page", 150.0) == body.decode()


def test_boobpedia_bio_that_says_just_a_moment_is_searched(urlopen):
    page = fixture("boobpedia.html").replace(
        '<ul class="gallery', '<p>Her catchphrase is "Just a moment..." (see cf-chl).</p>\n<ul class="gallery')
    urlopen.outcome = lambda url: FakeResponse(page.encode()) if url == BOOBPEDIA_URL else http_error(404)
    outcome = image_search.search_single_source("boobpedia", NAME, NAME)
    assert outcome["status"] == "ok"
    assert len(outcome["results"]) == 2


def test_fetch_raises_not_found_on_404(urlopen):
    urlopen.outcome = http_error(404)
    with pytest.raises(SourceNotFound) as caught:
        image_search._fetch("https://www.example.com/page", 150.0)
    assert isinstance(caught.value, SourceHTTPError)
    assert caught.value.status == 404


@pytest.mark.parametrize("outcome,status", [
    (http_error(500), 500),
    (http_error(502, b"<html>Bad gateway</html>"), 502),
    (http_error(503, b"<html><title>Service Unavailable</title>Down for maintenance</html>"), 503),
    (FakeResponse(b"", status=204), 204),
])
def test_fetch_raises_http_error_on_other_statuses(urlopen, outcome, status):
    urlopen.outcome = outcome
    with pytest.raises(SourceHTTPError) as caught:
        image_search._fetch("https://www.example.com/page", 150.0)
    assert caught.value.status == status
    assert not isinstance(caught.value, (SourceNotFound, SourceBlocked))
    assert str(status) in str(caught.value)


@pytest.mark.parametrize("error", [
    urllib.error.URLError("[Errno -2] Name or service not known"),
    ConnectionResetError("reset by peer"),
])
def test_fetch_raises_source_error_on_network_failures(urlopen, error):
    urlopen.outcome = error
    with pytest.raises(SourceError) as caught:
        image_search._fetch("https://www.example.com/page", 150.0)
    assert type(caught.value) is SourceError
    assert "www.example.com" in str(caught.value)


def test_source_budget_is_25_seconds():
    assert image_search.SOURCE_BUDGET_SECONDS == 25


# ---------------------------------------------------------------------------
# Each source parses its fixture
# ---------------------------------------------------------------------------

def test_babepedia_fixture(web):
    web.routes[BABEPEDIA_URL] = fixture("babepedia.html")
    found = image_search.search_babepedia(NAME)
    assert isinstance(found, SourceResult)
    assert pairs(found) == [
        ("https://www.babepedia.com/pics/Jane%20Example.jpg",
         "https://www.babepedia.com/pics/Jane%20Example_thumb3.jpg"),
        ("https://www.babepedia.com/pics/Jane%20Example2.jpg",
         "https://www.babepedia.com/pics/Jane%20Example2_thumb3.jpg"),
        ("https://www.babepedia.com/galleries/ExampleStudio-JaneExampleInTheGarden/05.jpg",
         "https://www.babepedia.com/galleries-thumbs/ExampleStudio-JaneExampleInTheGarden/05.jpg"),
        ("https://www.babepedia.com/galleries/ExampleStudio-JaneExampleByThePool/04.jpg",
         "https://www.babepedia.com/galleries-thumbs/ExampleStudio-JaneExampleByThePool/04.jpg"),
    ]
    assert {r["source"] for r in found} == {"Babepedia"}
    assert found[0]["title"] == "Jane Example - Babepedia"
    assert not found.partial and found.warnings == []
    assert web.urls() == [BABEPEDIA_URL]


def freeones_gallery_photos(folder):
    """The photos of one gallery fixture: photo 4 is listed only as a square crop, so it is skipped."""
    return [
        freeones_photo(1, 2336, 3504, folder),
        freeones_photo(2, 2336, 3504, folder),
        freeones_photo(3, 3504, 2336, folder),
    ]


# Gallery 1 (in the garden) comes first on the list page, then gallery 2 (by the pool)
FREEONES_EXPECTED = freeones_gallery_photos("gg/hh") + freeones_gallery_photos("jj/kk")


def test_freeones_fixture(web):
    serve_freeones(web)
    found = image_search.search_freeones(NAME)
    # Gallery links are deduplicated and kept in page order
    assert web.urls()[0] == FREEONES_LIST_URL
    assert sorted(web.urls()[1:]) == sorted([FREEONES_GALLERY_1, FREEONES_GALLERY_2])
    assert photos(found) == FREEONES_EXPECTED
    assert {r["source"] for r in found} == {"FreeOnes"}
    assert found[0]["title"] == "Jane Example - FreeOnes"
    assert not found.partial


def test_freeones_photos_come_from_the_json_ld_unescaped(web):
    serve_freeones(web)
    found = image_search.search_freeones(NAME)
    for image, thumbnail, width, height in photos(found):
        assert "\\" not in image + thumbnail
        assert "/1440x0/" in image  # the full photo, resized only to 1440px wide
        assert "/fit-in/0x230/" in thumbnail  # small, but not cropped
        assert (width, height) in ((2336, 3504), (3504, 2336))  # the original size


def test_freeones_skips_a_photo_listed_only_as_a_square_crop(web):
    serve_freeones(web)
    found = image_search.search_freeones(NAME)
    assert "fakePath00014" in fixture("freeones_gallery.html")
    assert not any("fakePath00014" in url for url in all_urls(found))
    assert not any(image_search._is_freeones_crop(r["image"]) for r in found)


def test_freeones_ignores_the_images_around_the_gallery(web):
    serve_freeones(web)
    found = image_search.search_freeones(NAME)
    # Not returned: the header crop, the square crops the page shows, the sponsor banner,
    # the related gallery (another performer) and its srcset, the publisher logo
    for marker in ("fakePathHead1", "fakeSigCrop", "fakePathSpon1", "fakePathRel01", "logo"):
        assert not any(marker in url for url in all_urls(found)), marker
    # No match ever runs past a URL into the rest of a srcset
    for url in all_urls(found):
        assert re.fullmatch(r"https://thumbs\.freeones\.com/\S+\.jpg", url) and "," not in url, url


@pytest.mark.parametrize("url,crop", [
    ("https://thumbs.freeones.com/photo/s/350x350/center/middle/filters:upscale():quality(85)/a/b/c/p.jpg", True),
    ("https://thumbs.freeones.com/photo/s/290x100:744x554/350x350/center/middle/filters:upscale()/a/p.jpg", True),
    ("https://thumbs.freeones.com/photo/s/290x100:744x554/1440x0/filters:quality(85)/a/p.jpg", True),
    ("https://thumbs.freeones.com/photo/s/1440x0/filters:quality(85)/a/b/c/p.jpg", False),
    ("https://thumbs.freeones.com/photo/s/0x1440/filters:quality(85)/a/b/c/p.jpg", False),
    ("https://thumbs.freeones.com/photo/s/fit-in/0x230/center/top/filters:upscale():quality(85)/a/p.jpg", False),
    ("https://img.freeones.com/photos/a/b/p.jpg", False),
])
def test_freeones_crop_urls(url, crop):
    assert image_search._is_freeones_crop(url) is crop


def test_freeones_gallery_without_usable_json_ld_gives_nothing_from_that_page(web):
    serve_freeones(web)
    # Only the page's square crops, and a JSON-LD block that does not parse
    web.routes[FREEONES_GALLERY_2] = (
        '<script type="application/ld+json">{"@type":"ImageGallery","associatedMedia":[</script>\n'
        '<img src="https://thumbs.freeones.com/photo/x/350x350/center/middle/filters:upscale()/jj/kk/p.jpg">\n'
    )
    found = image_search.search_freeones(NAME)
    assert photos(found) == freeones_gallery_photos("gg/hh")
    assert not found.partial


def freeones_json_ld(page):
    """The gallery fixture's JSON-LD block, parsed."""
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))


def with_json_ld(page, data):
    """page with its JSON-LD block replaced by data."""
    return re.sub(r'(<script type="application/ld\+json">).*?(</script>)',
                  lambda m: m.group(1) + json.dumps(data) + m.group(2), page, flags=re.S)


def test_freeones_json_ld_gallery_inside_a_graph(web):
    serve_freeones(web)
    gallery = freeones_json_ld(fixture("freeones_gallery.html"))
    del gallery["@context"]
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage", "name": "x"}, gallery]}
    web.routes[FREEONES_GALLERY_1] = with_json_ld(fixture("freeones_gallery.html"), graph)
    found = image_search.search_freeones(NAME)
    assert photos(found) == FREEONES_EXPECTED


def test_freeones_json_ld_type_as_a_list(web):
    serve_freeones(web)
    gallery = freeones_json_ld(fixture("freeones_gallery.html"))
    gallery["@type"] = ["ImageGallery", "CreativeWork"]
    web.routes[FREEONES_GALLERY_1] = with_json_ld(fixture("freeones_gallery.html"), gallery)
    found = image_search.search_freeones(NAME)
    assert photos(found) == FREEONES_EXPECTED


def test_freeones_gallery_without_json_ld_uses_the_unlocked_photo_links(web):
    serve_freeones(web)
    page = re.sub(r'<script type="application/ld\+json">.*?</script>', "", fixture("freeones_gallery.html"), flags=re.S)
    web.routes[FREEONES_GALLERY_1] = page
    found = image_search.search_freeones(NAME)
    # Photos 1 and 2 are linked full size (data-size is the linked copy's size); photo 3 is locked
    linked = [freeones_photo(n, 1440, 2160)[0] for n in (1, 2)]
    assert photos(found)[:2] == [(url, url, 1440, 2160) for url in linked]
    assert photos(found)[2:] == freeones_gallery_photos("jj/kk")
    assert not found.partial


def test_freeones_photo_links_skip_crops_and_other_links():
    page = (
        '<a data-id="1" href="https://thumbs.freeones.com/photo/s/350x350/center/middle/a/p.jpg" data-size="350x350">x</a>\n'
        '<a data-id="2" data-type="video" href="https://thumbs.freeones.com/photo/v/1440x0/a/v.jpg" data-size="1440x810">x</a>\n'
        '<a href="https://thumbs.freeones.com/photo/n/1440x0/a/n.jpg" data-size="1440x960">no data-id</a>\n'
        '<a data-size="1440x960" href="https://thumbs.freeones.com/photo/q/1440x0/a/q.jpg?x=1&amp;y=2" data-id="3">x</a>\n'
        '<a data-id="4" href="https://thumbs.freeones.com/photo/r/1440x0/a/r.jpg">no size</a>\n'
    )
    assert image_search._freeones_gallery_photos(page) == [
        ("https://thumbs.freeones.com/photo/q/1440x0/a/q.jpg?x=1&y=2",
         "https://thumbs.freeones.com/photo/q/1440x0/a/q.jpg?x=1&y=2", 1440, 960),
        ("https://thumbs.freeones.com/photo/r/1440x0/a/r.jpg", "https://thumbs.freeones.com/photo/r/1440x0/a/r.jpg", 0, 0),
    ]


def test_freeones_photo_without_a_thumbnail_uses_the_image():
    page = (
        '<script type="application/ld+json">{"@type":"ImageGallery","associatedMedia":['
        '{"@type":"ImageObject","width":"1200","height":800,'
        '"url":"https:\\/\\/thumbs.freeones.com\\/photo\\/s\\/1440x0\\/filters:quality(85)\\/a\\/p.jpg"},'
        '{"@type":"ImageObject","url":"https:\\/\\/thumbs.freeones.com\\/photo\\/t\\/1440x0\\/a\\/q.jpg",'
        '"thumbnailUrl":"https:\\/\\/thumbs.freeones.com\\/photo\\/u\\/fit-in\\/0x230\\/a\\/q.jpg","width":null}'
        ']}</script>'
    )
    assert image_search._freeones_gallery_photos(page) == [
        ("https://thumbs.freeones.com/photo/s/1440x0/filters:quality(85)/a/p.jpg",
         "https://thumbs.freeones.com/photo/s/1440x0/filters:quality(85)/a/p.jpg", 1200, 800),
        ("https://thumbs.freeones.com/photo/t/1440x0/a/q.jpg",
         "https://thumbs.freeones.com/photo/u/fit-in/0x230/a/q.jpg", 0, 0),
    ]


def test_pornpics_fixture(web):
    web.routes[PORNPICS_INDEX_URL] = fixture("pornpics_index.html")
    for gid in ("30000001", "30000002", "30000003"):
        web.routes[pornpics_gallery_url(gid)] = pornpics_gallery(gid)
    found = image_search.search_pornpics(NAME)
    expected = [("https://cdni.pornpics.com/models/j/jane_example.jpg",
                 "https://cdni.pornpics.com/models/j/jane_example.jpg")]
    for gid in ("30000001", "30000002", "30000003"):  # sorted, not page order
        for photo in ("001_aa11", "002_bb22"):
            expected.append((f"https://cdni.pornpics.com/1280/1/2/{gid}/{gid}_{photo}.jpg",
                             f"https://cdni.pornpics.com/460/1/2/{gid}/{gid}_{photo}.jpg"))
    assert pairs(found) == expected
    assert found[0]["title"] == "Jane Example - PornPics Profile"
    assert not found.partial


# The fixture model page's galleries, sorted, and the content id each one's photos live under
ELITEBABES_GALLERIES = [
    ("https://www.elitebabes.com/jane-example-by-the-pool-40003/", "540003"),
    ("https://www.elitebabes.com/jane-example-in-the-garden-40001/", "540001"),
    ("https://www.elitebabes.com/jane-example-on-the-beach/", "540002"),
]


def elitebabes_photos(content):
    """What search_elitebabes returns for one gallery fixture: its two photos, with their sizes."""
    base = f"https://cdn.elitebabes.com/content/{content}/"
    return [
        (base + "0001-01.jpg", base + "0001-01_w400.jpg", 801, 1200),
        (base + "0001-02.jpg", base + "0001-02_w400.jpg", 1200, 801),
    ]


def serve_elitebabes(web):
    web.routes[ELITEBABES_INDEX_URL] = fixture("elitebabes_index.html")
    web.fallback = lambda url: elitebabes_gallery(url) if url != ELITEBABES_INDEX_URL else None


def test_elitebabes_fixture(web):
    serve_elitebabes(web)
    found = image_search.search_elitebabes(NAME)
    assert sorted(web.urls()[1:]) == [url for url, _ in ELITEBABES_GALLERIES]
    expected = [photo for _, content in ELITEBABES_GALLERIES for photo in elitebabes_photos(content)]
    assert photos(found) == expected
    assert {r["source"] for r in found} == {"EliteBabes"}
    assert found[0]["title"] == "Jane Example - EliteBabes"


def test_elitebabes_finds_the_galleries_by_their_tiles(web):
    # Today's gallery links carry no class attribute, and most but not all slugs end in a number.
    # Tag, sort and model links, and the other models' slider, are not galleries.
    serve_elitebabes(web)
    image_search.search_elitebabes(NAME)
    assert sorted(web.urls()) == sorted([ELITEBABES_INDEX_URL] + [url for url, _ in ELITEBABES_GALLERIES])


def test_elitebabes_returns_only_the_gallerys_own_photos(web):
    url, content = ELITEBABES_GALLERIES[1]
    web.routes[ELITEBABES_INDEX_URL] = f'<figure><a href="{url}" title="Jane Example in the garden">x</a></figure>'
    web.routes[url] = elitebabes_gallery(url)
    found = image_search.search_elitebabes(NAME)
    assert photos(found) == elitebabes_photos(content)
    # Not the related galleries' previews and photo link, the collections, or /content/lists/
    page = elitebabes_gallery(url)
    for other in ("/590001/", "/590002/", "/590003/", "/lists/"):
        assert other in page
        assert not any(other in u for u in all_urls(found)), other


def test_boobpedia_fixture(web):
    web.routes[BOOBPEDIA_URL] = fixture("boobpedia.html")
    found = image_search.search_boobpedia(NAME)
    assert pairs(found) == [
        ("https://www.boobpedia.com/wiki/images/a/ab/Jane_Example_01.jpg",
         "https://www.boobpedia.com/wiki/images/thumb/a/ab/Jane_Example_01.jpg/240px-Jane_Example_01.jpg"),
        ("https://www.boobpedia.com/wiki/images/c/cd/Jane_Example_02.jpg",
         "https://www.boobpedia.com/wiki/images/thumb/c/cd/Jane_Example_02.jpg/120px-Jane_Example_02.jpg"),
    ]


def test_javdatabase_fixture_reads_pages_until_a_404(web):
    web.routes[JAVDATABASE_URL] = fixture("javdatabase_1.html")
    web.routes[JAVDATABASE_URL + "?ipage=2"] = fixture("javdatabase_2.html")
    found = image_search.search_javdatabase(NAME)
    # Page 3 is a 404: the end of the pages, not a failure
    assert web.urls() == [JAVDATABASE_URL, JAVDATABASE_URL + "?ipage=2", JAVDATABASE_URL + "?ipage=3"]
    # There is no /covers/full/ copy, so each cover is the thumbnail the page links
    cover = "https://www.javdatabase.com/covers/thumb/ex/exmp0000{}ps.webp"
    assert pairs(found) == [
        ("https://www.javdatabase.com/idolimages/full/jane-example.webp",
         "https://www.javdatabase.com/idolimages/thumb/jane-example.webp"),
        (cover.format(1), cover.format(1)),
        (cover.format(2), cover.format(2)),
        (cover.format(3), cover.format(3)),
    ]
    assert [r["title"] for r in found] == ["Jane Example - JavDatabase"] + ["Jane Example - JavDatabase Cover"] * 3
    assert not found.partial and found.warnings == []


def test_javdatabase_skips_other_idols_and_ads(web):
    web.routes[JAVDATABASE_URL] = fixture("javdatabase_1.html")
    found = image_search.search_javdatabase(NAME, max_pages=1)
    page = fixture("javdatabase_1.html")
    # Related idols (a slug that merely starts with hers too), and the sponsored ad cards
    for other in ("other-idol", "jane-example-2", "/vertical/", "adxx00001ps"):
        assert other in page
        assert not any(other in url for url in all_urls(found)), other
    assert not any("/covers/full/" in url for url in all_urls(found))
    assert len(found) == 3  # her picture and the two covers


def test_javdatabase_keeps_the_idols_own_picture_inside_a_sponsor_link(web):
    # The portrait links to a sponsor, but it is her picture: keep it even without the og:image tags
    page = "\n".join(line for line in fixture("javdatabase_1.html").splitlines() if "og:image" not in line)
    web.routes[JAVDATABASE_URL] = page
    found = image_search.search_javdatabase(NAME, max_pages=1)
    assert found[0]["image"] == "https://www.javdatabase.com/idolimages/full/jane-example.webp"


def test_javdatabase_own_idol_is_the_one_the_page_is_for(web):
    # The site may redirect to another slug for her; the canonical link names the page's idol
    web.routes[JAVDATABASE_URL] = (
        '<link rel="canonical" href="https://www.javdatabase.com/idols/jane-example-jp/">\n'
        '<img src="https://www.javdatabase.com/idolimages/full/jane-example-jp.webp" alt="Jane Example">\n'
        '<img src="https://www.javdatabase.com/idolimages/thumb/jane-example.webp" alt="Someone else">\n'
    )
    found = image_search.search_javdatabase(NAME, max_pages=1)
    assert pairs(found) == [("https://www.javdatabase.com/idolimages/full/jane-example-jp.webp",
                             "https://www.javdatabase.com/idolimages/thumb/jane-example-jp.webp")]


def ddg_routes(web, vqd_page=None, api=None):
    def route(url):
        if url.startswith("https://duckduckgo.com/i.js?"):
            return api if api is not None else fixture("duckduckgo.json")
        if url.startswith("https://duckduckgo.com/?"):
            return vqd_page if vqd_page is not None else fixture("duckduckgo_vqd.html")
        return None
    web.fallback = route


def test_duckduckgo_fixture(web):
    ddg_routes(web)
    found = image_search.search_duckduckgo_images("Jane Example pornstar")
    token_url, api_url = web.urls()
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(token_url).query)["q"] == ["Jane Example pornstar"]
    api = urllib.parse.parse_qs(urllib.parse.urlsplit(api_url).query)
    assert api["vqd"] == ["4-11112222333344445555666677778888"]
    assert api["p"] == ["-1"]
    api_headers = web.requests[1][1]
    assert api_headers["Referer"] == token_url
    # The 200x150 image and the result without an image are skipped
    assert pairs(found) == [
        ("https://images.example.com/photos/jane-example-1.jpg", "https://tse1.explicit.bing.net/th/id/OIP.fakeThumbnail1"),
        ("https://images.example.org/jane-example-3.jpg", "https://tse3.explicit.bing.net/th/id/OIP.fakeThumbnail3"),
    ]
    assert found[0]["title"] == "Jane Example & friends"
    assert (found[0]["width"], found[0]["height"]) == (1280, 1920)


def test_duckduckgo_result_titled_just_a_moment_is_returned(urlopen, sleeps):
    data = json.loads(fixture("duckduckgo.json"))
    data["results"][0]["title"] = "Just a moment..."
    data["results"][2]["title"] = "cf-chl"

    def route(url):
        if url.startswith("https://duckduckgo.com/i.js?"):
            return FakeResponse(json.dumps(data).encode())
        if url.startswith("https://duckduckgo.com/?"):
            return FakeResponse(fixture("duckduckgo_vqd.html").encode())
        return http_error(404)

    urlopen.outcome = route
    outcome = image_search.search_single_source("duckduckgo", NAME, "Jane Example pornstar")
    assert outcome["status"] == "ok"
    assert [r["title"] for r in outcome["results"]] == ["Just a moment...", "cf-chl"]
    assert sleeps == []
    assert len(urlopen.calls) == 2


RATE_LIMITED = "DuckDuckGo rate-limited this search; try again later"


@pytest.fixture
def sleeps(monkeypatch):
    """Records time.sleep calls instead of waiting."""
    calls = []
    monkeypatch.setattr(image_search.time, "sleep", calls.append)
    return calls


def ddg_sequence(web, token_pages, api_pages):
    """Serve DuckDuckGo's token page and API with the n-th answer for the n-th request.

    An answer that is an exception is raised.
    """
    counts = {"token": 0, "api": 0}

    def route(url):
        if url.startswith("https://duckduckgo.com/i.js?"):
            kind, pages = "api", api_pages
        elif url.startswith("https://duckduckgo.com/?"):
            kind, pages = "token", token_pages
        else:
            return None
        page = pages[min(counts[kind], len(pages) - 1)]
        counts[kind] += 1
        return page
    web.fallback = route
    return counts


def ddg_kinds(web):
    return ["api" if u.startswith("https://duckduckgo.com/i.js?") else "token" for u in web.urls()]


def test_duckduckgo_without_a_token_retries_once_then_is_blocked(web, sleeps):
    ddg_routes(web, vqd_page="<html><body>Sorry</body></html>")
    with pytest.raises(SourceBlocked, match=RATE_LIMITED):
        image_search.search_duckduckgo_images("Jane Example pornstar")
    assert sleeps == [2]
    assert ddg_kinds(web) == ["token", "token"]


def test_duckduckgo_retries_after_a_missing_token(web, sleeps):
    ddg_sequence(web, ["<html>Sorry</html>", fixture("duckduckgo_vqd.html")], [fixture("duckduckgo.json")])
    found = image_search.search_duckduckgo_images("Jane Example pornstar")
    assert len(found) == 2
    assert sleeps == [2]
    assert ddg_kinds(web) == ["token", "token", "api"]


def test_duckduckgo_retries_after_a_403_on_the_token_page(web, sleeps):
    ddg_sequence(web, [SourceBlocked("duckduckgo.com blocked the request (HTTP 403)"), fixture("duckduckgo_vqd.html")],
                 [fixture("duckduckgo.json")])
    assert len(image_search.search_duckduckgo_images("Jane Example pornstar")) == 2
    assert sleeps == [2]


def test_duckduckgo_403_on_the_api_gets_a_fresh_token_and_one_retry(web, sleeps):
    ddg_sequence(web, [fixture("duckduckgo_vqd.html")],
                 [SourceBlocked("duckduckgo.com blocked the request (HTTP 403)"), fixture("duckduckgo.json")])
    assert len(image_search.search_duckduckgo_images("Jane Example pornstar")) == 2
    assert sleeps == [2]
    assert ddg_kinds(web) == ["token", "api", "token", "api"]


def test_duckduckgo_a_second_403_is_rate_limited(web, sleeps):
    ddg_sequence(web, [fixture("duckduckgo_vqd.html")], [SourceBlocked("HTTP 403")])
    with pytest.raises(SourceBlocked, match=RATE_LIMITED):
        image_search.search_duckduckgo_images("Jane Example pornstar")
    assert sleeps == [2]
    assert ddg_kinds(web) == ["token", "api", "token", "api"]


def test_duckduckgo_does_not_retry_a_timeout(web, sleeps):
    ddg_sequence(web, [SourceTimeout("duckduckgo.com did not answer in time")], [""])
    with pytest.raises(SourceTimeout):
        image_search.search_duckduckgo_images("Jane Example pornstar")
    assert sleeps == []
    assert ddg_kinds(web) == ["token"]


class Clock:
    """A fake _now: requests and sleeps move it forward."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(web, monkeypatch):
    """A fake clock, and a fake _fetch that runs out of time at the deadline as the real one does."""
    fake = Clock()
    monkeypatch.setattr(image_search, "_now", fake)
    monkeypatch.setattr(image_search.time, "sleep", fake.sleep)

    def fetch(url, deadline, headers=None):
        if fake() >= deadline:
            raise SourceTimeout(f"Ran out of time before fetching {image_search._host(url)}")
        return web(url, deadline, headers)

    monkeypatch.setattr(image_search, "_fetch", fetch)
    return fake


def slow_block(clock, seconds):
    """A token page that takes this long to answer, without a token."""
    def page():
        clock.now += seconds
        return "<html>Sorry</html>"
    return page


def test_duckduckgo_block_late_in_the_budget_is_rate_limited_not_a_timeout(web, clock):
    # Blocked with 1 s of the budget left: waiting 2 s to retry would only run out of time
    ddg_sequence(web, [slow_block(clock, 24), fixture("duckduckgo_vqd.html")], [fixture("duckduckgo.json")])
    deadline = clock() + image_search.SOURCE_BUDGET_SECONDS
    outcome = image_search.search_single_source("duckduckgo", NAME, "Jane Example pornstar")
    assert outcome == {"results": [], "status": "blocked", "error": RATE_LIMITED}
    assert clock.sleeps == []
    assert ddg_kinds(web) == ["token"]
    assert clock() <= deadline


@pytest.mark.parametrize("taken,retried", [(19, True), (21, False)])
def test_duckduckgo_retries_only_with_time_for_the_wait_and_a_search(web, clock, taken, retried):
    # A retry needs the 2 s wait plus 3 s for its requests: 6 s left is enough, 4 s is not
    ddg_sequence(web, [slow_block(clock, taken), fixture("duckduckgo_vqd.html")], [fixture("duckduckgo.json")])
    outcome = image_search.search_single_source("duckduckgo", NAME, "Jane Example pornstar")
    if retried:
        assert outcome["status"] == "ok" and len(outcome["results"]) == 2
        assert clock.sleeps == [2]
    else:
        assert outcome["status"] == "blocked" and outcome["error"] == RATE_LIMITED
        assert clock.sleeps == []


def test_duckduckgo_http_202_is_rate_limiting_and_retried(web, sleeps):
    ddg_sequence(web, [fixture("duckduckgo_vqd.html")],
                 [SourceHTTPError(202, "https://duckduckgo.com/i.js"), fixture("duckduckgo.json")])
    assert len(image_search.search_duckduckgo_images("Jane Example pornstar")) == 2
    assert sleeps == [2]
    assert ddg_kinds(web) == ["token", "api", "token", "api"]


def test_duckduckgo_a_second_202_is_rate_limited(urlopen, sleeps):
    def route(url):
        if url.startswith("https://duckduckgo.com/i.js?"):
            return FakeResponse(b"", status=202)
        if url.startswith("https://duckduckgo.com/?"):
            return FakeResponse(fixture("duckduckgo_vqd.html").encode())
        return http_error(404)

    urlopen.outcome = route
    outcome = image_search.search_single_source("duckduckgo", NAME, "Jane Example pornstar")
    assert outcome == {"results": [], "status": "blocked", "error": RATE_LIMITED}
    assert sleeps == [2]
    assert len(urlopen.calls) == 4


def test_duckduckgo_unreadable_reply_is_blocked_not_scraped(web, sleeps):
    ddg_routes(web, api="<html>not json \"image\":\"https://x.example/a.jpg\"</html>")
    with pytest.raises(SourceBlocked, match=RATE_LIMITED):
        image_search.search_duckduckgo_images("Jane Example pornstar")


def test_duckduckgo_html_page_images_are_not_scraped(web, sleeps):
    page = '<html>"image":"https://x.example/a.jpg","thumbnail":"https://x.example/t.jpg"</html>'
    ddg_routes(web, vqd_page=page)
    with pytest.raises(SourceBlocked):
        image_search.search_duckduckgo_images("Jane Example pornstar")


# ---------------------------------------------------------------------------
# Not found, failed galleries and failed index pages
# ---------------------------------------------------------------------------

SITE_SOURCES = ["babepedia", "freeones", "pornpics", "elitebabes", "boobpedia", "javdatabase"]
SCRAPERS = {
    "babepedia": image_search.search_babepedia,
    "freeones": image_search.search_freeones,
    "pornpics": image_search.search_pornpics,
    "elitebabes": image_search.search_elitebabes,
    "boobpedia": image_search.search_boobpedia,
    "javdatabase": image_search.search_javdatabase,
}


@pytest.mark.parametrize("source", SITE_SOURCES)
def test_a_404_on_the_performer_page_is_not_found_not_an_error(web, source):
    found = SCRAPERS[source](NAME)
    assert list(found) == [] and not found.partial and found.warnings == []
    outcome = image_search.search_single_source(source, NAME, NAME)
    assert outcome == {"results": [], "status": "empty"}


def test_a_failed_gallery_is_skipped_and_the_source_is_partial(web):
    serve_pornpics(web, ["30000001", "30000002", "30000003"])
    web.routes[pornpics_gallery_url("30000002")] = SourceHTTPError(500, pornpics_gallery_url("30000002"))
    found = image_search.search_pornpics(NAME)
    galleries = {r["image"].split("/")[-2] for r in found}
    assert galleries == {"30000001", "30000003"}
    assert found.partial
    assert found.warnings == ["1 of 3 galleries failed to load (www.pornpics.com returned HTTP 500)"]

    outcome = image_search.search_single_source("pornpics", NAME, NAME)
    assert outcome["status"] == "partial"
    assert len(outcome["results"]) == 4
    assert outcome["warnings"] == found.warnings
    assert "error" not in outcome


def test_freeones_skips_a_blocked_gallery(web):
    serve_freeones(web)
    web.routes[FREEONES_GALLERY_1] = SourceBlocked("www.freeones.com blocked the request (HTTP 403)")
    found = image_search.search_freeones(NAME)
    assert photos(found) == freeones_gallery_photos("jj/kk")
    assert found.partial
    assert found.warnings == ["1 of 2 galleries failed to load (www.freeones.com blocked the request (HTTP 403))"]


def test_javdatabase_skips_a_failed_later_page(web):
    web.routes[JAVDATABASE_URL] = fixture("javdatabase_1.html")
    web.routes[JAVDATABASE_URL + "?ipage=2"] = SourceHTTPError(503, JAVDATABASE_URL)
    web.routes[JAVDATABASE_URL + "?ipage=3"] = fixture("javdatabase_2.html")
    found = image_search.search_javdatabase(NAME)
    assert len(found) == 4  # idol, 2 covers from page 1, the new cover from page 3
    assert found.partial
    # Pages 1-3 were read (page 4 is a 404, the end); page 2 failed
    assert found.warnings == ["1 of 3 pages failed to load (www.javdatabase.com returned HTTP 503)"]


@pytest.mark.parametrize("source,index_url", [
    ("pornpics", PORNPICS_INDEX_URL),
    ("elitebabes", ELITEBABES_INDEX_URL),
    ("freeones", FREEONES_LIST_URL),
])
def test_an_index_page_failure_is_an_error(web, source, index_url):
    web.routes[index_url] = SourceHTTPError(500, index_url)
    with pytest.raises(SourceHTTPError):
        SCRAPERS[source](NAME)
    outcome = image_search.search_single_source(source, NAME, NAME)
    assert outcome["status"] == "error"
    assert outcome["results"] == []
    assert "HTTP 500" in outcome["error"]


def test_a_blocked_index_page_is_blocked(web):
    web.routes[ELITEBABES_INDEX_URL] = SourceBlocked("www.elitebabes.com answered with a Cloudflare challenge page")
    outcome = image_search.search_single_source("elitebabes", NAME, NAME)
    assert outcome == {
        "results": [],
        "status": "blocked",
        "error": "www.elitebabes.com answered with a Cloudflare challenge page",
    }


def test_a_timed_out_index_page_is_a_timeout(web):
    web.routes[BABEPEDIA_URL] = SourceTimeout("www.babepedia.com did not answer in time")
    outcome = image_search.search_single_source("babepedia", NAME, NAME)
    assert outcome["status"] == "timeout"
    assert outcome["error"] == "www.babepedia.com did not answer in time"


def test_when_every_gallery_fails_the_source_fails(web):
    web.routes[FREEONES_LIST_URL] = fixture("freeones_list.html")
    web.routes[FREEONES_GALLERY_1] = SourceTimeout("www.freeones.com did not answer in time")
    web.routes[FREEONES_GALLERY_2] = SourceTimeout("www.freeones.com did not answer in time")
    outcome = image_search.search_single_source("freeones", NAME, NAME)
    assert outcome["status"] == "timeout"
    assert outcome["results"] == []
    assert outcome["error"] == "None of the 2 galleries loaded in time"

    web.routes[FREEONES_GALLERY_1] = SourceHTTPError(500, FREEONES_GALLERY_1)
    web.routes[FREEONES_GALLERY_2] = SourceHTTPError(500, FREEONES_GALLERY_2)
    outcome = image_search.search_single_source("freeones", NAME, NAME)
    assert outcome["status"] == "error"
    assert outcome["error"] == "None of the 2 galleries loaded (www.freeones.com returned HTTP 500)"

    web.routes[FREEONES_GALLERY_2] = SourceBlocked("www.freeones.com blocked the request (HTTP 403)")
    outcome = image_search.search_single_source("freeones", NAME, NAME)
    assert outcome["status"] == "blocked"


# ---------------------------------------------------------------------------
# Parallel galleries, deterministic caps, deadlines
# ---------------------------------------------------------------------------

def test_gallery_ids_are_sorted_before_the_cap(web):
    ids = [str(30000100 - 3 * i) for i in range(21)]  # descending: page order is not sorted order
    serve_pornpics(web, ids)
    found = image_search.search_pornpics(NAME, max_galleries=20)
    fetched = [u for u in web.urls() if "/galleries/" in u]
    assert sorted(fetched) == [pornpics_gallery_url(gid) for gid in sorted(ids)[:20]]
    # Results follow the sorted gallery order, whatever order the fetches finished in
    order = list(dict.fromkeys(r["image"].split("/")[-2] for r in found))
    assert order == sorted(ids)[:20]


def test_elitebabes_gallery_links_are_sorted_before_the_cap(web):
    slugs = [f"https://www.elitebabes.com/jane-example-set-{n}-{40000 + n}/" for n in (12, 3, 7, 1, 9, 11, 2, 5, 10, 4, 8, 6)]
    web.routes[ELITEBABES_INDEX_URL] = "".join(f'<figure><a href="{u}" title="x">x</a></figure>\n' for u in slugs)
    web.fallback = lambda url: elitebabes_gallery(url) if url != ELITEBABES_INDEX_URL else None
    image_search.search_elitebabes(NAME, max_galleries=10)
    assert sorted(web.urls()[1:]) == sorted(slugs)[:10]


def test_gallery_pages_are_fetched_four_at_a_time(web):
    ids = [str(30000001 + i) for i in range(12)]
    serve_pornpics(web, ids)
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def slow(gid):
        def page():
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return pornpics_gallery(gid)
        return page

    for gid in ids:
        web.routes[pornpics_gallery_url(gid)] = slow(gid)
    found = image_search.search_pornpics(NAME)
    assert len(found) == 24
    assert state["peak"] == image_search.GALLERY_WORKERS == 4


def test_the_executor_is_shut_down_without_waiting(web, monkeypatch):
    calls = []
    real = image_search.concurrent.futures.ThreadPoolExecutor

    class SpyExecutor(real):
        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs.get("max_workers", args[0] if args else None)))
            super().__init__(*args, **kwargs)

        def shutdown(self, *args, **kwargs):
            calls.append(("shutdown", kwargs))
            return super().shutdown(*args, **kwargs)

    monkeypatch.setattr(image_search.concurrent.futures, "ThreadPoolExecutor", SpyExecutor)
    serve_pornpics(web, ["30000001", "30000002"])
    image_search.search_pornpics(NAME)
    assert calls == [("init", 4), ("shutdown", {"wait": False, "cancel_futures": True})]


def test_a_hung_gallery_is_abandoned_at_the_deadline(web, monkeypatch):
    budget = 0.6
    monkeypatch.setattr(image_search, "SOURCE_BUDGET_SECONDS", budget)
    ids = [str(30000001 + i) for i in range(20)]
    serve_pornpics(web, ids)
    hung = ids[7]
    release = threading.Event()

    def hang():
        release.wait(10)
        raise SourceTimeout("www.pornpics.com did not answer in time")

    web.routes[pornpics_gallery_url(hung)] = hang
    try:
        started = time.monotonic()
        outcome = image_search.search_single_source("pornpics", NAME, NAME)
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < budget + 0.5
    assert outcome["status"] == "partial"
    assert outcome["warnings"] == ["1 of 20 galleries timed out"]
    galleries = {r["image"].split("/")[-2] for r in outcome["results"]}
    assert galleries == set(ids) - {hung}
    assert len(outcome["results"]) == 19 * 2


def test_search_single_source_gives_each_source_the_budget(monkeypatch):
    monkeypatch.setattr(image_search, "_now", lambda: 1000.0)
    seen = {}

    def fake_babepedia(name, max_results=50, deadline=None):
        seen["deadline"] = deadline
        return SourceResult()

    monkeypatch.setattr(image_search, "search_babepedia", fake_babepedia)
    image_search.search_single_source("babepedia", NAME, NAME)
    assert seen["deadline"] == 1000.0 + image_search.SOURCE_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# search_single_source: status, errors and warnings
# ---------------------------------------------------------------------------

def test_status_ok(web):
    web.routes[BABEPEDIA_URL] = fixture("babepedia.html")
    outcome = image_search.search_single_source("babepedia", NAME, NAME)
    assert outcome["status"] == "ok"
    assert len(outcome["results"]) == 4
    assert set(outcome) == {"results", "status"}


def test_status_empty_when_the_page_has_no_images(web):
    web.routes[BOOBPEDIA_URL] = "<html><body>No pictures yet</body></html>"
    assert image_search.search_single_source("boobpedia", NAME, NAME) == {"results": [], "status": "empty"}


def test_unknown_source_is_an_error():
    outcome = image_search.search_single_source("nosuchsite", NAME, NAME)
    assert outcome == {"results": [], "status": "error", "error": "Unknown source"}


def test_an_unexpected_exception_is_an_error(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("parser exploded")

    monkeypatch.setattr(image_search, "search_boobpedia", broken)
    outcome = image_search.search_single_source("boobpedia", NAME, NAME)
    assert outcome["status"] == "error"
    assert "parser exploded" in outcome["error"]
    assert outcome["results"] == []


def test_duckduckgo_uses_the_full_query(web):
    ddg_routes(web)
    outcome = image_search.search_single_source("duckduckgo", NAME, "Jane Example pornstar")
    assert outcome["status"] == "ok"
    assert len(outcome["results"]) == 2
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(web.urls()[0]).query)["q"]
    assert query == ["Jane Example pornstar"]


def _result(image, source="Babepedia"):
    return {"thumbnail": image, "image": image, "title": "t", "source": source, "width": 0, "height": 0}


@pytest.mark.parametrize("bad,warning", [
    (2, "2 results from unexpected hosts were dropped"),
    (1, "1 result from an unexpected host was dropped"),
])
def test_dropped_hosts_add_a_warning(monkeypatch, bad, warning):
    results = [_result("https://www.babepedia.com/pics/Jane%20Example.jpg")]
    results += [_result(f"https://evil.example.com/{i}.jpg") for i in range(bad)]
    monkeypatch.setattr(image_search, "search_babepedia", lambda *a, **k: SourceResult(results))
    outcome = image_search.search_single_source("babepedia", NAME, NAME)
    assert outcome["status"] == "ok"
    assert [r["image"] for r in outcome["results"]] == ["https://www.babepedia.com/pics/Jane%20Example.jpg"]
    assert outcome["warnings"] == [warning]


def test_drop_disallowed_hosts_returns_the_dropped_count():
    kept, dropped = image_search.drop_disallowed_hosts(
        [_result("https://www.babepedia.com/pics/a.jpg"), _result("http://10.0.0.4/b.jpg")], "test"
    )
    assert [r["image"] for r in kept] == ["https://www.babepedia.com/pics/a.jpg"]
    assert dropped == 1


def test_warnings_are_combined(web, monkeypatch):
    found = SourceResult(
        [_result("https://www.babepedia.com/pics/a.jpg"), _result("https://evil.example.com/b.jpg")],
        warnings=["1 of 2 galleries timed out"],
        partial=True,
    )
    monkeypatch.setattr(image_search, "search_babepedia", lambda *a, **k: found)
    outcome = image_search.search_single_source("babepedia", NAME, NAME)
    assert outcome["status"] == "partial"
    assert outcome["warnings"] == ["1 of 2 galleries timed out", "1 result from an unexpected host was dropped"]


# ---------------------------------------------------------------------------
# main(): the reply the current UI reads
# ---------------------------------------------------------------------------

class Exited(BaseException):
    pass


def run_main(monkeypatch, capsys, payload):
    """Run main() with payload as stdin. Returns (parsed stdout, exit code or None)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    codes = []

    def fake_exit(code):
        codes.append(code)
        raise Exited

    monkeypatch.setattr(image_search.os, "_exit", fake_exit)
    try:
        image_search.main()
    except Exited:
        pass
    out = capsys.readouterr().out
    return json.loads(out), (codes[0] if codes else None)


def test_main_reply_shape(web, monkeypatch, capsys):
    web.routes[BABEPEDIA_URL] = fixture("babepedia.html")
    reply, _ = run_main(monkeypatch, capsys, {"args": {
        "mode": "search", "query": "Jane Example pornstar", "performerName": NAME, "source": "babepedia",
    }})
    output = reply["output"]
    assert output["query"] == "Jane Example pornstar"
    assert output["source"] == "babepedia"
    assert output["status"] == "ok"
    assert len(output["results"]) == 4
    assert "error" not in output


def test_main_reply_carries_the_source_error(web, monkeypatch, capsys):
    web.routes[BABEPEDIA_URL] = SourceBlocked("www.babepedia.com blocked the request (HTTP 403)")
    reply, _ = run_main(monkeypatch, capsys, {"args": {
        "mode": "search", "query": NAME, "performerName": NAME, "source": "babepedia",
    }})
    assert reply["output"]["status"] == "blocked"
    assert reply["output"]["error"] == "www.babepedia.com blocked the request (HTTP 403)"
    assert reply["output"]["results"] == []


# ---------------------------------------------------------------------------
# main(): the plugin's stdin/stdout contract, and dead code
# ---------------------------------------------------------------------------

def search_args(**over):
    args = {"mode": "search", "query": "Jane Example pornstar", "performerName": NAME, "source": "babepedia"}
    args.update(over)
    return {"args": {k: v for k, v in args.items() if v is not None}}


def test_main_prints_the_source_outcome(web, monkeypatch, capsys):
    web.routes[BABEPEDIA_URL] = fixture("babepedia.html")
    reply, code = run_main(monkeypatch, capsys, search_args())
    out = reply["output"]
    assert out["status"] == "ok" and out["results"]
    assert out["query"] == "Jane Example pornstar" and out["source"] == "babepedia"
    assert code == 0


def test_main_without_a_source_needs_one(monkeypatch, capsys):
    reply, code = run_main(monkeypatch, capsys, search_args(source=None))
    assert reply == {"error": "source is required"}
    assert code == 0


def test_main_unknown_mode_and_missing_query(monkeypatch, capsys):
    reply, _ = run_main(monkeypatch, capsys, search_args(mode="frobnicate"))
    assert "error" in reply and "output" not in reply
    reply, _ = run_main(monkeypatch, capsys, search_args(query=""))
    assert "error" in reply and "output" not in reply
    reply, _ = run_main(monkeypatch, capsys, "not json")
    assert "error" in reply


def test_main_exception_becomes_an_error_outcome(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(image_search, "search_single_source", boom)
    reply, code = run_main(monkeypatch, capsys, search_args())
    out = reply["output"]
    assert out["status"] == "error" and "kaboom" in out["error"] and out["results"] == []
    assert out["source"] == "babepedia" and out["query"] == "Jane Example pornstar"
    assert code == 0


def test_bing_is_no_longer_a_source(web):
    outcome = image_search.search_single_source("bing", NAME, "Jane Example pornstar")
    assert outcome["status"] == "error" and outcome["error"] == "Unknown source"
    assert web.requests == []


REMOVED_NAMES = [
    "SIZE_THRESHOLDS", "ASPECT_THRESHOLDS", "normalize_name_for_url", "get_image_dimensions",
    "filter_by_size_and_layout", "_is_small_image_url", "search_all_sources",
]


@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_removed_names_have_no_references(name):
    assert not hasattr(image_search, name)
    for filename in ("image_search.py", "test_image_search.py", "test_image_hosts.py", "performerImageSearch.yml"):
        with open(os.path.join(PLUGIN_DIR, filename), encoding="utf-8") as f:
            assert name not in f.read(), f"{name} still in {filename}"


def test_duckduckgo_description_in_the_manifest():
    with open(os.path.join(PLUGIN_DIR, "performerImageSearch.yml"), encoding="utf-8") as f:
        text = f.read()
    assert "description: Web image search with SafeSearch off. It often gets rate-limited, so it is off by default." in text


HANG_SCRIPT = r"""
import json, sys, time
sys.path.insert(0, {plugin_dir!r})
import image_search
from image_search import SourceNotFound

def fetch(url, deadline, headers=None):
    if url == {index_url!r}:
        return open({index_file!r}, encoding="utf-8").read()
    time.sleep(3600)  # every gallery never answers

image_search._fetch = fetch
image_search.SOURCE_BUDGET_SECONDS = {budget}
image_search.main()
"""


def test_main_replies_and_exits_while_a_gallery_thread_is_stuck():
    """Without os._exit, a gallery thread stuck in a socket read holds the process open
    (concurrent.futures joins its workers at exit) long past the source budget."""
    import subprocess
    budget = 2
    script = HANG_SCRIPT.format(
        plugin_dir=PLUGIN_DIR, index_url=PORNPICS_INDEX_URL,
        index_file=os.path.join(FIXTURES, "pornpics_index.html"), budget=budget)
    payload = json.dumps(search_args(source="pornpics"))
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script], input=payload, capture_output=True,
                          text=True, timeout=budget + 10)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert elapsed < budget + 5
    out = json.loads(proc.stdout)["output"]
    assert out["source"] == "pornpics"
    assert out["status"] in ("timeout", "error", "partial", "empty")
