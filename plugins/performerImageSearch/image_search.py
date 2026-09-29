#!/usr/bin/env python3
"""
Performer Image Search - Multi-Source Image Search Backend
Searches multiple adult image sources and combines results.
Supports mainstream, JAV, male, and trans performers.

Sources (configurable in Settings > Plugins):
1. Babepedia - Female performers, curated photos
2. PornPics - Mainstream performers (incl. male)
3. FreeOnes - Large database with male and trans performers
4. EliteBabes - Female performers, high-quality photosets
5. Boobpedia - Female performers, wiki-style
6. JavDatabase - Japanese adult video performers
7. DuckDuckGo Images - General image search fallback (SafeSearch off)

Uses only Python standard library - no pip dependencies.
"""

import concurrent.futures
import http.client
import ipaddress
import json
import re
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

# Import Stash-compatible logging
import log

# A current desktop Chrome. Sites behind Cloudflare treat old or non-browser user
# agents as bots, so bump the version now and then.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Headers sent with every request (see _fetch)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Each source gets this long in total (index page plus galleries)
SOURCE_BUDGET_SECONDS = 25
# No single socket operation waits longer than this
REQUEST_TIMEOUT_SECONDS = 10
# Gallery pages fetched at the same time, per source
GALLERY_WORKERS = 4

# Text that only appears on Cloudflare's "checking your browser" challenge pages
CLOUDFLARE_CHALLENGE_MARKERS = ("cf-chl", "Just a moment...")

# Size filter thresholds (in pixels)
SIZE_THRESHOLDS = {
    "Large": 500000,    # >= 500k pixels (e.g., 700x700 or larger)
    "Medium": 100000,   # >= 100k pixels (e.g., 316x316)
    "Small": 0,         # < 100k pixels
}

# Aspect ratio thresholds
ASPECT_THRESHOLDS = {
    "Portrait": (0, 0.9),     # width/height < 0.9
    "Square": (0.9, 1.1),     # 0.9 <= ratio <= 1.1
    "Landscape": (1.1, float('inf')),  # ratio > 1.1
}


# Image hosts each scraper returns. The Stash server fetches the chosen URL itself
# (performerUpdate image:), so results pointing anywhere else are dropped.
SOURCE_IMAGE_HOSTS = {
    "Babepedia": {"www.babepedia.com"},
    "FreeOnes": {"thumbs.freeones.com", "ch-thumbs.freeones.com", "img.freeones.com"},
    "PornPics": {"cdni.pornpics.com"},
    "EliteBabes": {"cdn.elitebabes.com"},
    "Boobpedia": {"www.boobpedia.com"},
    "JavDatabase": {"www.javdatabase.com"},
}

# Sources whose images come from arbitrary sites. Only public hosts are allowed.
OPEN_WEB_SOURCES = {"DuckDuckGo"}

LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa", ".localdomain")


def _is_public_host(host):
    """True if host looks like an internet host rather than this machine or the LAN."""
    if not host or host == "localhost" or host.endswith(LOCAL_HOST_SUFFIXES):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    # Single-label names only resolve on a LAN. A numeric or hex last label is IPv4
    # shorthand (127.1, 0x7f.1) that some resolvers accept; no real TLD looks like that.
    last_label = host.rsplit(".", 1)[-1]
    return "." in host and not re.fullmatch(r"\d+|0x[0-9a-f]*", last_label)


