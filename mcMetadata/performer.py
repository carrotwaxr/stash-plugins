import os
import stashapi.log as log
from utils.files import download_image

# Constants
BATCH_SIZE = 100


def process_all_performers(stash, settings, api_key):
    count = stash.find_performers(
        f={},
        filter={"per_page": 1},
        get_count=True,
    )[0]

    log.debug(f"{str(count)} performers to scan.")

    for r in range(1, int(count / BATCH_SIZE) + 1):
        start = r * BATCH_SIZE
        end = start + BATCH_SIZE
        if end > count:
            end = count

        log.debug(f"Processing {str(start)}-{str(end)}")

        performers = stash.find_performers(
            f={},
            filter={"page": r, "per_page": BATCH_SIZE},
        )

        for performer in performers:
            process_performer(performer, settings, api_key, True)


def process_performer(performer, settings, api_key, overwrite=False):
    try:
        log.debug(f"Processing performer {performer['name']}")
        if settings["enable_actor_images"] is False:
            return

        if performer["image_path"]:
            image_path = __get_actor_image_path(performer["name"], settings)
            image_url = f"{performer['image_path']}&apikey={api_key}"
            dir = os.path.dirname(image_path)

            if not os.path.exists(dir) and settings["dry_run"] is False:
                os.makedirs(dir)

            if overwrite is True or not os.path.exists(image_path):
                download_image(image_url, image_path, settings)
        else:
            log.debug(
                f"Skipping performer {performer['name']} because they have no image_path"
            )

    except Exception as err:
        log.error(f"Error processing Performer {performer['name']}: {str(err)}")


def __get_actor_image_path(performer_name, settings):
    match settings["media_server"]:
        case "jellyfin":
            return f"{settings['actor_metadata_path']}{performer_name[0]}{os.path.sep}{performer_name}{os.path.sep}folder.jpg"
        case "emby":
            return f"{settings['actor_metadata_path']}{performer_name[0].lower()}{os.path.sep}{performer_name}{os.path.sep}folder.jpg"
