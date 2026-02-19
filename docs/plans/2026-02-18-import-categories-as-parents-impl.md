# Import Categories as Parent Tags — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When bulk-importing StashDB tags from the Browse tab, automatically resolve and assign parent tags based on StashDB categories, with a preview dialog for user confirmation.

**Architecture:** Intercept `handleImportSelected()` to detect categories among selected tags, show a preview/edit modal, then pass resolved parent mappings into the existing import loop. Reuses existing `findLocalParentMatches()`, `categoryMappings`, `createTag()`, and `showParentSearchModal()` patterns.

**Tech Stack:** Vanilla JS (Stash plugin API), CSS (existing `.tm-modal` pattern), Node.js test runner (existing pattern)

---

### Task 1: Extract `resolveCategoryParents()` — Pure Logic Function

This function takes selected StashDB tag IDs and returns a map of `{ categoryName: { parentTagId, parentTagName, resolution, description } }`.

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (insert after `findLocalParentMatches` at ~line 827)
- Test: `plugins/tagManager/tests/test_import_parents.js` (create)

**Step 1: Write the failing test**

Create `plugins/tagManager/tests/test_import_parents.js`:

```javascript
/**
 * Unit tests for category parent resolution during import.
 * Run with: node plugins/tagManager/tests/test_import_parents.js
 */

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

// --- Mock data ---
const localTags = [
  { id: '10', name: 'Action', aliases: [], parent_count: 0, description: '' },
  { id: '20', name: 'Clothing', aliases: ['Apparel'], parent_count: 0, description: 'Existing desc' },
  { id: '30', name: 'Some Child', aliases: [], parent_count: 1, description: '' },
];

const stashdbTags = [
  { id: 's1', name: 'Anal', category: { id: 'c1', name: 'Action', group: 'ACTION', description: 'Action category' } },
  { id: 's2', name: 'Blindfold', category: { id: 'c2', name: 'Accessories', group: 'ACTION', description: 'Wearable accessories' } },
  { id: 's3', name: 'Skirt', category: { id: 'c3', name: 'Clothing', group: 'SCENE', description: 'Clothing items' } },
  { id: 's4', name: 'No Category Tag', category: null },
  { id: 's5', name: 'Oral', category: { id: 'c1', name: 'Action', group: 'ACTION', description: 'Action category' } },
];

let categoryMappings = {};

// Copy of findLocalParentMatches
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
  }
  matches.sort((a, b) => b.score - a.score);
  return matches.slice(0, 5);
}

// --- Function under test ---
function resolveCategoryParents(selectedIds) {
  const result = {};

  for (const stashdbId of selectedIds) {
    const tag = stashdbTags.find(t => t.id === stashdbId);
    if (!tag?.category) continue;

    const catName = tag.category.name;
    if (result[catName]) continue; // Already resolved

    // 1. Check saved mapping
    const savedId = categoryMappings[catName];
    if (savedId) {
      const savedTag = localTags.find(t => t.id === savedId);
      if (savedTag) {
        result[catName] = {
          parentTagId: savedTag.id,
          parentTagName: savedTag.name,
          resolution: 'saved',
          description: tag.category.description || '',
        };
        continue;
      }
    }

    // 2. Exact name match from local tags
    const matches = findLocalParentMatches(catName);
    const exactMatch = matches.find(m => m.matchType === 'exact');
    if (exactMatch) {
      result[catName] = {
        parentTagId: exactMatch.tag.id,
        parentTagName: exactMatch.tag.name,
        resolution: 'exact',
        description: tag.category.description || '',
      };
      continue;
    }

    // 3. Will create new
    result[catName] = {
      parentTagId: null,
      parentTagName: catName,
      resolution: 'create',
      description: tag.category.description || '',
    };
  }

  return result;
}

// --- Tests ---
console.log('\n=== resolveCategoryParents tests ===\n');

test('returns empty for tags with no categories', () => {
  const result = resolveCategoryParents(['s4']);
  assertEqual(result, {});
});

test('resolves existing local tag by exact name', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('flags create for category with no local match', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].parentTagId, null);
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('uses saved mapping when available', () => {
  categoryMappings = { 'Action': '20' }; // Override to Clothing tag
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '20');
  assertEqual(result['Action'].resolution, 'saved');
});

test('falls back to match if saved mapping points to deleted tag', () => {
  categoryMappings = { 'Action': '999' }; // Non-existent
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('deduplicates categories across multiple tags', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1', 's5']); // Both are Action
  assertEqual(Object.keys(result).length, 1);
  assertEqual(result['Action'].parentTagId, '10');
});

test('resolves multiple categories independently', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1', 's2', 's3']);
  assertEqual(Object.keys(result).length, 3);
  assertEqual(result['Action'].resolution, 'exact');
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Clothing'].resolution, 'exact');
  assertEqual(result['Clothing'].parentTagId, '20');
});

test('carries category description for create entries', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('skips tags with null category', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s4', 's1']);
  assertEqual(Object.keys(result).length, 1); // Only Action
});

// --- Summary ---
console.log(`\n=== Summary ===\n`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
```