def is_allowed_image_url(url, source):
    """Check that the Stash server may be asked to fetch this image URL for this source."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if source in SOURCE_IMAGE_HOSTS:
        return host in SOURCE_IMAGE_HOSTS[source]
    if source in OPEN_WEB_SOURCES:
        return _is_public_host(host)
    return False


def drop_disallowed_hosts(results, label):
    """Remove results whose image or thumbnail URL fails is_allowed_image_url.

    Returns (kept results, number dropped).
    """
    allowed = []
    for result in results:
        source = result.get("source")
        urls = [result.get("image", "")]
        if result.get("thumbnail"):
            urls.append(result["thumbnail"])
        if all(is_allowed_image_url(u, source) for u in urls):
            allowed.append(result)
    dropped = len(results) - len(allowed)
    if dropped:
        log.LogWarning(f"[{label}] Dropped {dropped} images from unexpected hosts")
    return allowed, dropped


def normalize_name_for_url(name):
    """Convert performer name to URL-friendly format."""
    # Replace spaces with underscores or hyphens depending on the site
    return name.strip()


# --- Fetching -----------------------------------------------------------------
#
# Every request goes through _fetch, which enforces a deadline and turns each kind
# of failure into one of the exceptions below. Scrapers return a SourceResult or
# raise; search_single_source turns the outcome into a status for the UI.


class SourceError(Exception):
    """A source could not be searched. The message is shown to the user."""

    result_status = "error"


class SourceTimeout(SourceError):
    """The deadline passed, or the site stopped answering."""

    result_status = "timeout"


class SourceBlocked(SourceError):
    """The site refused us: HTTP 403 or 429, or a Cloudflare challenge page."""

    result_status = "blocked"


class SourceHTTPError(SourceError):
    """The site answered with an HTTP status other than 200."""

    def __init__(self, status, url=""):
        self.status = status
        self.url = url
        super().__init__(f"{_host(url) or 'The site'} returned HTTP {status}")


class SourceNotFound(SourceHTTPError):
    """HTTP 404. On a performer page it means the site has no such performer."""

    def __init__(self, url=""):
        super().__init__(404, url)


class SourceResult(list):
    """A scraper's results (it is the list of results) plus what went wrong on the way.

    warnings are user-facing notes about pages that were skipped; partial is True
    when some pages failed, so the results are incomplete.
    """

    def __init__(self, results=(), warnings=(), partial=False):
        super().__init__(results)
        self.warnings = list(warnings)
        self.partial = partial


def _now():
    """The clock deadlines are measured on (tests replace it)."""
    return time.monotonic()


def _default_deadline(deadline):
    return deadline if deadline is not None else _now() + SOURCE_BUDGET_SECONDS


def _host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _is_cloudflare_challenge(text):
    return any(marker in text for marker in CLOUDFLARE_CHALLENGE_MARKERS)


def _fetch(url, deadline, headers=None):
    """GET url and return the body as text. This is the only place requests are made.

    Sends HEADERS plus any extra headers. deadline is a _now() value: no socket
    operation waits longer than REQUEST_TIMEOUT_SECONDS or past it.

    Raises SourceTimeout (the deadline passed or the site stopped answering),
    SourceBlocked (HTTP 403 or 429, or a Cloudflare challenge page), SourceNotFound
    (HTTP 404), SourceHTTPError (any other status but 200) or SourceError (the site
    could not be reached).
    """
    host = _host(url) or url
    remaining = deadline - _now()
    if remaining <= 0:
        raise SourceTimeout(f"Ran out of time before fetching {host}")
    request = urllib.request.Request(url, headers={**HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=min(REQUEST_TIMEOUT_SECONDS, remaining)) as response:
            status = response.status
            chunks = []
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if _now() > deadline:
                    raise SourceTimeout(f"{host} did not finish sending in time")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(65536).decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        if e.code == 404:
            raise SourceNotFound(url) from None
        if e.code in (403, 429):
            raise SourceBlocked(f"{host} blocked the request (HTTP {e.code})") from None
        if _is_cloudflare_challenge(body):
            raise SourceBlocked(f"{host} answered with a Cloudflare challenge page (HTTP {e.code})") from None
        raise SourceHTTPError(e.code, url) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise SourceTimeout(f"{host} did not answer in time") from None
        raise SourceError(f"Could not reach {host}: {e.reason}") from None
    except (socket.timeout, TimeoutError):  # the same class from Python 3.10
        raise SourceTimeout(f"{host} did not answer in time") from None
    except (OSError, http.client.HTTPException) as e:
        raise SourceError(f"Could not read from {host}: {e}") from None

    text = b"".join(chunks).decode("utf-8", errors="ignore")
    if status != 200:
        raise SourceHTTPError(status, url)
    if _is_cloudflare_challenge(text):
        raise SourceBlocked(f"{host} answered with a Cloudflare challenge page")
    return text


def _fetch_performer_page(label, name, url, deadline):
    """Fetch a source's page for the performer; None when the site has no such page (404)."""
    log.LogDebug(f"[{label}] Fetching: {url}")
    try:
        return _fetch(url, deadline)
    except SourceNotFound:
        log.LogDebug(f"[{label}] Performer not found: {name}")
        return None


def _fetch_all(urls, deadline):
    """Fetch urls in parallel, GALLERY_WORKERS at a time, until the deadline.

    Returns a list in the order of urls holding each page's text, or the SourceError
    it failed with. Pages still loading at the deadline count as SourceTimeout; their
    threads are left to finish on their own, which _fetch's deadline makes quick.
    """
    if not urls:
        return []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=GALLERY_WORKERS)
    try:
        futures = [executor.submit(_fetch, url, deadline) for url in urls]
        concurrent.futures.wait(futures, timeout=max(0.0, deadline - _now()))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    pages = []
    for url, future in zip(urls, futures):
        if future.cancelled() or not future.done():
            pages.append(SourceTimeout(f"{_host(url)} did not answer in time"))
            continue
        error = future.exception()
        if error is None:
            pages.append(future.result())
        elif isinstance(error, SourceError):
            pages.append(error)
        else:
            pages.append(SourceError(f"{type(error).__name__}: {error}"))
    return pages


def _page_failures(pages, noun):
    """Summarize the failed entries of pages (from _fetch_all) for the user.

    Returns (warnings, error): one warning per kind of failure, and the error to raise
    when every page failed (None otherwise).
    """
    failures = [page for page in pages if isinstance(page, SourceError)]
    if not failures:
        return [], None
    total = len(pages)
    timeouts = [f for f in failures if isinstance(f, SourceTimeout)]
    others = [f for f in failures if not isinstance(f, SourceTimeout)]
    warnings = []
    if timeouts:
        warnings.append(f"{len(timeouts)} of {total} {noun} timed out")
    if others:
        warnings.append(f"{len(others)} of {total} {noun} failed to load ({others[0]})")
    if len(failures) < total:
        return warnings, None

    blocked = [f for f in failures if isinstance(f, SourceBlocked)]
    if blocked:
        return warnings, SourceBlocked(f"None of the {total} {noun} loaded ({blocked[0]})")
    if not others:
        return warnings, SourceTimeout(f"None of the {total} {noun} loaded in time")
    return warnings, SourceError(f"None of the {total} {noun} loaded ({others[0]})")


