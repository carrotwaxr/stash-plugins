# P4: Import New Tags from StashDB Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Browse" tab to Tag Manager where users can browse StashDB tags by category and import them locally.

**Architecture:** Add tab switcher to Tag Manager (Match | Browse). Browse view shows cached StashDB tags grouped by category. Users can multi-select and bulk import. Imported tags get stash_id set. NOTE: P2/P3 dependencies (category parent, blacklist) will be integrated when branches merge.

**Tech Stack:** JavaScript (Stash plugin API), CSS

---

## Background

Tag Manager currently only matches existing local tags. Users want to browse StashDB tags and import new ones they don't have yet.

**Key Features:**
- Tab switcher: Match (existing) | Browse (new)
- Browse view: StashDB tags grouped by category
- Visual indicator for tags that already exist locally
- Multi-select checkboxes for bulk import
- "Import Selected" creates local tags with stash_id

---

## Task 1: Add activeTab state and tab constants

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (State section, around line 30)

**Step 1: Add state variable**

After the existing state variables (around `let currentFilter`), add:

```javascript
  let activeTab = 'match'; // 'match' or 'browse'
  let browseCategory = null; // Selected category in browse view
  let selectedForImport = new Set(); // Tag IDs selected for import
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add browse tab state variables"
```

---

## Task 2: Add tab switcher UI to renderPage

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in renderPage, around line 540)

**Step 1: Add tab switcher HTML**

Find `renderPage` function. After the header div (after `</div>` for `tag-manager-stats`), add:

```javascript
        <div class="tm-tabs">
          <button class="tm-tab ${activeTab === 'match' ? 'tm-tab-active' : ''}" data-tab="match">Match Local Tags</button>
          <button class="tm-tab ${activeTab === 'browse' ? 'tm-tab-active' : ''}" data-tab="browse">Browse StashDB</button>
        </div>
```

**Step 2: Wrap existing content in conditional**

The existing content (endpoint selector, filters, tag list) should only show when `activeTab === 'match'`. Wrap the content block in:

```javascript
        ${activeTab === 'match' ? `
          ... existing endpoint, filter, and tag list content ...
        ` : `
          ${renderBrowseView()}
        `}
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add tab switcher to renderPage"
```

---

## Task 3: Add tab click handler

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after renderPage event handlers)

**Step 1: Add tab handler**

Find where event handlers are attached (look for `container.querySelector`). Add:

```javascript
    // Tab switching
    container.querySelectorAll('.tm-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const newTab = tab.dataset.tab;
        if (newTab !== activeTab) {
          activeTab = newTab;
          renderPage(container);
        }
      });
    });
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add tab click handler"
```

---

## Task 4: Add renderBrowseView function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (before renderPage)

**Step 1: Add the browse view render function**

```javascript
  /**
   * Render the browse/import view
   */
  function renderBrowseView() {
    if (!stashdbTags || stashdbTags.length === 0) {
      return `
        <div class="tm-browse-empty">
          <p>No StashDB tags cached. Click "Refresh Cache" above to load tags.</p>
        </div>
      `;
    }

    // Group tags by category
    const categories = {};
    const uncategorized = [];

    for (const tag of stashdbTags) {
      const catName = tag.category?.name || null;
      if (catName) {
        if (!categories[catName]) {
          categories[catName] = [];
        }
        categories[catName].push(tag);
      } else {
        uncategorized.push(tag);
      }
    }

    // Sort categories alphabetically
    const sortedCategories = Object.keys(categories).sort();

    // Build category list
    const categoryList = sortedCategories.map(cat => {
      const count = categories[cat].length;
      const isSelected = browseCategory === cat;
      return `<div class="tm-category-item ${isSelected ? 'tm-category-active' : ''}" data-category="${escapeHtml(cat)}">
        <span class="tm-category-name">${escapeHtml(cat)}</span>
        <span class="tm-category-count">${count}</span>
      </div>`;
    }).join('');

    // Add uncategorized if any
    const uncategorizedItem = uncategorized.length > 0
      ? `<div class="tm-category-item ${browseCategory === '__uncategorized__' ? 'tm-category-active' : ''}" data-category="__uncategorized__">
          <span class="tm-category-name">Uncategorized</span>
          <span class="tm-category-count">${uncategorized.length}</span>
        </div>`
      : '';

    // Render tag list for selected category
    let tagListHtml = '';
    if (browseCategory) {
      const tagsToShow = browseCategory === '__uncategorized__'
        ? uncategorized
        : (categories[browseCategory] || []);

      tagListHtml = renderBrowseTagList(tagsToShow);
    } else {
      tagListHtml = `<div class="tm-browse-hint">Select a category to view tags</div>`;
    }

    const selectedCount = selectedForImport.size;

    return `
      <div class="tm-browse">
        <div class="tm-browse-sidebar">
          <div class="tm-browse-sidebar-header">
            <strong>Categories</strong>
            <span class="tm-total-tags">${stashdbTags.length} total</span>
          </div>
          <div class="tm-category-list">
            ${categoryList}
            ${uncategorizedItem}
          </div>
        </div>
        <div class="tm-browse-main">
          <div class="tm-browse-toolbar">
            <div class="tm-selection-info">
              ${selectedCount > 0 ? `${selectedCount} tag${selectedCount > 1 ? 's' : ''} selected` : 'No tags selected'}
            </div>
            <button class="btn btn-primary" id="tm-import-selected" ${selectedCount === 0 ? 'disabled' : ''}>
              Import Selected
            </button>
          </div>
          <div class="tm-browse-tags">
            ${tagListHtml}
          </div>
        </div>
      </div>
    `;
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add renderBrowseView function"
```

