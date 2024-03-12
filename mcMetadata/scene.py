from datetime import datetime
import os
import stashapi.log as log
from performer import process_performer
from utils.files import download_image, rename_file, replace_file_ext
from utils.nfo import build_nfo_xml
from utils.replacer import get_new_path

IMPOSSIBLE_PATH = "$%^&@"


def process_scene(scene, stash, settings, cursor, api_key):
    try:
        log.debug(f"Processing Scene ID: {scene["id"]}")

        scene = __hydrate_scene(scene, stash)
        # rename/move primary video file if settings configured for that
        # if not, function will just return the current path and we'll proceed with that
        target_video_path = __rename_video(scene, settings, cursor)

        # overwrite nfo named after file, at file location (use renamed path if applicable)
        nfo_path = replace_file_ext(target_video_path, "nfo")
        __write_nfo(scene, nfo_path, settings)

        # copy any performer images to people directory

        for performer in scene["performers"] or []:
            try:
                process_performer(performer, settings, api_key)
            except Exception as err:
                log.error(f"Error processing performer image: {str(err)}")

        # download any missing artwork images from stash into path
        poster_path = replace_file_ext(target_video_path, "jpg", "-poster")
        if not os.path.exists(poster_path):
            screenshot_url = f"{scene["paths"]["screenshot"]}&apikey={api_key}"
            download_image(screenshot_url, poster_path, settings)
    except Exception as err:
        log.error(f"Error processing Scene ID {scene["id"]}: {str(err)}")


def __hydrate_scene(scene, stash):
    fragmented_performers = scene["performers"] or []
    performers = []
    for fragmented_performer in fragmented_performers:
        performer = stash.find_performer(
            fragmented_performer["id"], False, "id name gender image_path"
        )
        performers.append(performer)
    scene["performers"] = sorted(
        performers,
        key=lambda performer: f"{str(performer.get("gender", "UNKNOWN"))}_{performer["name"]}",
    )

    if scene["studio"]:
        scene["studio"] = stash.find_studio(
            scene["studio"]["id"], "id name parent_studio { ...Studio }"
        )

    return scene


def __rename_video(scene, settings, cursor):
    # get primary video file path
    video_path = scene["files"][0]["path"]

    if settings["enable_renamer"] is not True:
        log.debug("Skipping renaming because it's disabled in settings")
        return video_path

    expected_path = get_new_path(
        scene,
        settings["renamer_path"],
        settings["renamer_path_template"],
        settings.get("renamer_filepath_budget", 250),
    )

    if expected_path is False:
        return video_path

    # check if we should rename it
    renamer_path = settings.get("renamer_path", IMPOSSIBLE_PATH)
    renamer_ignore_in_path = settings.get("renamer_ignore_files_in_path", False)
    in_target_dir = video_path.startswith(renamer_path)

    do_ignore = False
    if renamer_ignore_in_path is True and in_target_dir is True:
        do_ignore = True

    if do_ignore is True or expected_path == video_path:
        log.debug("Skipping renaming because file is already organized")
        return video_path

    if os.path.exists(expected_path):
        log.info(f"Duplicate video. Expected path: {expected_path}")
        return video_path

    # rename/move video file. Will return False if it errors
    video_renamed_path = rename_file(video_path, expected_path, settings)
    if not video_renamed_path:
        return video_path

    # update database with new file location
    try:
        __db_rename(scene["id"], video_path, video_renamed_path, settings, cursor)
    except Exception as err:
        log.error(
            f"Error updating database for Scene ID {scene["id"]}. You can likely resolve this by running a scan on your library: {str(err)}"
        )

    # locate any existing metadata files, rename them as well
    potential_nfo_path = replace_file_ext(video_path, "nfo")
    if os.path.exists(potential_nfo_path):
        log.debug(f"Relocating existing NFO file: {potential_nfo_path}")
        rename_file(
            potential_nfo_path, replace_file_ext(video_renamed_path, "nfo"), settings
        )

    potential_poster_path = replace_file_ext(video_path, "jpg", "-poster")
    if os.path.exists(potential_poster_path):
        log.debug(f"Relocating existing Poster image: {potential_poster_path}")
        rename_file(
            potential_poster_path,
            replace_file_ext(video_renamed_path, "jpg", "-poster"),
            settings,
        )

    return video_renamed_path


def __db_rename(scene_id, old_filepath, new_filepath, settings, cursor):
    log.debug(f"Updating database for Scene ID {scene_id}")
    old_dir = os.path.dirname(old_filepath)
    new_dir = os.path.dirname(new_filepath)
    new_filename = os.path.basename(new_filepath)
    # 2022-09-17T11:25:52+02:00
    mod_time = datetime.now().astimezone().isoformat("T", "seconds")

    # get the next id that we should use if needed
    cursor.execute("SELECT MAX(id) from folders")
    new_id = cursor.fetchall()[0][0] + 1

    # get the old folder id
    cursor.execute("SELECT id FROM folders WHERE path=?", [old_dir])
    old_folder_id = cursor.fetchall()[0][0]

    # check if the folder of file is created in db
    cursor.execute("SELECT id FROM folders WHERE path=?", [new_dir])
    folder_id = cursor.fetchall()
    if not folder_id:
        dir = new_dir
        # reduce the path to find a parent folder
        for _ in range(1, len(new_dir.split(os.sep))):
            dir = os.path.dirname(dir)
            cursor.execute("SELECT id FROM folders WHERE path=?", [dir])
            parent_id = cursor.fetchall()
            if parent_id:
                # create a new row with the new folder with the parent folder find above
                cursor.execute(
                    "INSERT INTO 'main'.'folders'('id', 'path', 'parent_folder_id', 'mod_time', 'created_at', 'updated_at', 'zip_file_id') VALUES (?, ?, ?, ?, ?, ?, ?);",
                    [
                        new_id,
                        new_dir,
                        parent_id[0][0],
                        mod_time,
                        mod_time,
                        mod_time,
                        None,
                    ],
                )
                folder_id = new_id
                break
    else:
        folder_id = folder_id[0][0]
    if folder_id:
        cursor.execute(
            "SELECT file_id from scenes_files WHERE scene_id=?",
            [scene_id],
        )
        file_ids = cursor.fetchall()
        file_id = None
        for f in file_ids:
            # it can have multiple file for a scene
            cursor.execute("SELECT parent_folder_id from files WHERE id=?", [f[0]])
            check_parent = cursor.fetchall()[0][0]
            # if the parent id is the one found above section, we find our file.s
            if check_parent == old_folder_id:
                file_id = f[0]
                break
        if file_id:
            cursor.execute(
                "UPDATE files SET basename=?, parent_folder_id=?, updated_at=? WHERE id=?;",
                [new_filename, folder_id, mod_time, file_id],
            )
            if settings["renamer_enable_mark_organized"]:
                cursor.execute(
                    "UPDATE scenes SET organized=? WHERE id=?;", [True, scene_id]
                )
        else:
            raise Exception("Failed to find file_id")
    else:
        raise Exception(
            f"You need to setup a library with the new location ({new_dir}) and scan at least 1 file"
        )
    log.debug("Database updated")


def __write_nfo(scene, filepath, settings):
    try:
        nfo_xml = build_nfo_xml(scene)
        if settings["dry_run"] is False:
            f = open(filepath, "w", encoding="utf-8-sig")
            f.write(nfo_xml)
            f.close()
            log.info(f"Updated NFO file: {filepath}")
    except Exception as err:
        log.error(f"Error writing NFO: {str(err)}")
