# Tag Manager Multi-Endpoint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix multi-endpoint support in tagManager - preserve stash_ids across endpoints, make filters endpoint-aware, enable smart import linking.

**Architecture:** All changes are in a single JavaScript file (`tag-manager.js`). We'll add a helper function for endpoint-aware matching, then update each feature area. Unit tests in separate JS files using the existing test pattern.

**Tech Stack:** JavaScript (browser), Stash GraphQL API

---

## Task 1: Add Helper Function for Endpoint-Aware Matching

**Files:**
- Create: `plugins/tagManager/tests/test_endpoint_matching.js`
- Modify: `plugins/tagManager/tag-manager.js:939-942`

**Step 1: Write the failing test**

Create test file:

```javascript
/**
 * Unit tests for endpoint-aware tag matching functions.
 * Run with: node plugins/tagManager/tests/test_endpoint_matching.js
 */

// Test runner
let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`✓ ${name}`);
    passed++;
  } catch (e) {
    console.log(`✗ ${name}`);
    console.log(`  Error: ${e.message}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// Copy of function under test (will be updated after implementation)
function hasStashIdForEndpoint(tag, endpoint) {
  if (!tag || !endpoint) return false;
  return tag.stash_ids?.some(sid => sid.endpoint === endpoint) ?? false;
}

// Sample test data
const sampleTags = [
  {
    id: '1',
    name: 'Blonde',
    stash_ids: [
      { endpoint: 'https://stashdb.org/graphql', stash_id: 'abc123' }
    ]
  },
  {
    id: '2',
    name: 'Brunette',
    stash_ids: [
      { endpoint: 'https://stashdb.org/graphql', stash_id: 'def456' },
      { endpoint: 'https://pmvstash.org/graphql', stash_id: 'ghi789' }
    ]
  },
  {
    id: '3',
    name: 'MILF',
    stash_ids: []
  },
  {
    id: '4',
    name: 'Teen',
    stash_ids: null
  },
  {
    id: '5',
    name: 'Outdoor'
    // no stash_ids property at all
  },
];

// Tests
console.log('\n=== hasStashIdForEndpoint tests ===\n');

console.log('--- Basic Matching ---\n');

test('returns true when tag has stash_id for endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], 'https://stashdb.org/graphql'), true);
});

test('returns false when tag lacks stash_id for endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], 'https://pmvstash.org/graphql'), false);
});

test('returns true for tag with multiple stash_ids (matching endpoint)', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[1], 'https://stashdb.org/graphql'), true);
  assertEqual(hasStashIdForEndpoint(sampleTags[1], 'https://pmvstash.org/graphql'), true);
});

console.log('\n--- Edge Cases ---\n');

test('returns false for empty stash_ids array', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[2], 'https://stashdb.org/graphql'), false);
});

test('returns false for null stash_ids', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[3], 'https://stashdb.org/graphql'), false);
});

test('returns false for missing stash_ids property', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[4], 'https://stashdb.org/graphql'), false);
});

test('returns false for null tag', () => {
  assertEqual(hasStashIdForEndpoint(null, 'https://stashdb.org/graphql'), false);
});

test('returns false for null endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], null), false);
});

test('returns false for undefined endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], undefined), false);
});

// Summary
console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
```

**Step 2: Run test to verify it passes with inline function**

Run: `node plugins/tagManager/tests/test_endpoint_matching.js`
Expected: PASS (the test file includes the function inline for now)

**Step 3: Add helper function to tag-manager.js**

Add after `findLocalTagByStashId` function (around line 943):

```javascript
  /**
   * Check if a tag has a stash_id for a specific endpoint
   * @param {object} tag - Tag object with stash_ids array
   * @param {string} endpoint - Endpoint URL to check
   * @returns {boolean} - True if tag has stash_id for this endpoint
   */
  function hasStashIdForEndpoint(tag, endpoint) {
    if (!tag || !endpoint) return false;
    return tag.stash_ids?.some(sid => sid.endpoint === endpoint) ?? false;
  }
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tests/test_endpoint_matching.js plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add hasStashIdForEndpoint helper function"
```

---

## Task 2: Fix Endpoint-Aware Filtering in getFilteredTags

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:827-839`
- Modify: `plugins/tagManager/tests/test_endpoint_matching.js`

