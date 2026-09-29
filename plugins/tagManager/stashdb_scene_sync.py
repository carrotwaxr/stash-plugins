"""
Stash-box Scene Tag Sync

Syncs tags from each configured stash-box (StashDB, ThePornDB, ...) to the local
scenes linked to it, replicating Stash's Tagger merge behavior: tags are only
ever added, never removed.
"""

from dataclasses import dataclass, field
from typing import Optional

import log
from blacklist import Blacklist
from stash_client import StashError
from tag_cache import TagCache
from stashdb_api import (
    RateLimiter, StashDBAPIError, find_scene_by_id, find_scenes_by_fingerprints,
)

# Constants
DRY_RUN_LIMIT = 200  # Scenes per dry run, across all stash-boxes
FINGERPRINT_BATCH_SIZE = 40  # Max scenes per stash-box fingerprint query
DEFAULT_REQUESTS_PER_SECOND = 2  # Also the cap when a box allows more


@dataclass
class ProcessResult:
    """Result of processing a single scene."""
    status: str  # 'updated', 'no_changes', 'dry_run', 'error'
    tags_added: int = 0
    tags_skipped: int = 0
    error: Optional[str] = None


@dataclass
class SyncStats:
    """Statistics for sync operation."""
    total_scenes: int = 0
    processed: int = 0  # Scenes synced without error
    updated: int = 0
    no_changes: int = 0
    skipped: int = 0
    errors: int = 0
    tags_added_total: int = 0
    tags_skipped_total: int = 0
    error: Optional[str] = None  # Why the run stopped or failed, if it did
    by_endpoint: dict = field(default_factory=dict)  # {endpoint: {"name", **counts()}}

    def counts(self):
        """The counters as a dict, keyed as the plugin reports them."""
        return {
            "total_scenes": self.total_scenes,
            "processed": self.processed,
            "updated": self.updated,
            "no_changes": self.no_changes,
            "skipped": self.skipped,
            "errors": self.errors,
            "tags_added": self.tags_added_total,
            "tags_skipped": self.tags_skipped_total,
        }

    def add(self, other):
        """Add another SyncStats' counters to this one."""
        for name in ("total_scenes", "processed", "updated", "no_changes", "skipped",
                     "errors", "tags_added_total", "tags_skipped_total"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


class SyncAborted(Exception):
    """Internal: stops the whole run (a rejected API key, or the local Stash failing)."""


@dataclass
class _Run:
    """What every scene in a run is processed with."""
    client: object  # stash_client.LocalStash or a stand-in
    tag_cache: TagCache
    settings: dict
    blacklist: Blacklist


@dataclass
class _Box:
    """The stash-box being synced."""
    endpoint: str
    api_key: str
    name: str
    rate_limiter: RateLimiter


def match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint):
    """
    Match a stash-box tag to a local tag.

    Priority order (matches Stash's pkg/match/scraped.go:ScrapedTag):
    1. StashID link - local tag has same stash-box ID for this endpoint
    2. Name match - local tag name equals stash-box tag name (case-insensitive)
    3. Alias match - local tag alias equals stash-box tag name (case-insensitive)

    Args:
        stashdb_tag: Dict with 'id', 'name' from the stash-box
        tag_cache: TagCache instance with lookup maps
        endpoint: Stash-box endpoint URL

    Returns:
        Local tag ID (str) if matched, None if no match
    """
    stashdb_id = stashdb_tag.get("id")
    stashdb_name = stashdb_tag.get("name", "")

    # Priority 1: Match by StashID link
    if stashdb_id:
        local_id = tag_cache.by_stashdb_id(endpoint, stashdb_id)
        if local_id:
            return local_id

    # Priority 2: Match by local tag name
    local_id = tag_cache.by_name(stashdb_name)
    if local_id:
        return local_id

    # Priority 3: Match by local tag alias
    local_id = tag_cache.by_alias(stashdb_name)
    if local_id:
        return local_id

    return None