def _gallery_pages(label, urls, deadline):
    """Fetch gallery pages in parallel; returns (the loaded pages in url order, warnings, error).

    error is set only when every page failed (see _page_failures).
    """
    log.LogDebug(f"[{label}] Fetching {len(urls)} galleries")
    pages = _fetch_all(urls, deadline)
    for url, page in zip(urls, pages):
        if isinstance(page, SourceError):
            log.LogDebug(f"[{label}] Skipped {url}: {page}")
    warnings, error = _page_failures(pages, "galleries")
    for warning in warnings:
        log.LogWarning(f"[{label}] {warning}")
    loaded = [page for page in pages if not isinstance(page, SourceError)]
    return loaded, warnings, error


def search_babepedia(name, max_results=50, deadline=None):
    """
    Search Babepedia for performer images.

    Pulls the performer's profile photos plus the gallery preview images that
    Babepedia lists on the main /babe/<name> page. The numbered gallery pages
    are JS/ad-rendered and not worth following, so we harvest the gallery
    preview thumbnails (/galleries-thumbs/<gallery>/NN.jpg) shown on the profile
    and map each to its full-size image under /galleries/.

    Note: Babepedia sits behind Cloudflare and may refuse server, VPN or datacenter
    addresses. That raises SourceBlocked, which search_single_source reports to the
    UI as the source's "blocked" status.
    """
    deadline = _default_deadline(deadline)
    # Babepedia uses underscores in URLs
    url_name = name.replace(" ", "_")
    url = f"https://www.babepedia.com/babe/{urllib.parse.quote(url_name)}"
    html = _fetch_performer_page("Babepedia", name, url, deadline)
    if html is None:
        return SourceResult()

    results = []
    seen = set()

    # 1. Profile photos: href="/pics/Name.jpg" (full-size; *_thumb3.jpg are thumbs)
    for match in re.findall(r'href="(/pics/[^"]+\.jpg)"', html):
        if "_thumb" in match or match in seen:
            continue
        seen.add(match)
        image_url = f"https://www.babepedia.com{match}"
        thumb_url = image_url.replace(".jpg", "_thumb3.jpg")
        results.append({
            "thumbnail": thumb_url,
            "image": image_url,
            "title": f"{name} - Babepedia",
            "source": "Babepedia",
            "width": 0,
            "height": 0,
        })

    # 2. Gallery previews: "/galleries-thumbs/<gallery>/NN.jpg". The full-size
    #    image lives at the same path under "/galleries/".
    for thumb in re.findall(r'["\'](/galleries-thumbs/[^"\']+\.jpg)["\']', html):
        if thumb in seen:
            continue
        seen.add(thumb)
        thumb_url = f"https://www.babepedia.com{thumb}"
        image_url = thumb_url.replace("/galleries-thumbs/", "/galleries/")
        results.append({
            "thumbnail": thumb_url,
            "image": image_url,
            "title": f"{name} - Babepedia",
            "source": "Babepedia",
            "width": 0,
            "height": 0,
        })

    log.LogInfo(f"[Babepedia] Found {len(results)} images for: {name}")
    return SourceResult(results[:max_results])


def search_freeones(name, max_results=200, max_galleries=20, deadline=None):
    """
    Search FreeOnes for performer images.
    FreeOnes has extensive photo galleries for performers.
    Fetches gallery list, then drills into individual galleries.
    Note: FreeOnes uses complex CDN URLs - the thumbnails are often already large.
    """
    deadline = _default_deadline(deadline)
    # FreeOnes uses hyphens in URLs
    url_name = name.lower().replace(" ", "-")
    base_url = f"https://www.freeones.com/{urllib.parse.quote(url_name)}/photos"
    html = _fetch_performer_page("FreeOnes", name, base_url, deadline)
    if html is None:
        return SourceResult()

    # Gallery links, format /performer-name/photos/gallery-slug, deduplicated in page order
    gallery_pattern = rf'href="(/{re.escape(url_name)}/photos/[^"]+)"'
    gallery_paths = list(dict.fromkeys(re.findall(gallery_pattern, html)))
    gallery_urls = [f"https://www.freeones.com{path}" for path in gallery_paths][:max_galleries]
    log.LogInfo(f"[FreeOnes] Found {len(gallery_paths)} unique galleries for: {name}")

    pages, warnings, error = _gallery_pages("FreeOnes", gallery_urls, deadline)

    results = []
    seen_images = set()
    pattern = r'(https://(?:thumbs|ch-thumbs|img)\.freeones\.com/[^"\']+\.(?:jpg|webp|png))'
    for page in pages:
        for thumb_url in re.findall(pattern, page):
            if thumb_url in seen_images or 'favicon' in thumb_url or 'logo' in thumb_url:
                continue
            seen_images.add(thumb_url)

            # FreeOnes CDN structure is complex - the thumbs are often already good quality
            # URLs like /350x350/ or /1440x0/ indicate resize params
            # Keep original URL as both thumb and full since transformations return 403
            image_url = thumb_url

            results.append({
                "thumbnail": thumb_url,
                "image": image_url,
                "title": f"{name} - FreeOnes",
                "source": "FreeOnes",
                "width": 0,
                "height": 0,
            })

    if error and not results:
        raise error
    log.LogInfo(f"[FreeOnes] Found {len(results)} images for: {name}")
    return SourceResult(results[:max_results], warnings, partial=bool(warnings))


