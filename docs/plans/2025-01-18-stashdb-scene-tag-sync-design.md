# StashDB Scene Tag Sync - Design Document

## Overview

Add a new Plugin Task to tagManager that syncs tags from StashDB to local Stash scenes. This automates the manual process of re-scraping scenes in the Tagger UI to pull updated tags after linking local tags to StashDB via StashIDs.

## Problem Statement

With Stash 0.30's new feature allowing Tags to be linked to StashDB Tags via StashIDs, users need a way to backfill tags on existing scenes. Currently this requires:
1. Opening Tagger view
2. Searching each scene
3. Selecting the correct match
4. Clicking Save (with Tag mode set to Merge)

For libraries with tens of thousands of scenes that already have StashDB IDs, this manual process is impractical.

## Solution

A Plugin Task that:
1. Queries all local scenes with StashDB IDs
2. Fetches tag data from StashDB for each scene
3. Matches StashDB tags to local tags using Stash's exact matching logic
4. Merges new tags onto scenes (preserving existing tags)

## Requirements

### Functional Requirements

1. **Match Stash's Tagger behavior exactly** - Use the same tag matching logic:
   - Priority 1: Match by StashID link (local tag linked to same StashDB tag ID)
   - Priority 2: Match by local tag name = StashDB tag name (case-insensitive)
   - Priority 3: Match by local tag alias = StashDB tag name (case-insensitive)

2. **Merge mode only** - Preserve all existing tags on scenes, only add new matched tags

3. **Skip unmatched tags silently** - If a StashDB tag has no local match, skip it (user can add/link tags and re-run)

4. **Dry Run mode** - Log what would happen without making changes, capped at 200 scenes for preview

5. **Process all scenes with StashIDs** - Sort by `updated_at` ascending to process oldest first

6. **Graceful failure** - If a scene fails, log the error and continue with the next scene

### Non-Functional Requirements

1. **Efficient API usage** - Use batch fingerprint queries where possible, fall back to individual queries
2. **Rate limiting** - Stay under StashDB's 240 req/min limit (use 2 req/sec with backoff)
3. **Solid logging** - Use log levels effectively for troubleshooting
4. **Testable** - Unit tests, integration tests, manual test scripts

## Technical Design

### Architecture

```
tagManager/
├── tagManager.yml           # Add new task + setting
├── tag_manager.py           # Add mode handler
├── stashdb_scene_sync.py    # NEW: Main sync logic
├── stashdb_api.py           # Existing: Add scene query methods
└── tests/
    ├── test_stashdb_scene_sync.py    # NEW: Unit tests
    └── integration_test_sync.py       # NEW: Integration tests
```

### New Setting

```yaml
# In tagManager.yml
settings:
  # ... existing settings ...
  syncDryRun:
    displayName: "Scene Tag Sync - Dry Run"
    description: "Preview what tags would be added without making changes (caps at 200 scenes)"
    type: BOOLEAN
```

### New Task

```yaml
# In tagManager.yml
tasks:
  - name: "Sync Scene Tags from StashDB"
    description: "Fetch tags from StashDB for all scenes with StashIDs and merge with local tags"
    defaultArgs:
      mode: sync_scene_tags
```

### Core Algorithm

```python
def sync_scene_tags(stash, settings, stashdb_client):
    """
    Main sync algorithm.

    1. Build tag lookup cache from local Stash
    2. Query scenes with StashIDs (paginated, sorted by updated_at ASC)
    3. Pass 1: Batch query StashDB by fingerprints (40 scenes per request)
       - Validate returned scene StashID matches expected
       - Process tag merge for matches
       - Queue mismatches for retry
    4. Pass 2: Sequential query for retry queue (findScene by ID)
    5. Log summary statistics
    """
```

### Tag Matching Logic

Must exactly replicate `pkg/match/scraped.go:ScrapedTag()`:

```python
def match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint):
    """
    Match a StashDB tag to a local tag.

    Priority order (matches Stash's ScrapedTag function):
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
    stashdb_id = stashdb_tag.get('id')
    stashdb_name = stashdb_tag.get('name', '')

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
```

### Tag Cache Structure

```python
class TagCache:
    """
    Pre-built lookup maps for efficient tag matching.

    Maps:
    - stashdb_id_map: {(endpoint, stashdb_id): local_tag_id}
    - name_map: {lowercase_name: local_tag_id}
    - alias_map: {lowercase_alias: local_tag_id}
    """

    @classmethod
    def build(cls, stash):
        """
        Query all local tags and build lookup maps.

        For each tag:
        - Add name to name_map
        - Add each alias to alias_map
        - For each stash_id, add to stashdb_id_map
        """
```

