# Missing Scenes Pagination Design

## Problem Statement

The Missing Scenes plugin times out (504 errors) when querying tags/performers/studios with large scene counts on StashDB. For example, the "Lingerie" tag has 66,984 scenes - fetching all of them takes ~22 minutes, far exceeding HTTP timeout thresholds.

**Root cause**: The current implementation fetches ALL scenes from StashDB before returning anything to the UI.

**Constraint**: StashDB's API has no "exclude by scene ID" filter, so we cannot ask for "scenes matching tag X, excluding these 500 IDs I own." Filtering must happen locally after fetching.

## Solution Overview

Implement **"Fetch Until Full"** pagination with **local stash_id caching**:

1. Frontend requests a page of missing scenes (e.g., 50 results)
2. Backend fetches StashDB pages incrementally, filtering against cached local stash_ids
3. Backend returns results as soon as the requested count is reached
4. Frontend can request more pages, backend resumes from where it left off
5. User controls sort field and direction

## Design Details

### 1. API Changes

#### New `find_missing` Parameters

```python
# Request
{
    "operation": "find_missing",
    "entity_type": "tag",           # "performer" | "studio" | "tag"
    "entity_id": "123",
    "endpoint": "https://...",      # optional, stash-box endpoint

    # NEW pagination parameters:
    "page_size": 50,                # results per page (default: 50, max: 100)
    "cursor": null,                 # opaque cursor for "Load More" (null = start)
    "sort": "DATE",                 # "DATE" | "TITLE" | "CREATED_AT" | "UPDATED_AT"
    "direction": "DESC"             # "ASC" | "DESC"
}

# Response
{
    "entity_name": "Lingerie",
    "entity_type": "tag",
    "stashdb_name": "stashdb.org",
    "stashdb_url": "https://stashdb.org",

    # Counts
    "total_on_stashdb": 66984,      # known from first StashDB response
    "total_local": 523,             # scenes you own (from cache)
    "missing_count_estimate": null, # unknown until fully loaded, or exact if complete
    "missing_count_loaded": 150,    # how many missing we've found so far

    # Pagination state
    "cursor": "eyJwYWdlIjo0LCJvZmZzZXQiOjEyfQ==",  # base64 encoded state
    "has_more": true,               # more results available
    "is_complete": false,           # true when we've checked ALL StashDB scenes

    # Results
    "missing_scenes": [...],        # array of scene objects (page_size items)
    "whisparr_configured": true
}
```

#### Cursor Structure (Internal)

```python
# Encoded as base64 JSON in the cursor string
{
    "stashdb_page": 4,              # next StashDB page to fetch
    "offset": 12,                   # scenes to skip from that page (already returned)
    "sort": "DATE",
    "direction": "DESC",
    "entity_type": "tag",
    "entity_stash_id": "abc-123",   # StashDB ID of the entity
    "endpoint": "https://..."
}
```

### 2. Backend Flow

```
find_missing(page_size=50, cursor=None, sort="DATE", direction="DESC")
    │
    ├─► If cursor is None (first request):
    │       1. Get entity from local Stash, extract StashDB ID
    │       2. Load/build local stash_id cache for endpoint
    │       3. Initialize: stashdb_page=1, offset=0
    │
    ├─► If cursor provided:
    │       1. Decode cursor, restore state
    │       2. Use cached local stash_ids (already in memory)
    │
    ▼
    collected = []
    while len(collected) < page_size:
        │
        ├─► Fetch StashDB page (stashdb_page, per_page=100, sort, direction)
        │       - If empty or error: break (no more results)
        │
        ├─► For each scene starting at offset:
        │       - If scene.id NOT IN local_stash_ids_cache:
        │           - Add to collected
        │           - If len(collected) == page_size: stop, save position
        │
        ├─► If page exhausted: stashdb_page++, offset=0
        │
        └─► Safety: if stashdb_page > MAX_PAGES_PER_REQUEST (e.g., 50): break
    │
    ▼
    Return results + new cursor + metadata
```

### 3. Local Stash_ID Cache

#### Cache Strategy

```python
# In-memory cache, lives for duration of plugin process
_local_stash_id_cache = {
    # endpoint -> set of stash_ids
    "https://stashdb.org/graphql": {"uuid1", "uuid2", ...},
}

_cache_metadata = {
    "https://stashdb.org/graphql": {
        "count": 523,
        "built_at": "2025-12-17T10:30:00Z"
    }
}
```

#### Cache Building

```python
def get_or_build_cache(endpoint: str) -> set:
    if endpoint in _local_stash_id_cache:
        return _local_stash_id_cache[endpoint]

    # Query local Stash for all scenes with this endpoint
    # Request minimal fields for speed
    stash_ids = set()
    page = 1
    while True:
        result = stash_graphql("""
            query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
                findScenes(filter: $filter, scene_filter: $scene_filter) {
                    count
                    scenes {
                        stash_ids {
                            endpoint
                            stash_id
                        }
                    }
                }
            }
        """, {
            "filter": {"page": page, "per_page": 100},
            "scene_filter": {
                "stash_id_endpoint": {
                    "endpoint": endpoint,
                    "modifier": "NOT_NULL"
                }
            }
        })

        for scene in result["findScenes"]["scenes"]:
            for sid in scene["stash_ids"]:
                if sid["endpoint"] == endpoint:
                    stash_ids.add(sid["stash_id"])

        if page * 100 >= result["findScenes"]["count"]:
            break
        page += 1

    _local_stash_id_cache[endpoint] = stash_ids
    _cache_metadata[endpoint] = {
        "count": len(stash_ids),
        "built_at": datetime.now().isoformat()
    }

    return stash_ids
```