**Step 1: Add tests for getFilteredTags to test file**

Append to `test_endpoint_matching.js`:

```javascript
// Test getFilteredTags behavior
console.log('\n=== getFilteredTags endpoint-aware tests ===\n');

// Simulated state
let localTags = [];
let selectedStashBox = null;
let currentFilter = 'unmatched';

function getFilteredTags() {
  const endpoint = selectedStashBox?.endpoint;

  const hasEndpointMatch = (tag) => hasStashIdForEndpoint(tag, endpoint);

  const unmatchedTags = localTags.filter(t => !hasEndpointMatch(t));
  const matchedTags = localTags.filter(t => hasEndpointMatch(t));

  switch (currentFilter) {
    case 'matched':
      return { filtered: matchedTags, unmatched: unmatchedTags, matched: matchedTags };
    case 'all':
      return { filtered: localTags, unmatched: unmatchedTags, matched: matchedTags };
    default: // 'unmatched'
      return { filtered: unmatchedTags, unmatched: unmatchedTags, matched: matchedTags };
  }
}

test('unmatched filter shows tags without stash_id for selected endpoint', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // Tags 3, 4, 5 have no StashDB stash_id
  assertEqual(result.filtered.length, 3);
  assertEqual(result.unmatched.length, 3);
  assertEqual(result.matched.length, 2);
});

test('unmatched filter is endpoint-specific', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://pmvstash.org/graphql' };
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // Only tag 2 (Brunette) has PMVstash stash_id
  // So unmatched should be tags 1, 3, 4, 5
  assertEqual(result.filtered.length, 4);
  assertEqual(result.matched.length, 1);
});

test('matched filter shows tags with stash_id for selected endpoint', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'matched';

  const result = getFilteredTags();
  // Tags 1 and 2 have StashDB stash_id
  assertEqual(result.filtered.length, 2);
});

test('all filter shows all tags', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'all';

  const result = getFilteredTags();
  assertEqual(result.filtered.length, 5);
});

test('handles null selectedStashBox gracefully', () => {
  localTags = sampleTags;
  selectedStashBox = null;
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // With no endpoint, all tags should be "unmatched"
  assertEqual(result.filtered.length, 5);
  assertEqual(result.matched.length, 0);
});
```

**Step 2: Run test to verify it passes**

Run: `node plugins/tagManager/tests/test_endpoint_matching.js`
Expected: PASS

**Step 3: Update getFilteredTags in tag-manager.js**

Replace lines 827-839:

```javascript
  /**
   * Get filtered tags based on current filter setting and selected endpoint
   */
  function getFilteredTags() {
    const endpoint = selectedStashBox?.endpoint;

    const hasEndpointMatch = (tag) => hasStashIdForEndpoint(tag, endpoint);

    const unmatchedTags = localTags.filter(t => !hasEndpointMatch(t));
    const matchedTags = localTags.filter(t => hasEndpointMatch(t));

    switch (currentFilter) {
      case 'matched':
        return { filtered: matchedTags, unmatched: unmatchedTags, matched: matchedTags };
      case 'all':
        return { filtered: localTags, unmatched: unmatchedTags, matched: matchedTags };
      default: // 'unmatched'
        return { filtered: unmatchedTags, unmatched: unmatchedTags, matched: matchedTags };
    }
  }
```

**Step 4: Run tests**

Run: `node plugins/tagManager/tests/test_endpoint_matching.js`
Expected: PASS

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tests/test_endpoint_matching.js
git commit -m "feat(tagManager): make getFilteredTags endpoint-aware

Fixes issue where tags matched to one endpoint (e.g., StashDB) would
not appear as unmatched when viewing a different endpoint (e.g., PMVstash).
Now 'unmatched' means 'no stash_id for THIS endpoint'."
```

---

## Task 3: Fix Stash ID Merging in Apply Match Flow

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1946-1952`

**Step 1: Review current code**

Current code at lines 1946-1952 creates a new stash_ids array with only the new entry:
```javascript
const updateInput = {
  id: tag.id,
  stash_ids: [{
    endpoint: endpoint,
    stash_id: stashdbTag.id,
  }],
};
```

