"""
mcMetadata - Stash Plugin for Media Center Metadata Generation

Generates NFO files for Jellyfin/Emby, organizes video files according to
configurable templates, and exports performer images to media server folders.

Version: 1.5.0

Stash runs this file with the plugin's JSON input on stdin (see run()). Importing it
does nothing, so tests can drive run() with their own input and Stash client.
"""

import json
import sys
from stashapi_check import stashapi_problem

# Plugin configuration
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
    import utils.logger as log
    from plugin_settings import map_settings

    try:
        config = stash_instance.get_configuration()
        plugin_config = config.get("plugins", {}).get(PLUGIN_ID, {})
    except Exception as err:
        log.error(f"Failed to get plugin configuration: {err}")
        plugin_config = {}

    return map_settings(plugin_config)


def get_plugin_mode(plugin_args):
    """Determine the plugin execution mode from args.

    Returns:
        str: The mode string (e.g., 'bulk', 'performers', 'Scene.Update.Post')

    Raises:
        ValueError: If no valid mode or hook context is provided
    """
    mode = plugin_args.get("mode")
    hook_context = plugin_args.get("hookContext")

    if mode is None and hook_context is None:
        raise ValueError("Invalid plugin args: no mode or hookContext provided")

    return mode or hook_context["type"]


def main(json_input, stash):
    """Handle one plugin invocation: load settings, then run the requested mode.

    Args:
        json_input: The JSON input Stash sent, parsed
        stash: Stash client (StashInterface)
    """
    # Imported here, not at the top: these import stashapi, which run() checks first
    from utils.logger import init_file_logger, close_file_logger
    from utils.run_flow import is_disabled_hook_run, is_dry_run
    import utils.logger as log
    from performer import process_all_performers, process_performer_hook
    from scene import process_all_scenes, process_scene
    from conditions import should_process, describe_active_conditions
    from utils.self_updates import plugin_data_dir, should_skip_hook

    plugin_args = json_input.get("args", {})
    try:
        # Loaded here so a bad setting is a logged error, not a traceback
        settings = get_settings(stash)
        # Where the plugin keeps its own files (self-update markers); not a user setting
        settings["data_dir"] = plugin_data_dir(json_input["server_connection"])

        mode = get_plugin_mode(plugin_args)

        # A disabled hook is a silent no-op: decide before any logging or log-file I/O
        if is_disabled_hook_run(mode, settings):
            return

        # Initialize file logging if configured
        if settings.get("log_file_path"):
            init_file_logger(settings["log_file_path"])

        log.debug(f"Plugin mode: {mode}")
        log.debug(f"Dry run: {is_dry_run(settings)}")

        # Log current settings for debugging
        if is_dry_run(settings):
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
            log.info(describe_active_conditions(settings))
            process_all_scenes(stash, settings, api_key)
            log.info("Bulk scene update completed")

        elif mode == "performers":
            log.info("Starting bulk performer update")
            process_all_performers(stash, settings, api_key)
            log.info("Bulk performer update completed")

        elif mode == "Performer.Update.Post":
            # Gated by Enable Actor Images (not Enable Scene Update Hook)
            process_performer_hook(stash, plugin_args["hookContext"]["id"], settings, api_key)

        elif mode == "Scene.Update.Post":
            if not settings.get("enable_hook", False):
                log.debug("Hook disabled, skipping")
                return

            hook_context = plugin_args["hookContext"]
            scene_id = hook_context["id"]

            # mcMetadata's own organized update (the renamer's mark-organized step)
            # fires this hook while that run is still processing the scene
            if should_skip_hook(hook_context, settings["data_dir"]):
                log.debug(f"Scene {scene_id}: skipping mcMetadata's own organized update")
                return

            scene = stash.find_scene(scene_id)

            if not scene:
                log.warning(f"Scene {scene_id} not found")
                return

            # Unified processing-conditions gate (organized / required tags /
            # directory scope / StashID). This subsumes the old hookTriggerMode
            # and the cascade guard: organizedCondition=require gives organized-only
            # processing; organizedCondition=skip avoids reprocessing organized scenes.
            ok, reason = should_process(scene, settings)
            if not ok:
                log.debug(f"Scene {scene_id} skipped by processing conditions: {reason}")
                return

            log.info(f"Processing scene {scene_id}")
            process_scene(scene, stash, settings, api_key)

        else:
            log.warning(f"Unknown mode: {mode}")

    except Exception as err:
        log.error(f"Plugin error: {err}")
        sys.exit(1)
    finally:
        # Always close file logger to ensure log is written
        close_file_logger()


def run(stdin_text, stash_factory=None):
    """Entry point: run the plugin on the JSON input Stash sends on stdin.

    Prints the plugin's JSON reply on stdout.

    Args:
        stdin_text: The JSON input, as text
        stash_factory: Builds the Stash client from server_connection; defaults to
            stashapi's StashInterface (tests pass a fake)
    """
    # Check stashapp-tools before anything imports stashapi, and report a clear error
    # instead of a traceback. (stashapi.log is unavailable here, so use Stash's raw
    # log protocol: \x01e\x02 prefix on stderr.)
    problem = stashapi_problem()
    if problem:
        sys.stderr.write(f"\x01e\x02{problem}\n")
        sys.stderr.flush()
        print(json.dumps({"error": problem}))
        return

    # Parse JSON context passed from Stash
    json_input = json.loads(stdin_text)

    # Initialize Stash API
    if stash_factory is None:
        from stashapi.stashapp import StashInterface

        stash_factory = StashInterface
    stash = stash_factory(json_input["server_connection"])

    main(json_input, stash)
    print(json.dumps({"output": "ok"}))


if __name__ == "__main__":
    run(sys.stdin.read())
