"""Unified processing-conditions gate for mcMetadata.

`should_process` is the single source of truth for whether a scene gets processed,
used identically by the hook and the bulk task. It is a pure function with no Stash
dependency so it is trivial to test and reason about.

Gates (all independently optional, AND-combined; an unset gate never blocks):
  - organized:     organized_condition = require | skip | ignore
  - StashID:       require_stash_id (bool)
  - required tags: required_tags (list of names, ANY-match)
  - directory:     include_paths / exclude_paths (lists of globs; exclude wins)

Returns (passes, reason). reason is "" on pass, else a short skip key suitable for
the bulk dry-run histogram: not_organized | is_organized | no_stash_id |
missing_required_tag | excluded_path | outside_include_paths.
"""

from fnmatch import fnmatchcase


def _matches_any(path, patterns):
    """Case-insensitive fnmatch of a file path against any of the glob patterns."""
    p = path.lower()
    return any(fnmatchcase(p, pat.lower()) for pat in patterns)


def build_scene_filter(settings):
    """Server-side prefilter for the bulk task — an OPTIMIZATION, never authoritative.

    Pushes only the gates Stash can express identically (organized, StashID) into
    find_scenes so the whole library isn't fetched. It MUST stay a superset of what
    should_process accepts: tags and directory globs are deliberately NOT pushed (they
    are evaluated client-side), so the returned filter can never drop a scene the gate
    would have processed. should_process remains the source of truth.
    """
    f = {}
    organized_condition = settings.get("organized_condition", "ignore")
    if organized_condition == "require":
        f["organized"] = True
    elif organized_condition == "skip":
        f["organized"] = False
    if settings.get("require_stash_id", False):
        f["stash_id_endpoint"] = {"endpoint": "", "modifier": "NOT_NULL", "stash_id": ""}
    return f


def describe_active_conditions(settings):
    """One-line summary of the active gates, for a startup log (#13 discoverability)."""
    parts = []
    organized_condition = settings.get("organized_condition", "ignore")
    if organized_condition in ("require", "skip"):
        parts.append(f"organized={organized_condition}")
    if settings.get("require_stash_id", False):
        parts.append("stashID=required")
    required_tags = settings.get("required_tags") or []
    if required_tags:
        parts.append(f"tags=[{', '.join(required_tags)}]")
    include_paths = settings.get("include_paths") or []
    if include_paths:
        parts.append(f"include=[{', '.join(include_paths)}]")
    exclude_paths = settings.get("exclude_paths") or []
    if exclude_paths:
        parts.append(f"exclude=[{', '.join(exclude_paths)}]")

    if not parts:
        return "Active conditions: none (processing all scenes)"
    return "Active conditions: " + ", ".join(parts)


def format_bulk_summary(summary, dry_run=False):
    """Render a bulk-run summary into log lines: totals + a skip-reason histogram.

    This answers "which scenes were skipped, and why" for the dry-run preview, so users
    can confirm their conditions before a live run. Returns a list of strings to log.
    """
    prefix = "[DRY RUN] " if dry_run else ""
    scanned = summary.get("scanned", 0)
    processed = summary.get("processed", 0)
    errors = summary.get("errors", 0)
    skipped = summary.get("skipped", {}) or {}
    total_skipped = sum(skipped.values())

    lines = [
        f"{prefix}Bulk scan complete: {scanned} scanned",
        f"  -> processed: {processed}",
        f"  -> skipped: {total_skipped}",
    ]
    # Histogram, most common reason first (ties broken alphabetically for stability).
    for reason, n in sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"      {reason.ljust(24, '.')} {n}")
    if errors:
        lines.append(f"  -> errors: {errors}")

    samples = summary.get("samples") or []
    if samples:
        rendered = " | ".join(f"[{sid}] {reason}" for sid, reason in samples)
        lines.append(f"  sample skipped: {rendered}")
    return lines


def should_process(scene, settings):
    """Evaluate the processing gates for a scene. Returns (bool, reason_str)."""
    # --- organized -------------------------------------------------------
    organized_condition = settings.get("organized_condition", "ignore")
    organized = bool(scene.get("organized", False))
    if organized_condition == "require" and not organized:
        return (False, "not_organized")
    if organized_condition == "skip" and organized:
        return (False, "is_organized")

    # --- StashID ---------------------------------------------------------
    if settings.get("require_stash_id", False) and not scene.get("stash_ids"):
        return (False, "no_stash_id")

    # --- required tags (ANY-match) ---------------------------------------
    required_tags = settings.get("required_tags") or []
    if required_tags:
        scene_tags = {t.get("name", "") for t in (scene.get("tags") or [])}
        if not scene_tags.intersection(required_tags):
            return (False, "missing_required_tag")

    # --- directory scope (exclude beats include) -------------------------
    include_paths = settings.get("include_paths") or []
    exclude_paths = settings.get("exclude_paths") or []
    if include_paths or exclude_paths:
        paths = [f.get("path", "") for f in (scene.get("files") or [])]
        if exclude_paths and any(_matches_any(p, exclude_paths) for p in paths):
            return (False, "excluded_path")
        if include_paths and not any(_matches_any(p, include_paths) for p in paths):
            return (False, "outside_include_paths")

    return (True, "")
