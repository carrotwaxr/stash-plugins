"""
mcMetadata - Stash Plugin for Media Center Metadata Generation

Generates NFO files for Jellyfin/Emby, organizes video files according to
configurable templates, and exports performer images to media server folders.

Version: 1.5.0
"""

import json
import sys
from stashapi.stashapp import StashInterface
from utils.logger import init_file_logger, close_file_logger
import utils.logger as log
from performer import process_all_performers
from scene import process_all_scenes, process_scene
from conditions import should_process, describe_active_conditions
from plugin_settings import map_settings

# Minimum stashapp-tools version required for schema 72+ compatibility
MIN_STASHAPP_TOOLS_VERSION = "0.2.59"


def _parse_version(version_str):
    """Parse version string into tuple of integers for comparison."""
    try:
        return tuple(int(x) for x in version_str.split('.'))
    except (ValueError, AttributeError):
        return (0,)


def check_stashapp_tools_version():
    """Check if stashapp-tools is at minimum required version.

    Older versions have a bug with auto-generated GraphQL fragments that
    causes "Cannot spread fragment 'Folder' within itself" errors on
    Stash schema 72+. Version 0.2.59 includes the fix.
    """
    try:
        import importlib.metadata
        version = importlib.metadata.version("stashapp-tools")
        if _parse_version(version) < _parse_version(MIN_STASHAPP_TOOLS_VERSION):
            log.warning(
                f"stashapp-tools version {version} is outdated. "
                f"Version {MIN_STASHAPP_TOOLS_VERSION}+ is required for Stash schema 72+. "
                f"Run: pip install --upgrade stashapp-tools"
            )
            return False
        log.debug(f"stashapp-tools version: {version}")
        return True
    except importlib.metadata.PackageNotFoundError:
        log.debug("stashapp-tools package not found, skipping version check")
        return True
    except Exception as e:
        log.debug(f"Could not check stashapp-tools version: {e}")
        return True

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
    via the configuration endpoint. The camelCase -> snake_case mapping
    (with defaults, list-parsing, and the hookTriggerMode migration) lives
    in plugin_settings.map_settings so it can be unit-tested independently.

    Returns:
        dict: Settings dictionary with snake_case keys for internal use
    """
    try:
        config = stash_instance.get_configuration()
        plugin_config = config.get("plugins", {}).get(PLUGIN_ID, {})
    except Exception as err:
        log.error(f"Failed to get plugin configuration: {err}")
        plugin_config = {}

    return map_settings(plugin_config)


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

        # Check stashapp-tools version for schema compatibility
        check_stashapp_tools_version()

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
            log.info(describe_active_conditions(SETTINGS))
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

            # Unified processing-conditions gate (organized / required tags /
            # directory scope / StashID). This subsumes the old hookTriggerMode
            # and the cascade guard: organizedCondition=require gives organized-only
            # processing; organizedCondition=skip avoids reprocessing organized scenes.
            ok, reason = should_process(scene, SETTINGS)
            if not ok:
                log.debug(f"Scene {scene_id} skipped by processing conditions: {reason}")
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
