"""
StashDB Scene Tag Sync

Syncs tags from StashDB to local Stash scenes, replicating
Stash's Tagger merge behavior.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import log


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
