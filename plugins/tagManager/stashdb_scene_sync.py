"""
StashDB Scene Tag Sync

Syncs tags from StashDB to local Stash scenes, replicating
Stash's Tagger merge behavior.
"""

import log


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
