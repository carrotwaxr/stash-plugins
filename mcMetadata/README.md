# mcMetadata Plugin for [Stash](https://github.com/stashapp/stash)

This plugin is intended for those who are managing their collection with Stash but actually serve the content via another application like Emby or Jellyfin. If this describes your setup you probably already know that there's a few plugins for scraping/generating metadata for those media servers but of course even when those plugins are maintained and working they don't do half as good a job at scraping metadata as Stash does.

Therefore, why not delegate this responsibility to Stash? Enter mcMetadata. The plugin will use your Stash database to generate the `.nfo` files as well as the scene and performer images that these media servers can utilize.

## Features
- On Scene Update:
    - Organizes/renames video file as well as any existing metadata files.
    - Generates/updates `.nfo` file with the same name as the video file, in the same folder.
    - Generates performer images in your chosen media server's metadata folder.
- Bulk Scene Updater: Runs the scene updater against every scene in your collection.
- Bulk Performer Updater: Copies all performer images that exist to your chosen media server's metadata folder.
- Has a dry run mode where no files are actually touched.
- All actions are configurable via the UI and the `settings.ini` file. See the Configuration section for all available settings.

## Installation
Download (zip or git clone) into your Stash plugins directory. Reload plugins.

## Configuration
All configuration values are stored in the `settings.ini` file in the plugin's root directory. You will need to customize this file manually before using the plugin. An explanation of each setting and what is does can be found below.

- `dry_run`:
    - **Accepted**: `true` | `false`
    - **Required**: `true`
    - **Description**: When `true` no files or database records are modified
    - **How to Change**: Via the UI in Settings >> Tasks. Scroll down to the Plugin Tasks section and choose the "Toggle Dry Run" option. The Logs page using the Debug log level will show what the setting has been updated to.
- `enable_actor_images`:
    - **Accepted**: `true` | `false`
    - **Required**: `true`
    - **Description**: When `false` no performer images will be copied to the media server.
    - **How to Change**: Manually in the `settings.ini` file.
- `enable_hook`:
    - **Accepted**: `true` | `false`
    - **Required**: `true`
    - **Description**: When `false` the Scene update hook will be disabled. This effectively means that the plugin will do nothing unless you run one of the Bulk actions via the Plugin Tasks UI.
    - **How to Change**: Via the UI in Settings >> Tasks. Scroll down to the Plugin Tasks section and choose the "Enable" or "Disable".
- `enable_renamer`:
    - **Accepted**: `true` | `false`
    - **Required**: `true`
    - **Description**: When `false` no files will be organized/renamed.
    - **How to Change**: Via the UI in Settings >> Tasks. Scroll down to the Plugin Tasks section and choose the "Toggle Renamer" option. The Logs page using the Debug log level will show what the setting has been updated to.
- `actor_metadata_path`:
    - **Accepted**: Any valid, already existing directory path.
    - **Required**: `false` unless `enable_actor_images` is `true`
    - **Description**: This should point to the "people" directory for your chosen media server application. You will likely need to map this path to Stash's Docker container for it to be available to Stash. For Emby, this path is `<embyConfigDir>/metadata/people/` and for Jellyfin it's `<jellyfinConfigDir>/data/metadata/People/`
    - **How to Change**: Manually in the `settings.ini` file.
    - **Example**: `/jellyfin/data/metadata/People/`
- `media_server`:
    - **Accepted**: `emby` | `jellyfin`
    - **Required**: `false` unless `enable_actor_images` is `true`
    - **Description**: Used by the performer updater to determine the filepath of the performer images.
    - **How to Change**: Manually in the `settings.ini` file.
- `renamer_enable_mark_organized`:
    - **Accepted**: `true` | `false`
    - **Required**: `false` unless `enable_renamer` is `true`
    - **Description**: When `true` files that are organized/renamed are also marked as Organized in Stash.
    - **How to Change**: Manually in the `settings.ini` file.
- `renamer_filename_budget`:
    - **Accepted**: Numbers `40-800`
    - **Required**: `false` unless `enable_renamer` is `true`
    - **Description**: Determines the maximum length of a filename for use when organizing/renaming files.
    - **How to Change**: Manually in the `settings.ini` file.
- `renamer_ignore_files_in_path`:
    - **Accepted**: `true` | `false`
    - **Required**: `false` unless `enable_renamer` is `true`
    - **Description**: When `true` files already in `renamer_path` will not be organized/renamed.
    - **How to Change**: Manually in the `settings.ini` file.
- `renamer_path`:
    - **Accepted**: Any valid directory path.
    - **Required**: `false` unless `enable_renamer` is `true`
    - **Description**: This should point to a directory where you would like organized/renamed scenes moved to. This will be used in combination with your `renamer_path_template` settings to determine what the scene's filepath should be.
    - **How to Change**: Manually in the `settings.ini` file.
    - **Example**: `/data/tagged/`
- `renamer_path_template`:
    - **Accepted**: A pattern using any combination of valid filename characters, path separators and replacers.
    - **Required**: `false` unless `enable_renamer` is `true`
    - **Description**: This should point to a directory where you would like organized/renamed scenes moved to. This will be used in combination with your `renamer_path_template` settings to determine what the scene's filepath should be.
    - **How to Change**: Manually in the `settings.ini` file.
    - **Example**: `$Studio/$Title - $FemalePerformers $MalePerformers $ReleaseDate [WEBDL-$Resolution]`
    - **Replacers**:
        - `$Studio`: replaced with the scene's Studio name. Illegal filename characters are replaced.
        - `$Studios`: replaced with directories representing the scene's Studio hierarchy. Illegal filename characters are replaced.
        - `$StashID`: replaced with the scene's Stash ID.
        - `$Title`: replaced with the scene's Title. Illegal filename characters are replaced.
        - `$ReleaseYear`: replaced with the year of the scene's Release Date.
        - `$ReleaseDate`: replaced with the scene's Release Date.
        - `$Resolution`: replaced with the scene's calculated Resolution. (480p, 720p, 1080p, 1440p, 4K, 8K)
        - `$Quality`: replaced with the scene's calculated video Quality. (LOW, SD, HD, FHD, 2K, QHD, UHD, FUHD)
        - `$FemalePerformers`: replaced with a space separated list of female scene performer names.
        - `$MalePerformers`: replaced with a space separated list of male scene performer names.
        - `$Performers`: replaced with a space separated list of all scene performer names.
        - `$Tags`: replaced with a space separated list of all scene tags.
    - **Uniqueness**: In order to ensure that filenames are reasonably unique, your `renamer_path_template` must:
        - Contain `$StashID` or
        - Contain (`$Studio` or `$Studios`), `$Title` and `$ReleaseDate`

## Troubleshooting
If you go to Settings >> Logs in Stash and change your Log Level to Debug, you should see a verbose output that can aid in troubleshooting or opening an issue here on Github.

## Remaining To-Do
- Improve logging, potentially using the toast notifications.
- Add additional configuration settings based on user feedback.
- Test with Emby, Jellyfin and maybe Plex?
- Verify all modes, settings and replacer options.
- Write unit tests.