---

## Task 5: Add renderBrowseTagList function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (before renderBrowseView)

**Step 1: Add the tag list renderer**

```javascript
  /**
   * Check if a StashDB tag already exists locally
   */
  function findLocalTagByStashId(stashdbId) {
    return localTags.find(t =>
      t.stash_ids?.some(sid => sid.stash_id === stashdbId)
    );
  }

  /**
   * Render list of tags for browse/import view
   */
  function renderBrowseTagList(tags) {
    if (!tags || tags.length === 0) {
      return '<div class="tm-browse-empty">No tags in this category</div>';
    }

    const rows = tags.map(tag => {
      const localTag = findLocalTagByStashId(tag.id);
      const existsLocally = !!localTag;
      const isSelected = selectedForImport.has(tag.id);

      return `
        <div class="tm-browse-tag ${existsLocally ? 'tm-exists-locally' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${existsLocally ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${existsLocally ? `<span class="tm-local-exists" title="Linked to: ${escapeHtml(localTag.name)}">✓ Exists</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    return rows;
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add renderBrowseTagList function"
```

---

## Task 6: Add browse view event handlers

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after tab handlers)

**Step 1: Add category and checkbox handlers**

```javascript
    // Browse view handlers (only when browse tab active)
    if (activeTab === 'browse') {
      // Category selection
      container.querySelectorAll('.tm-category-item').forEach(item => {
        item.addEventListener('click', () => {
          browseCategory = item.dataset.category;
          renderPage(container);
        });
      });

      // Checkbox selection
      container.querySelectorAll('.tm-browse-tag input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const tagEl = e.target.closest('.tm-browse-tag');
          const stashdbId = tagEl.dataset.stashdbId;
          if (e.target.checked) {
            selectedForImport.add(stashdbId);
          } else {
            selectedForImport.delete(stashdbId);
          }
          // Update selection count display
          const infoEl = container.querySelector('.tm-selection-info');
          const btnEl = container.querySelector('#tm-import-selected');
          if (infoEl) {
            const count = selectedForImport.size;
            infoEl.textContent = count > 0 ? `${count} tag${count > 1 ? 's' : ''} selected` : 'No tags selected';
          }
          if (btnEl) {
            btnEl.disabled = selectedForImport.size === 0;
          }
        });
      });

      // Import button
      const importBtn = container.querySelector('#tm-import-selected');
      if (importBtn) {
        importBtn.addEventListener('click', () => handleImportSelected(container));
      }
    }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add browse view event handlers"