def process_scene(scene, remote_scene, tag_cache, client, settings, endpoint, blacklist):
    """
    Decide which tags a scene gains from its stash-box scene, and add them.

    Writes only in live mode, and only the missing tags (ADD mode), so tags added
    to the scene since it was fetched are kept.

    Args:
        scene: Local scene dict with id, tags
        remote_scene: Stash-box scene dict with tags
        tag_cache: TagCache instance
        client: LocalStash (not used in dry run)
        settings: Sync settings dict with 'dry_run' key
        endpoint: Stash-box endpoint URL
        blacklist: Blacklist instance for filtering tags

    Returns:
        ProcessResult with status, tags_added, tags_skipped
    """
    scene_id = scene.get("id", "unknown")
    existing_tags = scene.get("tags", []) or []
    existing_tag_ids = set(str(t.get("id", "")) for t in existing_tags if t.get("id"))

    # Which stash-box tags to consider
    remote_tags = remote_scene.get("tags", []) or []
    remote_tags, hidden_count = blacklist.filter_tags(remote_tags)
    if hidden_count > 0:
        log.LogDebug(f"Scene {scene_id}: Filtered {hidden_count} blacklisted tags")

    # Match them to local tags
    new_tag_ids = set()
    skipped_tags = []
    for remote_tag in remote_tags:
        local_id = match_stashdb_tag_to_local(remote_tag, tag_cache, endpoint)

        if local_id:
            if local_id not in existing_tag_ids:
                new_tag_ids.add(local_id)
                log.LogDebug(f"Scene {scene_id}: matched '{remote_tag.get('name', '')}' -> local tag {local_id}")
            else:
                log.LogTrace(f"Scene {scene_id}: tag '{remote_tag.get('name', '')}' already present")
        else:
            skipped_tags.append(remote_tag.get("name", ""))
            log.LogDebug(f"Scene {scene_id}: no local match for '{remote_tag.get('name', '')}'")

    if not new_tag_ids:
        return ProcessResult(status="no_changes", tags_skipped=len(skipped_tags))

    # What to write
    tag_ids = sorted(new_tag_ids)
    tag_names = [tag_cache.get_name(tid) or tid for tid in tag_ids]

    if settings.get("dry_run", True):
        log.LogInfo(f"[DRY RUN] Scene {scene_id}: would add {len(tag_ids)} tags: {tag_names}")
        return ProcessResult(status="dry_run", tags_added=len(tag_ids), tags_skipped=len(skipped_tags))

    try:
        client.add_scene_tags(scene_id, tag_ids)
    except StashError as e:
        log.LogError(f"Scene {scene_id}: failed to update - {e}")
        return ProcessResult(status="error", tags_skipped=len(skipped_tags), error=str(e))

    log.LogInfo(f"Scene {scene_id}: added {len(tag_ids)} tags: {tag_names}")
    return ProcessResult(status="updated", tags_added=len(tag_ids), tags_skipped=len(skipped_tags))


def sync_scene_tags(client, boxes, settings):
    """
    Main sync algorithm.

    1. Build tag lookup cache from local Stash
    2. For each stash-box, in order:
       a. Fetch the local scenes linked to it (sorted by updated_at ASC)
       b. Pass 1: batch query the box by fingerprints (40 scenes per request)
       c. Pass 2: query the retry queue one scene at a time (findScene by ID)
    3. Log summary statistics

    A rejected API key, or the local Stash failing to return tags or scenes, stops
    the whole run and sets `error`. Other stash-box errors count the scenes
    involved as errors and the run continues.

    Args:
        client: LocalStash for the local Stash
        boxes: List of {endpoint, api_key, name, max_requests_per_minute} dicts,
            each with an api_key
        settings: Sync settings dict with 'dry_run' and 'tag_blacklist' keys

    Returns:
        SyncStats with operation statistics
    """
    stats = SyncStats()
    dry_run = settings.get("dry_run", True)

    blacklist = Blacklist(settings.get("tag_blacklist") or "")
    log.LogDebug(f"Loaded blacklist with {blacklist.count} patterns")

    names = ", ".join(_box_name(b) for b in boxes)
    log.LogInfo(f"Starting scene tag sync from {names or 'no stash-boxes'} (dry_run={dry_run})")

    try:
        tag_cache = _build_tag_cache(client)
        run = _Run(client=client, tag_cache=tag_cache, settings=settings, blacklist=blacklist)

        for index, box_config in enumerate(boxes):
            limit = None
            if dry_run:
                limit = DRY_RUN_LIMIT - stats.total_scenes
                if limit <= 0:
                    log.LogInfo(f"[DRY RUN] Reached the {DRY_RUN_LIMIT}-scene limit; skipping {_box_name(box_config)}")
                    break

            box_stats = SyncStats()
            box = _make_box(box_config)
            try:
                _sync_box(run, box, box_stats, limit, _progress(index, len(boxes)))
            finally:
                stats.add(box_stats)
                stats.by_endpoint[box.endpoint] = {"name": box.name, **box_stats.counts()}
                _log_box_summary(box.name, box_stats, dry_run)

    except SyncAborted as e:
        stats.error = str(e)
        log.LogError(f"Scene tag sync stopped: {e}")

    if stats.error is None and stats.processed == 0 and stats.errors > 0:
        stats.error = f"All {stats.errors} scenes failed to sync. See the Stash log for details."
        log.LogError(stats.error)

    _log_summary(stats, dry_run)
    return stats


