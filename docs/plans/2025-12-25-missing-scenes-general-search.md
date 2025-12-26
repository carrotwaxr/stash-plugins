# Missing Scenes General Search Design

**Date**: 2025-12-25
**Status**: Draft
**Branch**: `feature/missing-scenes-general-search`

## Overview & Goals

**Missing Scenes General Search** extends the existing Missing Scenes plugin with a standalone browse experience for discovering StashDB content without navigating to specific entity pages.

### Goals

- Browse all StashDB scenes you're missing, filtered by preferences
- Filter by favorite performers, studios, and/or tags in a single view
- Exclude unwanted content via configurable tag UUIDs
- Provide multiple entry points for discoverability
- Maintain performance with large favorite collections

### Non-Goals (for initial release)

- Trending/discovery mode (future enhancement)
- Saved filter presets
- Bulk actions beyond "Add to Whisparr"

---

## Entry Points & Navigation

### Entry Point 1: Plugin Settings Link

In **Settings > Plugins > Missing Scenes**, add a prominent button/link:

```
[Missing Scenes] - "Search for missing scenes across all your favorites"
```

### Entry Point 2: Scenes Page Button

Inject a button into the main Scenes page header (alongside existing filter controls):

```
[Missing Scenes]
```

Uses the same mutation observer pattern as existing entity page buttons.

### Entry Point 3: Dedicated Route

Full page accessible at:

```
/plugin/missingScenes/browse
```

This route is bookmarkable and can be linked from external tools.

### Technical Note

Stash plugins register custom routes via the `ui.requires` field or by injecting route handlers in JavaScript. The page will be a full SPA-style view rendered by the plugin's JS, not a server-rendered page.

---

## User Interface Layout

### Page Structure

```
┌─────────────────────────────────────────────────────────────┐
│  Missing Scenes                                    [Close X] │
├─────────────────────────────────────────────────────────────┤
│  Filters:                                                    │
│  ┌─────────────────┐ ┌─────────────────┐ ┌────────────────┐ │
│  │☐ Fav Performers │ │☐ Fav Studios    │ │☐ Fav Tags      │ │
│  └─────────────────┘ └─────────────────┘ └────────────────┘ │
│                                                              │
│  Sort: [Release Date ▾]  [Newest First ▾]                   │
├─────────────────────────────────────────────────────────────┤
│  Stats: Showing 50 of ~12,345 missing scenes                │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ Scene 1 │ │ Scene 2 │ │ Scene 3 │ │ Scene 4 │  ...      │
│  │  thumb  │ │  thumb  │ │  thumb  │ │  thumb  │           │
│  │  title  │ │  title  │ │  title  │ │  title  │           │
│  │ studio  │ │ studio  │ │ studio  │ │ studio  │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                              │
│                    [Load More]                               │
└─────────────────────────────────────────────────────────────┘
```

### Filter Behavior

- **No filters checked (default)**: Query all StashDB scenes, filter out owned, paginate
- **One or more checked**: AND logic - scenes must match ALL enabled filters
- Excluded tags (from settings) always applied regardless of checkbox state

### Initial State

- All checkboxes **unchecked**
- Immediately loads first page of results (newest first by default)
- Same browsing experience as stashdb.org, minus scenes you own

---

## Plugin Settings

### New Settings

Add to `missingScenes.yml`:

```yaml
excludedTags:
  displayName: Excluded Tags
  description: Comma-separated list of StashDB Tag UUIDs to exclude from all Missing Scenes results. Find tag UUIDs on StashDB tag pages.
  type: STRING

favoriteLimit:
  displayName: Favorite Limit
  description: Maximum number of favorites to use when filtering (default 100). Higher values may slow queries.
  type: NUMBER
```

### Example Excluded Tags Value

```
a1b2c3d4-...,e5f6g7h8-...,i9j0k1l2-...
```

### Settings Summary

| Setting | Purpose | Default |
|---------|---------|---------|
| `excludedTags` | Tag UUIDs to always exclude | (empty) |
| `favoriteLimit` | Max favorites per filter type | 100 |
| Existing settings | Stash-box endpoint, Whisparr config, etc. | (unchanged) |

---

## Backend API Design

### New Operation: `browse_stashdb`

