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
7. Bing Images - Fallback for all performer types

Uses only Python standard library - no pip dependencies.
"""

import json
import sys
import re
import time
import urllib.request
import urllib.parse
from html import unescape

# Import Stash-compatible logging
import log

# Common headers for requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

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


def normalize_name_for_url(name):
    """Convert performer name to URL-friendly format."""
    # Replace spaces with underscores or hyphens depending on the site
    return name.strip()


def get_image_dimensions(url):
    """
    Try to get image dimensions by reading just the header bytes.
    Returns (width, height) or (0, 0) if unable to determine.
    """
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as response:
            # Read first 500 bytes which should contain JPEG header
            data = response.read(500)

            # Check for JPEG (most common)
            if data[:2] == b'\xff\xd8':
                i = 2
                while i < len(data) - 8:
                    if data[i] != 0xff:
                        break
                    marker = data[i + 1]
                    # SOF markers contain dimensions
                    if marker in (0xc0, 0xc2):
                        height = (data[i + 5] << 8) | data[i + 6]
                        width = (data[i + 7] << 8) | data[i + 8]
                        return (width, height)
                    # Skip to next marker
                    if marker in (0xd8, 0xd9, 0x01) or 0xd0 <= marker <= 0xd7:
                        i += 2
                    else:
                        length = (data[i + 2] << 8) | data[i + 3]
                        i += 2 + length

            # Check for PNG
            elif data[:8] == b'\x89PNG\r\n\x1a\n':
                if data[12:16] == b'IHDR':
                    width = int.from_bytes(data[16:20], 'big')
                    height = int.from_bytes(data[20:24], 'big')
                    return (width, height)

    except Exception:
        pass

    return (0, 0)


def filter_by_size_and_layout(results, size_filter="All", layout_filter="All"):
    """
    Filter results by size and layout (aspect ratio).
    Size: Large (>= 500k px), Medium (100k-500k px), Small (< 100k px), All
    Layout: Portrait (< 0.9 ratio), Square (0.9-1.1), Landscape (> 1.1), All
    """
    if size_filter == "All" and layout_filter == "All":
        return results

    filtered = []
    for result in results:
        width = result.get("width", 0)
        height = result.get("height", 0)

        # If we don't have dimensions, try to fetch them
        if (width == 0 or height == 0) and (size_filter != "All" or layout_filter != "All"):
            width, height = get_image_dimensions(result.get("image", ""))
            result["width"] = width
            result["height"] = height

        # If still no dimensions, include by default
        if width == 0 or height == 0:
            filtered.append(result)
            continue

        pixels = width * height
        ratio = width / height

        # Size filter
        if size_filter != "All":
            if size_filter == "Large" and pixels < SIZE_THRESHOLDS["Large"]:
                continue
            elif size_filter == "Medium":
                if pixels < SIZE_THRESHOLDS["Medium"] or pixels >= SIZE_THRESHOLDS["Large"]:
                    continue
            elif size_filter == "Small" and pixels >= SIZE_THRESHOLDS["Medium"]:
                continue

        # Layout filter
        if layout_filter != "All":
            min_ratio, max_ratio = ASPECT_THRESHOLDS.get(layout_filter, (0, float('inf')))
            if not (min_ratio <= ratio < max_ratio):
                continue

        filtered.append(result)

    return filtered


def search_babepedia(name, max_results=50):
    """
    Search Babepedia for performer images.
    Babepedia has curated photos of adult performers.
    Fetches from main page and gallery pages.
    """
    results = []

    try:
        # Babepedia uses underscores in URLs
        url_name = name.replace(" ", "_")
        base_url = f"https://www.babepedia.com/babe/{urllib.parse.quote(url_name)}"
        log.LogDebug(f"[Babepedia] Base URL: {base_url}")

        # Pages to try: main page and gallery subpages
        urls_to_try = [
            base_url,
            f"{base_url}/gallery",
            f"{base_url}/pics",
        ]

        seen = set()

        for url in urls_to_try:
            if len(results) >= max_results:
                break

            try:
                log.LogDebug(f"[Babepedia] Fetching: {url}")
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

                # Extract image links - format: href="/pics/Name.jpg"
                pattern = r'href="(/pics/[^"]+\.jpg)"'
                matches = re.findall(pattern, html)
                log.LogDebug(f"[Babepedia] Found {len(matches)} image links on {url}")

                for match in matches:
                    if match in seen:
                        continue
                    seen.add(match)

                    # Build full URLs
                    image_url = f"https://www.babepedia.com{match}"
                    # Thumbnail is the same but with _thumb3 suffix
                    thumb_url = image_url.replace(".jpg", "_thumb3.jpg")

                    results.append({
                        "thumbnail": thumb_url,
                        "image": image_url,
                        "title": f"{name} - Babepedia",
                        "source": "Babepedia",
                        "width": 0,
                        "height": 0,
                    })

                    if len(results) >= max_results:
                        break

            except urllib.error.HTTPError as e:
                log.LogDebug(f"[Babepedia] HTTP {e.code} for {url}")
                continue
            except Exception as e:
                log.LogDebug(f"[Babepedia] Error fetching {url}: {e}")
                continue

        log.LogInfo(f"[Babepedia] Found {len(results)} images for: {name}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.LogDebug(f"[Babepedia] Performer not found: {name}")
        else:
            log.LogWarning(f"[Babepedia] HTTP error {e.code}: {name}")
    except Exception as e:
        log.LogWarning(f"[Babepedia] Error: {e}")

    return results


def search_freeones(name, max_results=200, max_galleries=20):
    """
    Search FreeOnes for performer images.
    FreeOnes has extensive photo galleries for performers.
    Fetches gallery list, then drills into individual galleries.
    Note: FreeOnes uses complex CDN URLs - the thumbnails are often already large.
    """
    results = []

    try:
        # FreeOnes uses hyphens in URLs
        url_name = name.lower().replace(" ", "-")
        base_url = f"https://www.freeones.com/{urllib.parse.quote(url_name)}/photos"
        log.LogDebug(f"[FreeOnes] Base URL: {base_url}")

        seen_images = set()
        seen_galleries = set()
        gallery_urls = []

        # First, get list of gallery URLs from the main photos page
        try:
            log.LogDebug(f"[FreeOnes] Fetching gallery list from: {base_url}")
            req = urllib.request.Request(base_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

            # Extract gallery links - format: /performer-name/photos/gallery-slug
            gallery_pattern = rf'href="(/{re.escape(url_name)}/photos/[^"]+)"'
            gallery_matches = re.findall(gallery_pattern, html)
            log.LogDebug(f"[FreeOnes] Found {len(gallery_matches)} gallery link matches")

            for gallery_path in gallery_matches:
                if gallery_path not in seen_galleries and '/photos/' in gallery_path:
                    seen_galleries.add(gallery_path)
                    gallery_urls.append(f"https://www.freeones.com{gallery_path}")

            log.LogInfo(f"[FreeOnes] Found {len(gallery_urls)} unique galleries for: {name}")

        except Exception as e:
            log.LogWarning(f"[FreeOnes] Failed to get gallery list: {e}")

        # Fetch images from each gallery
        log.LogDebug(f"[FreeOnes] Processing up to {min(len(gallery_urls), max_galleries)} galleries")
        for i, gallery_url in enumerate(gallery_urls[:max_galleries]):
            if len(results) >= max_results:
                log.LogDebug(f"[FreeOnes] Reached max results ({max_results}), stopping")
                break

            try:
                log.LogDebug(f"[FreeOnes] Fetching gallery {i+1}: {gallery_url}")
                req = urllib.request.Request(gallery_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

                # Extract image URLs from gallery page
                pattern = r'(https://(?:thumbs|ch-thumbs|img)\.freeones\.com/[^"\']+\.(?:jpg|webp|png))'
                matches = re.findall(pattern, html)
                log.LogDebug(f"[FreeOnes] Gallery {i+1}: Found {len(matches)} image URLs")

                added_from_gallery = 0
                for thumb_url in matches:
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
                    added_from_gallery += 1

                    if len(results) >= max_results:
                        break

                log.LogDebug(f"[FreeOnes] Gallery {i+1}: Added {added_from_gallery} unique images")

            except urllib.error.HTTPError as e:
                log.LogDebug(f"[FreeOnes] Gallery {i+1}: HTTP {e.code}")
                continue
            except Exception as e:
                log.LogDebug(f"[FreeOnes] Gallery {i+1}: Error - {e}")
                continue

        log.LogInfo(f"[FreeOnes] Found {len(results)} images for: {name}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.LogDebug(f"[FreeOnes] Performer not found: {name}")
        else:
            log.LogWarning(f"[FreeOnes] HTTP error {e.code}: {name}")
    except Exception as e:
        log.LogWarning(f"[FreeOnes] Error: {e}")

    return results


def search_pornpics(name, max_results=200, max_galleries=20):
    """
    Search PornPics for performer images.
    PornPics has extensive galleries organized by performer.
    Extracts gallery set IDs from performer page, then drills into each gallery.
    """
    results = []

    try:
        # PornPics uses hyphens and lowercase
        url_name = name.lower().replace(" ", "-")
        base_url = f"https://www.pornpics.com/pornstars/{urllib.parse.quote(url_name)}/"
        log.LogDebug(f"[PornPics] Base URL: {base_url}")

        seen_images = set()
        gallery_ids = []

        # First, get the performer page
        try:
            log.LogDebug(f"[PornPics] Fetching performer page: {base_url}")
            req = urllib.request.Request(base_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

            # Get profile image first
            profile_pattern = r'(https://cdni\.pornpics\.com/models/[^"\']+\.jpg)'
            profile_matches = re.findall(profile_pattern, html)
            log.LogDebug(f"[PornPics] Found {len(profile_matches)} profile image matches")
            for img_url in profile_matches[:1]:
                if img_url not in seen_images:
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
            # The 8-digit number (67655164) is the gallery/set ID
            set_id_pattern = r'/(\d{8})/\d{8}_'
            set_id_matches = re.findall(set_id_pattern, html)
            gallery_ids = list(set(set_id_matches))  # Deduplicate

            log.LogDebug(f"[PornPics] Found {len(gallery_ids)} unique gallery IDs for: {name}")

        except Exception as e:
            log.LogDebug(f"[PornPics] Failed to get performer page: {e}")

        # Drill into each gallery using /galleries/{set_id}/
        log.LogDebug(f"[PornPics] Processing up to {min(len(gallery_ids), max_galleries)} galleries")
        for i, gallery_id in enumerate(gallery_ids[:max_galleries]):
            if len(results) >= max_results:
                log.LogDebug(f"[PornPics] Reached max results ({max_results}), stopping")
                break

            gallery_url = f"https://www.pornpics.com/galleries/{gallery_id}/"

            try:
                log.LogDebug(f"[PornPics] Fetching gallery {i+1}: {gallery_url}")
                req = urllib.request.Request(gallery_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

                # Extract image URLs from gallery
                pattern = r'(https://cdni\.pornpics\.com/(?:460|1280)/[^"\'>\s]+\.jpg)'
                matches = re.findall(pattern, html)
                log.LogDebug(f"[PornPics] Gallery {i+1}: Found {len(matches)} image URLs")

                added_from_gallery = 0
                for img_url in matches:
                    if img_url in seen_images:
                        continue
                    seen_images.add(img_url)

                    # Use 460 as thumbnail, 1280 as full
                    thumb_url = img_url.replace("/1280/", "/460/")
                    image_url = img_url.replace("/460/", "/1280/")

                    results.append({
                        "thumbnail": thumb_url,
                        "image": image_url,
                        "title": f"{name} - PornPics",
                        "source": "PornPics",
                        "width": 0,
                        "height": 0,
                    })
                    added_from_gallery += 1

                    if len(results) >= max_results:
                        break

                log.LogDebug(f"[PornPics] Gallery {i+1}: Added {added_from_gallery} unique images")

            except urllib.error.HTTPError as e:
                log.LogDebug(f"[PornPics] Gallery {i+1}: HTTP {e.code}")
                continue
            except Exception as e:
                log.LogDebug(f"[PornPics] Gallery {i+1}: Error - {e}")
                continue

        log.LogInfo(f"[PornPics] Found {len(results)} images for: {name}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.LogDebug(f"[PornPics] Performer not found: {name}")
        else:
            log.LogWarning(f"[PornPics] HTTP error {e.code}: {name}")
    except Exception as e:
        log.LogWarning(f"[PornPics] Error: {e}")

    return results


def search_elitebabes(name, max_results=100, max_galleries=10):
    """
    Search EliteBabes for performer images.
    EliteBabes has high-quality photosets with multiple size options.
    URL format: https://cdn.elitebabes.com/content/XXXXXX/filename_w400.jpg
    Sizes: _w200, _w400, _w600, _w800, or no suffix for full size (~400KB).
    """
    results = []

    try:
        # EliteBabes uses hyphens and lowercase
        url_name = name.lower().replace(" ", "-")
        base_url = f"https://www.elitebabes.com/model/{urllib.parse.quote(url_name)}/"
        log.LogDebug(f"[EliteBabes] Base URL: {base_url}")

        seen_images = set()
        gallery_urls = []

        # First, get the model page
        try:
            log.LogDebug(f"[EliteBabes] Fetching model page: {base_url}")
            req = urllib.request.Request(base_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

            # Extract gallery links - format: /gallery-name-12345/
            gallery_pattern = r'href="(https://www\.elitebabes\.com/[^"]+/)"[^>]*class="[^"]*gallery[^"]*"'
            gallery_matches = re.findall(gallery_pattern, html)

            # Also try simpler pattern for gallery links
            if not gallery_matches:
                gallery_pattern2 = r'href="(https://www\.elitebabes\.com/[a-z0-9-]+-\d+/)"'
                gallery_matches = re.findall(gallery_pattern2, html)

            gallery_urls = list(set(gallery_matches))[:max_galleries]
            log.LogDebug(f"[EliteBabes] Found {len(gallery_urls)} gallery links")

        except Exception as e:
            log.LogDebug(f"[EliteBabes] Failed to get model page: {e}")

        # Fetch images from each gallery
        log.LogDebug(f"[EliteBabes] Processing up to {min(len(gallery_urls), max_galleries)} galleries")
        for i, gallery_url in enumerate(gallery_urls[:max_galleries]):
            if len(results) >= max_results:
                log.LogDebug(f"[EliteBabes] Reached max results ({max_results}), stopping")
                break

            try:
                log.LogDebug(f"[EliteBabes] Fetching gallery {i+1}: {gallery_url}")
                req = urllib.request.Request(gallery_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

                # Extract image URLs - format: cdn.elitebabes.com/content/XXXXXX/filename_wNNN.jpg
                pattern = r'(https://cdn\.elitebabes\.com/content/[^"\'>\s]+_w(?:200|400|600|800)\.jpg)'
                matches = re.findall(pattern, html)
                log.LogDebug(f"[EliteBabes] Gallery {i+1}: Found {len(matches)} image URLs")

                added_from_gallery = 0
                for img_url in matches:
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
                    added_from_gallery += 1

                    if len(results) >= max_results:
                        break

                log.LogDebug(f"[EliteBabes] Gallery {i+1}: Added {added_from_gallery} unique images")

            except urllib.error.HTTPError as e:
                log.LogDebug(f"[EliteBabes] Gallery {i+1}: HTTP {e.code}")
                continue
            except Exception as e:
                log.LogDebug(f"[EliteBabes] Gallery {i+1}: Error - {e}")
                continue

        log.LogInfo(f"[EliteBabes] Found {len(results)} images for: {name}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.LogDebug(f"[EliteBabes] Performer not found: {name}")
        else:
            log.LogWarning(f"[EliteBabes] HTTP error {e.code}: {name}")
    except Exception as e:
        log.LogWarning(f"[EliteBabes] Error: {e}")

    return results


def search_boobpedia(name, max_results=50):
    """
    Search Boobpedia for performer images.
    Boobpedia is a MediaWiki-style site with performer photos.
    Thumbnails are relative paths: /wiki/images/thumb/X/XX/Filename.jpg/NNNpx-Filename.jpg
    Full size: /wiki/images/X/XX/Filename.jpg
    """
    results = []

    try:
        # Boobpedia uses underscores in URLs (wiki style)
        url_name = name.replace(" ", "_")
        base_url = f"https://www.boobpedia.com/boobs/{urllib.parse.quote(url_name)}"
        log.LogDebug(f"[Boobpedia] Base URL: {base_url}")

        seen = set()

        try:
            log.LogDebug(f"[Boobpedia] Fetching: {base_url}")
            req = urllib.request.Request(base_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

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

        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.LogDebug(f"[Boobpedia] Performer not found: {name}")
            else:
                log.LogWarning(f"[Boobpedia] HTTP {e.code} for {base_url}")
        except Exception as e:
            log.LogDebug(f"[Boobpedia] Error fetching {base_url}: {e}")

    except Exception as e:
        log.LogWarning(f"[Boobpedia] Error: {e}")

    return results


def search_javdatabase(name, max_results=100, max_pages=5):
    """
    Search JavDatabase for JAV performer images.
    JavDatabase has idol profiles with photos and movie covers.
    URL format: https://www.javdatabase.com/idols/name-here/
    Image format: https://www.javdatabase.com/idolimages/full/name.webp
    """
    results = []

    try:
        # JavDatabase uses lowercase hyphenated names
        url_name = name.lower().replace(" ", "-")
        base_url = f"https://www.javdatabase.com/idols/{urllib.parse.quote(url_name)}/"
        log.LogDebug(f"[JavDatabase] Base URL: {base_url}")

        seen = set()

        # Try to get the main profile page and paginated gallery pages
        pages_to_try = [base_url] + [f"{base_url}?ipage={i}" for i in range(2, max_pages + 1)]

        for page_url in pages_to_try:
            if len(results) >= max_results:
                break

            try:
                log.LogDebug(f"[JavDatabase] Fetching: {page_url}")
                req = urllib.request.Request(page_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

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

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    log.LogDebug(f"[JavDatabase] Page not found: {page_url}")
                    break  # No more pages
                else:
                    log.LogDebug(f"[JavDatabase] HTTP {e.code} for {page_url}")
                continue
            except Exception as e:
                log.LogDebug(f"[JavDatabase] Error fetching {page_url}: {e}")
                continue

        log.LogInfo(f"[JavDatabase] Found {len(results)} images for: {name}")

    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.LogDebug(f"[JavDatabase] Performer not found: {name}")
        else:
            log.LogWarning(f"[JavDatabase] HTTP error {e.code}: {name}")
    except Exception as e:
        log.LogWarning(f"[JavDatabase] Error: {e}")

    return results


def search_bing_images(query, size="Large", layout="All", max_results=20):
    """
    Search Bing Images with safe search off.
    Used as a fallback when performer-specific sites don't have results.
    """
    results = []

    # Size filter mapping
    size_map = {"Large": "large", "Medium": "medium", "Small": "small", "All": ""}
    layout_map = {"Portrait": "tall", "Landscape": "wide", "Square": "square", "All": ""}

    size_param = size_map.get(size, "")
    layout_param = layout_map.get(layout, "")

    # Build filter string
    filters = []
    if size_param:
        filters.append(f"filterui:imagesize-{size_param}")
    if layout_param:
        filters.append(f"filterui:aspect-{layout_param}")
    filters.append("filterui:photo-photo")

    filter_str = "+".join(filters)
    log.LogDebug(f"[Bing] Query: {query}, filters: {filter_str}")

    try:
        params = {
            "q": query,
            "first": "1",
            "count": str(max_results),
            "qft": filter_str,
            "form": "IRFLTR",
            "safeSearch": "Off",
        }

        url = "https://www.bing.com/images/async?" + urllib.parse.urlencode(params)
        log.LogDebug(f"[Bing] Fetching: {url[:100]}...")

        headers = {
            **HEADERS,
            "Referer": "https://www.bing.com/images/search?" + urllib.parse.urlencode({"q": query}),
        }

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')
            log.LogDebug(f"[Bing] Received {len(html)} bytes")

        # Parse image data from Bing's response
        pattern = r'"murl":"([^"]+)".*?"turl":"([^"]+)".*?"t":"([^"]*)"'
        matches = re.findall(pattern, html)
        log.LogDebug(f"[Bing] Primary pattern found {len(matches)} matches")

        for match in matches[:max_results]:
            image_url = match[0].replace("\\u0026", "&")
            thumb_url = match[1].replace("\\u0026", "&")
            title = unescape(match[2].replace("\\u0026", "&"))

            results.append({
                "thumbnail": thumb_url,
                "image": image_url,
                "title": title,
                "source": "Bing",
                "width": 0,
                "height": 0,
            })

        # Fallback pattern if first didn't work
        if not results:
            log.LogDebug("[Bing] Primary pattern failed, trying fallback pattern")
            pattern2 = r'class="iusc"[^>]*m="([^"]+)"'
            matches2 = re.findall(pattern2, html)

            for match in matches2[:max_results]:
                try:
                    data = match.replace("&quot;", '"').replace("&amp;", "&")
                    murl_match = re.search(r'"murl":"([^"]+)"', data)
                    turl_match = re.search(r'"turl":"([^"]+)"', data)
                    title_match = re.search(r'"t":"([^"]*)"', data)

                    if murl_match and turl_match:
                        results.append({
                            "thumbnail": turl_match.group(1),
                            "image": murl_match.group(1),
                            "title": unescape(title_match.group(1)) if title_match else "",
                            "source": "Bing",
                            "width": 0,
                            "height": 0,
                        })
                except:
                    continue

        log.LogInfo(f"[Bing] Found {len(results)} images for query: {query}")

    except Exception as e:
        log.LogWarning(f"[Bing] Error: {e}")

    return results


def search_single_source(source, name, query, size_filter="All", layout_filter="All"):
    """
    Search a single source for images.
    Used for streaming results to the client one source at a time.
    """
    performer_name = name.strip()
    results = []

    log.LogInfo(f"[{source}] Starting search for: {performer_name}")
    log.LogDebug(f"[{source}] Full query: {query}")

    start_time = time.time()

    if source == "babepedia":
        results = search_babepedia(performer_name, 50)
    elif source == "pornpics":
        results = search_pornpics(performer_name, 200, 20)
    elif source == "freeones":
        results = search_freeones(performer_name, 200, 20)
    elif source == "elitebabes":
        results = search_elitebabes(performer_name, 100, 10)
    elif source == "boobpedia":
        results = search_boobpedia(performer_name, 50)
    elif source == "javdatabase":
        results = search_javdatabase(performer_name, 100, 5)
    elif source == "bing":
        # Use the full query (with suffix) for Bing
        results = search_bing_images(query, size_filter, layout_filter, 30)
    else:
        log.LogWarning(f"Unknown source: {source}")

    elapsed = time.time() - start_time
    log.LogDebug(f"[{source}] Search completed in {elapsed:.2f}s, found {len(results)} results")

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

    log.LogInfo(f"[{source}] Returning {len(unique_results)} unique images")
    return unique_results


def search_all_sources(name, query, size_filter="All", layout_filter="All"):
    """
    Search all sources and combine results.
    Prioritizes adult-specific sites, falls back to Bing.
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
    babepedia_results = search_babepedia(performer_name, 50)
    all_results.extend(babepedia_results)

    # 2. PornPics - drills into galleries (up to 200 images from 20 galleries)
    pornpics_results = search_pornpics(performer_name, 200, 20)
    all_results.extend(pornpics_results)

    # 3. FreeOnes - drills into galleries (up to 200 images from 20 galleries)
    freeones_results = search_freeones(performer_name, 200, 20)
    all_results.extend(freeones_results)

    # 4. Bing as fallback if we didn't find much from adult sites
    if len(all_results) < 20:
        # Use the full query (with suffix) for Bing, pass filters to Bing API
        bing_results = search_bing_images(query, size_filter, layout_filter, 30)
        all_results.extend(bing_results)

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
            # Search single source (streaming mode)
            results = search_single_source(
                source=source,
                name=performer_name,
                query=query,
                size_filter=size_filter,
                layout_filter=layout_filter
            )
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
                "error": str(e)
            }
        }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
