import os
import tempfile
import urllib.request
import urllib.error
import utils.logger as log

# JPEG magic bytes (SOI marker)
JPEG_MAGIC = b'\xff\xd8\xff'
# PNG magic bytes
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
# WebP magic bytes (RIFF....WEBP)
WEBP_MAGIC = b'RIFF'
WEBP_HEADER = b'WEBP'


def _is_valid_image(filepath):
    """Check if a file is a valid image by inspecting its magic bytes.

    Args:
        filepath: Path to the file to check

    Returns:
        bool: True if the file appears to be a valid image
    """
    try:
        with open(filepath, 'rb') as f:
            header = f.read(12)

        if len(header) < 4:
            return False

        # Check for JPEG
        if header[:3] == JPEG_MAGIC:
            return True

        # Check for PNG
        if header[:8] == PNG_MAGIC:
            return True

        # Check for WebP (RIFF....WEBP)
        if header[:4] == WEBP_MAGIC and header[8:12] == WEBP_HEADER:
            return True

        return False
    except Exception:
        return False


def download_image(url, dest_filepath, settings):
    """Download an image from a URL and save it to a file.

    Only saves the file if the download succeeds and the content is a valid image.
    Logs errors for troubleshooting.

    Args:
        url: The URL to download from
        dest_filepath: Where to save the image
        settings: Plugin settings dict (checks dry_run)

    Returns:
        bool: True if successful, False otherwise
    """
    if settings.get("dry_run", False):
        return True

    # Sanitize URL for logging (hide API key)
    safe_url = url.split('&apikey=')[0] + '&apikey=***' if '&apikey=' in url else url

    log.debug(f"Downloading image from {safe_url}")

    # Download to a temp file first, then validate before moving
    temp_fd, temp_path = tempfile.mkstemp(suffix='.tmp')
    os.close(temp_fd)

    try:
        # Make the request
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=30) as response:
            # Check HTTP status
            if response.status != 200:
                log.error(f"Failed to download image: HTTP {response.status} from {safe_url}")
                return False

            # Check content type
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                log.error(f"Invalid content type '{content_type}' from {safe_url} (expected image/*)")
                return False

            # Read and save to temp file
            with open(temp_path, 'wb') as f:
                f.write(response.read())

        # Validate the downloaded file is actually an image
        if not _is_valid_image(temp_path):
            file_size = os.path.getsize(temp_path)
            log.error(f"Downloaded file is not a valid image ({file_size} bytes) from {safe_url}")
            return False

        # Create destination directory if needed
        dest_dir = os.path.dirname(dest_filepath)
        if dest_dir and not os.path.exists(dest_dir):
            os.makedirs(dest_dir)

        # Move temp file to destination
        os.replace(temp_path, dest_filepath)
        log.debug(f"Saved image to {dest_filepath}")
        return True

    except urllib.error.HTTPError as e:
        log.error(f"HTTP error downloading image: {e.code} {e.reason} from {safe_url}")
        return False
    except urllib.error.URLError as e:
        log.error(f"URL error downloading image: {e.reason} from {safe_url}")
        return False
    except TimeoutError:
        log.error(f"Timeout downloading image from {safe_url}")
        return False
    except Exception as e:
        log.error(f"Error downloading image from {safe_url}: {e}")
        return False
    finally:
        # Clean up temp file if it still exists
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def rename_file(filepath, dest_filepath, settings):
    dir = os.path.dirname(dest_filepath)
    try:
        if not os.path.exists(dir) and settings["dry_run"] is False:
            os.makedirs(dir)  # pragma: no cover
        try:
            if settings["dry_run"] is False:
                os.rename(filepath, dest_filepath)  # pragma: no cover
                log.debug(f"Renamed {filepath} to {dest_filepath}")  # pragma: no cover
            return dest_filepath
        except Exception as err:  # pragma: no cover
            log.error(f"Error renaming file {filepath} to {dest_filepath}: {str(err)}")
            return False
    except Exception as d_err:
        log.error(f"Error creating directory {dir}: {str(d_err)}")
        return False


def replace_file_ext(filepath, ext, suffix=""):
    path = os.path.splitext(filepath)
    return path[0] + suffix + "." + ext