```

---

## Task 7: Add handleImportSelected function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (before renderBrowseView)

**Step 1: Add the import handler**

```javascript
  /**
   * Handle importing selected StashDB tags
   */
  async function handleImportSelected(container) {
    if (selectedForImport.size === 0) return;

    const statusEl = container.querySelector('.tm-selection-info');
    const btnEl = container.querySelector('#tm-import-selected');

    if (statusEl) statusEl.textContent = 'Importing...';
    if (btnEl) btnEl.disabled = true;

    let imported = 0;
    let errors = 0;

    for (const stashdbId of selectedForImport) {
      const stashdbTag = stashdbTags.find(t => t.id === stashdbId);
      if (!stashdbTag) continue;

      try {
        // Create local tag with stash_id
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
          imported++;
        }
      } catch (e) {
        console.error(`[tagManager] Failed to import "${stashdbTag.name}":`, e);
        errors++;
      }
    }

    // Clear selection and re-render
    selectedForImport.clear();

    const message = errors > 0
      ? `Imported ${imported} tag${imported !== 1 ? 's' : ''}, ${errors} error${errors !== 1 ? 's' : ''}`
      : `Imported ${imported} tag${imported !== 1 ? 's' : ''}`;

    if (statusEl) statusEl.textContent = message;

    // Re-render after short delay to show message
    setTimeout(() => renderPage(container), 1500);
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add handleImportSelected function"
```

---

## Task 8: Add browse view CSS

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (at end of file)

**Step 1: Add tab and browse styles**

```css
/* Tab Switcher */
.tm-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 20px;
  border-bottom: 2px solid var(--bs-border-color, #444);
}

.tm-tab {
  padding: 10px 20px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--bs-secondary-color, #888);
  font-weight: 500;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: all 0.2s ease;
}