**Step 2: Run test to verify it passes (tests include the function)**

Run: `node plugins/tagManager/tests/test_import_parents.js`
Expected: All 9 tests PASS (function is defined inline in the test file)

**Step 3: Add `resolveCategoryParents` to `tag-manager.js`**

Insert after `findLocalParentMatches` (after line 827):

```javascript
  /**
   * Resolve parent tags for categories found among selected StashDB tags.
   * Returns: { categoryName: { parentTagId, parentTagName, resolution, description } }
   * resolution is one of: 'saved', 'exact', 'create'
   */
  function resolveCategoryParents(selectedIds) {
    const result = {};

    for (const stashdbId of selectedIds) {
      const tag = stashdbTags.find(t => t.id === stashdbId);
      if (!tag?.category) continue;

      const catName = tag.category.name;
      if (result[catName]) continue;

      // 1. Check saved mapping
      const savedId = categoryMappings[catName];
      if (savedId) {
        const savedTag = localTags.find(t => t.id === savedId);
        if (savedTag) {
          result[catName] = {
            parentTagId: savedTag.id,
            parentTagName: savedTag.name,
            resolution: 'saved',
            description: tag.category.description || '',
          };
          continue;
        }
      }

      // 2. Exact name match
      const matches = findLocalParentMatches(catName);
      const exactMatch = matches.find(m => m.matchType === 'exact');
      if (exactMatch) {
        result[catName] = {
          parentTagId: exactMatch.tag.id,
          parentTagName: exactMatch.tag.name,
          resolution: 'exact',
          description: tag.category.description || '',
        };
        continue;
      }

      // 3. Will create
      result[catName] = {
        parentTagId: null,
        parentTagName: catName,
        resolution: 'create',
        description: tag.category.description || '',
      };
    }

    return result;
  }
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tests/test_import_parents.js
git commit -m "feat(tagManager): add resolveCategoryParents for import parent resolution"
```

---

### Task 2: Build Category Parent Preview Modal

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (insert after `resolveCategoryParents`)
- Modify: `plugins/tagManager/tag-manager.css` (append styles)

**Step 1: Add `showCategoryPreviewModal()` function**

Insert after `resolveCategoryParents` in `tag-manager.js`. This function shows the preview dialog and returns a Promise that resolves with `{ parentMap, remember }` or `null` (skip parents).

