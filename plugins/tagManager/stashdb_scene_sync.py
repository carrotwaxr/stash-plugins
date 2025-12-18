"""
StashDB Scene Tag Sync

Syncs tags from StashDB to local Stash scenes, replicating
Stash's Tagger merge behavior.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import log
from tag_cache import TagCache
from stashdb_api import RateLimiter, find_scene_by_id, find_scenes_by_fingerprints

# Constants
DRY_RUN_LIMIT = 200
BATCH_SIZE = 100  # Scenes to fetch per page from local Stash
FINGERPRINT_BATCH_SIZE = 40  # Max scenes per StashDB fingerprint query


@dataclass
class ProcessResult:
    """Result of processing a single scene."""
    status: str  # 'updated', 'no_changes', 'dry_run', 'error'
    tags_added: int = 0
    tags_skipped: int = 0
    merged_tag_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None


def match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint):
    """
    Match a StashDB tag to a local tag.

    Priority order (matches Stash's pkg/match/scraped.go:ScrapedTag):
    1. StashID link - local tag has same StashDB ID for this endpoint
    2. Name match - local tag name equals StashDB tag name (case-insensitive)
    3. Alias match - local tag alias equals StashDB tag name (case-insensitive)

    Args:
        stashdb_tag: Dict with 'id', 'name' from StashDB
        tag_cache: TagCache instance with lookup maps
        endpoint: StashDB endpoint URL

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


def process_scene(scene, stashdb_scene, tag_cache, stash, settings, endpoint):
    """
    Process a single scene's tag merge.

    Args:
        scene: Local scene dict with id, tags
        stashdb_scene: StashDB scene dict with tags
        tag_cache: TagCache instance
        stash: StashInterface (can be None for dry_run)
        settings: Plugin settings dict with 'dry_run' key
        endpoint: StashDB endpoint URL

    Returns:
        ProcessResult with status, tags_added, tags_skipped, merged_tag_ids
    """
    scene_id = scene.get("id", "unknown")
    existing_tags = scene.get("tags", []) or []
    existing_tag_ids = set(str(t.get("id", "")) for t in existing_tags if t.get("id"))

    new_tag_ids = set()
    skipped_tags = []

    stashdb_tags = stashdb_scene.get("tags", []) or []

    for stashdb_tag in stashdb_tags:
        local_id = match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint)

        if local_id:
            if local_id not in existing_tag_ids:
                new_tag_ids.add(local_id)
                log.LogDebug(f"Scene {scene_id}: matched '{stashdb_tag.get('name', '')}' -> local tag {local_id}")
            else:
                log.LogTrace(f"Scene {scene_id}: tag '{stashdb_tag.get('name', '')}' already present")
        else:
            skipped_tags.append(stashdb_tag.get("name", ""))
            log.LogDebug(f"Scene {scene_id}: no local match for '{stashdb_tag.get('name', '')}'")

    merged_tag_ids = list(existing_tag_ids | new_tag_ids)

    if not new_tag_ids:
        return ProcessResult(
            status="no_changes",
            tags_added=0,
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )

    if settings.get("dry_run", True):
        tag_names = [tag_cache.get_name(tid) or tid for tid in new_tag_ids]
        log.LogInfo(f"[DRY RUN] Scene {scene_id}: would add {len(new_tag_ids)} tags: {tag_names}")
        return ProcessResult(
            status="dry_run",
            tags_added=len(new_tag_ids),
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )

    # Live mode - update the scene
    try:
        stash.update_scene({"id": scene_id, "tag_ids": merged_tag_ids})
        tag_names = [tag_cache.get_name(tid) or tid for tid in new_tag_ids]
        log.LogInfo(f"Scene {scene_id}: added {len(new_tag_ids)} tags: {tag_names}")
        return ProcessResult(
            status="updated",
            tags_added=len(new_tag_ids),
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids
        )
    except Exception as e:
        log.LogError(f"Scene {scene_id}: failed to update - {e}")
        return ProcessResult(
            status="error",
            tags_added=0,
            tags_skipped=len(skipped_tags),
            merged_tag_ids=merged_tag_ids,
            error=str(e)
        )


@dataclass
class SyncStats:
    """Statistics for sync operation."""
    total_scenes: int = 0
    processed: int = 0
    updated: int = 0
    no_changes: int = 0
    skipped: int = 0
    errors: int = 0
    tags_added_total: int = 0
    tags_skipped_total: int = 0


