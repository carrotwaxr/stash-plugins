# P2: StashDB Category Integration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When accepting a tag match, set parent relationship based on StashDB category with smart local matching.

**Architecture:** Add category/parent section to match dialog. Search local tags for matches to category name (exact, fuzzy, alias). Remember mappings in plugin settings. On Apply, create parent tag if needed and set `parent_ids` on the updated tag.

**Tech Stack:** JavaScript (Stash plugin API), CSS

---

## Background

StashDB tags have a `category` field with `id`, `name`, and `group`. We want to:
1. Show the category in the match dialog (already done - display only)
2. Let user select/create a local parent tag that corresponds to the category
3. Set the parent relationship when saving
4. Remember mappings for future use

## Task 1: Add categoryMappings state variable

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in State section, around line 25)

**Step 1: Add state variable**

Find this section (around line 19-29):

```javascript
  // State
  let settings = { ...DEFAULTS };
  let stashBoxes = []; // Configured stash-box endpoints from Stash
  let selectedStashBox = null; // Currently selected stash-box
  let stashdbTags = null; // Cached tags for selected endpoint
  let cacheStatus = null; // Cache status for selected endpoint
  let localTags = []; // Local Stash tags
  let currentPage = 1;
  let isLoading = false;
  let isCacheLoading = false;
  let matchResults = {}; // Cache of tag_id -> matches
  let currentFilter = 'unmatched'; // 'unmatched', 'matched', or 'all'
```

Add after `currentFilter`:

```javascript
  let categoryMappings = {}; // Cache of category_name -> local_tag_id
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add categoryMappings state for P2"
```

---

## Task 2: Add loadCategoryMappings and saveCategoryMappings functions

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after `loadSettings` function, around line 130)

**Step 1: Add the persistence functions**

Insert after `loadSettings()`:

```javascript
  /**
   * Load category mappings from plugin settings
   */
  async function loadCategoryMappings() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID] || {};

      // Parse JSON string from settings
      if (pluginConfig.categoryMappings) {
        try {
          categoryMappings = JSON.parse(pluginConfig.categoryMappings);
          console.debug("[tagManager] Loaded category mappings:", Object.keys(categoryMappings).length);
        } catch (e) {
          console.warn("[tagManager] Failed to parse category mappings:", e);
          categoryMappings = {};
        }
      }
    } catch (e) {
      console.error("[tagManager] Failed to load category mappings:", e);
    }
  }

  /**
   * Save category mappings to plugin settings
   */
  async function saveCategoryMappings() {
    try {
      const query = `
        mutation ConfigurePlugin($plugin_id: ID!, $input: Map!) {
          configurePlugin(plugin_id: $plugin_id, input: $input)
        }
      `;

      await graphqlRequest(query, {
        plugin_id: PLUGIN_ID,
        input: {
          categoryMappings: JSON.stringify(categoryMappings)
        }
      });
      console.debug("[tagManager] Saved category mappings");
    } catch (e) {
      console.error("[tagManager] Failed to save category mappings:", e);
    }
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add category mapping persistence functions"
```

---

## Task 3: Add findLocalParentMatches function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after `validateBeforeSave` function, around line 478)

**Step 1: Add the smart matching function**

