# Parent Dropdown Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix parent dropdown to show existing parents and prioritize them over "Create X"

**Architecture:** Add `parents` to local tags query, update selection logic to prioritize existing parents, update dropdown rendering to show "(current parent)" label

**Tech Stack:** JavaScript, Stash GraphQL API

---

## Progress Tracking

| Task | Description | Status |
|------|-------------|--------|
| 1 | Add parents to fetchLocalTags query | Pending |
| 2 | Update parent selection logic | Pending |
| 3 | Update dropdown rendering | Pending |
| 4 | Manual testing | Pending |

---

### Task 1: Add Parents to fetchLocalTags Query

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:206-222`

**Step 1: Add parents field to GraphQL query**

In `fetchLocalTags()`, add `parents { id name }` to the query:

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

  const data = await graphqlRequest(query);
  return data?.findTags?.tags || [];
}
```

**Step 2: Verify change works**

Deploy to test instance and verify tags now have `parents` array in console:
```bash
rsync -av --delete plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/
```

Then in browser console: `localTags.filter(t => t.parents?.length > 0)` should show tags with parents.

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add parents to local tags query"
```

---

### Task 2: Update Parent Selection Logic

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1711-1729`

**Step 1: Update selection logic to prioritize existing parents**

Replace the current logic:

```javascript
// Category/parent state
const hasCategory = !!stashdbTag.category?.name;
let selectedParentId = null;
let createParentIfMissing = true;
let parentMatches = [];

if (hasCategory) {
  // Check for saved mapping first
  const savedMapping = categoryMappings[stashdbTag.category.name];
  if (savedMapping) {
    selectedParentId = savedMapping;
  } else {
    // Find local matches
    parentMatches = findLocalParentMatches(stashdbTag.category.name);
    if (parentMatches.length > 0) {
      selectedParentId = parentMatches[0].tag.id;
    }
  }
}
```

With:

```javascript
// Category/parent state
const hasCategory = !!stashdbTag.category?.name;
let selectedParentId = null;
let createParentIfMissing = true;
let parentMatches = [];
const existingParents = tag.parents || [];

if (hasCategory) {
  // Check for saved mapping first
  const savedMapping = categoryMappings[stashdbTag.category.name];
  if (savedMapping) {
    selectedParentId = savedMapping;
  } else if (existingParents.length > 0) {
    // Tag already has a parent - use it
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

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): prioritize existing parents in selection logic"
```

---

### Task 3: Update Dropdown Rendering

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1974-1983`

**Step 1: Update dropdown HTML to show existing parents**

Replace the current dropdown:

```javascript
<select id="tm-parent-select" class="form-control">
  <option value="">-- No parent --</option>
  <option value="__create__" ${!selectedParentId ? 'selected' : ''}>Create "${escapeHtml(stashdbTag.category.name)}"</option>
  ${parentMatches.map(m => `
    <option value="${m.tag.id}" ${selectedParentId === m.tag.id ? 'selected' : ''}>
      ${escapeHtml(m.tag.name)} (${m.matchType})
    </option>
  `).join('')}
</select>
```

With:

```javascript
<select id="tm-parent-select" class="form-control">
  <option value="">-- No parent --</option>
  ${existingParents.map(p => `
    <option value="${p.id}" ${selectedParentId === p.id ? 'selected' : ''}>
      ${escapeHtml(p.name)} (current parent)
    </option>
  `).join('')}
  ${(!existingParents.length && !parentMatches.length) ? `
    <option value="__create__" selected>Create "${escapeHtml(stashdbTag.category.name)}"</option>
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

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): show existing parents in dropdown with label"
```

---

### Task 4: Manual Testing

**Step 1: Deploy to test instance**

```bash
rsync -av --delete plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/
```

**Step 2: Test existing parent shows in dropdown**

1. In Stash UI, go to Tags
2. Find a tag that has a parent (or create one: edit a tag, set parent)
3. Go to Tag Manager > Match tab
4. Find a match for that tag
5. Click "View" to open diff dialog
6. Verify:
   - Existing parent appears with "(current parent)" label
   - Existing parent is pre-selected
   - "Create X" appears at bottom

**Step 3: Test no existing parent with matches**

1. Find a tag without a parent
2. Match tab, find a match where StashDB category matches a local tag
3. Verify:
   - Best match is pre-selected
   - "Create X" at bottom, not selected

**Step 4: Test no existing parent, no matches**

1. Find a tag without a parent
2. Match tab, find a match where StashDB category has no local matches
3. Verify:
   - "Create X" is selected by default

**Step 5: Commit test verification note**

No code change - manual verification complete.