def search_pornpics(name, max_results=200, max_galleries=20, deadline=None):
    """
    Search PornPics for performer images.
    PornPics has extensive galleries organized by performer.
    Extracts gallery set IDs from performer page, then drills into each gallery.
    """
    deadline = _default_deadline(deadline)
    # PornPics uses hyphens and lowercase
    url_name = name.lower().replace(" ", "-")
    base_url = f"https://www.pornpics.com/pornstars/{urllib.parse.quote(url_name)}/"
    html = _fetch_performer_page("PornPics", name, base_url, deadline)
    if html is None:
        return SourceResult()

    results = []
    seen_images = set()

    # Get profile image first
    profile_pattern = r'(https://cdni\.pornpics\.com/models/[^"\']+\.jpg)'
    for img_url in re.findall(profile_pattern, html)[:1]:
        seen_images.add(img_url)
        results.append({
            "thumbnail": img_url,
            "image": img_url.replace("/460/", "/1280/"),
            "title": f"{name} - PornPics Profile",
            "source": "PornPics",
            "width": 0,
            "height": 0,
        })

    # Extract gallery set IDs from image URLs on the performer page
    # Format: /460/7/91/67655164/67655164_004_98f9.jpg
    # The 8-digit number (67655164) is the gallery/set ID. Sorted, so the cap
    # always picks the same galleries.
    gallery_ids = sorted(set(re.findall(r'/(\d{8})/\d{8}_', html)))
    log.LogDebug(f"[PornPics] Found {len(gallery_ids)} unique gallery IDs for: {name}")
    gallery_urls = [f"https://www.pornpics.com/galleries/{gid}/" for gid in gallery_ids[:max_galleries]]

    pages, warnings, error = _gallery_pages("PornPics", gallery_urls, deadline)

    # Each photo links the 1280px image and shows the 460px one
    pattern = r'(https://cdni\.pornpics\.com/(?:460|1280)/[^"\'>\s]+\.jpg)'
    for page in pages:
        for img_url in re.findall(pattern, page):
            # Use 460 as thumbnail, 1280 as full
            thumb_url = img_url.replace("/1280/", "/460/")
            image_url = img_url.replace("/460/", "/1280/")
            if image_url in seen_images:
                continue
            seen_images.add(image_url)

            results.append({
                "thumbnail": thumb_url,
                "image": image_url,
                "title": f"{name} - PornPics",
                "source": "PornPics",
                "width": 0,
                "height": 0,
            })

    if error and not results:
        raise error
    log.LogInfo(f"[PornPics] Found {len(results)} images for: {name}")
    return SourceResult(results[:max_results], warnings, partial=bool(warnings))


def search_elitebabes(name, max_results=100, max_galleries=10, deadline=None):
    """
    Search EliteBabes for performer images.
    EliteBabes has high-quality photosets with multiple size options.
    URL format: https://cdn.elitebabes.com/content/XXXXXX/filename_w400.jpg
    Sizes: _w200, _w400, _w600, _w800, or no suffix for full size (~400KB).
    """
    deadline = _default_deadline(deadline)
    # EliteBabes uses hyphens and lowercase
    url_name = name.lower().replace(" ", "-")
    base_url = f"https://www.elitebabes.com/model/{urllib.parse.quote(url_name)}/"
    html = _fetch_performer_page("EliteBabes", name, base_url, deadline)
    if html is None:
        return SourceResult()

    # Extract gallery links - format: /gallery-name-12345/
    gallery_pattern = r'href="(https://www\.elitebabes\.com/[^"]+/)"[^>]*class="[^"]*gallery[^"]*"'
    gallery_matches = re.findall(gallery_pattern, html)

    # Also try simpler pattern for gallery links
    if not gallery_matches:
        gallery_pattern2 = r'href="(https://www\.elitebabes\.com/[a-z0-9-]+-\d+/)"'
        gallery_matches = re.findall(gallery_pattern2, html)

    # Sorted, so the cap always picks the same galleries
    gallery_urls = sorted(set(gallery_matches))[:max_galleries]
    log.LogDebug(f"[EliteBabes] Found {len(set(gallery_matches))} gallery links")

    pages, warnings, error = _gallery_pages("EliteBabes", gallery_urls, deadline)

    results = []
    seen_images = set()
    # Image URLs - format: cdn.elitebabes.com/content/XXXXXX/filename_wNNN.jpg
    pattern = r'(https://cdn\.elitebabes\.com/content/[^"\'>\s]+_w(?:200|400|600|800)\.jpg)'
    for page in pages:
        for img_url in re.findall(pattern, page):
            # Normalize to base (remove size suffix for full-size)
            # _w400.jpg -> .jpg (full size)
            base_img = re.sub(r'_w\d+\.jpg$', '.jpg', img_url)

            if base_img in seen_images:
                continue
            seen_images.add(base_img)

            # Use _w400 as thumbnail, no suffix for full size
            thumb_url = base_img.replace('.jpg', '_w400.jpg')
            image_url = base_img  # Full size has no suffix

            results.append({
                "thumbnail": thumb_url,
                "image": image_url,
                "title": f"{name} - EliteBabes",
                "source": "EliteBabes",
                "width": 0,
                "height": 0,
            })

    if error and not results:
        raise error
    log.LogInfo(f"[EliteBabes] Found {len(results)} images for: {name}")
    return SourceResult(results[:max_results], warnings, partial=bool(warnings))