### StashDB API Methods

Add to `stashdb_api.py`:

```python
def find_scene_by_id(self, scene_id):
    """
    Query StashDB for a single scene by its ID.

    GraphQL: findScene(id: ID!): Scene

    Returns scene dict with tags, or None if not found.
    """

def find_scenes_by_fingerprints(self, fingerprint_batches):
    """
    Batch query StashDB for scenes by fingerprints.

    GraphQL: findScenesBySceneFingerprints(fingerprints: [[FingerprintQueryInput!]!]!)

    Args:
        fingerprint_batches: List of fingerprint lists (max 40 batches)
        Each fingerprint: {'hash': str, 'algorithm': 'MD5'|'OSHASH'|'PHASH'}

    Returns list of lists of scene dicts.
    """
```

### Rate Limiting

```python
class RateLimiter:
    """
    Token bucket rate limiter for StashDB API calls.

    - Target: 2 requests/second (conservative, under 4/s limit)
    - Handles 429 responses with exponential backoff
    - Max 3 retries before giving up on a request
    """

    def __init__(self, requests_per_second=2):
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0

    def wait(self):
        """Block until rate limit allows next request."""

    def backoff(self, attempt):
        """Calculate backoff delay: 2^attempt seconds (1s, 2s, 4s)."""
```

### Scene Processing

```python
def process_scene(scene, stashdb_scene, tag_cache, stash, settings, endpoint):
    """
    Process a single scene's tag merge.

    Args:
        scene: Local scene dict with id, tag_ids
        stashdb_scene: StashDB scene dict with tags
        tag_cache: TagCache instance
        stash: StashInterface
        settings: Plugin settings
        endpoint: StashDB endpoint URL

    Returns:
        ProcessResult with status, tags_added, tags_skipped
    """
    existing_tag_ids = set(t['id'] for t in scene.get('tags', []))
    new_tag_ids = set()
    skipped_tags = []

    for stashdb_tag in stashdb_scene.get('tags', []):
        local_id = match_stashdb_tag_to_local(stashdb_tag, tag_cache, endpoint)

        if local_id:
            if local_id not in existing_tag_ids:
                new_tag_ids.add(local_id)
                log.debug(f"Scene {scene['id']}: matched '{stashdb_tag['name']}' -> local tag {local_id}")
        else:
            skipped_tags.append(stashdb_tag['name'])
            log.debug(f"Scene {scene['id']}: no local match for '{stashdb_tag['name']}'")

    if not new_tag_ids:
        return ProcessResult(status='no_changes', tags_added=0, tags_skipped=len(skipped_tags))

    merged_tag_ids = list(existing_tag_ids | new_tag_ids)

    if settings['dry_run']:
        tag_names = [tag_cache.get_name(tid) for tid in new_tag_ids]
        log.info(f"[DRY RUN] Scene {scene['id']}: would add {len(new_tag_ids)} tags: {tag_names}")
        return ProcessResult(status='dry_run', tags_added=len(new_tag_ids), tags_skipped=len(skipped_tags))

    try:
        stash.update_scene({'id': scene['id'], 'tag_ids': merged_tag_ids})
        log.info(f"Scene {scene['id']}: added {len(new_tag_ids)} tags")
        return ProcessResult(status='updated', tags_added=len(new_tag_ids), tags_skipped=len(skipped_tags))
    except Exception as e:
        log.error(f"Scene {scene['id']}: failed to update - {e}")
        return ProcessResult(status='error', tags_added=0, tags_skipped=len(skipped_tags), error=str(e))
```

### Logging Strategy

| Level | Usage | Example |
|-------|-------|---------|
| ERROR | API failures, update failures, exceptions | `Scene abc123: failed to update - Connection refused` |
| WARNING | Scene skipped, unexpected state | `Scene abc123: skipped - no fingerprints for batch query` |
| INFO | Progress, scenes updated, summary | `Scene abc123: added 3 tags [Anal, Blonde, Creampie]` |
| DEBUG | Match decisions, API details, cache hits | `Scene abc123: matched 'Anal Creampie' -> local tag 456` |

### Error Handling

| Error | Handling |
|-------|----------|
| API 429 (rate limit) | Exponential backoff, retry up to 3 times |
| API 5xx (server error) | Log ERROR, skip scene, continue |
| API timeout | Log ERROR, skip scene, continue |
| Malformed response | Log ERROR with details, skip scene, continue |
| Scene update fails | Log ERROR, scene unchanged, continue |
| Unexpected exception | Log ERROR with traceback, skip scene, continue |

### Two-Pass Processing