def _box_name(box_config):
    return box_config.get("name") or box_config.get("endpoint") or "stash-box"


def _make_box(box_config):
    """A _Box with a rate limiter honoring the box's max_requests_per_minute."""
    try:
        mrpm = float(box_config.get("max_requests_per_minute") or 0)
    except (TypeError, ValueError):
        mrpm = 0
    rps = min(DEFAULT_REQUESTS_PER_SECOND, mrpm / 60) if mrpm > 0 else DEFAULT_REQUESTS_PER_SECOND
    return _Box(
        endpoint=box_config.get("endpoint", ""),
        api_key=box_config.get("api_key", ""),
        name=_box_name(box_config),
        rate_limiter=RateLimiter(requests_per_second=rps),
    )


def _build_tag_cache(client):
    log.LogInfo("Building tag cache from local Stash...")
    try:
        local_tags = client.find_all_tags()
    except StashError as e:
        raise SyncAborted(f"Could not read tags from Stash: {e}") from e
    tag_cache = TagCache.build(local_tags)
    linked_count = len(tag_cache.stashdb_id_map)
    log.LogInfo(f"Tag cache built: {tag_cache.tag_count} tags ({linked_count} stash-box links)")
    return tag_cache


def _progress(box_index, box_count):
    """Report progress within one box as a slice of the whole run."""
    def report(fraction):
        log.LogProgress((box_index + min(max(fraction, 0.0), 1.0)) / box_count)
    return report


def _sync_box(run, box, stats, limit, progress):
    """Sync the local scenes linked to one stash-box."""
    log.LogInfo(f"{box.name}: querying scenes linked to {box.endpoint}...")
    if limit is not None:
        log.LogInfo(f"[DRY RUN] {box.name}: checking at most {limit} scenes")
    try:
        # Fetch them all before writing: writes bump updated_at, the sort key.
        scenes = list(run.client.iter_scenes_with_stash_id(box.endpoint, limit=limit))
    except StashError as e:
        raise SyncAborted(f"Could not read scenes linked to {box.name} from Stash: {e}") from e

    stats.total_scenes = len(scenes)
    if not scenes:
        log.LogInfo(f"{box.name}: no linked scenes")
        progress(1.0)
        return
    log.LogInfo(f"{box.name}: found {len(scenes)} linked scenes")

    log.LogInfo(f"{box.name}: pass 1, batch lookup by fingerprints...")
    retry_queue = []
    processed = _process_pass_one(run, box, scenes, stats, retry_queue, progress)
    log.LogInfo(f"{box.name}: pass 1 complete: {processed} processed, {len(retry_queue)} in retry queue")

    if retry_queue:
        log.LogInfo(f"{box.name}: pass 2, looking up {len(retry_queue)} scenes one at a time...")
        _process_pass_two(run, box, retry_queue, stats, progress)
    progress(1.0)


def _aborted_by(box, error):
    """The SyncAborted for a request the box rejected."""
    reason = f"HTTP {error.status_code}" if error.status_code else "not authorized"
    return SyncAborted(
        f"{box.name} rejected the request ({reason}). "
        "Check the API key in Settings → Metadata Providers."
    )


def _get_scene_stashdb_id(scene, endpoint):
    """Extract a scene's stash-box ID for the given endpoint."""
    stash_ids = scene.get("stash_ids", []) or []
    for sid in stash_ids:
        if sid.get("endpoint") == endpoint:
            return sid.get("stash_id")
    return None


def _get_scene_fingerprints(scene):
    """Extract fingerprints from scene for a stash-box query."""
    fingerprints = []
    files = scene.get("files", []) or []

    for file_info in files:
        for fp in file_info.get("fingerprints", []) or []:
            fp_type = fp.get("type", "").upper()
            fp_value = fp.get("value", "")

            if fp_type in ("MD5", "OSHASH", "PHASH") and fp_value:
                fingerprints.append({
                    "hash": fp_value,
                    "algorithm": fp_type
                })

    return fingerprints


def _process(run, box, scene, remote_scene, stats):
    """Process one scene against its stash-box scene and count the result."""
    result = process_scene(
        scene, remote_scene, run.tag_cache,
        run.client, run.settings, box.endpoint, run.blacklist
    )
    _update_stats(stats, result)
    return result


