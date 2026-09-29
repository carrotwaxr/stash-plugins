import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error
import utils.logger as log
from utils.run_flow import is_dry_run
from utils.videos import is_video

# JPEG magic bytes (SOI marker)
JPEG_MAGIC = b'\xff\xd8\xff'
# JPEG end-of-image marker
JPEG_EOI = b'\xff\xd9'
# PNG magic bytes
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
# PNG IEND chunk marker
PNG_IEND = b'IEND'
# WebP magic bytes (RIFF....WEBP)
WEBP_MAGIC = b'RIFF'
WEBP_HEADER = b'WEBP'

# Minimum valid image size in bytes (reject truncated downloads)
MIN_IMAGE_SIZE = 1000

# Retry settings for transient network errors
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds


def _is_valid_image(filepath):
    """Check if a file is a valid image by inspecting magic bytes and end markers.

    Validates both the header (magic bytes) and tail (end-of-image markers) to
    detect truncated downloads that have valid headers but incomplete content.

    Args:
        filepath: Path to the file to check

    Returns:
        bool: True if the file appears to be a valid, complete image
    """
    try:
        file_size = os.path.getsize(filepath)

        if file_size < MIN_IMAGE_SIZE:
            return False

        with open(filepath, 'rb') as f:
            header = f.read(12)

            if len(header) < 4:
                return False

            # Check for JPEG
            if header[:3] == JPEG_MAGIC:
                # Verify JPEG EOI marker in last 8 bytes
                f.seek(-8, 2)
                tail = f.read(8)
                return JPEG_EOI in tail

            # Check for PNG
            if header[:8] == PNG_MAGIC:
                # Verify PNG IEND chunk in last 12 bytes
                f.seek(-12, 2)
                tail = f.read(12)
                return PNG_IEND in tail

            # Check for WebP (RIFF....WEBP)
            if header[:4] == WEBP_MAGIC and header[8:12] == WEBP_HEADER:
                return True

        return False
    except Exception:
        return False


def authenticated_url(url, settings, api_key):
    """Add auth to a Stash image URL: nothing with a session cookie, else the API key, else nothing."""
    if (settings or {}).get("session_cookie") or not api_key:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}apikey={urllib.parse.quote(str(api_key), safe='')}"


def _safe_url(url):
    """The URL with any apikey query value hidden, for logging."""
    parts = urllib.parse.urlsplit(url)
    if "apikey=" not in parts.query:
        return url
    query = "&".join(
        "apikey=***" if p.startswith("apikey=") else p for p in parts.query.split("&")
    )
    return urllib.parse.urlunsplit(parts._replace(query=query))


_APIKEY_VALUE = re.compile(r"(apikey=)[^&\s'\"]*", re.IGNORECASE)


def _scrub(text):
    """text (e.g. an exception's) with every apikey= value hidden, for logging."""
    return _APIKEY_VALUE.sub(r"\1***", str(text))


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Don't follow redirects: urllib would copy the session Cookie header to the new
    URL, possibly another host. A 3xx then raises HTTPError with its code."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _urlopen(request, timeout):
    """urllib's urlopen, refusing redirects."""
    return _OPENER.open(request, timeout=timeout)