#### Cache Invalidation

- **Manual**: Add `refresh_cache: true` parameter to force rebuild
- **Automatic**: Could invalidate after N minutes or when scene count changes (future enhancement)

### 4. Frontend Changes

#### State Management

```javascript
// New state variables
let currentCursor = null;
let hasMore = true;
let isComplete = false;
let totalLoaded = 0;
let sortField = "DATE";
let sortDirection = "DESC";
```

#### UI Components

1. **Sort Controls** (in stats bar or header):
   ```html
   <select id="ms-sort-field">
       <option value="DATE">Release Date</option>
       <option value="TITLE">Title</option>
       <option value="CREATED_AT">Added to StashDB</option>
       <option value="UPDATED_AT">Last Updated</option>
   </select>
   <select id="ms-sort-direction">
       <option value="DESC">Newest First</option>
       <option value="ASC">Oldest First</option>
   </select>
   ```

2. **Updated Stats Bar**:
   ```
   Tag: Lingerie | On StashDB: 66,984 | You Have: 523 | Missing: ~66,461 (150 loaded)
   ```
   - Show estimate with "~" prefix until complete
   - Show "(X loaded)" to indicate progress

3. **Load More Button** (replaces auto-load-all):
   ```html
   <button id="ms-load-more" class="ms-btn">
       Load More (showing 150 of ~66,461)
   </button>
   ```
   - Disabled when `isComplete` or loading
   - Hidden when no more results

4. **Progress Indicator** (during load):
   ```
   Loading... fetching page 4 from StashDB
   ```

#### Event Handlers

```javascript
async function handleSearch() {
    // Reset state for new search
    currentCursor = null;
    hasMore = true;
    isComplete = false;
    missingScenes = [];
    totalLoaded = 0;

    await loadPage();
}

async function loadPage() {
    if (!hasMore || isLoading) return;

    isLoading = true;
    showLoadingIndicator();

    const result = await findMissingScenes(
        currentEntityType,
        currentEntityId,
        selectedEndpoint,
        {
            page_size: 50,
            cursor: currentCursor,
            sort: sortField,
            direction: sortDirection
        }
    );

    // Append new results
    missingScenes = [...missingScenes, ...result.missing_scenes];
    totalLoaded = missingScenes.length;

    // Update pagination state
    currentCursor = result.cursor;
    hasMore = result.has_more;
    isComplete = result.is_complete;

    updateStats(result);
    renderResults();
    updateLoadMoreButton();

    isLoading = false;
}

function handleSortChange() {
    // Changing sort requires starting over
    handleSearch();
}
```

### 5. Safety Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_PAGES_PER_REQUEST` | 50 | Prevent single request from fetching too many StashDB pages |
| `MAX_STASHDB_PAGE` | 1000 | Absolute limit on StashDB pagination (100,000 scenes) |
| `REQUEST_TIMEOUT` | 120s | Match Stash's plugin timeout |
| `PAGE_SIZE_MAX` | 100 | Maximum results per frontend request |
| `PAGE_SIZE_DEFAULT` | 50 | Default results per frontend request |

### 6. Error Handling

1. **StashDB timeout mid-pagination**: Return what we have with `has_more: true`, let user retry
2. **Cache build fails**: Return error, suggest user check Stash connectivity
3. **Invalid cursor**: Treat as new search (cursor=null)
4. **Rate limiting (429)**: Existing retry logic handles this

## Migration Path

### Phase 1: Backend Pagination (Breaking Change Protection)
- Add new parameters with defaults matching current behavior
- If `page_size` not provided, fall back to current "fetch all" behavior (with warning log)
- This allows old frontend to keep working

### Phase 2: Frontend Update
- Update JS to use new pagination
- Add sort controls
- Add "Load More" button
- Update "Add All to Whisparr" to work with loaded results only

### Phase 3: Deprecate Old Behavior
- Remove "fetch all" fallback
- Update settings UI if needed

## Testing Plan

1. **Unit Tests**:
   - Cursor encoding/decoding
   - Cache building
   - "Fetch until full" logic with mock StashDB responses

2. **Integration Tests**:
   - Small entity (< 100 scenes): single page, no cursor
   - Medium entity (500 scenes): multiple pages
   - Large entity with high ownership %: verify no empty pages

3. **Manual Testing**:
   - Test with "Lingerie" tag (66k scenes)
   - Verify no 504 errors
   - Test sort field changes
   - Test "Load More" flow
   - Test Whisparr integration still works

## Design Decisions

1. **"Add All to Whisparr"**: Adds all currently LOADED scenes only. Button text reflects this: "Add All to Whisparr (47)" where 47 is the number of loaded scenes not yet in Whisparr.

2. **Cache Refresh**: No manual refresh button needed. Cache is rebuilt automatically:
   - On first search of a session
   - When the cached endpoint doesn't match the requested endpoint
   - The cache is lightweight and fast to build

3. **Estimated vs Exact Counts**: The estimate `total_on_stashdb - total_local` is acceptable. `total_local` counts only scenes with stash_ids for the endpoint (not all local scenes), so the estimate is reasonably accurate.

## Summary

This design replaces the "fetch everything upfront" approach with incremental pagination that:
- Returns results in seconds instead of minutes
- Eliminates 504 timeouts
- Gives users control over sort order
- Uses caching to make filtering efficient
- Maintains compatibility during rollout
