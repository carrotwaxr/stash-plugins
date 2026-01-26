# Tag Manager Multi-Endpoint Support

**Issue**: [#91](https://github.com/carrotwaxr/stash-plugins/issues/91)
**Date**: 2026-01-25
**Status**: Approved

## Problem Summary

The tagManager plugin has several issues with multi-endpoint (multi-stash-box) support:

1. **Stash IDs replaced instead of merged** - When matching a tag to a stash-box endpoint, existing stash_ids from other endpoints are deleted instead of preserved
2. **Filter is endpoint-agnostic** - "Show Unmatched" doesn't consider which endpoint is selected; a tag matched to StashDB still appears matched when PMVstash is selected
3. **Browse tab can't link existing tags** - Importing a tag that already exists locally fails with "tag already exists" error instead of adding the stash_id

### Minor UI Issues
- Action text says "StashDB ID will be added" regardless of selected endpoint
- Browse tab lacks matched/unmatched filter dropdown
- Tab name "Browse StashDB" is confusing (StashDB is a specific endpoint)
- Cache doesn't reload when switching endpoints on Browse tab

## Design

### 1. Stash ID Merging Fix

**Location**: `tag-manager.js` lines ~1946-1952

**Current behavior**:
```javascript
const updateInput = {
  id: tag.id,
  stash_ids: [{
    endpoint: endpoint,
    stash_id: stashdbTag.id,
  }],
};
```

**Fixed behavior**:
```javascript
// Preserve existing stash_ids, filter out current endpoint (allows re-matching)
const existingStashIds = tag.stash_ids || [];
const filteredStashIds = existingStashIds.filter(
  sid => sid.endpoint !== endpoint
);

const updateInput = {
  id: tag.id,
  stash_ids: [...filteredStashIds, {
    endpoint: endpoint,
    stash_id: stashdbTag.id,
  }],
};
```

Also update local state at line ~2102 to preserve existing stash_ids.

### 2. Endpoint-Aware Filtering

**Location**: `tag-manager.js` lines ~827-839

**Current behavior**: Checks if tag has *any* stash_ids

**Fixed behavior**: Check if tag has stash_id for *selected* endpoint

```javascript
function getFilteredTags() {
  const endpoint = selectedStashBox?.endpoint;

  const hasEndpointMatch = (tag) =>
    tag.stash_ids?.some(sid => sid.endpoint === endpoint);

  const unmatchedTags = localTags.filter(t => !hasEndpointMatch(t));
  const matchedTags = localTags.filter(t => hasEndpointMatch(t));

  switch (currentFilter) {
    case 'matched':
      return { filtered: matchedTags, unmatched: unmatchedTags, matched: matchedTags };
    case 'all':
      return { filtered: localTags, unmatched: unmatchedTags, matched: matchedTags };
    default:
      return { filtered: unmatchedTags, unmatched: unmatchedTags, matched: matchedTags };
  }
}
```

### 3. Browse Tab Smart Import

**Location**: `tag-manager.js` lines ~878-918

When importing, check if tag exists locally. If so, update it (add stash_id) instead of failing on create.

```javascript
const existingTag = localTags.find(t =>
  t.name.toLowerCase() === stashdbTag.name.toLowerCase() ||
  t.aliases?.some(a => a.toLowerCase() === stashdbTag.name.toLowerCase())
);

if (existingTag) {
  // Update existing tag with new stash_id
  const existingStashIds = existingTag.stash_ids || [];
  const filteredStashIds = existingStashIds.filter(
    sid => sid.endpoint !== selectedStashBox.endpoint
  );

  await updateTag({
    id: existingTag.id,
    stash_ids: [...filteredStashIds, {
      endpoint: selectedStashBox.endpoint,
      stash_id: stashdbId
    }]
  });
  linked++;
} else {
  // Create new tag (existing logic)
  await tagCreate({ ... });
  created++;
}
```

**UI Changes**:
- Allow checkbox selection for existing tags (currently disabled)
- Add tooltip: "Will link to existing tag: {name}"
- Update success message: "Created X tags, linked Y existing"

### 4. UI Polish

#### 4a. Dynamic Endpoint Name
Change "StashDB ID will be added" to use actual endpoint name:
```javascript
const endpointName = selectedStashBox?.name ||
  selectedStashBox?.endpoint?.replace(/https?:\/\//, '').split('/')[0] ||
  'Stash-Box';
```

#### 4b. Browse Tab Filter
Add matched/unmatched/all dropdown to Browse tab, filtering by whether each stashdb tag exists locally for this endpoint.

#### 4c. Tab Name
Change "Browse StashDB" to "Browse Stash-Box"

#### 4d. Cache Reload
When endpoint changes on Browse tab, auto-refresh cache for new endpoint if not already cached.

## Files Modified

- `plugins/tagManager/tag-manager.js` - All changes

## Testing Plan

Using test instance at `http://10.0.0.4:6971`:

1. **Stash ID merging**
   - Create/find a tag, match to StashDB
   - Switch to PMVstash, match same tag
   - Verify both stash_ids are preserved

2. **Endpoint-aware filtering**
   - With a tag matched to StashDB only
   - Select PMVstash, verify tag shows in "Unmatched"
   - Match to PMVstash, verify it moves to "Matched"

3. **Smart import**
   - Create a local tag manually
   - On Browse tab, find same tag name in stash-box
   - Import it, verify stash_id is added (no error)

4. **UI polish**
   - Verify endpoint name appears correctly in action text
   - Verify Browse tab filter works
   - Verify cache reloads on endpoint switch
