"""Pure mapping from Stash's camelCase plugin config to internal snake_case settings.

Kept separate from mcMetadata.py (which reads stdin at import) so the mapping —
including list-parsing and the hookTriggerMode -> organizedCondition migration — is
unit-testable without a Stash connection.
"""

import utils.logger as log

_VALID_ORGANIZED = ("require", "skip", "ignore")

BUDGET_MIN = 40
BUDGET_MAX = 800

# The rename template must be unique per scene: one of these key sets must be fully
# present (substring match), so two scenes can't render to the same path.
VALID_TEMPLATE_UNIQUENESS = [
    ["$StashID"],
    ["$Studio", "$Title", "$ReleaseDate"],
    ["$Studios", "$Title", "$ReleaseDate"],
]


def template_is_unique(template):
    """True if the template satisfies one of the uniqueness rules."""
    template = template or ""
    return any(all(k in template for k in keys) for keys in VALID_TEMPLATE_UNIQUENESS)


def _bool(config, key, default):
    """Real bool, "true"/"false" (any case), or 1/0; anything else -> default + warning."""
    value = config.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    log.warning(f"Setting {key}={value!r} is not a valid true/false value; using default {default}")
    return default


def _number(config, key, default):
    """int/float/numeric string -> number; else default + warning (None -> default)."""
    value = config.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        log.warning(f"Setting {key}={value!r} is not a number; using default {default}")
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        num = float(str(value).strip())
        if num != num or num in (float("inf"), float("-inf")):
            raise ValueError
        return int(num) if num == int(num) else num
    except (ValueError, TypeError):
        log.warning(f"Setting {key}={value!r} is not a number; using default {default}")
        return default


def _str(config, key, default=""):
    """String setting: None -> default; other non-strings are stringified with a warning."""
    value = config.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    log.warning(f"Setting {key}={value!r} is not text; using it as {str(value)!r}")
    return str(value)


def _split_csv(value):
    """Comma-separated string -> trimmed list, empties dropped."""
    if value is not None and not isinstance(value, str):
        value = str(value)
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def _resolve_organized_condition(plugin_config):
    """organizedCondition if valid, else migrate the legacy hookTriggerMode.

    An empty/missing organizedCondition resolves from hookTriggerMode
    (on_organized -> require, anything else -> ignore). A present but unrecognized
    value is "invalid": should_process rejects every scene (fail closed).
    """
    value = plugin_config.get("organizedCondition")
    if value is not None and not isinstance(value, str):
        log.warning(
            f"Setting organizedCondition={value!r} is invalid; valid values are "
            f"{', '.join(_VALID_ORGANIZED)}. No scenes will be processed until it is fixed."
        )
        return "invalid"
    raw = (value or "").strip().lower()
    if raw in _VALID_ORGANIZED:
        return raw
    if raw:
        log.warning(
            f"Setting organizedCondition={value!r} is invalid; valid values are "
            f"{', '.join(_VALID_ORGANIZED)}. No scenes will be processed until it is fixed."
        )
        return "invalid"
    legacy = _str(plugin_config, "hookTriggerMode").strip().lower()
    if legacy == "on_organized":
        return "require"
    return "ignore"


def map_settings(plugin_config):
    """Map a Stash plugin config dict to the internal settings dict (with defaults)."""
    plugin_config = plugin_config or {}

    budget = _number(plugin_config, "renamerFilepathBudget", 250)
    if budget < BUDGET_MIN or budget > BUDGET_MAX:
        clamped = min(max(budget, BUDGET_MIN), BUDGET_MAX)
        log.warning(
            f"renamerFilepathBudget={budget} is outside {BUDGET_MIN}-{BUDGET_MAX}; using {clamped}"
        )
        budget = clamped
    budget = int(budget)

    enable_renamer = _bool(plugin_config, "enableRenamer", False)
    template = _str(
        plugin_config,
        "renamerPathTemplate",
        "$Studio/$Title - $Performers $ReleaseDate [$Resolution]",
    )
    if enable_renamer and not template_is_unique(template):
        log.error(
            "renamerPathTemplate does not meet the uniqueness rule: it must contain $StashID, "
            "or $Studio (or $Studios) together with $Title and $ReleaseDate. "
            "Renaming is disabled for this run; NFO and poster generation still run."
        )
        enable_renamer = False

    return {
        "dry_run": _bool(plugin_config, "dryRun", True),  # Default to safe mode
        "log_file_path": _str(plugin_config, "logFilePath"),  # Optional file logging
        "enable_hook": _bool(plugin_config, "enableHook", False),  # Default off for safety
        # Processing conditions (unified gate)
        "organized_condition": _resolve_organized_condition(plugin_config),
        "require_stash_id": _bool(plugin_config, "requireStashId", False),  # Default off - process all scenes (#127)
        "required_tags": _split_csv(plugin_config.get("requiredTags")),
        "include_paths": _split_csv(plugin_config.get("includePaths")),
        "exclude_paths": _split_csv(plugin_config.get("excludePaths")),
        # Renamer
        "enable_renamer": enable_renamer,
        "renamer_path": _str(plugin_config, "renamerPath"),
        "renamer_path_template": template,
        "renamer_filepath_budget": budget,
        "renamer_ignore_files_in_path": _bool(plugin_config, "renamerIgnoreFilesInPath", False),
        "renamer_enable_mark_organized": _bool(plugin_config, "renamerMarkOrganized", True),
        "renamer_multi_file_mode": _str(plugin_config, "renamerMultiFileMode", "all"),
        "renamer_move_sidecars": _bool(plugin_config, "renamerMoveSidecars", True),
        # NFO
        "nfo_skip_existing": _bool(plugin_config, "nfoSkipExisting", False),
        "nfo_exclude_fields": [f.lower() for f in _split_csv(plugin_config.get("nfoExcludeFields"))],
        "nfo_filename": _str(plugin_config, "nfoFilename") or "{basename}.nfo",
        "poster_filename": _str(plugin_config, "posterFilename") or "{basename}-poster.jpg",
        "backdrop_filename": _str(plugin_config, "backdropFilename"),
        "nfo_rating_field": _str(plugin_config, "nfoRatingField").strip().lower() or "both",
        # Actor images
        "enable_actor_images": _bool(plugin_config, "enableActorImages", False),
        "media_server": _str(plugin_config, "mediaServer", "jellyfin"),
        "actor_metadata_path": _str(plugin_config, "actorMetadataPath"),
    }