**Step 2: Update to preserve existing stash_ids**

Replace lines 1945-1952 with:

```javascript
      // Build update input - preserve existing stash_ids from other endpoints
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

**Step 3: Update local state merge (line ~2102)**

Find the line that updates `localTags[idx].stash_ids = updateInput.stash_ids;` (around line 2102) - this is already correct since `updateInput.stash_ids` now contains the merged array.

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): preserve existing stash_ids when applying match

Previously, applying a match would replace ALL stash_ids with just the
new endpoint's ID. Now preserves stash_ids from other endpoints, only
replacing the entry for the current endpoint if it exists."
```

---

## Task 4: Fix Dynamic Endpoint Name in Action Text

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1828`

**Step 1: Locate the hardcoded text**

Line 1828: `<strong>StashDB ID will be added:</strong>`

**Step 2: Add helper to get endpoint display name**

Add near other helper functions (around line 940):

```javascript
  /**
   * Get a readable display name for a stash-box endpoint
   * @param {object} stashBox - Stash-box object with endpoint and optionally name
   * @returns {string} - Display name like "StashDB" or "pmvstash.org"
   */
  function getEndpointDisplayName(stashBox) {
    if (!stashBox) return 'Stash-Box';
    if (stashBox.name) return stashBox.name;
    // Extract domain from endpoint URL
    try {
      const url = new URL(stashBox.endpoint);
      return url.hostname.replace(/^www\./, '');
    } catch {
      return 'Stash-Box';
    }
  }
```

**Step 3: Update the action text**

Replace line 1828:

```javascript
            <strong>${escapeHtml(getEndpointDisplayName(selectedStashBox))} ID will be added:</strong> ${escapeHtml(stashdbTag.id)}
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): show correct endpoint name in action text

Changes 'StashDB ID will be added' to use the actual selected
endpoint's name (e.g., 'pmvstash.org ID will be added')."
```

---

## Task 5: Rename Browse Tab from "Browse StashDB" to "Browse Stash-Box"

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1175`

**Step 1: Find and replace tab label**

Line 1175 contains: `>Browse StashDB</button>`

**Step 2: Update to generic name**

Replace with: `>Browse Stash-Box</button>`

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): rename Browse tab to 'Browse Stash-Box'

The tab works with any stash-box endpoint, not just StashDB specifically."
```

---

## Task 6: Update findLocalTagByStashId to be Endpoint-Aware

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:939-942`

**Step 1: Current function**

```javascript
  function findLocalTagByStashId(stashdbId) {
    return localTags.find(t =>
      t.stash_ids?.some(sid => sid.stash_id === stashdbId)
    );
  }
```

This finds a tag by stash_id regardless of endpoint. For smart import, we also need to find tags by name that DON'T have the current endpoint's stash_id.

**Step 2: Add new helper function for name-based lookup**

Add after `findLocalTagByStashId`:

```javascript
  /**
   * Find a local tag by name or alias match (case-insensitive)
   * @param {string} name - Name to search for
   * @returns {object|undefined} - Matching local tag or undefined
   */
  function findLocalTagByName(name) {
    if (!name) return undefined;
    const lowerName = name.toLowerCase();
    return localTags.find(t =>
      t.name.toLowerCase() === lowerName ||
      t.aliases?.some(a => a.toLowerCase() === lowerName)
    );
  }
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add findLocalTagByName helper for smart import"
```

---

