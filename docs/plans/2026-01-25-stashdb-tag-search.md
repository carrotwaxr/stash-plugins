# StashDB Tag Search Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a search box to the Browse StashDB tab that filters tags by name or alias across all categories.

**Architecture:** Client-side filtering of the already-cached `stashdbTags` array. When user types in search box, hide sidebar and show flat list of matching tags with category badges. No backend changes needed.

**Tech Stack:** Vanilla JavaScript (existing plugin architecture), CSS

---

## Task 1: Add State Variable and Filter Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:33-34`

**Step 1: Add browseSearchQuery state variable**

At line 34, after `let selectedForImport = new Set();`, add:

```javascript
let browseSearchQuery = ''; // Search query for browse view
```

**Step 2: Add filterTagsBySearch function**

After the `escapeHtml` function (around line 555), add the search filter function:

```javascript
/**
 * Filter StashDB tags by search query (matches name and aliases)
 */
function filterTagsBySearch(query) {
  if (!query || !stashdbTags) return [];
  const lowerQuery = query.toLowerCase().trim();
  if (!lowerQuery) return [];

  return stashdbTags.filter(tag => {
    // Check tag name
    if (tag.name.toLowerCase().includes(lowerQuery)) return true;
    // Check aliases
    if (tag.aliases?.some(alias => alias.toLowerCase().includes(lowerQuery))) return true;
    return false;
  });
}
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add search state and filter function for browse view"
```

---

## Task 2: Add CSS Styles for Search UI

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (after line 1034)

**Step 1: Add search input styles**

Add after `.tm-selection-controls .tm-selection-info` block:

```css
/* Browse search */
.tm-browse-search {
  position: relative;
  margin-bottom: 15px;
}

.tm-browse-search-input {
  width: 100%;
  padding: 10px 35px 10px 12px;
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
  background: var(--bs-body-bg, #1a1a2e);
  color: var(--bs-body-color, #fff);
  font-size: 0.95em;
}

.tm-browse-search-input:focus {
  outline: none;
  border-color: var(--bs-primary, #0d6efd);
}

.tm-browse-search-input::placeholder {
  color: var(--bs-secondary-color, #888);
}

.tm-browse-search-clear {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--bs-secondary-color, #888);
  cursor: pointer;
  padding: 4px 8px;
  font-size: 1.1em;
  line-height: 1;
}

.tm-browse-search-clear:hover {
  color: var(--bs-body-color, #fff);
}

/* Category badge for search results */
.tm-tag-category-badge {
  background: var(--bs-info-bg-subtle, #1a3a4a);
  color: var(--bs-info, #0dcaf0);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.75em;
  white-space: nowrap;
}

/* Sidebar hidden state during search */
.tm-browse-sidebar.tm-sidebar-hidden {
  display: none;
}

/* Search results count */
.tm-search-results-count {
  color: var(--bs-secondary-color, #888);
  font-size: 0.9em;
  margin-bottom: 10px;
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add CSS styles for browse search UI"
```

---

## Task 3: Create renderSearchResults Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after `renderBrowseTagList` function, around line 957)

**Step 1: Add renderSearchResults function**

```javascript
/**
 * Render search results as a flat list with category badges
 */
function renderSearchResults(tags) {
  if (!tags || tags.length === 0) {
    return `<div class="tm-browse-empty">No tags found matching "${escapeHtml(browseSearchQuery)}"</div>`;
  }

  const rows = tags.map(tag => {
    const localTag = findLocalTagByStashId(tag.id);
    const existsLocally = !!localTag;
    const isSelected = selectedForImport.has(tag.id);
    const categoryName = tag.category?.name || 'Uncategorized';

    return `
      <div class="tm-browse-tag ${existsLocally ? 'tm-exists-locally' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
        <label class="tm-browse-checkbox">
          <input type="checkbox" ${isSelected ? 'checked' : ''} ${existsLocally ? 'disabled' : ''}>
        </label>
        <div class="tm-browse-tag-info">
          <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
          <span class="tm-tag-category-badge">${escapeHtml(categoryName)}</span>
          ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
        </div>
        <div class="tm-browse-tag-status">
          ${existsLocally ? `<span class="tm-local-exists" title="Linked to: ${escapeHtml(localTag.name)}">✓ Exists</span>` : ''}
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

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add renderSearchResults function for flat results list"
```

---

## Task 4: Modify renderBrowseView to Include Search

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:962-1053` (the `renderBrowseView` function)

**Step 1: Add search input to the main content area and conditionally hide sidebar**

Replace the `renderBrowseView` function with:

```javascript
function renderBrowseView() {
  if (!stashdbTags || stashdbTags.length === 0) {
    return `
      <div class="tm-browse-empty">
        <p>No StashDB tags cached. Click "Refresh Cache" above to load tags.</p>
      </div>
    `;
  }

  const isSearching = browseSearchQuery.trim().length > 0;

  // Group tags by category (for sidebar)
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

  // Render tag list based on search or category selection
  let tagListHtml = '';
  if (isSearching) {
    const searchResults = filterTagsBySearch(browseSearchQuery);
    tagListHtml = renderSearchResults(searchResults);
  } else if (browseCategory) {
    const tagsToShow = browseCategory === '__uncategorized__'
      ? uncategorized
      : (categories[browseCategory] || []);
    tagListHtml = renderBrowseTagList(tagsToShow);
  } else {
    tagListHtml = `<div class="tm-browse-hint">Select a category to view tags, or search above</div>`;
  }

  const selectedCount = selectedForImport.size;

  return `
    <div class="tm-browse">
      <div class="tm-browse-sidebar ${isSearching ? 'tm-sidebar-hidden' : ''}">
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
        <div class="tm-browse-search">
          <input type="text" class="tm-browse-search-input" id="tm-browse-search"
                 placeholder="Search tags by name or alias..."
                 value="${escapeHtml(browseSearchQuery)}">
          ${browseSearchQuery ? '<button type="button" class="tm-browse-search-clear" id="tm-search-clear">&times;</button>' : ''}
        </div>
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
git commit -m "feat(tagManager): update renderBrowseView with search input and conditional sidebar"
```

---

## Task 5: Add Event Handlers for Search

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in the event handler section, around line 1234-1284)

**Step 1: Add search input event handler with debounce**

Find the section where browse view event handlers are attached (after the `if (activeTab === 'browse')` block that handles category clicks, checkboxes, select all, etc.). Add the search handlers at the start of that `if` block:

After line `if (activeTab === 'browse') {` (around line 1197), add:

```javascript
    // Search input with debounce
    let searchTimeout = null;
    const searchInput = container.querySelector('#tm-browse-search');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
          browseSearchQuery = e.target.value;
          renderPage(container);
          // Re-focus and restore cursor position
          const newInput = container.querySelector('#tm-browse-search');
          if (newInput) {
            newInput.focus();
            newInput.setSelectionRange(newInput.value.length, newInput.value.length);
          }
        }, 200);
      });
    }

    // Clear search button
    const clearBtn = container.querySelector('#tm-search-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        browseSearchQuery = '';
        renderPage(container);
      });
    }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add event handlers for search input with debounce"
```

---

## Task 6: Update Select All to Work with Search Results

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (around line 1261-1270)

**Step 1: Modify Select All button to work in search mode**

The existing Select All already selects visible `.tm-browse-tag:not(.tm-exists-locally)` elements, which will work for both category view and search results. No code change needed here - the existing implementation is correct.

**Step 2: Verify and commit (no changes needed)**

The existing Select All implementation at lines 1262-1269 already works correctly:
```javascript
selectAllBtn.addEventListener('click', () => {
  container.querySelectorAll('.tm-browse-tag:not(.tm-exists-locally) input[type="checkbox"]').forEach(cb => {
    cb.checked = true;
    const tagEl = cb.closest('.tm-browse-tag');
    selectedForImport.add(tagEl.dataset.stashdbId);
  });
  renderPage(container);
});
```

This selects all visible tags regardless of whether they're from category view or search results.

---

## Task 7: Manual Testing

**Files:** None (testing only)

**Step 1: Test search by tag name**

1. Open Stash UI, navigate to Plugins → Tag Manager
2. Click "Browse StashDB" tab
3. Ensure cache is loaded (click "Refresh Cache" if needed)
4. Type a tag name in the search box
5. Verify: sidebar disappears, flat results appear with category badges

**Step 2: Test search by alias**

1. Search for a term that's an alias (not the main tag name)
2. Verify: the tag with that alias appears in results

**Step 3: Test case insensitivity**

1. Search with different cases (e.g., "MILF", "milf", "Milf")
2. Verify: same results regardless of case

**Step 4: Test clear search**

1. With search results showing, click the × button
2. Verify: search clears, sidebar reappears, previous category (if any) is restored

**Step 5: Test selection persistence**

1. Select a tag from search results
2. Clear search
3. Navigate to that tag's category
4. Verify: the checkbox is still checked

**Step 6: Test import from search**

1. Search for tags
2. Select one or more
3. Click "Import Selected"
4. Verify: import works correctly

**Step 7: Test empty results**

1. Search for gibberish that won't match anything
2. Verify: "No tags found matching..." message appears

**Step 8: Commit final version**

```bash
git add -A
git commit -m "feat(tagManager): complete StashDB tag search implementation"
```

---

## Summary

| Task | Description | Est. Lines Changed |
|------|-------------|-------------------|
| 1 | State variable + filter function | ~15 |
| 2 | CSS styles | ~50 |
| 3 | renderSearchResults function | ~30 |
| 4 | Update renderBrowseView | ~90 (replace) |
| 5 | Event handlers | ~20 |
| 6 | Verify Select All (no changes) | 0 |
| 7 | Manual testing | 0 |

**Total:** ~205 lines added/modified
