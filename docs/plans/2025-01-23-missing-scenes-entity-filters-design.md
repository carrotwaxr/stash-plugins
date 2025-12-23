# Missing Scenes Entity Filters Design

## Overview

Add two "Favorite [Entity]" filter checkboxes to each Missing Scenes dialog, allowing users to filter results to only scenes that include their favorited performers, studios, or tags. Filters are applied server-side (Python backend) and persist within the browser session.

| Searching by | Checkbox 1 | Checkbox 2 |
|-------------|------------|------------|
| Performer | Favorite Studios | Favorite Tags |
| Studio | Favorite Performers | Favorite Tags |
| Tag | Favorite Performers | Favorite Studios |

When multiple checkboxes are checked, results must match **all** checked filters (AND logic).

## Background

PR #47 by jittarao proposed a frontend-only implementation for filtering by favorite performers. After review, we determined that backend filtering is the better approach because:

1. **Pagination correctness** - Frontend filtering only works on loaded scenes; backend filtering ensures pagination returns correctly filtered results
2. **Single source of truth** - Backend already owns all Stash/StashDB query logic
3. **No wasted data transfer** - Only matching scenes sent to frontend
4. **Simpler frontend** - Just checkboxes that toggle request parameters

## Matching Strategy

**Strict stash_id matching only.** A scene's performer/studio/tag is considered a "favorite" only if:

1. The local entity is marked as favorite in Stash
2. The local entity has a stash_id linked to the same StashDB endpoint being searched
3. That stash_id matches the scene's performer/studio/tag ID from StashDB

Name-based fallback matching is explicitly excluded to avoid false positives.

## Backend Changes (Python)

### New function: `get_favorite_stash_ids()`

```python
def get_favorite_stash_ids(entity_type: str, endpoint: str) -> set[str]:
    """
    Get stash_ids for all favorited entities of a type.

    Args:
        entity_type: "performer", "studio", or "tag"
        endpoint: StashDB endpoint URL to match stash_ids against

    Returns:
        Set of StashDB IDs for favorites linked to that endpoint
    """
```

Uses `filter_favorites: true` in the GraphQL query. Paginates through all results since users may have many favorites.

### Modified: `find_missing_scenes_paginated()`

Accepts new optional parameters:
- `filter_favorite_performers: bool`
- `filter_favorite_studios: bool`
- `filter_favorite_tags: bool`

When any filter is enabled:
1. Fetch the corresponding favorite stash_ids once (cached for the request)
2. Pass favorite sets to `fetch_until_full()`

### Modified: `fetch_until_full()`

Accepts optional favorite stash_id sets:
- `favorite_performer_ids: set[str] | None`
- `favorite_studio_ids: set[str] | None`
- `favorite_tag_ids: set[str] | None`

During scene processing, check if scene passes all enabled filters before adding to collected results. This ensures pagination works correctly - we keep fetching StashDB pages until we have enough *filtered* results.

### Filter logic for scenes

```python
def scene_passes_filters(scene, favorite_performer_ids, favorite_studio_ids, favorite_tag_ids):
    # If no filters enabled, pass
    if not any([favorite_performer_ids, favorite_studio_ids, favorite_tag_ids]):
        return True

    # Check each enabled filter (AND logic)
    if favorite_performer_ids is not None:
        scene_performer_ids = {p["performer"]["id"] for p in scene.get("performers", [])}
        if not scene_performer_ids & favorite_performer_ids:
            return False

    if favorite_studio_ids is not None:
        studio = scene.get("studio")
        if not studio or studio.get("id") not in favorite_studio_ids:
            return False

    if favorite_tag_ids is not None:
        scene_tag_ids = {t["id"] for t in scene.get("tags", [])}
        if not scene_tag_ids & favorite_tag_ids:
            return False

    return True
```

## Frontend Changes (JavaScript)

### New state variables

```javascript
// Filter state (persists within session)
let filterFavoritePerformers = false;
let filterFavoriteStudios = false;
let filterFavoriteTags = false;
```

### Modified: `createModal()`

Renders two checkboxes dynamically based on `currentEntityType`:

| `currentEntityType` | Show checkboxes for |
|---------------------|---------------------|
| `"performer"` | Studios, Tags |
| `"studio"` | Performers, Tags |
| `"tag"` | Performers, Studios |

Checkboxes placed in the sort controls row.

### Modified: Backend calls

When calling `find_missing` operation, include filter flags:

```javascript
variables: {
  operation: "find_missing",
  entity_type: currentEntityType,
  entity_id: currentEntityId,
  filter_favorite_performers: filterFavoritePerformers,
  filter_favorite_studios: filterFavoriteStudios,
  filter_favorite_tags: filterFavoriteTags,
  // ... existing params
}
```

### Checkbox behavior

When toggled:
1. Update state variable
2. Reset pagination (clear results, reset cursor)
3. Re-fetch from backend with new filter params

## Files to Modify

- `plugins/missingScenes/missing_scenes.py` - Add `get_favorite_stash_ids()`, modify `find_missing_scenes_paginated()` and `fetch_until_full()`
- `plugins/missingScenes/missing-scenes.js` - Add filter state, checkboxes, pass filter params to backend
- `plugins/missingScenes/missing-scenes.css` - Minor styling for checkbox layout (if needed)

## Out of Scope

- Name fallback matching (excluded for accuracy)
- Favorite highlighting on scene cards (can be added later)
- Per-entity-type filter persistence (session-wide is sufficient)

## Credits

Feature idea inspired by PR #47 from jittarao.