## Task 7: Implement Smart Import on Browse Tab

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:863-934` (handleImportSelected function)

**Step 1: Update handleImportSelected to link existing tags**

Replace the function body (lines 863-934):

```javascript
  /**
   * Handle importing selected StashDB tags
   */
  async function handleImportSelected(container) {
    if (selectedForImport.size === 0) return;
    if (isImporting) return; // Prevent double-click

    isImporting = true;

    const statusEl = container.querySelector('.tm-selection-info');
    const btnEl = container.querySelector('#tm-import-selected');

    if (statusEl) statusEl.textContent = 'Importing...';
    if (btnEl) btnEl.disabled = true;

    let created = 0;
    let linked = 0;
    let errors = 0;

    for (const stashdbId of selectedForImport) {
      const stashdbTag = stashdbTags.find(t => t.id === stashdbId);
      if (!stashdbTag) continue;

      try {
        // Check if tag exists locally by name
        const existingTag = findLocalTagByName(stashdbTag.name);

        if (existingTag) {
          // UPDATE: Link existing tag to this endpoint
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

          // Update local state
          const idx = localTags.findIndex(t => t.id === existingTag.id);
          if (idx >= 0) {
            localTags[idx].stash_ids = [...filteredStashIds, {
              endpoint: selectedStashBox.endpoint,
              stash_id: stashdbId
            }];
          }

          linked++;
        } else {
          // CREATE: New tag with stash_id
          const input = {
            name: stashdbTag.name,
            description: stashdbTag.description || '',
            aliases: stashdbTag.aliases || [],
            stash_ids: [{
              endpoint: selectedStashBox.endpoint,
              stash_id: stashdbId
            }]
          };

          const query = `
            mutation TagCreate($input: TagCreateInput!) {
              tagCreate(input: $input) {
                id
                name
              }
            }
          `;

          const data = await graphqlRequest(query, { input });
          if (data?.tagCreate) {
            // Add to local tags
            localTags.push({
              id: data.tagCreate.id,
              name: data.tagCreate.name,
              aliases: stashdbTag.aliases || [],
              stash_ids: input.stash_ids
            });
            created++;
          }
        }
      } catch (e) {
        console.error(`[tagManager] Failed to import/link "${stashdbTag.name}":`, e);
        errors++;
      }
    }

    // Clear selection and re-render
    selectedForImport.clear();

    // Build result message
    const parts = [];
    if (created > 0) parts.push(`Created ${created} tag${created !== 1 ? 's' : ''}`);
    if (linked > 0) parts.push(`linked ${linked} existing`);
    if (errors > 0) parts.push(`${errors} error${errors !== 1 ? 's' : ''}`);
    const message = parts.join(', ') || 'No changes';

    if (statusEl) statusEl.textContent = message;

    // Re-render after short delay to show message, then reset import guard
    setTimeout(() => {
      isImporting = false;
      renderPage(container);
    }, 1500);
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): smart import links existing tags instead of erroring

When importing from Browse tab, if a tag with the same name already
exists locally, add the stash_id to it instead of failing with
'tag already exists' error."
```

---

## Task 8: Update Browse Tag List UI for Smart Import

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:948-975` (renderBrowseTagList function)
- Modify: `plugins/tagManager/tag-manager.js:980-1012` (renderSearchResults function)

**Step 1: Update renderBrowseTagList**

The current code disables checkboxes for existing tags. Update to allow selection with a different status message.

Replace the `renderBrowseTagList` function:

```javascript
  /**
   * Render list of tags for browse/import view
   */
  function renderBrowseTagList(tags) {
    if (!tags || tags.length === 0) {
      return '<div class="tm-browse-empty">No tags in this category</div>';
    }

    const endpoint = selectedStashBox?.endpoint;

    const rows = tags.map(tag => {
      // Check if linked to THIS endpoint specifically
      const isLinkedToEndpoint = hasStashIdForEndpoint(
        localTags.find(t => t.stash_ids?.some(sid => sid.stash_id === tag.id)),
        endpoint
      );
      // Check if tag exists locally by name (for smart import)
      const existsByName = findLocalTagByName(tag.name);
      const canLink = existsByName && !isLinkedToEndpoint;

      const isSelected = selectedForImport.has(tag.id);

      let statusHtml = '';
      if (isLinkedToEndpoint) {
        statusHtml = `<span class="tm-local-exists" title="Already linked to ${escapeHtml(getEndpointDisplayName(selectedStashBox))}">✓ Linked</span>`;
      } else if (canLink) {
        statusHtml = `<span class="tm-can-link" title="Will link to existing tag: ${escapeHtml(existsByName.name)}">→ Link to "${escapeHtml(existsByName.name)}"</span>`;
      }

      return `
        <div class="tm-browse-tag ${isLinkedToEndpoint ? 'tm-exists-locally' : ''} ${canLink ? 'tm-can-link-row' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${isLinkedToEndpoint ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${statusHtml}
          </div>
        </div>
      `;
    }).join('');

    return rows;
  }
```