def _process_pass_one(run, box, scenes, stats, retry_queue, progress):
    """
    Process scenes in batches using fingerprint queries.

    Scenes without fingerprints, or whose fingerprints don't find their linked
    stash-box scene, go to `retry_queue`. A failed batch (other than a rejected
    API key) counts its scenes as errors.

    Returns number of scenes processed.
    """
    processed = 0

    for batch_start in range(0, len(scenes), FINGERPRINT_BATCH_SIZE):
        batch = scenes[batch_start:batch_start + FINGERPRINT_BATCH_SIZE]

        fingerprint_batches = []
        batch_scenes = []
        for scene in batch:
            fps = _get_scene_fingerprints(scene)
            if fps:
                fingerprint_batches.append(fps)
                batch_scenes.append(scene)
            else:
                retry_queue.append(scene)
                log.LogDebug(f"Scene {scene.get('id')}: no fingerprints, queued for pass 2")

        if fingerprint_batches:
            try:
                results = find_scenes_by_fingerprints(
                    box.endpoint, box.api_key, fingerprint_batches, box.rate_limiter
                ) or []
            except StashDBAPIError as e:
                if e.is_auth_error:
                    raise _aborted_by(box, e) from e
                log.LogError(f"{box.name}: fingerprint lookup failed for {len(batch_scenes)} scenes: {e}")
                stats.errors += len(batch_scenes)
            else:
                for i, scene in enumerate(batch_scenes):
                    expected_id = _get_scene_stashdb_id(scene, box.endpoint)
                    candidates = (results[i] if i < len(results) else None) or []
                    remote_scene = next((s for s in candidates if s and s.get("id") == expected_id), None)

                    if not remote_scene:
                        retry_queue.append(scene)
                        log.LogDebug(f"Scene {scene.get('id')}: fingerprint didn't match {box.name} ID {expected_id}, queued for pass 2")
                        continue

                    _process(run, box, scene, remote_scene, stats)
                    processed += 1

        progress((batch_start + len(batch)) / len(scenes) * 0.8)

    return processed


def _process_pass_two(run, box, retry_queue, stats, progress):
    """Process scenes individually by stash-box ID."""
    for i, scene in enumerate(retry_queue):
        progress(0.8 + i / len(retry_queue) * 0.2)
        remote_id = _get_scene_stashdb_id(scene, box.endpoint)

        if not remote_id:
            log.LogWarning(f"Scene {scene.get('id')}: no {box.name} ID found, skipping")
            stats.skipped += 1
            continue

        try:
            remote_scene = find_scene_by_id(box.endpoint, box.api_key, remote_id, box.rate_limiter)
        except StashDBAPIError as e:
            if e.is_auth_error:
                raise _aborted_by(box, e) from e
            log.LogError(f"Scene {scene.get('id')}: {box.name} lookup of {remote_id} failed: {e}")
            stats.errors += 1
            continue

        if not remote_scene:
            log.LogWarning(f"Scene {scene.get('id')}: {box.name} scene {remote_id} not found")
            stats.skipped += 1
            continue

        _process(run, box, scene, remote_scene, stats)


def _update_stats(stats, result):
    """Update stats based on ProcessResult."""
    stats.tags_skipped_total += result.tags_skipped

    if result.status == "error":
        stats.errors += 1
        return

    stats.processed += 1
    stats.tags_added_total += result.tags_added
    if result.status in ("updated", "dry_run"):  # dry_run counts as would-be-updated
        stats.updated += 1
    elif result.status == "no_changes":
        stats.no_changes += 1


def _log_box_summary(name, stats, dry_run):
    """Log one line summing up a stash-box's part of the run."""
    prefix = "[DRY RUN] " if dry_run else ""
    log.LogInfo(
        f"{prefix}{name}: {stats.total_scenes} scenes, "
        f"{stats.updated} {'would be updated' if dry_run else 'updated'}, "
        f"{stats.no_changes} unchanged, {stats.skipped} skipped, {stats.errors} errors, "
        f"{stats.tags_added_total} tags {'would be ' if dry_run else ''}added"
    )


def _log_summary(stats, dry_run):
    """Log final summary statistics."""
    prefix = "[DRY RUN] " if dry_run else ""

    log.LogInfo(f"{prefix}Sync {'stopped' if stats.error else 'complete'}!")
    log.LogInfo(f"  Total scenes: {stats.total_scenes}")
    log.LogInfo(f"  Processed: {stats.processed}")
    log.LogInfo(f"  {'Would update' if dry_run else 'Updated'}: {stats.updated}")
    log.LogInfo(f"  No changes needed: {stats.no_changes}")
    log.LogInfo(f"  Skipped: {stats.skipped}")
    log.LogInfo(f"  Errors: {stats.errors}")
    log.LogInfo(f"  Tags {'would be ' if dry_run else ''}added: {stats.tags_added_total}")
    log.LogInfo(f"  Unmatched tags skipped: {stats.tags_skipped_total}")
