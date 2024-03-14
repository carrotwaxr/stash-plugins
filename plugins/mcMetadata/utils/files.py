import os
import urllib.request
import stashapi.log as log


def download_image(url, dest_filepath, settings):  # pragma: no cover
    if settings["dry_run"] is False:
        urllib.request.urlretrieve(url, dest_filepath)
        log.debug(f"Downloading image {url} to {dest_filepath}")


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