def search_boobpedia(name, max_results=50, deadline=None):
    """
    Search Boobpedia for performer images.
    Boobpedia is a MediaWiki-style site with performer photos.
    Thumbnails are relative paths: /wiki/images/thumb/X/XX/Filename.jpg/NNNpx-Filename.jpg
    Full size: /wiki/images/X/XX/Filename.jpg
    """
    deadline = _default_deadline(deadline)
    # Boobpedia uses underscores in URLs (wiki style)
    url_name = name.replace(" ", "_")
    base_url = f"https://www.boobpedia.com/boobs/{urllib.parse.quote(url_name)}"
    html = _fetch_performer_page("Boobpedia", name, base_url, deadline)
    if html is None:
        return SourceResult()

    results = []
    seen = set()

    # Extract thumbnail image links (relative paths)
    # Format: /wiki/images/thumb/X/XX/Filename.jpg/NNNpx-Filename.jpg
    # We want content images, not icons (which have small sizes like 16px, 18px)
    pattern = r'src="(/wiki/images/thumb/[^"]+)"'
    matches = re.findall(pattern, html)
    log.LogDebug(f"[Boobpedia] Found {len(matches)} thumbnail matches")

    for thumb_path in matches:
        # Skip small icons (16px, 18px, 70px are usually icons or tiny thumbs)
        if re.search(r'/(?:16|18|20)px-', thumb_path):
            continue

        if thumb_path in seen or len(results) >= max_results:
            continue
        seen.add(thumb_path)

        # Transform thumbnail to full-size
        # /wiki/images/thumb/X/XX/Filename.jpg/NNNpx-Filename.jpg -> /wiki/images/X/XX/Filename.jpg
        match = re.match(r'(/wiki/images/)thumb/([a-z0-9]/[a-z0-9]+/[^/]+\.(?:jpg|jpeg|png|gif))/\d+px-', thumb_path, re.IGNORECASE)
        if match:
            full_path = match.group(1) + match.group(2)
        else:
            # Fallback: just use thumbnail path
            full_path = thumb_path

        thumb_url = f"https://www.boobpedia.com{thumb_path}"
        image_url = f"https://www.boobpedia.com{full_path}"

        results.append({
            "thumbnail": thumb_url,
            "image": image_url,
            "title": f"{name} - Boobpedia",
            "source": "Boobpedia",
            "width": 0,
            "height": 0,
        })

    log.LogInfo(f"[Boobpedia] Found {len(results)} images for: {name}")
    return SourceResult(results)


def _parse_javdatabase_page(html, name, seen, results, max_results):
    """Append the idol images, covers and vertical images on one JavDatabase page to results."""
    # Extract profile/idol images (webp format)
    # Pattern: /idolimages/full/name.webp or /idolimages/thumb/name.webp
    idol_pattern = r'(https://www\.javdatabase\.com/idolimages/(?:full|thumb)/[^"\'>\s]+\.webp)'
    idol_matches = re.findall(idol_pattern, html)
    log.LogDebug(f"[JavDatabase] Found {len(idol_matches)} idol image matches")

    for img_url in idol_matches:
        if img_url in seen:
            continue
        seen.add(img_url)

        # Use thumb as thumbnail, full as image
        if '/thumb/' in img_url:
            thumb_url = img_url
            image_url = img_url.replace('/thumb/', '/full/')
        else:
            image_url = img_url
            thumb_url = img_url.replace('/full/', '/thumb/')

        results.append({
            "thumbnail": thumb_url,
            "image": image_url,
            "title": f"{name} - JavDatabase",
            "source": "JavDatabase",
            "width": 0,
            "height": 0,
        })

        if len(results) >= max_results:
            break

    # Also extract movie cover thumbnails
    # Pattern: /covers/thumb/prefix/codeps.webp
    cover_pattern = r'(https://www\.javdatabase\.com/covers/thumb/[^"\'>\s]+\.webp)'
    cover_matches = re.findall(cover_pattern, html)
    log.LogDebug(f"[JavDatabase] Found {len(cover_matches)} cover matches")

    for img_url in cover_matches[:20]:  # Limit covers per page
        if img_url in seen or len(results) >= max_results:
            continue
        seen.add(img_url)

        # Covers: thumb -> full by replacing path
        thumb_url = img_url
        image_url = img_url.replace('/covers/thumb/', '/covers/full/')

        results.append({
            "thumbnail": thumb_url,
            "image": image_url,
            "title": f"{name} - JavDatabase Cover",
            "source": "JavDatabase",
            "width": 0,
            "height": 0,
        })

    # Extract vertical/promotional images
    vertical_pattern = r'(https://www\.javdatabase\.com/vertical/[^"\'>\s]+\.jpg)'
    vertical_matches = re.findall(vertical_pattern, html)
    log.LogDebug(f"[JavDatabase] Found {len(vertical_matches)} vertical matches")

    for img_url in vertical_matches[:10]:
        if img_url in seen or len(results) >= max_results:
            continue
        seen.add(img_url)

        results.append({
            "thumbnail": img_url,
            "image": img_url,
            "title": f"{name} - JavDatabase",
            "source": "JavDatabase",
            "width": 0,
            "height": 0,
        })