**Pass 1 - Batch by Fingerprints:**
```python
def pass_one_batch_fingerprints(scenes, stashdb_client, tag_cache, stash, settings):
    """
    Process scenes in batches of 40 using fingerprint queries.

    For efficiency, query StashDB by fingerprints (40 scenes per request).
    Validate that returned scene's StashID matches expected.

    Returns:
        processed_count: Number of scenes successfully processed
        retry_queue: List of scenes that need individual lookup
    """
```

**Pass 2 - Sequential Fallback:**
```python
def pass_two_sequential(retry_queue, stashdb_client, tag_cache, stash, settings):
    """
    Process remaining scenes individually by StashDB ID.

    For scenes where fingerprint matching failed or returned wrong scene.
    Uses findScene(id) for guaranteed accuracy.
    """
```

## Testing Plan

### Unit Tests (`tests/test_stashdb_scene_sync.py`)

1. **Tag matching logic:**
   - Match by StashID link
   - Match by name (case-insensitive)
   - Match by alias (case-insensitive)
   - No match returns None
   - Priority order is respected

2. **Tag cache building:**
   - Names indexed correctly
   - Aliases indexed correctly
   - StashIDs indexed by endpoint

3. **Merge calculation:**
   - Existing tags preserved
   - New tags added
   - Duplicates not added
   - Empty merge (no new tags)

4. **Dry run behavior:**
   - No mutations called
   - Correct logging
   - Caps at 200 scenes

5. **Error handling:**
   - Scene continues after error
   - Error logged correctly
   - Summary stats accurate

### Integration Tests (`tests/integration_test_sync.py`)

Uses credentials from `~/code/.env`:

1. **StashDB API connectivity:**
   - `findScene(id)` returns valid data
   - `findScenesBySceneFingerprints` returns valid data
   - Rate limiting works correctly

2. **Local Stash connectivity:**
   - Can query scenes with StashIDs
   - Can query tags with StashIDs
   - Can update scene tags (on test scene)

3. **End-to-end dry run:**
   - Process small batch of real scenes
   - Verify log output matches expected
   - No mutations made

### Manual Testing

1. Run with `syncDryRun: true` on full library
2. Review logs for correctness
3. Pick 5 scenes, verify expected tags would be added
4. Run with `syncDryRun: false` on small filtered set
5. Verify tags actually added correctly
6. Verify mcMetadata hook behavior (if enabled)

## Configuration

### Environment Variables (from `~/code/.env`)

```
STASHDB_URL=https://stashdb.org/graphql
STASHDB_API_KEY=<key>
STASH_URL=http://10.0.0.4:6969
STASH_API_KEY=<key>
```

### Plugin Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `syncDryRun` | BOOLEAN | true | Preview mode - log without changes |

## Dependencies

- `stashapi` (stashapp-tools) - Stash GraphQL client
- `requests` - HTTP client for StashDB
- `time` - Rate limiting

No new dependencies required.

## Documentation

Add to tagManager README:

```markdown
## Sync Scene Tags from StashDB

Automatically sync tags from StashDB to your local scenes.

### Prerequisites
1. Scenes must have StashDB IDs (linked via Tagger or other means)
2. Local tags should be linked to StashDB tags via StashIDs (use Tag Manager UI)

### Usage
1. Go to Settings > Tasks > Plugin Tasks
2. Find "Sync Scene Tags from StashDB"
3. Enable "Dry Run" in Plugin Settings to preview changes
4. Click Run

### How It Works
- Queries all scenes with StashDB IDs
- For each scene, fetches tag data from StashDB
- Matches StashDB tags to local tags by:
  1. StashID link (most reliable)
  2. Exact name match
  3. Exact alias match
- Adds matched tags (preserves existing tags)
- Skips unmatched tags (add/link them locally and re-run)

### Notes
- If mcMetadata's Scene.Update.Post hook is enabled, it will trigger for each updated scene
- Large libraries may take hours to process (rate limited to respect StashDB)
- Safe to re-run - already-tagged scenes are skipped efficiently
```

## Open Questions (Resolved)

1. ~~Rate limiting~~ - 2 req/sec with exponential backoff on 429
2. ~~Dry run cap~~ - 200 scenes
3. ~~Cancellation~~ - Use Stash's built-in task cancellation
4. ~~Progress tracking~~ - No state persistence, operation is idempotent

## References

- Stash tag matching: `stash/pkg/match/scraped.go:ScrapedTag()`
- StashDB scene query: `stash-box/graphql/schema/schema.graphql:findScene`
- StashDB fingerprint batch: `findScenesBySceneFingerprints` (max 40)
- Rate limit PR: https://github.com/stashapp/stash/pull/5764