def download_image(url, dest_filepath, settings):
    """Download an image from a URL and save it to a file.

    Only saves the file if the download succeeds and the content is a valid image.
    Retries on transient network errors. Validates Content-Length, minimum file size,
    and image end markers to prevent saving corrupt/truncated files.

    Args:
        url: The URL to download from
        dest_filepath: Where to save the image
        settings: Plugin settings dict (checks dry_run)

    Returns:
        bool: True if successful (or would be, in a dry run), False otherwise
    """
    if is_dry_run(settings):
        log.info(f"[DRY RUN] Would download image to: {dest_filepath}")
        return True

    # Sanitize URL for logging (hide API key)
    safe_url = _safe_url(url)

    log.debug(f"Downloading image from {safe_url}")

    for attempt in range(1, MAX_RETRIES + 2):  # attempts 1 through MAX_RETRIES+1
        # Download to a temp file first, then validate before moving
        temp_fd, temp_path = tempfile.mkstemp(suffix='.tmp')
        os.close(temp_fd)

        try:
            # Make the request
            request = urllib.request.Request(url)
            cookie = (settings or {}).get("session_cookie")
            if cookie and cookie.get("value"):
                request.add_header("Cookie", f"{cookie.get('name') or 'session'}={cookie['value']}")
            with _urlopen(request, timeout=30) as response:
                # Check HTTP status
                if response.status != 200:
                    log.error(f"Failed to download image: HTTP {response.status} from {safe_url}")
                    return False

                # Check content type
                content_type = response.headers.get('Content-Type', '')
                if content_type.startswith('text/html'):
                    log.error(
                        f"Got an HTML page instead of an image from {safe_url}. Stash likely redirected "
                        "to its login page: the plugin isn't authenticated. Check that Stash passes a "
                        "session to plugins, or generate an API key in Settings > Security."
                    )
                    return False
                if not content_type.startswith('image/'):
                    log.error(f"Invalid content type '{content_type}' from {safe_url} (expected image/*)")
                    return False

                expected_size = response.headers.get('Content-Length')

                # Read and save to temp file
                data = response.read()
                with open(temp_path, 'wb') as f:
                    f.write(data)

            actual_size = len(data)

            # Validate Content-Length if provided
            if expected_size is not None:
                expected_size = int(expected_size)
                if actual_size != expected_size:
                    log.error(
                        f"Size mismatch: got {actual_size} bytes, expected {expected_size} bytes from {safe_url}"
                    )
                    if attempt <= MAX_RETRIES:
                        log.info(f"Retrying download (attempt {attempt + 1}/{MAX_RETRIES + 1})...")
                        time.sleep(RETRY_DELAY)
                        continue
                    return False

            # Check minimum size
            if actual_size < MIN_IMAGE_SIZE:
                log.error(
                    f"Downloaded file too small ({actual_size} bytes, minimum {MIN_IMAGE_SIZE}) from {safe_url}"
                )
                return False

            # Validate the downloaded file is actually a complete image
            if not _is_valid_image(temp_path):
                log.error(f"Downloaded file is not a valid image ({actual_size} bytes) from {safe_url}")
                if attempt <= MAX_RETRIES:
                    log.info(f"Retrying download (attempt {attempt + 1}/{MAX_RETRIES + 1})...")
                    time.sleep(RETRY_DELAY)
                    continue
                return False

            # Create destination directory if needed
            dest_dir = os.path.dirname(dest_filepath)
            if dest_dir and not os.path.exists(dest_dir):
                os.makedirs(dest_dir)

            # Move temp file to destination (shutil.move handles cross-filesystem moves)
            shutil.move(temp_path, dest_filepath)
            log.debug(f"Saved image ({actual_size} bytes) to {dest_filepath}")
            return True

        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                log.error(
                    f"Image download from {safe_url} was redirected (HTTP {e.code}), likely to the "
                    "login page: not authenticated. Check that Stash passes a session to plugins, "
                    "or generate an API key in Settings > Security."
                )
            else:
                log.error(f"HTTP error downloading image: {e.code} {_scrub(e.reason)} from {safe_url}")
            return False
        except urllib.error.URLError as e:
            log.error(f"URL error downloading image: {_scrub(e.reason)} from {safe_url}")
            if attempt <= MAX_RETRIES:
                log.info(f"Retrying download (attempt {attempt + 1}/{MAX_RETRIES + 1})...")
                time.sleep(RETRY_DELAY)
                continue
            return False
        except TimeoutError:
            log.error(f"Timeout downloading image from {safe_url}")
            if attempt <= MAX_RETRIES:
                log.info(f"Retrying download (attempt {attempt + 1}/{MAX_RETRIES + 1})...")
                time.sleep(RETRY_DELAY)
                continue
            return False
        except Exception as e:
            log.error(f"Error downloading image from {safe_url}: {_scrub(e)}")
            return False
        finally:
            # Clean up temp file if it still exists
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    return False


def _owner_stem(name, stems):
    """The longest stem in stems that name starts with, followed by "." or "-"; None if none."""
    owner = None
    for stem in stems:
        if (
            len(name) > len(stem)
            and name.startswith(stem)
            and name[len(stem)] in ".-"
            and (owner is None or len(stem) > len(owner))
        ):
            owner = stem
    return owner


def find_sidecars(video_path, other_videos=()):
    """Files in the video's folder that belong to it: named `<stem>.<anything>` or `<stem>-<anything>`.

    A file belongs to the video in its folder with the longest stem it starts with, so
    `Show-Part2.srt` is Show-Part2.mp4's, not Show.mp4's. other_videos are more video
    paths to count as in the folder (a scene's videos this run already moved away).
    Excludes the video itself and every other video.
    """
    folder = os.path.dirname(video_path)
    video_name = os.path.basename(video_path)
    stem = os.path.splitext(video_name)[0]
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    stems = {stem}
    stems.update(
        os.path.splitext(n)[0] for n in names if is_video(n) and os.path.isfile(os.path.join(folder, n))
    )
    stems.update(
        os.path.splitext(os.path.basename(p))[0] for p in other_videos if os.path.dirname(p) == folder
    )
    found = []
    for name in names:
        if name == video_name or is_video(name) or _owner_stem(name, stems) != stem:
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found.append(path)
    return found


def is_same_file(path, other):
    """True if other exists and is path's own file: a case-only rename (movie.mp4 -> Movie.mp4)
    on a case-insensitive filesystem (Windows, macOS), which isn't a collision."""
    try:
        return os.path.exists(other) and os.path.samefile(path, other)
    except OSError:
        return False


def rename_file(filepath, dest_filepath, settings):
    """Move a file, never overwriting (a case-only rename is allowed). Returns the destination, or False."""
    if os.path.exists(dest_filepath) and not is_same_file(filepath, dest_filepath):
        log.warning(f"Not moving {filepath}: destination already exists at {dest_filepath}")
        return False
    try:
        if is_dry_run(settings):
            return dest_filepath
        os.makedirs(os.path.dirname(dest_filepath), exist_ok=True)
        shutil.move(filepath, dest_filepath)
        log.debug(f"Renamed {filepath} to {dest_filepath}")
        return dest_filepath
    except Exception as err:
        log.error(f"Error renaming file {filepath} to {dest_filepath}: {str(err)}")
        return False


def replace_file_ext(filepath, ext, suffix=""):
    path = os.path.splitext(filepath)
    return path[0] + suffix + "." + ext
