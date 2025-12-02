"""
mcMetadata - Stash Plugin for Media Center Metadata Generation

Generates NFO files for Jellyfin/Emby, organizes video files according to
configurable templates, and exports performer images to media server folders.

Version: 1.0.0
"""

import json
import os
import sys
import stashapi.log as log
from stashapi.stashapp import StashInterface
from performer import process_all_performers
from scene import process_all_scenes, process_scene
from utils.settings import read_settings, update_setting

# Parse JSON context passed from Stash
json_input = json.loads(sys.stdin.read())

# Initialize Stash API
stash = StashInterface(json_input["server_connection"])

# Plugin configuration
PLUGIN_ARGS = json_input.get("args", {})
SETTINGS_FILEPATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.ini"
)
SETTINGS = read_settings(SETTINGS_FILEPATH)

# Modes that modify settings (don't require API key)
SETTINGS_MODES = ["disable", "dryrun", "enable", "renamer"]


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


def handle_settings_mode(mode):
    """Handle settings toggle modes.

    Args:
        mode: The settings mode to handle

    Returns:
        bool: True if a settings mode was handled, False otherwise
    """
    if mode not in SETTINGS_MODES:
        return False

    if mode == "enable":
        log.info("Enabling hooks")
        update_setting(SETTINGS_FILEPATH, "enable_hook", "true")
    elif mode == "disable":
        log.info("Disabling hooks")
        update_setting(SETTINGS_FILEPATH, "enable_hook", "false")
    elif mode == "dryrun":
        if SETTINGS["dry_run"]:
            log.info("Disabling dry run mode")
            update_setting(SETTINGS_FILEPATH, "dry_run", "false")
        else:
            log.info("Enabling dry run mode")
            update_setting(SETTINGS_FILEPATH, "dry_run", "true")
    elif mode == "renamer":
        if SETTINGS["enable_renamer"]:
            log.info("Disabling renamer")
            update_setting(SETTINGS_FILEPATH, "enable_renamer", "false")
        else:
            log.info("Enabling renamer")
            update_setting(SETTINGS_FILEPATH, "enable_renamer", "true")

    return True


def main():
    """Main entry point for the plugin."""
    try:
        mode = get_plugin_mode()
        log.debug(f"Plugin mode: {mode}")
        log.debug(f"Dry run: {SETTINGS['dry_run']}")

        # Handle settings toggle modes (no API key needed)
        if handle_settings_mode(mode):
            return

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

            stash_ids = scene.get("stash_ids", [])
            if stash_ids:
                log.info(f"Processing scene {scene_id}")
                process_scene(scene, stash, SETTINGS, api_key)
            else:
                log.debug(f"Scene {scene_id} has no StashID, skipping")

        else:
            log.warning(f"Unknown mode: {mode}")

    except Exception as err:
        log.error(f"Plugin error: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