```javascript
  /**
   * Show category parent preview modal before import.
   * Returns Promise<{ parentMap: {catName: parentTagId}, remember: boolean } | null>
   * null = user chose "Import without Parents"
   */
  function showCategoryPreviewModal(categoryResolutions) {
    return new Promise((resolve) => {
      const categories = Object.entries(categoryResolutions);

      const modal = document.createElement('div');
      modal.className = 'tm-modal-backdrop';
      modal.innerHTML = `
        <div class="tm-modal tm-modal-wide">
          <div class="tm-modal-header">
            <h3>Assign Parent Tags by Category</h3>
            <button class="tm-close-btn">&times;</button>
          </div>
          <div class="tm-modal-body">
            <p class="tm-preview-intro">
              The selected tags belong to ${categories.length} ${categories.length === 1 ? 'category' : 'categories'}.
              Each category will be mapped to a parent tag.
            </p>
            <table class="tm-category-preview-table">
              <thead>
                <tr>
                  <th>StashDB Category</th>
                  <th>Parent Tag</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${categories.map(([catName, info]) => `
                  <tr data-category="${escapeHtml(catName)}">
                    <td class="tm-cat-name">${escapeHtml(catName)}</td>
                    <td class="tm-cat-parent">
                      <span class="tm-cat-parent-name">${escapeHtml(info.parentTagName)}</span>
                    </td>
                    <td class="tm-cat-status">
                      <span class="tm-cat-resolution tm-cat-${info.resolution}">
                        ${info.resolution === 'saved' ? 'Saved mapping' :
                          info.resolution === 'exact' ? 'Matched' :
                          'Will create'}
                      </span>
                    </td>
                    <td>
                      <button class="btn btn-sm btn-secondary tm-cat-change-btn">Change</button>
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
            <label class="tm-remember-label">
              <input type="checkbox" id="tm-import-remember" checked>
              Remember these mappings for future imports
            </label>
          </div>
          <div class="tm-modal-footer">
            <button class="btn btn-secondary" id="tm-import-skip-parents">Import without Parents</button>
            <button class="btn btn-primary" id="tm-import-with-parents">Import with Parents</button>
          </div>
        </div>
      `;

      document.body.appendChild(modal);

      // Track current resolutions (user may change them)
      const currentResolutions = JSON.parse(JSON.stringify(categoryResolutions));

      // Change button handlers
      modal.querySelectorAll('.tm-cat-change-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const row = btn.closest('tr');
          const catName = row.dataset.category;
          showCategoryParentSearch(catName, currentResolutions, row);
        });
      });

      // Import with Parents
      modal.querySelector('#tm-import-with-parents').addEventListener('click', () => {
        const parentMap = {};
        for (const [catName, info] of Object.entries(currentResolutions)) {
          parentMap[catName] = info.parentTagId; // null means create
        }
        const remember = modal.querySelector('#tm-import-remember').checked;
        modal.remove();
        resolve({ parentMap, remember, resolutions: currentResolutions });
      });

      // Import without Parents
      modal.querySelector('#tm-import-skip-parents').addEventListener('click', () => {
        modal.remove();
        resolve(null);
      });

      // Close = cancel entire import
      modal.querySelector('.tm-close-btn').addEventListener('click', () => {
        modal.remove();
        resolve('cancel');
      });
      modal.addEventListener('click', (e) => {
        if (e.target === modal) { modal.remove(); resolve('cancel'); }
      });
    });
  }

  /**
   * Open parent search modal scoped to a category row in the preview.
   * Updates currentResolutions and the row display when user selects a tag.
   */
  function showCategoryParentSearch(catName, currentResolutions, row) {
    const searchModal = document.createElement('div');
    searchModal.className = 'tm-modal-backdrop tm-search-modal';
    searchModal.style.zIndex = '1060'; // Above preview modal
    searchModal.innerHTML = `
      <div class="tm-modal tm-modal-small">
        <div class="tm-modal-header">
          <h3>Choose Parent for "${escapeHtml(catName)}"</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <input type="text" class="form-control tm-cat-search-input"
                 placeholder="Search tags..." value="${escapeHtml(catName)}">
          <div class="tm-search-results tm-cat-search-results">
            <div class="tm-loading">Type to search...</div>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(searchModal);

    const input = searchModal.querySelector('.tm-cat-search-input');
    const resultsEl = searchModal.querySelector('.tm-cat-search-results');

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
            currentResolutions[catName] = {
              parentTagId: tag.id,
              parentTagName: tag.name,
              resolution: 'manual',
              description: currentResolutions[catName]?.description || '',
            };
            // Update row display
            row.querySelector('.tm-cat-parent-name').textContent = tag.name;
            row.querySelector('.tm-cat-resolution').textContent = 'Manual';
            row.querySelector('.tm-cat-resolution').className = 'tm-cat-resolution tm-cat-manual';
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

**Step 2: Add CSS styles**

Append to `plugins/tagManager/tag-manager.css`:

```css
/* Category preview modal */
.tm-preview-intro {
  margin-bottom: 15px;
  color: var(--bs-secondary-color, #888);
}

.tm-category-preview-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 15px;
}

.tm-category-preview-table th,
.tm-category-preview-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--bs-border-color, #444);
}