def search_javdatabase(name, max_results=100, max_pages=5, deadline=None):
    """
    Search JavDatabase for JAV performer images.
    JavDatabase has idol profiles with photos and movie covers.
    URL format: https://www.javdatabase.com/idols/name-here/
    Image format: https://www.javdatabase.com/idolimages/full/name.webp

    Reads the profile page, then ?ipage=2..max_pages one at a time until a 404
    (the last page). A later page that fails is skipped.
    """
    deadline = _default_deadline(deadline)
    # JavDatabase uses lowercase hyphenated names
    url_name = name.lower().replace(" ", "-")
    base_url = f"https://www.javdatabase.com/idols/{urllib.parse.quote(url_name)}/"
    html = _fetch_performer_page("JavDatabase", name, base_url, deadline)
    if html is None:
        return SourceResult()

    results = []
    seen = set()
    pages = [html]
    _parse_javdatabase_page(html, name, seen, results, max_results)

    for page_url in [f"{base_url}?ipage={i}" for i in range(2, max_pages + 1)]:
        if len(results) >= max_results:
            break
        log.LogDebug(f"[JavDatabase] Fetching: {page_url}")
        try:
            html = _fetch(page_url, deadline)
        except SourceNotFound:
            log.LogDebug(f"[JavDatabase] Page not found: {page_url}")
            break  # No more pages
        except SourceError as e:
            log.LogDebug(f"[JavDatabase] Skipped {page_url}: {e}")
            pages.append(e)
            continue
        pages.append(html)
        _parse_javdatabase_page(html, name, seen, results, max_results)

    warnings, _ = _page_failures(pages, "pages")  # page 1 loaded, so never all failed
    for warning in warnings:
        log.LogWarning(f"[JavDatabase] {warning}")
    log.LogInfo(f"[JavDatabase] Found {len(results)} images for: {name}")
    return SourceResult(results, warnings, partial=bool(warnings))


def search_duckduckgo_images(query, size="Large", layout="All", max_results=50, deadline=None):
    """
    Search DuckDuckGo Images with safe search off.
    Used as a fallback when performer-specific sites don't have results.
    DDG requires a two-step process: get vqd token, then query /i.js
    """
    deadline = _default_deadline(deadline)
    results = []

    log.LogDebug(f"[DuckDuckGo] Query: {query}, size: {size}, layout: {layout}")

    # DDG wants these on top of the shared HEADERS to avoid 403
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    # Step 1: Get the vqd token from the HTML search page
    search_params = urllib.parse.urlencode({
        "q": query,
        "t": "h_",
        "iar": "images",
        "iax": "images",
        "ia": "images",
    })
    search_url = f"https://duckduckgo.com/?{search_params}"

    log.LogDebug(f"[DuckDuckGo] Getting vqd token...")
    html = _fetch(search_url, deadline, headers)

    # Extract vqd token - multiple patterns
    vqd = None
    vqd_patterns = [
        r'vqd="([^"]+)"',
        r"vqd='([^']+)'",
        r'vqd=([0-9a-zA-Z_-]+)',
        r'"vqd":"([^"]+)"',
    ]
    for pattern in vqd_patterns:
        match = re.search(pattern, html)
        if match:
            vqd = match.group(1)
            break

    if not vqd:
        log.LogWarning("[DuckDuckGo] Could not extract vqd token, trying HTML scraping")
        # Fallback: scrape images directly from HTML
        img_pattern = r'"image":"(https?://[^"]+)"'
        thumb_pattern = r'"thumbnail":"(https?://[^"]+)"'

        images_found = re.findall(img_pattern, html)
        thumbs_found = re.findall(thumb_pattern, html)

        for i, img_url in enumerate(images_found[:max_results]):
            thumb_url = thumbs_found[i] if i < len(thumbs_found) else img_url
            img_url = img_url.replace("\\u002F", "/").replace("\\/", "/")
            thumb_url = thumb_url.replace("\\u002F", "/").replace("\\/", "/")

            results.append({
                "thumbnail": thumb_url,
                "image": img_url,
                "title": query,
                "source": "DuckDuckGo",
                "width": 0,
                "height": 0,
            })

        if not results:
            # No token is how DDG turns away a search it is rate-limiting
            raise SourceBlocked("DuckDuckGo did not return a search token; it may be rate-limiting this address")
        log.LogInfo(f"[DuckDuckGo] Found {len(results)} images via HTML scraping")
        return SourceResult(results)

    log.LogDebug(f"[DuckDuckGo] Got vqd token: {vqd[:20]}...")

    # Step 2: Query the image API
    size_map = {"Large": "Large", "Medium": "Medium", "Small": "Small", "All": ""}
    layout_map = {"Portrait": "Tall", "Landscape": "Wide", "Square": "Square", "All": ""}

    filters = []
    if size_map.get(size):
        filters.append(f"size:{size_map[size]}")
    if layout_map.get(layout):
        filters.append(f"aspectratio:{layout_map[layout]}")
    filter_str = ",".join(filters) if filters else ""

    image_params = {
        "l": "us-en",
        "o": "json",
        "q": query,
        "vqd": vqd,
        "f": filter_str + ",,,",
        "p": "-1",  # SafeSearch off (-1 = off, 1 = moderate)
        "s": "0",
    }

    api_url = "https://duckduckgo.com/i.js?" + urllib.parse.urlencode(image_params)
    log.LogDebug(f"[DuckDuckGo] Fetching images from API...")

    # Update headers for API request
    api_headers = {
        **headers,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": search_url,
        "X-Requested-With": "XMLHttpRequest",
    }

    data = _fetch(api_url, deadline, api_headers)

    # Parse JSON response
    try:
        json_data = json.loads(data)
        images = json_data.get("results", [])
        log.LogDebug(f"[DuckDuckGo] Got {len(images)} results from API")
    except json.JSONDecodeError:
        log.LogDebug("[DuckDuckGo] JSON parse failed, trying regex extraction")
        pattern = r'"image"\s*:\s*"([^"]+)"'
        thumb_pat = r'"thumbnail"\s*:\s*"([^"]+)"'
        title_pat = r'"title"\s*:\s*"([^"]*)"'

        img_matches = re.findall(pattern, data)
        thumb_matches = re.findall(thumb_pat, data)
        title_matches = re.findall(title_pat, data)

        images = []
        for i, img in enumerate(img_matches):
            images.append({
                "image": img,
                "thumbnail": thumb_matches[i] if i < len(thumb_matches) else img,
                "title": title_matches[i] if i < len(title_matches) else "",
            })
        log.LogDebug(f"[DuckDuckGo] Regex found {len(images)} images")

    for img in images[:max_results]:
        img_url = img.get("image", "")
        thumb_url = img.get("thumbnail", "")
        title = img.get("title", "")

        if not img_url:
            continue

        # Unescape URLs
        img_url = img_url.replace("\\u0026", "&").replace("\\/", "/").replace("\\u002F", "/")
        thumb_url = thumb_url.replace("\\u0026", "&").replace("\\/", "/").replace("\\u002F", "/")

        width = img.get("width", 0)
        height = img.get("height", 0)

        # Skip small images (< 300px on shorter dimension)
        if width > 0 and height > 0:
            if min(width, height) < 300:
                continue

        results.append({
            "thumbnail": thumb_url or img_url,
            "image": img_url,
            "title": unescape(title) if title else query,
            "source": "DuckDuckGo",
            "width": width,
            "height": height,
        })

    log.LogInfo(f"[DuckDuckGo] Found {len(results)} images for query: {query}")
    return SourceResult(results)