```javascript
  /**
   * Find local tags that could be parent tags for a given category name.
   * Searches by exact match, alias match, and fuzzy match.
   *
   * @param {string} categoryName - The StashDB category name to match
   * @returns {object[]} - Array of { tag, matchType, score } sorted by relevance
   */
  function findLocalParentMatches(categoryName) {
    if (!categoryName) return [];

    const lowerCategoryName = categoryName.toLowerCase();
    const matches = [];

    for (const tag of localTags) {
      // Skip tags that are children (have parents) - they're less likely to be category tags
      // But don't skip completely, just deprioritize
      const isChild = tag.parent_count > 0;

      // Exact name match
      if (tag.name.toLowerCase() === lowerCategoryName) {
        matches.push({ tag, matchType: 'exact', score: isChild ? 95 : 100 });
        continue;
      }

      // Name contains category (e.g., "CATEGORY: Action" contains "Action")
      if (tag.name.toLowerCase().includes(lowerCategoryName)) {
        matches.push({ tag, matchType: 'contains', score: isChild ? 85 : 90 });
        continue;
      }

      // Alias match
      if (tag.aliases?.some(a => a.toLowerCase() === lowerCategoryName)) {
        matches.push({ tag, matchType: 'alias', score: isChild ? 80 : 85 });
        continue;
      }

      // Fuzzy match on name (simple: starts with same letters)
      if (tag.name.toLowerCase().startsWith(lowerCategoryName.slice(0, 3)) &&
          tag.name.length < categoryName.length + 5) {
        matches.push({ tag, matchType: 'fuzzy', score: isChild ? 60 : 70 });
      }
    }

    // Sort by score descending
    matches.sort((a, b) => b.score - a.score);

    // Limit to top 5
    return matches.slice(0, 5);
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add findLocalParentMatches for category matching"
```

---

## Task 4: Add createTag function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after `updateTag` function, around line 1160)

**Step 1: Add the create tag function**

Find `updateTag` function and add after it:

```javascript
  /**
   * Create a new tag via GraphQL
   */
  async function createTag(input) {
    const query = `
      mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) {
          id
          name
        }
      }
    `;

    const data = await graphqlRequest(query, { input });
    return data?.tagCreate;
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add createTag function for parent tag creation"
```

---

## Task 5: Update showDiffDialog to add category/parent section

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in `showDiffDialog`, modal HTML around line 895)

**Step 1: Add category state and UI**

In `showDiffDialog`, after the line `let editableAliases = new Set(...)` (around line 813), add:

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

**Step 2: Add the category row to the table HTML**

Find the closing `</tbody>` tag in the modal HTML (around line 895), and add BEFORE it:

```javascript
              ${hasCategory ? `
              <tr>
                <td>Parent Tag</td>
                <td colspan="3">
                  <div class="tm-category-section">
                    <div class="tm-category-info">
                      <span class="tm-category-label">StashDB Category:</span>
                      <span class="tm-category-name">${escapeHtml(stashdbTag.category.name)}</span>
                    </div>
                    <div class="tm-parent-select">
                      <select id="tm-parent-select" class="form-control">
                        <option value="">-- No parent --</option>
                        <option value="__create__" ${!selectedParentId ? 'selected' : ''}>Create "${escapeHtml(stashdbTag.category.name)}"</option>
                        ${parentMatches.map(m => `
                          <option value="${m.tag.id}" ${selectedParentId === m.tag.id ? 'selected' : ''}>
                            ${escapeHtml(m.tag.name)} (${m.matchType})
                          </option>
                        `).join('')}
                      </select>
                      <button type="button" class="btn btn-secondary btn-sm" id="tm-parent-search-btn">Search...</button>
                    </div>
                    <div class="tm-parent-remember">
                      <label>
                        <input type="checkbox" id="tm-remember-mapping" checked>
                        Remember this mapping
                      </label>
                    </div>
                  </div>
                </td>
              </tr>
              ` : ''}
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add category/parent section to match dialog"
```

---

## Task 6: Add parent search modal

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after modal creation, before event handlers)

**Step 1: Add search modal function and handler**

Add this function inside `showDiffDialog`, after `renderAliasPills`:

```javascript
    // Parent tag search modal
    function showParentSearchModal() {
      const searchModal = document.createElement('div');
      searchModal.className = 'tm-modal-backdrop tm-search-modal';
      searchModal.innerHTML = `
        <div class="tm-modal tm-modal-small">
          <div class="tm-modal-header">
            <h3>Search Parent Tag</h3>
            <button class="tm-close-btn">&times;</button>
          </div>
          <div class="tm-modal-body">
            <input type="text" id="tm-parent-search-input" class="form-control"
                   placeholder="Search tags..." value="${escapeHtml(stashdbTag.category?.name || '')}">
            <div class="tm-search-results" id="tm-parent-search-results">
              <div class="tm-loading">Type to search...</div>
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(searchModal);

      const input = searchModal.querySelector('#tm-parent-search-input');
      const resultsEl = searchModal.querySelector('#tm-parent-search-results');

      function doSearch() {
        const term = input.value.trim().toLowerCase();
        if (!term) {
          resultsEl.innerHTML = '<div class="tm-loading">Type to search...</div>';
          return;
        }

        const matches = localTags.filter(t =>
          t.name.toLowerCase().includes(term) ||
          t.aliases?.some(a => a.toLowerCase().includes(term))
        ).slice(0, 10);

        if (matches.length === 0) {
          resultsEl.innerHTML = '<div class="tm-no-matches">No matching tags found</div>';
          return;
        }

        resultsEl.innerHTML = matches.map(t => `
          <div class="tm-search-result" data-tag-id="${t.id}">
            <span class="tm-result-name">${escapeHtml(t.name)}</span>
            ${t.aliases?.length ? `<span class="tm-result-aliases">${escapeHtml(t.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
        `).join('');

        resultsEl.querySelectorAll('.tm-search-result').forEach(el => {
          el.addEventListener('click', () => {
            const tagId = el.dataset.tagId;
            const tag = localTags.find(t => t.id === tagId);
            if (tag) {
              // Update the parent select
              const select = modal.querySelector('#tm-parent-select');
              // Add option if not present
              if (!select.querySelector(`option[value="${tagId}"]`)) {
                const option = document.createElement('option');
                option.value = tagId;
                option.textContent = tag.name;
                select.appendChild(option);
              }
              select.value = tagId;
              selectedParentId = tagId;
            }
            searchModal.remove();
          });
        });
      }

      input.addEventListener('input', doSearch);
      input.focus();
      doSearch();

      searchModal.querySelector('.tm-close-btn').addEventListener('click', () => searchModal.remove());
      searchModal.addEventListener('click', (e) => {
        if (e.target === searchModal) searchModal.remove();
      });
    }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add parent tag search modal"
```

---

## Task 7: Add event handlers for parent selection

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after modal event handlers, around line 970)

**Step 1: Add parent select handlers**

After `renderAliasPills()` is called (around line 913), add:

```javascript
    // Parent selection handlers (if category exists)
    if (hasCategory) {
      const parentSelect = modal.querySelector('#tm-parent-select');
      if (parentSelect) {
        parentSelect.addEventListener('change', (e) => {
          selectedParentId = e.target.value === '' ? null : e.target.value;
        });
      }

      const searchBtn = modal.querySelector('#tm-parent-search-btn');
      if (searchBtn) {
        searchBtn.addEventListener('click', showParentSearchModal);
      }
    }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add parent selection event handlers"
```

---

## Task 8: Update Apply handler to set parent

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in Apply click handler, before `try { await updateTag`)

**Step 1: Add parent handling logic**

In the Apply handler, after `updateInput.aliases = sanitizedAliases;` and before the pre-validation block, add:

```javascript
      // Handle parent tag from category
      let parentTagId = null;
      if (hasCategory && selectedParentId) {
        if (selectedParentId === '__create__') {
          // Create the parent tag
          try {
            const newParent = await createTag({ name: stashdbTag.category.name });
            parentTagId = newParent.id;
            // Add to localTags for future reference
            localTags.push({ id: newParent.id, name: newParent.name, aliases: [] });
            console.debug(`[tagManager] Created parent tag: ${newParent.name}`);
          } catch (e) {
            console.error('[tagManager] Failed to create parent tag:', e);
            errorEl.innerHTML = `<div class="tm-error-message">Failed to create parent tag: ${escapeHtml(e.message)}</div>`;
            errorEl.style.display = 'block';
            return;
          }
        } else {
          parentTagId = selectedParentId;
        }

        // Set parent_ids on the update input
        if (parentTagId) {
          updateInput.parent_ids = [parentTagId];
        }

        // Save mapping if checkbox is checked
        const rememberCheckbox = modal.querySelector('#tm-remember-mapping');
        if (rememberCheckbox?.checked && parentTagId) {
          categoryMappings[stashdbTag.category.name] = parentTagId;
          saveCategoryMappings(); // Fire and forget
        }
      }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): handle parent tag creation and assignment on Apply"
```

---

## Task 9: Add CSS for category section

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (after error action styles, around line 485)

**Step 1: Add category section styles**

```css
/* Category/Parent Tag Section */
.tm-category-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tm-category-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tm-category-label {
  font-weight: 500;
  color: var(--bs-secondary-color, #888);
}

.tm-category-name {
  padding: 2px 8px;
  background: var(--bs-primary-bg-subtle, #1a3a5c);
  color: var(--bs-primary, #0d6efd);
  border-radius: 4px;
  font-weight: 500;
}

.tm-parent-select {
  display: flex;
  gap: 8px;
  align-items: center;
}

.tm-parent-select select {
  flex: 1;
  max-width: 300px;
}

.tm-parent-remember {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
}

.tm-parent-remember label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

/* Parent Search Modal */
.tm-modal-small .tm-modal {
  max-width: 400px;
}

.tm-search-results {
  max-height: 300px;
  overflow-y: auto;
  margin-top: 10px;
}

.tm-search-result {
  padding: 10px;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tm-search-result:hover {
  background: var(--bs-secondary-bg, #2d2d44);
}

.tm-result-name {
  font-weight: 500;
}

.tm-result-aliases {
  font-size: 0.8em;
  color: var(--bs-secondary-color, #888);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "style(tagManager): add CSS for category/parent section"
```

---

## Task 10: Update page initialization to load mappings

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in TagManagerPage init, around line 1300)

**Step 1: Add loadCategoryMappings call**

In the `TagManagerPage` component's `init()` function, find where `loadSettings()` is called and add after it:

```javascript
        await loadCategoryMappings();
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): load category mappings on page init"
```

---

## Task 11: Add categoryMappings setting to YAML

**Files:**
- Modify: `plugins/tagManager/tagManager.yml` (in settings section)

**Step 1: Add setting definition**

Add after `syncDryRun` setting:

```yaml
  categoryMappings:
    displayName: Category Mappings (Internal)
    description: JSON mapping of StashDB categories to local parent tag IDs. Managed automatically.
    type: STRING
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tagManager.yml
git commit -m "feat(tagManager): add categoryMappings setting to YAML"
```

---

## Task 12: Write unit tests

**Files:**
- Create: `plugins/tagManager/tests/test_category_matching.js`

**Step 1: Write tests**

```javascript
/**
 * Unit tests for category matching functions.
 * Run with: node plugins/tagManager/tests/test_category_matching.js
 */

// Mock localTags for testing
const localTags = [
  { id: '1', name: 'Action', aliases: ['Acts'], parent_count: 0 },
  { id: '2', name: 'CATEGORY: Action', aliases: [], parent_count: 0 },
  { id: '3', name: 'Activities', aliases: ['Action'], parent_count: 0 },
  { id: '4', name: 'Comedy', aliases: [], parent_count: 0 },
  { id: '5', name: 'Some Child Tag', aliases: [], parent_count: 1 },
];

// Copy of findLocalParentMatches for testing
function findLocalParentMatches(categoryName) {
  if (!categoryName) return [];

  const lowerCategoryName = categoryName.toLowerCase();
  const matches = [];

  for (const tag of localTags) {
    const isChild = tag.parent_count > 0;

    if (tag.name.toLowerCase() === lowerCategoryName) {
      matches.push({ tag, matchType: 'exact', score: isChild ? 95 : 100 });
      continue;
    }

    if (tag.name.toLowerCase().includes(lowerCategoryName)) {
      matches.push({ tag, matchType: 'contains', score: isChild ? 85 : 90 });
      continue;
    }

    if (tag.aliases?.some(a => a.toLowerCase() === lowerCategoryName)) {
      matches.push({ tag, matchType: 'alias', score: isChild ? 80 : 85 });
      continue;
    }

    if (tag.name.toLowerCase().startsWith(lowerCategoryName.slice(0, 3)) &&
        tag.name.length < categoryName.length + 5) {
      matches.push({ tag, matchType: 'fuzzy', score: isChild ? 60 : 70 });
    }
  }

  matches.sort((a, b) => b.score - a.score);
  return matches.slice(0, 5);
}

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

// Tests
console.log('\n=== findLocalParentMatches tests ===\n');

test('finds exact name match with highest score', () => {
  const matches = findLocalParentMatches('Action');
  assertEqual(matches[0].tag.id, '1');
  assertEqual(matches[0].matchType, 'exact');
  assertEqual(matches[0].score, 100);
});

test('finds tag containing category name', () => {
  const matches = findLocalParentMatches('Action');
  const containsMatch = matches.find(m => m.tag.id === '2');
  assertEqual(containsMatch.matchType, 'contains');
});

test('finds alias match', () => {
  const matches = findLocalParentMatches('Acts');
  assertEqual(matches[0].tag.id, '1');
  assertEqual(matches[0].matchType, 'alias');
});

test('returns empty for no match', () => {
  const matches = findLocalParentMatches('NonexistentCategory');
  assertEqual(matches.length, 0);
});

test('returns empty for null/empty input', () => {
  assertEqual(findLocalParentMatches(null).length, 0);
  assertEqual(findLocalParentMatches('').length, 0);
});

test('case insensitive matching', () => {
  const matches = findLocalParentMatches('ACTION');
  assertEqual(matches[0].tag.id, '1');
});

test('limits results to 5', () => {
  // Add more matching tags temporarily
  for (let i = 10; i < 20; i++) {
    localTags.push({ id: String(i), name: `Action${i}`, aliases: [], parent_count: 0 });
  }
  const matches = findLocalParentMatches('Action');
  assertEqual(matches.length <= 5, true);
});

// Summary
console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
```

**Step 2: Run tests**

```bash
node plugins/tagManager/tests/test_category_matching.js
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tests/test_category_matching.js
git commit -m "test(tagManager): add unit tests for category matching"
```

---

## Task 13: Manual testing and push

**Step 1: Verify git status**

```bash
git log --oneline feature/tag-manager-backlog..HEAD
git diff feature/tag-manager-backlog..HEAD --stat
```

**Step 2: Push branch**

```bash
git push -u origin feature/p2-category-integration
```

**Step 3: Create PR targeting feature/tag-manager-backlog**

```bash
gh pr create --base feature/tag-manager-backlog --title "feat(tagManager): StashDB category integration (P2)" --body "..."
```

---

## Summary of Changes

| File | Changes |
|------|---------|
| `tag-manager.js` | Added `categoryMappings` state, `loadCategoryMappings()`, `saveCategoryMappings()`, `findLocalParentMatches()`, `createTag()`, category/parent UI in dialog, parent search modal, Apply handler updates |
| `tag-manager.css` | Category section styles, parent search modal styles |
| `tagManager.yml` | Added `categoryMappings` setting |
| `tests/test_category_matching.js` | Unit tests for category matching |

**Commits:**
1. `feat(tagManager): add categoryMappings state for P2`
2. `feat(tagManager): add category mapping persistence functions`
3. `feat(tagManager): add findLocalParentMatches for category matching`
4. `feat(tagManager): add createTag function for parent tag creation`
5. `feat(tagManager): add category/parent section to match dialog`
6. `feat(tagManager): add parent tag search modal`
7. `feat(tagManager): add parent selection event handlers`
8. `feat(tagManager): handle parent tag creation and assignment on Apply`
9. `style(tagManager): add CSS for category/parent section`
10. `feat(tagManager): load category mappings on page init`
11. `feat(tagManager): add categoryMappings setting to YAML`
12. `test(tagManager): add unit tests for category matching`