def sync_scene_tags(stash, stashdb_url, stashdb_api_key, settings):
    """
    Main sync algorithm.

    1. Build tag lookup cache from local Stash
    2. Query scenes with StashIDs (paginated, sorted by updated_at ASC)
    3. Pass 1: Batch query StashDB by fingerprints (40 scenes per request)
    4. Pass 2: Sequential query for retry queue (findScene by ID)
    5. Log summary statistics

    Args:
        stash: StashInterface instance
        stashdb_url: StashDB GraphQL endpoint URL
        stashdb_api_key: StashDB API key
        settings: Plugin settings dict with 'dry_run' key

    Returns:
        SyncStats with operation statistics
    """
    stats = SyncStats()
    dry_run = settings.get("dry_run", True)

    log.LogInfo(f"Starting scene tag sync (dry_run={dry_run})")

    # Step 1: Build tag cache from local tags
    log.LogInfo("Building tag cache from local Stash...")
    local_tags = _fetch_all_local_tags(stash)
    tag_cache = TagCache.build(local_tags)

    stashdb_linked_count = len(tag_cache.stashdb_id_map)
    log.LogInfo(f"Tag cache built: {tag_cache.tag_count} tags ({stashdb_linked_count} with StashDB links)")

    # Step 2: Query scenes with StashIDs
    log.LogInfo("Querying scenes with StashDB IDs...")
    scenes = _fetch_scenes_with_stashdb_ids(stash, stashdb_url)
    stats.total_scenes = len(scenes)

    if stats.total_scenes == 0:
        log.LogInfo("No scenes with StashDB IDs found")
        return stats

    log.LogInfo(f"Found {stats.total_scenes} scenes with StashDB IDs")

    # Apply dry run limit
    if dry_run and stats.total_scenes > DRY_RUN_LIMIT:
        log.LogInfo(f"[DRY RUN] Limiting to {DRY_RUN_LIMIT} scenes (of {stats.total_scenes})")
        scenes = scenes[:DRY_RUN_LIMIT]

    # Initialize rate limiter
    rate_limiter = RateLimiter(requests_per_second=2)

    # Step 3: Pass 1 - Batch by fingerprints
    log.LogInfo("Pass 1: Batch processing by fingerprints...")
    retry_queue = []

    processed_in_pass1 = _process_pass_one(
        scenes, stashdb_url, stashdb_api_key, tag_cache,
        stash, settings, rate_limiter, stats, retry_queue
    )

    log.LogInfo(f"Pass 1 complete: {processed_in_pass1} processed, {len(retry_queue)} in retry queue")

    # Step 4: Pass 2 - Sequential fallback
    if retry_queue:
        log.LogInfo(f"Pass 2: Processing {len(retry_queue)} scenes sequentially...")
        _process_pass_two(
            retry_queue, stashdb_url, stashdb_api_key, tag_cache,
            stash, settings, rate_limiter, stats
        )

    # Step 5: Log summary
    _log_summary(stats, dry_run)

    return stats


def _fetch_all_local_tags(stash):
    """Fetch all tags from local Stash with stash_ids."""
    all_tags = []
    page = 1

    while True:
        result = stash.find_tags(
            f={},
            filter={"page": page, "per_page": BATCH_SIZE},
            fragment="id name aliases stash_ids { endpoint stash_id }"
        )

        if not result:
            break

        all_tags.extend(result)

        if len(result) < BATCH_SIZE:
            break

        page += 1

    return all_tags


def _fetch_scenes_with_stashdb_ids(stash, stashdb_url):
    """Fetch all scenes that have a StashDB ID for the given endpoint."""
    all_scenes = []
    page = 1

    filter_query = {
        "stash_id_endpoint": {
            "endpoint": stashdb_url,
            "modifier": "NOT_NULL",
            "stash_id": ""
        }
    }

    while True:
        result = stash.find_scenes(
            f=filter_query,
            filter={
                "page": page,
                "per_page": BATCH_SIZE,
                "sort": "updated_at",
                "direction": "ASC"
            },
            fragment="""
                id
                tags { id }
                stash_ids { endpoint stash_id }
                files {
                    fingerprints { type value }
                }
            """
        )

        if not result:
            break

        all_scenes.extend(result)

        if len(result) < BATCH_SIZE:
            break

        page += 1
        log.LogDebug(f"Fetched {len(all_scenes)} scenes so far...")

    return all_scenes


def _get_scene_stashdb_id(scene, endpoint):
    """Extract StashDB ID for a scene from the given endpoint."""
    stash_ids = scene.get("stash_ids", []) or []
    for sid in stash_ids:
        if sid.get("endpoint") == endpoint:
            return sid.get("stash_id")
    return None


def _get_scene_fingerprints(scene):
    """Extract fingerprints from scene for StashDB query."""
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