.tm-tab:hover {
  color: var(--bs-body-color, #ccc);
}

.tm-tab-active {
  color: var(--bs-primary, #0d6efd);
  border-bottom-color: var(--bs-primary, #0d6efd);
}

/* Browse View Layout */
.tm-browse {
  display: flex;
  gap: 20px;
  min-height: 500px;
}

.tm-browse-sidebar {
  width: 250px;
  flex-shrink: 0;
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
  overflow: hidden;
}

.tm-browse-sidebar-header {
  padding: 12px 15px;
  background: var(--bs-secondary-bg, #2d2d44);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tm-total-tags {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
}

.tm-category-list {
  max-height: 450px;
  overflow-y: auto;
}

.tm-category-item {
  padding: 10px 15px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--bs-border-color, #333);
}

.tm-category-item:hover {
  background: var(--bs-secondary-bg, #2d2d44);
}

.tm-category-active {
  background: var(--bs-primary-bg-subtle, #1a3a5c);
  border-left: 3px solid var(--bs-primary, #0d6efd);
}

.tm-category-count {
  background: var(--bs-secondary-bg, #3d3d54);
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 0.8em;
}

/* Browse Main Area */
.tm-browse-main {
  flex: 1;
  min-width: 0;
}

.tm-browse-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  padding: 10px 15px;
  background: var(--bs-secondary-bg, #2d2d44);
  border-radius: 4px;
}

.tm-selection-info {
  color: var(--bs-secondary-color, #888);
}

.tm-browse-tags {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tm-browse-tag {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 15px;
  background: var(--bs-body-bg, #1a1a2e);
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
}

.tm-browse-tag:hover {
  border-color: var(--bs-primary, #0d6efd);
}

.tm-exists-locally {
  background: var(--bs-success-bg-subtle, #1a3a1a);
  color: var(--bs-success, #198754);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.8em;
}

.tm-browse-tag.tm-exists-locally {
  opacity: 0.6;
}

.tm-browse-checkbox {
  flex-shrink: 0;
}

.tm-browse-tag-info {
  flex: 1;
  min-width: 0;
}

.tm-browse-tag-name {
  font-weight: 500;
}

.tm-browse-tag-aliases {
  display: block;
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
  margin-top: 2px;
}

.tm-browse-tag-status {
  flex-shrink: 0;
}

.tm-browse-empty,
.tm-browse-hint {
  padding: 40px;
  text-align: center;
  color: var(--bs-secondary-color, #888);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "style(tagManager): add browse view CSS"
```

---

## Task 9: Add Select All / Deselect All controls

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in renderBrowseView toolbar)

**Step 1: Update toolbar in renderBrowseView**

Find the toolbar div in `renderBrowseView` and add Select All / Deselect All buttons:

```javascript
          <div class="tm-browse-toolbar">
            <div class="tm-selection-controls">
              <button class="btn btn-sm btn-secondary" id="tm-select-all">Select All</button>
              <button class="btn btn-sm btn-secondary" id="tm-deselect-all">Deselect All</button>
              <span class="tm-selection-info">
                ${selectedCount > 0 ? `${selectedCount} tag${selectedCount > 1 ? 's' : ''} selected` : 'No tags selected'}
              </span>
            </div>
            <button class="btn btn-primary" id="tm-import-selected" ${selectedCount === 0 ? 'disabled' : ''}>
              Import Selected
            </button>
          </div>
```

**Step 2: Add handlers for Select All / Deselect All**

In the browse view handlers section, add:

```javascript
      // Select All / Deselect All
      const selectAllBtn = container.querySelector('#tm-select-all');
      const deselectAllBtn = container.querySelector('#tm-deselect-all');

      if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
          container.querySelectorAll('.tm-browse-tag:not(.tm-exists-locally) input[type="checkbox"]').forEach(cb => {
            cb.checked = true;
            const tagEl = cb.closest('.tm-browse-tag');
            selectedForImport.add(tagEl.dataset.stashdbId);
          });
          renderPage(container);
        });
      }

      if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
          selectedForImport.clear();
          renderPage(container);
        });
      }
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add Select All / Deselect All controls"
```

---

## Task 10: Add selection controls CSS

**Files:**
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add styles**

```css
/* Selection controls */
.tm-selection-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tm-selection-controls .tm-selection-info {
  margin-left: 10px;
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "style(tagManager): add selection controls CSS"
```

---

## Task 11: Push and create PR

**Step 1: View commits**

```bash
git log --oneline feature/tag-manager-backlog..HEAD
```

**Step 2: Push branch**

```bash
git push -u origin feature/p4-tag-import
```

**Step 3: Create PR**

```bash
gh pr create --base feature/tag-manager-backlog --title "feat(tagManager): import new tags from StashDB (P4)" --body "$(cat <<'EOF'
## Summary
- Added "Browse StashDB" tab to Tag Manager
- Users can browse cached StashDB tags grouped by category
- Tags that already exist locally are marked with "✓ Exists"
- Multi-select checkboxes with Select All / Deselect All
- "Import Selected" button creates local tags with stash_id linked

## Changes
- Added `activeTab`, `browseCategory`, `selectedForImport` state
- Added tab switcher UI (Match Local Tags | Browse StashDB)
- Added `renderBrowseView()` and `renderBrowseTagList()` functions
- Added `findLocalTagByStashId()` helper
- Added `handleImportSelected()` for bulk import
- Added browse view CSS with category sidebar layout

## Integration Notes
When merged with P2 and P3:
- P2 category integration: Imported tags will get parent relationship set
- P3 blacklist: Blacklisted tags will be filtered from browse view

## Test plan
- [ ] Open Tag Manager, verify two tabs appear
- [ ] Click "Browse StashDB" tab
- [ ] Verify categories are listed in sidebar
- [ ] Click a category to see tags
- [ ] Verify "✓ Exists" badge on tags that are already linked locally
- [ ] Select multiple tags with checkboxes
- [ ] Click "Import Selected" - verify tags are created with stash_id
- [ ] Verify imported tags now show "✓ Exists"

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Summary of Changes

| File | Changes |
|------|---------|
| `tag-manager.js` | Added browse tab state, tab switcher, `renderBrowseView()`, `renderBrowseTagList()`, `findLocalTagByStashId()`, `handleImportSelected()`, browse event handlers |
| `tag-manager.css` | Tab styles, browse view layout, category sidebar, tag list styles, selection controls |

**Commits:**
1. `feat(tagManager): add browse tab state variables`
2. `feat(tagManager): add tab switcher to renderPage`
3. `feat(tagManager): add tab click handler`
4. `feat(tagManager): add renderBrowseView function`
5. `feat(tagManager): add renderBrowseTagList function`
6. `feat(tagManager): add browse view event handlers`
7. `feat(tagManager): add handleImportSelected function`
8. `style(tagManager): add browse view CSS`
9. `feat(tagManager): add Select All / Deselect All controls`
10. `style(tagManager): add selection controls CSS`
