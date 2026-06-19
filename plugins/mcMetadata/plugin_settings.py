"""Pure mapping from Stash's camelCase plugin config to internal snake_case settings.

Kept separate from mcMetadata.py (which reads stdin at import) so the mapping —
including list-parsing and the hookTriggerMode -> organizedCondition migration — is
unit-testable without a Stash connection.
"""

_VALID_ORGANIZED = ("require", "skip", "ignore")


def _split_csv(value):
    """Comma-separated string -> trimmed list, empties dropped."""
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def _resolve_organized_condition(plugin_config):
    """organizedCondition if valid, else migrate the legacy hookTriggerMode.

    on_organized -> require; always/empty/unknown -> ignore. The new key wins.
    """
    raw = (plugin_config.get("organizedCondition") or "").strip().lower()
    if raw in _VALID_ORGANIZED:
        return raw
    legacy = (plugin_config.get("hookTriggerMode") or "").strip().lower()
    if legacy == "on_organized":
        return "require"
    return "ignore"


def map_settings(plugin_config):
    """Map a Stash plugin config dict to the internal settings dict (with defaults)."""
    return {
        "dry_run": plugin_config.get("dryRun", True),  # Default to safe mode
        "log_file_path": plugin_config.get("logFilePath", ""),  # Optional file logging
        "enable_hook": plugin_config.get("enableHook", False),  # Default off for safety
        # Processing conditions (unified gate)
        "organized_condition": _resolve_organized_condition(plugin_config),
        "require_stash_id": plugin_config.get("requireStashId", False),  # Default off - process all scenes (#127)
        "required_tags": _split_csv(plugin_config.get("requiredTags", "")),
        "include_paths": _split_csv(plugin_config.get("includePaths", "")),
        "exclude_paths": _split_csv(plugin_config.get("excludePaths", "")),
        # Renamer
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
        # NFO
        "nfo_skip_existing": plugin_config.get("nfoSkipExisting", False),
        "nfo_exclude_fields": [
            f.strip().lower()
            for f in plugin_config.get("nfoExcludeFields", "").split(",")
            if f.strip()
        ],
        # Actor images
        "enable_actor_images": plugin_config.get("enableActorImages", False),
        "media_server": plugin_config.get("mediaServer", "jellyfin"),
        "actor_metadata_path": plugin_config.get("actorMetadataPath", ""),
    }