def _process_pass_one(scenes, stashdb_url, stashdb_api_key, tag_cache,
                       stash, settings, rate_limiter, stats, retry_queue):
    """
    Process scenes in batches using fingerprint queries.

    Returns number of scenes processed successfully.
    """
    processed = 0

    # Group scenes into batches of FINGERPRINT_BATCH_SIZE
    for batch_start in range(0, len(scenes), FINGERPRINT_BATCH_SIZE):
        batch = scenes[batch_start:batch_start + FINGERPRINT_BATCH_SIZE]

        # Build fingerprint batches
        fingerprint_batches = []
        batch_scenes = []

        for scene in batch:
            fps = _get_scene_fingerprints(scene)
            if fps:
                fingerprint_batches.append(fps)
                batch_scenes.append(scene)
            else:
                # No fingerprints - add to retry queue
                retry_queue.append(scene)
                log.LogDebug(f"Scene {scene.get('id')}: no fingerprints, queued for pass 2")

        if not fingerprint_batches:
            continue

        # Query StashDB
        stashdb_results = find_scenes_by_fingerprints(
            stashdb_url, stashdb_api_key, fingerprint_batches, rate_limiter
        )

        # Process results
        for i, (scene, stashdb_scenes) in enumerate(zip(batch_scenes, stashdb_results)):
            expected_stashdb_id = _get_scene_stashdb_id(scene, stashdb_url)

            # Find matching StashDB scene by ID
            matched_stashdb_scene = None
            for sdb_scene in (stashdb_scenes or []):
                if sdb_scene and sdb_scene.get("id") == expected_stashdb_id:
                    matched_stashdb_scene = sdb_scene
                    break

            if not matched_stashdb_scene:
                # No match by fingerprint - add to retry queue
                retry_queue.append(scene)
                log.LogDebug(f"Scene {scene.get('id')}: fingerprint didn't match expected StashDB ID, queued for pass 2")
                continue

            # Process the scene
            result = process_scene(
                scene, matched_stashdb_scene, tag_cache,
                stash, settings, stashdb_url
            )

            _update_stats(stats, result)
            processed += 1

        # Progress update
        log.LogProgress(min(1.0, (batch_start + len(batch)) / len(scenes) * 0.8))

    return processed


def _process_pass_two(retry_queue, stashdb_url, stashdb_api_key, tag_cache,
                       stash, settings, rate_limiter, stats):
    """Process scenes individually by StashDB ID."""
    for i, scene in enumerate(retry_queue):
        stashdb_id = _get_scene_stashdb_id(scene, stashdb_url)

        if not stashdb_id:
            log.LogWarning(f"Scene {scene.get('id')}: no StashDB ID found, skipping")
            stats.skipped += 1
            continue

        stashdb_scene = find_scene_by_id(
            stashdb_url, stashdb_api_key, stashdb_id, rate_limiter
        )

        if not stashdb_scene:
            log.LogWarning(f"Scene {scene.get('id')}: StashDB scene {stashdb_id} not found")
            stats.skipped += 1
            continue

        result = process_scene(
            scene, stashdb_scene, tag_cache,
            stash, settings, stashdb_url
        )

        _update_stats(stats, result)

        # Progress update (pass 2 is the remaining 20%)
        log.LogProgress(0.8 + (i + 1) / len(retry_queue) * 0.2)


def _update_stats(stats, result):
    """Update stats based on ProcessResult."""
    stats.processed += 1
    stats.tags_added_total += result.tags_added
    stats.tags_skipped_total += result.tags_skipped

    if result.status == "updated":
        stats.updated += 1
    elif result.status == "no_changes":
        stats.no_changes += 1
    elif result.status == "dry_run":
        stats.updated += 1  # Count as would-be-updated
    elif result.status == "error":
        stats.errors += 1


def _log_summary(stats, dry_run):
    """Log final summary statistics."""
    prefix = "[DRY RUN] " if dry_run else ""

    log.LogInfo(f"{prefix}Sync complete!")
    log.LogInfo(f"  Total scenes: {stats.total_scenes}")
    log.LogInfo(f"  Processed: {stats.processed}")
    log.LogInfo(f"  {'Would update' if dry_run else 'Updated'}: {stats.updated}")
    log.LogInfo(f"  No changes needed: {stats.no_changes}")
    log.LogInfo(f"  Skipped: {stats.skipped}")
    log.LogInfo(f"  Errors: {stats.errors}")
    log.LogInfo(f"  Tags {'would be ' if dry_run else ''}added: {stats.tags_added_total}")
    log.LogInfo(f"  Unmatched tags skipped: {stats.tags_skipped_total}")
