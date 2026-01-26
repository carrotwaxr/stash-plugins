# Tag Manager Parent Dropdown Fix

**Issue**: [#80](https://github.com/carrotwaxr/stash-plugins/issues/80)
**Date**: 2026-01-25
**Status**: Draft

## Problem Summary

When opening the diff dialog for a tag that already has a parent, the parent dropdown:

1. **Doesn't show the existing parent** - The tag's current parent(s) are not displayed in the dropdown
2. **Defaults to "Create X"** - Even when good matches exist in `parentMatches`, "Create X" is selected by default if there's no saved mapping
3. **No visual indicator** - User can't tell if the tag already has a parent relationship

### Root Cause

The `fetchLocalTags()` function doesn't include `parents` data in its GraphQL query. When rendering the diff modal, `tag.parents` is undefined, so existing parent relationships aren't shown.

The logic at lines 1717-1728 only checks:
1. Saved category mappings
2. Fuzzy matches against StashDB category name

It never looks at the tag's actual current parents.

## Design

### 1. Add Parents to Local Tags Query

**Location**: `tag-manager.js` lines ~206-222

Add `parents` to `fetchLocalTags()` query:

```javascript
async function fetchLocalTags() {
  const query = `
    query FindTags {
      findTags(filter: { per_page: -1 }) {
        count
        tags {
          id
          name
          description
          aliases
          stash_ids {
            endpoint
            stash_id
          }
          parents {
            id
            name
          }
        }
      }
    }
  `;
  // ...
}
```

### 2. Update Parent Selection Logic

**Location**: `tag-manager.js` lines ~1711-1729

Update the parent selection logic to prioritize existing parents:

```javascript
// Category/parent state
const hasCategory = !!stashdbTag.category?.name;
let selectedParentId = null;
let createParentIfMissing = true;
let parentMatches = [];
let existingParents = tag.parents || [];

if (hasCategory) {
  // Check for saved mapping first
  const savedMapping = categoryMappings[stashdbTag.category.name];
  if (savedMapping) {
    selectedParentId = savedMapping;
  } else if (existingParents.length > 0) {
    // Tag already has a parent - use the first one
    selectedParentId = existingParents[0].id;
    createParentIfMissing = false;
  } else {
    // Find local matches by category name
    parentMatches = findLocalParentMatches(stashdbTag.category.name);
    if (parentMatches.length > 0) {
      selectedParentId = parentMatches[0].tag.id;
    }
  }
}
```

### 3. Update Dropdown Rendering

**Location**: `tag-manager.js` lines ~1974-1983

Update the dropdown to show existing parents with "(current parent)" label:

```javascript
<select id="tm-parent-select" class="form-control">
  <option value="">-- No parent --</option>
  ${existingParents.map(p => `
    <option value="${p.id}" ${selectedParentId === p.id ? 'selected' : ''}>
      ${escapeHtml(p.name)} (current parent)
    </option>
  `).join('')}
  ${!existingParents.length && !parentMatches.length ? `
    <option value="__create__" ${!selectedParentId ? 'selected' : ''}>Create "${escapeHtml(stashdbTag.category.name)}"</option>
  ` : ''}
  ${parentMatches.filter(m => !existingParents.some(p => p.id === m.tag.id)).map(m => `
    <option value="${m.tag.id}" ${selectedParentId === m.tag.id ? 'selected' : ''}>
      ${escapeHtml(m.tag.name)} (${m.matchType})
    </option>
  `).join('')}
  ${(existingParents.length || parentMatches.length) ? `
    <option value="__create__">Create "${escapeHtml(stashdbTag.category.name)}"</option>
  ` : ''}
</select>
```

**Dropdown Order:**
1. "-- No parent --" (always first)
2. Existing parents with "(current parent)" label
3. "Create X" if no existing parents and no matches (selected by default)
4. Fuzzy matches (excluding any already shown as existing parents)
5. "Create X" at bottom if there are existing parents or matches (not selected by default)

### 4. Fix "Create X" Default Selection

The current logic at line 1977 defaults "Create X" to selected when `!selectedParentId`. With the new logic:

- If tag has existing parent: `selectedParentId` is set to first parent, "Create X" not selected
- If fuzzy matches exist: `selectedParentId` is set to best match, "Create X" not selected
- Only if no existing parent AND no matches: `selectedParentId` is null, "Create X" selected

## Files Modified

- `plugins/tagManager/tag-manager.js`

## Testing Plan

Using test instance at `http://10.0.0.4:6971`:

1. **Existing parent shows in dropdown**
   - Find a tag that already has a parent (check in Tags > [tag] > Edit)
   - On Match tab, find a match for that tag
   - Open diff dialog, verify:
     - Existing parent appears with "(current parent)" label
     - Existing parent is pre-selected
     - "Create X" appears at bottom (not top)

2. **No existing parent - matches exist**
   - Find a tag without a parent
   - On Match tab, find a match where StashDB category matches a local tag name
   - Open diff dialog, verify:
     - Best match is pre-selected
     - "Create X" at bottom, not selected

3. **No existing parent - no matches**
   - Find a tag without a parent
   - On Match tab, find a match where StashDB category has no local matches
   - Open diff dialog, verify:
     - "Create X" is selected by default

4. **Saved mapping takes priority**
   - Match a tag and remember the mapping
   - Open another tag with same StashDB category
   - Verify saved mapping is pre-selected (even if tag has different existing parent)