.tm-category-preview-table th {
  font-weight: 600;
  color: var(--bs-secondary-color, #888);
  font-size: 0.85em;
  text-transform: uppercase;
}

.tm-cat-resolution {
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 0.85em;
}

.tm-cat-saved {
  background: var(--bs-info, #17a2b8);
  color: #fff;
}

.tm-cat-exact {
  background: var(--bs-success, #28a745);
  color: #fff;
}

.tm-cat-create {
  background: var(--bs-warning, #ffc107);
  color: #000;
}

.tm-cat-manual {
  background: var(--bs-primary, #0d6efd);
  color: #fff;
}

.tm-remember-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--bs-secondary-color, #888);
}
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add category parent preview modal for import"
```

---

### Task 3: Wire Preview into Import Flow

Modify `handleImportSelected()` to show the preview modal and use parent mappings during import.

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` — `handleImportSelected()` at line 872

**Step 1: Modify `handleImportSelected` to call preview and assign parents**

Replace the existing `handleImportSelected` function (lines 872–977):

```javascript
  async function handleImportSelected(container) {
    if (selectedForImport.size === 0) return;
    if (isImporting) return;

    // Resolve categories among selected tags
    const categoryResolutions = resolveCategoryParents(selectedForImport);
    const hasCategories = Object.keys(categoryResolutions).length > 0;

    let parentMap = null;   // { categoryName: parentTagId|null }
    let remember = false;
    let resolutions = null;

    if (hasCategories) {
      const result = await showCategoryPreviewModal(categoryResolutions);
      if (result === 'cancel') return; // User closed modal
      if (result !== null) {
        parentMap = result.parentMap;
        remember = result.remember;
        resolutions = result.resolutions;
      }
      // result === null means "Import without Parents"
    }

    isImporting = true;

    const statusEl = container.querySelector('.tm-selection-info');
    const btnEl = container.querySelector('#tm-import-selected');

    if (statusEl) statusEl.textContent = 'Importing...';
    if (btnEl) btnEl.disabled = true;

    let created = 0;
    let linked = 0;
    let parented = 0;
    let errors = 0;

    // Pre-create parent tags that need creating
    const createdParents = {}; // { categoryName: newTagId }
    if (parentMap) {
      for (const [catName, parentTagId] of Object.entries(parentMap)) {
        if (parentTagId === null) {
          // Need to create this parent tag
          try {
            const desc = resolutions[catName]?.description || '';
            const newTag = await createTag({ name: catName, description: desc });
            if (newTag) {
              createdParents[catName] = newTag.id;
              localTags.push({ id: newTag.id, name: newTag.name, aliases: [], stash_ids: [], parents: [] });
            }
          } catch (e) {
            console.error(`[tagManager] Failed to create parent tag "${catName}":`, e);
          }
        }
      }
    }

    for (const stashdbId of selectedForImport) {
      const stashdbTag = stashdbTags.find(t => t.id === stashdbId);
      if (!stashdbTag) continue;

      // Resolve parent ID for this tag's category
      let parentId = null;
      if (parentMap && stashdbTag.category) {
        const catName = stashdbTag.category.name;
        parentId = parentMap[catName] ?? createdParents[catName] ?? null;
        // For entries where parentMap has null but createdParents has the ID
        if (parentId === null && createdParents[catName]) {
          parentId = createdParents[catName];
        }
      }

      try {
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

          // Add parent if missing
          if (parentId) {
            const existingParentIds = (existingTag.parents || []).map(p => p.id);
            if (!existingParentIds.includes(parentId)) {
              await updateTag({
                id: existingTag.id,
                parent_ids: [...existingParentIds, parentId]
              });
              if (idx >= 0) {
                localTags[idx].parents = [...(localTags[idx].parents || []), { id: parentId }];
              }
              parented++;
            }
          }
        } else {
          // CREATE: New tag with stash_id and optional parent
          const input = {
            name: stashdbTag.name,
            description: stashdbTag.description || '',
            aliases: stashdbTag.aliases || [],
            stash_ids: [{
              endpoint: selectedStashBox.endpoint,
              stash_id: stashdbId
            }]
          };

          if (parentId) {
            input.parent_ids = [parentId];
          }

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
            localTags.push({
              id: data.tagCreate.id,
              name: data.tagCreate.name,
              aliases: stashdbTag.aliases || [],
              stash_ids: input.stash_ids,
              parents: parentId ? [{ id: parentId }] : []
            });
            created++;
            if (parentId) parented++;
          }
        }
      } catch (e) {
        console.error(`[tagManager] Failed to import/link "${stashdbTag.name}":`, e);
        errors++;
      }
    }

    // Save category mappings if requested
    if (remember && resolutions) {
      for (const [catName, info] of Object.entries(resolutions)) {
        const finalId = info.parentTagId || createdParents[catName];
        if (finalId) {
          categoryMappings[catName] = finalId;
        }
      }
      saveCategoryMappings();
    }

    // Clear selection and re-render
    selectedForImport.clear();

    // Build result message
    const parts = [];
    if (created > 0) parts.push(`Created ${created} tag${created !== 1 ? 's' : ''}`);
    if (linked > 0) parts.push(`linked ${linked} existing`);
    if (parented > 0) {
      const catCount = parentMap ? Object.keys(parentMap).length : 0;
      parts.push(`set parents for ${parented} (${catCount} ${catCount === 1 ? 'category' : 'categories'})`);
    }
    if (errors > 0) parts.push(`${errors} error${errors !== 1 ? 's' : ''}`);
    const message = parts.join(', ') || 'No changes';

    if (statusEl) statusEl.textContent = message;

    setTimeout(() => {
      isImporting = false;
      renderPage(container);
    }, 1500);
  }
```

**Step 2: Backfill description on existing parent tags**

Add description backfill after parent tag creation loop (inside the `parentMap` block, after creating parents):

```javascript
    // Backfill description on existing parent tags if empty
    if (parentMap && resolutions) {
      for (const [catName, parentTagId] of Object.entries(parentMap)) {
        if (parentTagId !== null && resolutions[catName]?.description) {
          const parentTag = localTags.find(t => t.id === parentTagId);
          if (parentTag && !parentTag.description) {
            try {
              await updateTag({ id: parentTagId, description: resolutions[catName].description });
              parentTag.description = resolutions[catName].description;
            } catch (e) {
              console.warn(`[tagManager] Failed to backfill description for "${catName}":`, e);
            }
          }
        }
      }
    }
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): wire category parent preview into import flow"
```

---

### Task 4: Manual Integration Test on Stash Test Instance

**Files:**
- No code changes — deployment and manual testing

**Step 1: Run unit tests**

```bash
node plugins/tagManager/tests/test_import_parents.js
node plugins/tagManager/tests/test_category_matching.js
node plugins/tagManager/tests/test_category_persistence.js
```

Expected: All pass

**Step 2: Deploy to test instance**

```bash
rsync -av --delete plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/
```

**Step 3: Manual test checklist**

1. Open Tag Manager → Browse StashDB tab
2. Select a category (e.g., "Action")
3. Select 3-5 tags
4. Click "Import Selected"
5. Verify the Category Parent Preview modal appears
6. Verify "Action" shows as matched (green badge) if local tag exists
7. Test "Change" button — search modal opens above preview
8. Test "Import without Parents" — tags imported, no parent set
9. Redo: select tags, click "Import Selected"
10. Test "Import with Parents" — tags imported with correct parent
11. Verify "Remember mappings" persists the mapping
12. Import from a category with no local match — verify "Will create" badge
13. Confirm parent tag is created after import
14. Import tags from "All" view with mixed categories — verify preview shows all
15. Re-import already-linked tags — verify parent is added if missing

**Step 4: Commit any fixes from testing**

```bash
git add -A && git commit -m "fix(tagManager): address integration test issues"
```

---

### Task 5: Add Integration Test for Import with Parents

**Files:**
- Modify: `plugins/tagManager/tests/test_import_parents.js` (extend)

**Step 1: Add tests for edge cases**

Append to `test_import_parents.js`:

```javascript
// --- Edge case tests ---
console.log('\n=== Edge case tests ===\n');

test('handles empty selection', () => {
  const result = resolveCategoryParents([]);
  assertEqual(result, {});
});

test('handles selection of only uncategorized tags', () => {
  const result = resolveCategoryParents(['s4']);
  assertEqual(result, {});
});

test('saved mapping takes priority over exact match', () => {
  categoryMappings = { 'Action': '20' }; // Mapped to Clothing instead of Action
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '20');
  assertEqual(result['Action'].parentTagName, 'Clothing');
  assertEqual(result['Action'].resolution, 'saved');
});

test('handles mixed categorized and uncategorized tags', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1', 's4', 's2']);
  assertEqual(Object.keys(result).length, 2); // Action + Accessories, not s4
  assertEqual(result['Action'] !== undefined, true);
  assertEqual(result['Accessories'] !== undefined, true);
});
```

**Step 2: Run tests**

```bash
node plugins/tagManager/tests/test_import_parents.js
```

Expected: All pass

**Step 3: Commit**

```bash
git add plugins/tagManager/tests/test_import_parents.js
git commit -m "test(tagManager): add edge case tests for category parent import"
```