**Step 2: Update renderSearchResults similarly**

Replace the `renderSearchResults` function:

```javascript
  /**
   * Render search results as a flat list with category badges
   */
  function renderSearchResults(tags) {
    if (!tags || tags.length === 0) {
      return `<div class="tm-browse-empty">No tags found matching "${escapeHtml(browseSearchQuery)}"</div>`;
    }

    const endpoint = selectedStashBox?.endpoint;

    const rows = tags.map(tag => {
      // Check if linked to THIS endpoint specifically
      const isLinkedToEndpoint = hasStashIdForEndpoint(
        localTags.find(t => t.stash_ids?.some(sid => sid.stash_id === tag.id)),
        endpoint
      );
      // Check if tag exists locally by name (for smart import)
      const existsByName = findLocalTagByName(tag.name);
      const canLink = existsByName && !isLinkedToEndpoint;

      const isSelected = selectedForImport.has(tag.id);
      const categoryName = tag.category?.name || 'Uncategorized';

      let statusHtml = '';
      if (isLinkedToEndpoint) {
        statusHtml = `<span class="tm-local-exists" title="Already linked to ${escapeHtml(getEndpointDisplayName(selectedStashBox))}">✓ Linked</span>`;
      } else if (canLink) {
        statusHtml = `<span class="tm-can-link" title="Will link to existing tag: ${escapeHtml(existsByName.name)}">→ Link</span>`;
      }

      return `
        <div class="tm-browse-tag ${isLinkedToEndpoint ? 'tm-exists-locally' : ''} ${canLink ? 'tm-can-link-row' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${isLinkedToEndpoint ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            <span class="tm-tag-category-badge">${escapeHtml(categoryName)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${statusHtml}
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="tm-search-results-count">${tags.length} tag${tags.length !== 1 ? 's' : ''} found</div>
      ${rows}
    `;
  }
```

**Step 3: Add CSS for new link state**

Find the `<style>` section in the file (search for `.tag-manager {`) and add:

```css
.tm-can-link-row {
  background-color: rgba(255, 193, 7, 0.1);
}
.tm-can-link {
  color: #ffc107;
  font-size: 0.85em;
}
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): update Browse UI to show linkable tags

Tags that exist locally but aren't linked to the current endpoint now
show 'Link to X' instead of being disabled. Adds visual distinction
for tags that will be linked vs created."
```

---

## Task 9: Add Browse Tab Filter Dropdown

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (state variables, renderBrowseView, event handlers)

**Step 1: Add state variable for browse filter**

Find the state variables section (around line 29-36) and add:

```javascript
  let browseFilter = 'all'; // 'all', 'unlinked', or 'linked' for browse tab
```

**Step 2: Update renderBrowseView to include filter dropdown**

Find the `renderBrowseView` function (around line 1017). After the search input section, add the filter dropdown. Look for the section that renders the category list and add before it:

```javascript
    // Add filter dropdown HTML after search section
    const filterDropdownHtml = `
      <div class="tm-browse-filters">
        <select id="tm-browse-filter" class="form-control">
          <option value="all" ${browseFilter === 'all' ? 'selected' : ''}>Show All</option>
          <option value="unlinked" ${browseFilter === 'unlinked' ? 'selected' : ''}>Show Unlinked</option>
          <option value="linked" ${browseFilter === 'linked' ? 'selected' : ''}>Show Linked</option>
        </select>
      </div>
    `;
```

**Step 3: Add filter logic to tag rendering**

In `renderBrowseTagList` and `renderSearchResults`, filter the tags based on `browseFilter`:

Add at the start of both functions:

```javascript
    // Apply browse filter
    const endpoint = selectedStashBox?.endpoint;
    let filteredTags = tags;

    if (browseFilter === 'linked') {
      filteredTags = tags.filter(tag => {
        const localMatch = localTags.find(t => t.stash_ids?.some(sid => sid.stash_id === tag.id));
        return hasStashIdForEndpoint(localMatch, endpoint);
      });
    } else if (browseFilter === 'unlinked') {
      filteredTags = tags.filter(tag => {
        const localMatch = localTags.find(t => t.stash_ids?.some(sid => sid.stash_id === tag.id));
        return !hasStashIdForEndpoint(localMatch, endpoint);
      });
    }

    tags = filteredTags;
```