def _is_small_image_url(img_url, min_size=300):
    """
    Check if an image URL indicates a small/thumbnail image.
    Returns True if the image should be skipped.
    """
    lower_url = img_url.lower()

    # Skip common thumbnail/icon patterns
    if '_tn.' in lower_url or '/tn/' in lower_url:
        return True
    if 'favico' in lower_url or 'icon' in lower_url or 'logo' in lower_url:
        return True
    if '/thumb/' in lower_url or '_thumb' in lower_url:
        return True
    # Skip UI elements, tiles, backgrounds
    if 'tile_' in lower_url or 'bg_' in lower_url or 'bg__' in lower_url:
        return True
    if 'ageconfirm' in lower_url or 'placeholder' in lower_url:
        return True

    # Check for dimension patterns in URL (e.g., 32x32, 57x57, 200x300)
    size_match = re.search(r'(\d+)x(\d+)', img_url)
    if size_match:
        w, h = int(size_match.group(1)), int(size_match.group(2))
        if w < min_size or h < min_size:
            return True

    # Check for small dimension in path (e.g., /460/, /200/)
    dim_match = re.search(r'/(\d{2,3})/', img_url)
    if dim_match:
        dim = int(dim_match.group(1))
        if dim < min_size:
            return True

    return False


def _run_scraper(source, name, query, size_filter, layout_filter, deadline):
    """Call the scraper for source. Returns its SourceResult, or None for an unknown source."""
    if source == "babepedia":
        return search_babepedia(name, 50, deadline=deadline)
    if source == "pornpics":
        return search_pornpics(name, 200, 20, deadline=deadline)
    if source == "freeones":
        return search_freeones(name, 200, 20, deadline=deadline)
    if source == "elitebabes":
        return search_elitebabes(name, 100, 10, deadline=deadline)
    if source == "boobpedia":
        return search_boobpedia(name, 50, deadline=deadline)
    if source == "javdatabase":
        return search_javdatabase(name, 100, 5, deadline=deadline)
    if source == "duckduckgo":
        # Use the full query (with suffix) for DuckDuckGo
        return search_duckduckgo_images(query, size_filter, layout_filter, 50, deadline=deadline)
    if source == "bing":
        # Legacy - kept for backwards compatibility but DuckDuckGo preferred
        log.LogDebug("[Bing] Redirecting to DuckDuckGo (Bing deprecated)")
        return search_duckduckgo_images(query, size_filter, layout_filter, 50, deadline=deadline)
    return None


