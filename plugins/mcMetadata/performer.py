import os
import utils.logger as log
from utils.files import download_image
from utils.paths import PathEscapeError, join_under, sanitize_component
from utils.run_flow import is_dry_run

# Constants
BATCH_SIZE = 100


def process_all_performers(stash, settings, api_key):
    """Process all performers to copy their images to media server.

    Args:
        stash: StashInterface instance
        settings: Plugin settings dict
        api_key: Stash API key for image URLs
    """
    count = stash.find_performers(
        f={},
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.info(f"Found {count} performers to process")

    if count == 0:
        log.info("No performers to process")
        return

    # Calculate total pages (ceiling division)
    total_pages = (count + BATCH_SIZE - 1) // BATCH_SIZE
    processed = 0
    errors = 0

    # Pages are 1-indexed in Stash API
    for page in range(1, total_pages + 1):
        start = (page - 1) * BATCH_SIZE + 1
        end = min(page * BATCH_SIZE, count)

        log.info(f"Processing performers {start}-{end} of {count} (page {page}/{total_pages})")

        performers = stash.find_performers(
            f={},
            filter={"page": page, "per_page": BATCH_SIZE},
        )

        for performer in performers:
            try:
                process_performer(performer, settings, api_key, overwrite=True)
                processed += 1
            except Exception as err:
                errors += 1
                log.error(f"Error processing performer {performer.get('name', 'unknown')}: {err}")

    log.info(f"Bulk performer update complete. Processed: {processed}, Errors: {errors}")


def process_performer(performer, settings, api_key, overwrite=False):
    """Process a single performer to copy their image to media server.

    Args:
        performer: Performer dict from Stash API
        settings: Plugin settings dict
        api_key: Stash API key for image URLs
        overwrite: If True, overwrite existing images
    """
    performer_name = performer.get("name", "unknown")
    log.debug(f"Processing performer {performer_name}")

    if not settings.get("enable_actor_images", False):
        return

    if not performer.get("image_path"):
        log.debug(f"Skipping performer {performer_name}: no image available")
        return

    image_path = get_actor_image_path(performer_name, settings)
    if not image_path:
        return

    image_url = f"{performer['image_path']}&apikey={api_key}"
    dest_dir = os.path.dirname(image_path)

    # Check if we should skip this performer
    if not overwrite and os.path.exists(image_path):
        log.debug(f"Skipping performer {performer_name}: image already exists")
        return

    # In dry run mode, just log what would happen
    if is_dry_run(settings):
        if os.path.exists(image_path):
            log.info(f"[DRY RUN] Would overwrite image for {performer_name}: {image_path}")
        else:
            log.info(f"[DRY RUN] Would create image for {performer_name}: {image_path}")
        return

    # Create directory if needed
    if not os.path.exists(dest_dir):
        try:
            os.makedirs(dest_dir)
            log.debug(f"Created directory: {dest_dir}")
        except OSError as err:
            log.error(f"Failed to create directory {dest_dir}: {err}")
            return

    # Download the image
    download_image(image_url, image_path, settings)


def get_actor_image_path(performer_name, settings):
    """Get the destination path for a performer image based on media server type.

    Args:
        performer_name: Name of the performer
        settings: Plugin settings dict

    Returns:
        str: Full path for the performer image, or None if invalid or unsupported
    """
    if not performer_name:
        return None

    base_path = settings.get("actor_metadata_path", "")
    if not base_path:
        return None

    media_server = str(settings.get("media_server", "jellyfin")).lower()
    # One safe folder name; the letter folder comes from it (still the raw first character)
    safe_name = sanitize_component(performer_name)
    first_letter = safe_name[0]

    if media_server not in ("jellyfin", "emby"):
        if media_server == "plex":
            # Plex manages performer images internally; no People folder to export to
            log.debug(f"Plex does not support external performer images, skipping {performer_name}")
        else:
            log.warning(f"Unknown media server type: {media_server}")
        return None

    try:
        if media_server == "jellyfin":
            # Jellyfin: /metadata/People/J/John Doe/folder.jpg
            return join_under(base_path, first_letter, safe_name, "folder.jpg")
        # Emby: /metadata/People/John Doe/folder.jpg (no A-Z subfolders)
        return join_under(base_path, safe_name, "folder.jpg")
    except PathEscapeError as err:
        log.warning(f"Skipping performer image for {performer_name!r}: {err}")
        return None


def process_performer_hook(stash, performer_id, settings, api_key):
    """Performer.Update.Post: re-export that performer's image when actor images are on."""
    if not settings.get("enable_actor_images", False):
        log.debug(f"Performer {performer_id}: actor images are disabled, skipping")
        return
    performer = stash.find_performer(performer_id, False, "id name image_path")
    if not performer:
        log.warning(f"Performer {performer_id} not found")
        return
    if is_dry_run(settings):
        log.info(f"[DRY RUN] Would re-export image for performer {performer.get('name', performer_id)}")
        return
    process_performer(performer, settings, api_key, overwrite=True)