**Step 4: Add event handler for browse filter**

In `attachEventHandlers` function, add:

```javascript
    // Browse filter dropdown
    container.querySelector('#tm-browse-filter')?.addEventListener('change', (e) => {
      browseFilter = e.target.value;
      renderPage(container);
    });
```

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add filter dropdown to Browse tab

Adds Show All/Unlinked/Linked filter to Browse tab, matching the
filter functionality on the Match Local Tags tab."
```

---

## Task 10: Auto-Refresh Cache on Endpoint Switch for Browse Tab

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1380-1394` (stashbox change handler)

**Step 1: Update endpoint change handler**

The current handler at lines 1380-1394 already clears `stashdbTags` and calls `loadCacheStatus()`, but it doesn't auto-load the cache if on the browse tab.

Update the handler to also load the cache when on browse tab:

```javascript
    // Stash-box dropdown
    container.querySelector('#tm-stashbox')?.addEventListener('change', async (e) => {
      const endpoint = e.target.value;
      const newStashBox = stashBoxes.find(sb => sb.endpoint === endpoint);
      if (newStashBox && newStashBox.endpoint !== selectedStashBox?.endpoint) {
        console.debug("[tagManager] Switching to stash-box:", newStashBox.name);
        selectedStashBox = newStashBox;
        // Clear cached data for previous endpoint
        stashdbTags = null;
        matchResults = {};
        cacheStatus = null;
        // Load cache status for new endpoint
        await loadCacheStatus();

        // If on browse tab and cache exists, load it automatically
        if (activeTab === 'browse' && cacheStatus?.exists && !cacheStatus?.expired) {
          await loadStashdbTags(container);
        }

        renderPage(container);
      }
    });
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): auto-load cache when switching endpoints on Browse tab

Previously, switching endpoints on Browse tab would show empty results
until manually refreshing. Now auto-loads cached tags if available."
```

---

## Task 11: Manual Testing on Test Instance

**Files:** None (manual testing)

**Step 1: Deploy to test instance**

```bash
rsync -av --delete plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/
```

**Step 2: Reload plugins in Stash UI**

Navigate to `http://10.0.0.4:6971`, go to Settings > Plugins > Reload

**Step 3: Test stash ID merging**

1. Find a tag that's already matched to one endpoint
2. Switch to a different endpoint
3. Match the same tag
4. Verify both stash_ids are preserved (check in tag edit page)

**Step 4: Test endpoint-aware filtering**

1. With a tag matched to StashDB only
2. Switch to PMVstash endpoint
3. Verify the tag appears in "Show Unmatched"
4. Match it to PMVstash
5. Verify it moves to "Show Matched"

**Step 5: Test smart import**

1. Create a local tag manually (e.g., "Test Tag")
2. Go to Browse Stash-Box tab
3. Find the same tag name in the stash-box
4. Import it
5. Verify the stash_id is added (no error)

**Step 6: Test UI polish items**

1. Verify endpoint name shows correctly in action text
2. Verify Browse tab has filter dropdown
3. Verify tab is named "Browse Stash-Box"
4. Verify cache loads when switching endpoints on Browse tab

**Step 7: Commit test verification note**

```bash
git commit --allow-empty -m "test(tagManager): verified multi-endpoint fixes on test instance

All manual tests passing:
- Stash IDs preserved across endpoints
- Filter is endpoint-aware
- Smart import links existing tags
- UI polish items working"
```

---

## Summary

| Task | Description | Status |
|------|-------------|--------|
| 1 | Add hasStashIdForEndpoint helper | Pending |
| 2 | Fix endpoint-aware filtering | Pending |
| 3 | Fix stash ID merging in apply match | Pending |
| 4 | Fix dynamic endpoint name | Pending |
| 5 | Rename Browse tab | Pending |
| 6 | Add findLocalTagByName helper | Pending |
| 7 | Implement smart import | Pending |
| 8 | Update Browse UI for smart import | Pending |
| 9 | Add Browse tab filter dropdown | Pending |
| 10 | Auto-refresh cache on endpoint switch | Pending |
| 11 | Manual testing | Pending |