def search_single_source(source, name, query, size_filter="All", layout_filter="All"):
    """
    Search a single source for images, within SOURCE_BUDGET_SECONDS.
    Used for streaming results to the client one source at a time.

    Returns {"results": [...], "status": ..., "error": ..., "warnings": [...]}, where
    status is one of:
      ok       results, nothing went wrong
      empty    the source has nothing for this performer
      partial  results, but some pages failed (see warnings)
      error    the source failed (see error)
      blocked  the site refused the request (403, 429 or a Cloudflare challenge)
      timeout  the site did not answer in time
    error is present only for error, blocked and timeout; warnings only when there are any.
    """
    performer_name = name.strip()

    log.LogInfo(f"[{source}] Starting search for: {performer_name}")
    log.LogDebug(f"[{source}] Full query: {query}")

    start_time = time.time()
    deadline = _now() + SOURCE_BUDGET_SECONDS

    try:
        found = _run_scraper(source, performer_name, query, size_filter, layout_filter, deadline)
    except SourceError as e:
        log.LogWarning(f"[{source}] Search failed ({e.result_status}): {e}")
        return {"results": [], "status": e.result_status, "error": str(e)}
    except Exception as e:
        log.LogError(f"[{source}] Search failed: {e}")
        log.LogDebug(f"[{source}] Traceback: {traceback.format_exc()}")
        return {"results": [], "status": "error", "error": f"Unexpected error: {e}"}

    if found is None:
        log.LogWarning(f"Unknown source: {source}")
        return {"results": [], "status": "error", "error": "Unknown source"}

    elapsed = time.time() - start_time
    log.LogDebug(f"[{source}] Search completed in {elapsed:.2f}s, found {len(found)} results")

    warnings = list(found.warnings)
    results, dropped = drop_disallowed_hosts(list(found), source)
    if dropped == 1:
        warnings.append("1 result from an unexpected host was dropped")
    elif dropped:
        warnings.append(f"{dropped} results from unexpected hosts were dropped")

    # Deduplicate within this source
    seen_urls = set()
    unique_results = []
    for result in results:
        img_url = result.get("image", "")
        if img_url and img_url not in seen_urls:
            seen_urls.add(img_url)
            unique_results.append(result)

    if len(unique_results) != len(results):
        log.LogDebug(f"[{source}] Removed {len(results) - len(unique_results)} duplicates within source")

    if found.partial:
        status = "partial"
    elif not unique_results:
        status = "empty"
    else:
        status = "ok"

    log.LogInfo(f"[{source}] Returning {len(unique_results)} unique images ({status})")
    outcome = {"results": unique_results, "status": status}
    if warnings:
        outcome["warnings"] = warnings
    return outcome


def search_all_sources(name, query, size_filter="All", layout_filter="All"):
    """
    Search all sources and combine results.
    Prioritizes adult-specific sites, falls back to DuckDuckGo.
    Returns ALL results at once (no pagination) since sources are finite.
    Note: This is kept for backwards compatibility, but per-source searching
    is now preferred for streaming results to the client.
    """
    all_results = []

    # Extract just the performer name (remove search suffix like "pornstar nude")
    performer_name = name.strip()

    # Search adult-specific sites first (these have curated, relevant images)
    log.LogInfo(f"Searching for performer: {performer_name}")

    # 1. Babepedia - usually has good profile photos (up to 50)
    # 2. PornPics - drills into galleries (up to 200 images from 20 galleries)
    # 3. FreeOnes - drills into galleries (up to 200 images from 20 galleries)
    # Each one's host check and time budget apply (see search_single_source)
    for source in ("babepedia", "pornpics", "freeones"):
        all_results.extend(search_single_source(source, performer_name, query)["results"])

    # 4. DuckDuckGo as fallback if we didn't find much from adult sites
    if len(all_results) < 20:
        # Use the full query (with suffix) for DuckDuckGo, pass filters
        ddg = search_single_source("duckduckgo", performer_name, query, size_filter, layout_filter)
        all_results.extend(ddg["results"])

    # Deduplicate by image URL
    seen_urls = set()
    unique_results = []
    for result in all_results:
        img_url = result.get("image", "")
        if img_url and img_url not in seen_urls:
            seen_urls.add(img_url)
            unique_results.append(result)

    log.LogInfo(f"Total unique images found: {len(unique_results)}")

    # Note: Filtering is now done client-side for performance
    # Backend returns all results, client filters after thumbnails load

    return unique_results


def main():
    """Main entry point - reads input from stdin, performs search, outputs results"""

    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Failed to parse input: {e}"}))
        return

    args = input_data.get("args", {})
    mode = args.get("mode", "search")

    log.LogDebug(f"Plugin called with mode: {mode}, args: {args}")

    if mode != "search":
        print(json.dumps({"error": f"Unknown mode: {mode}"}))
        return

    query = args.get("query", "")
    if not query:
        print(json.dumps({"error": "No search query provided"}))
        return

    # Use explicit performer name if provided, otherwise extract from query
    performer_name = args.get("performerName", "").strip()
    if not performer_name:
        # Fallback: try to extract from query by removing common suffixes
        performer_name = query
        for suffix in [" pornstar nude", " pornstar solo", " pornstar", " nude", " naked", " porn"]:
            if performer_name.lower().endswith(suffix):
                performer_name = performer_name[:-len(suffix)]
                break

    # Get filter options from args
    size_filter = args.get("size", "All")
    layout_filter = args.get("layout", "All")

    # Check if searching a specific source (for streaming)
    source = args.get("source", None)

    log.LogInfo(f"Searching for: {performer_name} (query={query}, source={source})")

    try:
        if source:
            # Search single source (streaming mode): results, status, and error
            # and warnings when there are any
            outcome = search_single_source(
                source=source,
                name=performer_name,
                query=query,
                size_filter=size_filter,
                layout_filter=layout_filter
            )
            output = {"output": {**outcome, "query": query, "source": source}}
        else:
            # Search all sources (legacy mode)
            results = search_all_sources(
                name=performer_name,
                query=query,
                size_filter=size_filter,
                layout_filter=layout_filter
            )
            output = {
                "output": {
                    "results": results,
                    "query": query,
                    "source": source
                }
            }
    except Exception as e:
        log.LogError(f"Search failed: {e}")
        output = {
            "output": {
                "results": [],
                "query": query,
                "source": source,
                "status": "error",
                "error": str(e)
            }
        }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