The Python backend will handle a new operation for general browsing:

```python
operation: "browse_stashdb"
args:
  page_size: 50
  cursor: <string|null>        # Pagination cursor
  sort: "DATE"                 # DATE, TITLE, CREATED_AT, UPDATED_AT, TRENDING
  direction: "DESC"            # ASC, DESC
  filter_favorite_performers: false
  filter_favorite_studios: false
  filter_favorite_tags: false
```

### Response Structure

```python
{
  "stashdb_name": "StashDB",
  "stashdb_url": "https://stashdb.org",
  "total_on_stashdb": 123456,       # Total scenes matching filters
  "missing_count_loaded": 50,        # Scenes in this response
  "cursor": "<next_page_cursor>",
  "has_more": true,
  "is_complete": false,
  "missing_scenes": [...],
  "whisparr_configured": true,
  "filters_active": false,
  "excluded_tags_applied": true
}
```

### Query Flow

1. Build StashDB query with:
   - Excluded tags via `tags: { value: [...], modifier: EXCLUDES }`
   - Optional performer filter via `performers: { value: [...], modifier: INCLUDES }`
   - Optional studio filter via `studios: { value: [...], modifier: INCLUDES }`
   - Optional tag filter via `tags: { value: [...], modifier: INCLUDES }` (combined with excludes)
2. Fetch page from StashDB
3. Filter out scenes already in local Stash (using cached stash_ids)
4. Return missing scenes with Whisparr status

---

## Performance & Limits

### Favorite ID Limits

When favorite filters are enabled, we fetch favorite entities from local Stash with limits:

| Entity | Sort Order | Limit | Rationale |
|--------|-----------|-------|-----------|
| Performers | `last_o_at DESC` | 100 | Most recently engaged |
| Studios | `scenes_count DESC` | 100 | Most content |
| Tags | `scenes_count DESC` | 100 | Most content |

If user has more favorites than the limit, only the top N (by sort) are used. The UI will show a notice:

```
"Using top 100 favorite performers (sorted by recent activity)"
```

### Local Scene Cache

Reuse existing `get_or_build_cache()` mechanism - builds a set of all local stash_ids for the endpoint on first request, cached for the session.

### Pagination Strategy

Same "fetch until full" approach as existing entity queries:

1. Fetch page from StashDB (100 scenes)
2. Filter against local cache + apply favorite filters
3. Collect until we have `page_size` missing scenes (default 50)
4. Return cursor for continuation

### Rate Limiting

Existing `stashbox_api` module handles:

- Delay between paginated requests (default 0.5s)
- Retry with backoff on 429/5xx errors
- Configurable via plugin settings

---

## Implementation Plan

### Phase 1: Backend Changes

1. **Add new settings** to `missingScenes.yml`
   - `excludedTags` (STRING)
   - `favoriteLimit` (NUMBER)

2. **Add `browse_stashdb` operation** to `missing_scenes.py`
   - Parse excluded tags from settings
   - Fetch favorite IDs with sorting/limits
   - Build StashDB query with filters
   - Reuse existing pagination and caching logic

3. **Update `stashbox_api.py`**
   - Add `query_scenes_browse()` function for unscoped queries
   - Support combined INCLUDES + EXCLUDES tag filters

### Phase 2: Frontend Changes

4. **Create browse page** (`missing-scenes-browse.js` or extend existing)
   - Full page layout with header, filters, grid, pagination
   - Route handler for `/plugin/missingScenes/browse`

5. **Add entry points**
   - Scenes page button injection
   - Settings page link (may require settings UI customization)

6. **Reuse existing components**
   - Scene card rendering
   - Whisparr integration buttons
   - Sort controls

### Phase 3: Polish

7. **UI feedback**
   - Loading states
   - "Using top N favorites" notices
   - Empty state messaging

8. **Testing**
   - Large favorite collections
   - Excluded tags filtering
   - Pagination edge cases

---

## Open Questions

None at this time.

## Future Enhancements

- **Trending/Discovery Mode**: Sort by TRENDING to surface popular new content
- **Saved Filter Presets**: Remember filter combinations
- **Text Search**: Search by scene title within results
- **Tag Browser**: UI for selecting excluded tags instead of raw UUIDs
