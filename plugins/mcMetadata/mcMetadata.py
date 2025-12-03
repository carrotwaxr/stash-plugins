"""
mcMetadata - Stash Plugin for Media Center Metadata Generation

Generates NFO files for Jellyfin/Emby, organizes video files according to
configurable templates, and exports performer images to media server folders.

Version: 1.2.3
"""

import json
import sys
from stashapi.stashapp import StashInterface
from utils.logger import init_file_logger, close_file_logger
import utils.logger as log
from performer import process_all_performers
from scene import process_all_scenes, process_scene

# Parse JSON context passed from Stash
json_input = json.loads(sys.stdin.read())

# Initialize Stash API
stash = StashInterface(json_input["server_connection"])

# Plugin configuration
PLUGIN_ARGS = json_input.get("args", {})
PLUGIN_ID = "mcMetadata"


def get_settings(stash_instance):
    """Load settings from Stash's plugin configuration.

    Stash stores plugin settings in its database and provides them
    via the configuration endpoint. This replaces the old ini file approach.

    Returns:
        dict: Settings dictionary with snake_case keys for internal use
    """
    try:
        config = stash_instance.get_configuration()
        plugin_config = config.get("plugins", {}).get(PLUGIN_ID, {})
    except Exception as err:
        log.error(f"Failed to get plugin configuration: {err}")
        plugin_config = {}

    # Map from Stash camelCase settings to internal snake_case
    # with sensible defaults
    return {
        "dry_run": plugin_config.get("dryRun", True),  # Default to safe mode
        "log_file_path": plugin_config.get("logFilePath", ""),  # Optional file logging
        "enable_hook": plugin_config.get("enableHook", False),  # Default off for safety
        "require_stash_id": plugin_config.get("requireStashId", False),  # Default off - process all scenes
        "enable_renamer": plugin_config.get("enableRenamer", False),
        "renamer_path": plugin_config.get("renamerPath", ""),
        "renamer_path_template": plugin_config.get(
            "renamerPathTemplate",
            "$Studio/$Title - $Performers $ReleaseDate [$Resolution]"
        ),
        "renamer_filepath_budget": plugin_config.get("renamerFilepathBudget", 250),
        "renamer_ignore_files_in_path": plugin_config.get("renamerIgnoreFilesInPath", False),
        "renamer_enable_mark_organized": plugin_config.get("renamerMarkOrganized", True),
        "renamer_multi_file_mode": plugin_config.get("renamerMultiFileMode", "all"),
        "nfo_skip_existing": plugin_config.get("nfoSkipExisting", False),
        "enable_actor_images": plugin_config.get("enableActorImages", False),
        "media_server": plugin_config.get("mediaServer", "jellyfin"),
        "actor_metadata_path": plugin_config.get("actorMetadataPath", ""),
    }


# Load settings from Stash
SETTINGS = get_settings(stash)


def get_plugin_mode():
    """Determine the plugin execution mode from args.

    Returns:
        str: The mode string (e.g., 'bulk', 'performers', 'Scene.Update.Post')

    Raises:
        ValueError: If no valid mode or hook context is provided
    """
    mode = PLUGIN_ARGS.get("mode")
    hook_context = PLUGIN_ARGS.get("hookContext")

    if mode is None and hook_context is None:
        raise ValueError("Invalid plugin args: no mode or hookContext provided")

    return mode or hook_context["type"]


def main():
    """Main entry point for the plugin."""
    try:
        mode = get_plugin_mode()

        # Initialize file logging if configured
        if SETTINGS.get("log_file_path"):
            init_file_logger(SETTINGS["log_file_path"])

        log.debug(f"Plugin mode: {mode}")
        log.debug(f"Dry run: {SETTINGS['dry_run']}")

        # Log current settings for debugging
        if SETTINGS["dry_run"]:
            log.info("[DRY RUN] Mode enabled - no changes will be made")

        # Get API key for modes that need it
        try:
            stash_config = stash.get_configuration()["general"]
            api_key = stash_config.get("apiKey", "")
        except Exception as err:
            log.error(f"Failed to get Stash configuration: {err}")
            sys.exit(1)

        # Handle processing modes
        if mode == "bulk":
            log.info("Starting bulk scene update")
            process_all_scenes(stash, SETTINGS, api_key)
            log.info("Bulk scene update completed")

        elif mode == "performers":
            log.info("Starting bulk performer update")
            process_all_performers(stash, SETTINGS, api_key)
            log.info("Bulk performer update completed")

        elif mode == "Scene.Update.Post":
            if not SETTINGS.get("enable_hook", False):
                log.debug("Hook disabled, skipping")
                return

            scene_id = PLUGIN_ARGS["hookContext"]["id"]
            scene = stash.find_scene(scene_id)

            if not scene:
                log.warning(f"Scene {scene_id} not found")
                return

            # Check if we require StashDB link (configurable, default OFF)
            require_stash_id = SETTINGS.get("require_stash_id", False)
            stash_ids = scene.get("stash_ids", [])

            if require_stash_id and not stash_ids:
                log.debug(f"Scene {scene_id} has no StashID, skipping (requireStashId is enabled)")
                return

            log.info(f"Processing scene {scene_id}")
            process_scene(scene, stash, SETTINGS, api_key)

        else:
            log.warning(f"Unknown mode: {mode}")

    except Exception as err:
        log.error(f"Plugin error: {err}")
        sys.exit(1)
    finally:
        # Always close file logger to ensure log is written
        close_file_logger()


if __name__ == "__main__":
    main()
