import os
import stashapi.log as log
from utils.files import download_image


def process_performer(performer, settings, api_key, overwrite=False):
    try:
        log.debug(f"Processing performer {performer["name"]}")
        if settings["enable_actor_images"] is False:
            return

        if performer["image_path"]:
            image_path = __get_actor_image_path(performer["name"])
            image_url = f"{performer["image_path"]}&apikey={api_key}"
            if overwrite is True or not os.path.exists(image_path):
                download_image(image_url, image_path, settings)
        else:
            log.debug(
                f"Skipping performer {performer["name"]} because they have no image_path"
            )

    except Exception as err:
        log.error(f"Error processing Performer {performer["name"]}: {str(err)}")


def __get_actor_image_path(performer_name, settings):
    match settings["media_server"]:
        case "jellyfin":
            return f"{settings["actor_metadata_path"]}{performer_name[0].lower()}{os.path.sep}{performer_name}{os.path.sep}folder.jpg"
        case "emby":
            return f"{settings["actor_metadata_path"]}{performer_name[0]}{os.path.sep}{performer_name}{os.path.sep}folder.jpg"
