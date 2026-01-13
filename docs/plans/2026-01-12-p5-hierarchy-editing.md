# P5: Tag Hierarchy Editing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add editing capabilities to the Tag Hierarchy view - drag-and-drop, context menu, keyboard shortcuts, and multi-parent visual indicators.

**Architecture:** Extend existing hierarchy view with interactive editing. Uses Stash GraphQL `TagUpdate` mutation to modify `parent_ids`. Supports multiple parents natively (Stash feature). Context menu and drag-drop as primary interaction patterns.

**Tech Stack:** Vanilla JavaScript (existing plugin pattern), Stash GraphQL API, CSS for visual feedback

---

## Task 1: Add Context Menu Infrastructure

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1372-1420` (renderTreeNode)
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Write the context menu HTML/CSS structure**

Add to `tag-manager.css`:
```css
/* Context menu */
.th-context-menu {
  position: fixed;
  background: var(--bs-body-bg, #1a1d1e);
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
  padding: 4px 0;
  min-width: 180px;
  z-index: 10000;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.th-context-menu-item {
  padding: 8px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}

.th-context-menu-item:hover {
  background: var(--bs-primary, #137cbd);
}

.th-context-menu-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.th-context-menu-item.disabled:hover {
  background: transparent;
}

.th-context-menu-separator {
  height: 1px;
  background: var(--bs-border-color, #444);
  margin: 4px 0;
}
```

**Step 2: Add context menu state and helper functions**

Add to `tag-manager.js` after hierarchy state variables (line ~1367):
```javascript
let contextMenuTag = null;
let contextMenuParentId = null;

function showContextMenu(x, y, tagId, parentId) {
  hideContextMenu();
  contextMenuTag = hierarchyTags.find(t => t.id === tagId);
  contextMenuParentId = parentId;
  if (!contextMenuTag) return;

  const menu = document.createElement('div');
  menu.className = 'th-context-menu';
  menu.id = 'th-context-menu';

  const hasParents = contextMenuTag.parents && contextMenuTag.parents.length > 0;
  const isUnderParent = parentId !== null;

  let menuHtml = `
    <div class="th-context-menu-item" data-action="add-parent">Add parent...</div>
    <div class="th-context-menu-item" data-action="add-child">Add child...</div>
  `;

  if (isUnderParent) {
    const parentTag = hierarchyTags.find(t => t.id === parentId);
    const parentName = parentTag ? parentTag.name : 'parent';
    menuHtml += `<div class="th-context-menu-separator"></div>`;
    menuHtml += `<div class="th-context-menu-item" data-action="remove-parent" data-parent-id="${parentId}">Remove from "${escapeHtml(parentName)}"</div>`;
  }

  if (hasParents) {
    menuHtml += `<div class="th-context-menu-item" data-action="make-root">Make root (remove all parents)</div>`;
  }

  menu.innerHTML = menuHtml;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;

  document.body.appendChild(menu);

  // Position adjustment if off-screen
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) {
    menu.style.left = `${window.innerWidth - rect.width - 10}px`;
  }
  if (rect.bottom > window.innerHeight) {
    menu.style.top = `${window.innerHeight - rect.height - 10}px`;
  }

  // Click handlers
  menu.querySelectorAll('.th-context-menu-item:not(.disabled)').forEach(item => {
    item.addEventListener('click', handleContextMenuAction);
  });

  // Close on click outside
  setTimeout(() => {
    document.addEventListener('click', hideContextMenu, { once: true });
  }, 0);
}

function hideContextMenu() {
  const menu = document.getElementById('th-context-menu');
  if (menu) menu.remove();
  contextMenuTag = null;
  contextMenuParentId = null;
}
```

**Step 3: Add right-click handler to tree nodes**

Modify `renderTreeNode()` to add data attributes and context menu trigger. Update the node content div:
```javascript
// In renderTreeNode(), update the th-node div to include parent context
const parentIdAttr = isRoot ? '' : `data-parent-id="${node.parentContextId || ''}"`;

return `
  <div class="th-node ${isRoot ? 'th-root' : ''}" data-tag-id="${node.id}" ${parentIdAttr}>
```

**Step 4: Add context menu event handler in attachHierarchyEventHandlers**

Add to `attachHierarchyEventHandlers()`:
```javascript
// Context menu on right-click
container.querySelectorAll('.th-node').forEach(node => {
  node.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const tagId = node.dataset.tagId;
    const parentId = node.closest('.th-children')?.dataset.parentId || null;
    showContextMenu(e.clientX, e.clientY, tagId, parentId);
  });
});
```

**Step 5: Verify context menu appears**

Test manually:
1. Navigate to Tag Hierarchy page
2. Right-click on any tag
3. Context menu should appear with appropriate options
4. Click outside should close menu

**Step 6: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add context menu infrastructure for hierarchy editing"
```

---

## Task 2: Implement Context Menu Actions

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`

**Step 1: Add handleContextMenuAction function**

```javascript
async function handleContextMenuAction(e) {
  const action = e.target.dataset.action;
  const parentIdToRemove = e.target.dataset.parentId;

  if (!contextMenuTag) return;
  hideContextMenu();

  switch (action) {
    case 'add-parent':
      showTagSearchDialog('parent', contextMenuTag);
      break;
    case 'add-child':
      showTagSearchDialog('child', contextMenuTag);
      break;
    case 'remove-parent':
      await removeParent(contextMenuTag.id, parentIdToRemove);
      break;
    case 'make-root':
      await makeRoot(contextMenuTag.id);
      break;
  }
}
```

**Step 2: Add removeParent function**

```javascript
async function removeParent(tagId, parentIdToRemove) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  if (!tag) return;

  const newParentIds = tag.parents
    .map(p => p.id)
    .filter(id => id !== parentIdToRemove);

  try {
    await updateTagParents(tagId, newParentIds);
    await refreshHierarchy();
    showToast(`Removed "${tag.name}" from parent`);
  } catch (err) {
    console.error('[tagManager] Failed to remove parent:', err);
    showToast(`Error: ${err.message}`, 'error');
  }
}
```

**Step 3: Add makeRoot function**

```javascript
async function makeRoot(tagId) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  if (!tag) return;

  if (tag.parents.length === 0) {
    showToast('Tag is already a root');
    return;
  }

  try {
    await updateTagParents(tagId, []);
    await refreshHierarchy();
    showToast(`"${tag.name}" is now a root tag`);
  } catch (err) {
    console.error('[tagManager] Failed to make root:', err);
    showToast(`Error: ${err.message}`, 'error');
  }
}
```

**Step 4: Add updateTagParents helper**

```javascript
async function updateTagParents(tagId, parentIds) {
  const query = `
    mutation TagUpdate($input: TagUpdateInput!) {
      tagUpdate(input: $input) {
        id
        name
        parents { id name }
      }
    }
  `;

  const result = await graphqlRequest(query, {
    input: {
      id: tagId,
      parent_ids: parentIds
    }
  });

  return result?.tagUpdate;
}
```

**Step 5: Add refreshHierarchy function**

```javascript
async function refreshHierarchy() {
  const container = document.querySelector('.tag-hierarchy-container');
  if (!container) return;

  try {
    hierarchyTags = await fetchAllTagsWithHierarchy();
    hierarchyTree = buildTagTree(hierarchyTags);
    hierarchyStats = getTreeStats(hierarchyTags);
    renderHierarchyPage(container);
  } catch (err) {
    console.error('[tagManager] Failed to refresh hierarchy:', err);
  }
}
```

**Step 6: Test remove parent and make root**

Test manually:
1. Right-click a tag that has a parent
2. Click "Remove from [parent]" - tag should move to root
3. Right-click a tag with multiple parents
4. Click "Make root" - tag should become root

**Step 7: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): implement remove parent and make root actions"
```

---

## Task 3: Add Tag Search Dialog for Add Parent/Child

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add CSS for search dialog**

```css
/* Tag search dialog */
.th-search-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: var(--bs-body-bg, #1a1d1e);
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 8px;
  padding: 20px;
  min-width: 400px;
  max-width: 500px;
  max-height: 80vh;
  z-index: 10001;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}

.th-search-dialog-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
}

.th-search-dialog h3 {
  margin: 0 0 16px 0;
  font-size: 1.1rem;
}

.th-search-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
  background: var(--bs-body-bg, #1a1d1e);
  color: inherit;
  margin-bottom: 12px;
}

.th-search-results {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--bs-border-color, #444);
  border-radius: 4px;
}

.th-search-result {
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--bs-border-color, #333);
}

.th-search-result:last-child {
  border-bottom: none;
}

.th-search-result:hover {
  background: var(--bs-primary, #137cbd);
}

.th-search-result.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.th-search-result-name {
  flex: 1;
}

.th-search-result-badge {
  font-size: 0.75rem;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--bs-secondary, #495057);
}

.th-search-empty {
  padding: 20px;
  text-align: center;
  color: var(--bs-secondary, #6c757d);
}
```

**Step 2: Add showTagSearchDialog function**

```javascript
function showTagSearchDialog(mode, targetTag) {
  // mode: 'parent' or 'child'
  const backdrop = document.createElement('div');
  backdrop.className = 'th-search-dialog-backdrop';
  backdrop.id = 'th-search-backdrop';

  const dialog = document.createElement('div');
  dialog.className = 'th-search-dialog';
  dialog.id = 'th-search-dialog';

  const title = mode === 'parent'
    ? `Add parent for "${escapeHtml(targetTag.name)}"`
    : `Add child to "${escapeHtml(targetTag.name)}"`;

  dialog.innerHTML = `
    <h3>${title}</h3>
    <input type="text" class="th-search-input" placeholder="Search tags..." autofocus>
    <div class="th-search-results">
      <div class="th-search-empty">Type to search...</div>
    </div>
  `;

  document.body.appendChild(backdrop);
  document.body.appendChild(dialog);

  const input = dialog.querySelector('.th-search-input');
  const results = dialog.querySelector('.th-search-results');

  // Debounced search
  let searchTimeout;
  input.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      performTagSearch(input.value, mode, targetTag, results);
    }, 200);
  });

  // Close on backdrop click or escape
  backdrop.addEventListener('click', closeTagSearchDialog);
  document.addEventListener('keydown', function escHandler(e) {
    if (e.key === 'Escape') {
      closeTagSearchDialog();
      document.removeEventListener('keydown', escHandler);
    }
  });

  input.focus();
}

function closeTagSearchDialog() {
  document.getElementById('th-search-backdrop')?.remove();
  document.getElementById('th-search-dialog')?.remove();
}
```

**Step 3: Add performTagSearch function**

```javascript
function performTagSearch(query, mode, targetTag, resultsContainer) {
  if (!query.trim()) {
    resultsContainer.innerHTML = '<div class="th-search-empty">Type to search...</div>';
    return;
  }

  const lowerQuery = query.toLowerCase();

  // Filter local tags
  let matches = hierarchyTags.filter(t => {
    // Don't show the target tag itself
    if (t.id === targetTag.id) return false;

    // Check name and aliases
    if (t.name.toLowerCase().includes(lowerQuery)) return true;
    if (t.aliases?.some(a => a.toLowerCase().includes(lowerQuery))) return true;
    return false;
  });

  // Sort by relevance (exact match first, then starts with, then contains)
  matches.sort((a, b) => {
    const aLower = a.name.toLowerCase();
    const bLower = b.name.toLowerCase();
    const aExact = aLower === lowerQuery;
    const bExact = bLower === lowerQuery;
    if (aExact && !bExact) return -1;
    if (bExact && !aExact) return 1;
    const aStarts = aLower.startsWith(lowerQuery);
    const bStarts = bLower.startsWith(lowerQuery);
    if (aStarts && !bStarts) return -1;
    if (bStarts && !aStarts) return 1;
    return a.name.localeCompare(b.name);
  });

  // Limit results
  matches = matches.slice(0, 20);

  if (matches.length === 0) {
    resultsContainer.innerHTML = '<div class="th-search-empty">No tags found</div>';
    return;
  }

  resultsContainer.innerHTML = matches.map(tag => {
    // Check for circular reference
    const wouldCreateCircle = mode === 'parent'
      ? wouldCreateCircularRef(tag.id, targetTag.id)
      : wouldCreateCircularRef(targetTag.id, tag.id);

    const isAlreadyRelated = mode === 'parent'
      ? targetTag.parents?.some(p => p.id === tag.id)
      : tag.parents?.some(p => p.id === targetTag.id);

    const disabled = wouldCreateCircle || isAlreadyRelated;
    const badge = wouldCreateCircle ? 'circular' : isAlreadyRelated ? 'already linked' : '';

    return `
      <div class="th-search-result ${disabled ? 'disabled' : ''}"
           data-tag-id="${tag.id}"
           data-mode="${mode}"
           data-target-id="${targetTag.id}">
        <span class="th-search-result-name">${escapeHtml(tag.name)}</span>
        ${badge ? `<span class="th-search-result-badge">${badge}</span>` : ''}
      </div>
    `;
  }).join('');

  // Click handlers
  resultsContainer.querySelectorAll('.th-search-result:not(.disabled)').forEach(item => {
    item.addEventListener('click', handleSearchResultClick);
  });
}
```

**Step 4: Add handleSearchResultClick function**

```javascript
async function handleSearchResultClick(e) {
  const tagId = e.currentTarget.dataset.tagId;
  const mode = e.currentTarget.dataset.mode;
  const targetId = e.currentTarget.dataset.targetId;

  closeTagSearchDialog();

  if (mode === 'parent') {
    await addParent(targetId, tagId);
  } else {
    await addChild(targetId, tagId);
  }
}

async function addParent(tagId, newParentId) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  if (!tag) return;

  const newParentIds = [...(tag.parents?.map(p => p.id) || []), newParentId];

  try {
    await updateTagParents(tagId, newParentIds);
    await refreshHierarchy();
    const parent = hierarchyTags.find(t => t.id === newParentId);
    showToast(`Added "${tag.name}" as child of "${parent?.name || 'parent'}"`);
  } catch (err) {
    console.error('[tagManager] Failed to add parent:', err);
    showToast(`Error: ${err.message}`, 'error');
  }
}

async function addChild(parentId, childId) {
  const child = hierarchyTags.find(t => t.id === childId);
  if (!child) return;

  const newParentIds = [...(child.parents?.map(p => p.id) || []), parentId];

  try {
    await updateTagParents(childId, newParentIds);
    await refreshHierarchy();
    const parent = hierarchyTags.find(t => t.id === parentId);
    showToast(`Added "${child.name}" as child of "${parent?.name || 'parent'}"`);
  } catch (err) {
    console.error('[tagManager] Failed to add child:', err);
    showToast(`Error: ${err.message}`, 'error');
  }
}
```

**Step 5: Test add parent and add child**

Test manually:
1. Right-click a tag, select "Add parent..."
2. Search for a tag, select it
3. Tag should now appear under the selected parent
4. Right-click a tag, select "Add child..."
5. Search for a tag, select it
6. Selected tag should now appear under the target tag

**Step 6: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add tag search dialog for parent/child operations"
```

---

## Task 4: Add Circular Reference Prevention

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`

**Step 1: Add wouldCreateCircularRef function**

```javascript
/**
 * Check if making potentialParentId a parent of tagId would create a circular reference.
 * This happens if tagId is already an ancestor of potentialParentId.
 */
function wouldCreateCircularRef(potentialParentId, tagId) {
  // Build a set of all ancestors of potentialParentId
  const ancestors = new Set();

  function collectAncestors(id) {
    const tag = hierarchyTags.find(t => t.id === id);
    if (!tag || !tag.parents) return;

    for (const parent of tag.parents) {
      if (ancestors.has(parent.id)) continue; // Already visited
      ancestors.add(parent.id);
      collectAncestors(parent.id);
    }
  }

  collectAncestors(potentialParentId);

  // If tagId is an ancestor of potentialParentId, adding potentialParentId as parent of tagId
  // would create: tagId -> potentialParentId -> ... -> tagId (circular)
  return ancestors.has(tagId);
}
```

**Step 2: Test circular reference prevention**

Test manually:
1. Create a chain: A -> B -> C (C is child of B, B is child of A)
2. Right-click on A, select "Add parent..."
3. Search for C - it should show "circular" badge and be disabled
4. Search for B - it should also show "circular" badge

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add circular reference prevention"
```

---

## Task 5: Add Multi-Parent Visual Indicator

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1372-1420` (renderTreeNode)
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add CSS for multi-parent indicator**

```css
/* Multi-parent indicator */
.th-multi-parent-badge {
  font-size: 0.7rem;
  padding: 2px 6px;
  border-radius: 10px;
  background: var(--bs-info, #0dcaf0);
  color: var(--bs-dark, #212529);
  margin-left: 8px;
  white-space: nowrap;
}

.th-node.th-highlighted .th-node-content {
  background: rgba(13, 202, 240, 0.15);
  border-radius: 4px;
}
```

**Step 2: Update renderTreeNode to show multi-parent badge**

In `renderTreeNode()`, add the badge after the name:

```javascript
// Multi-parent badge
const parentCount = node.parents?.length || 0;
const multiParentBadge = parentCount > 1
  ? `<span class="th-multi-parent-badge" title="Appears under ${parentCount} parents">${parentCount} parents</span>`
  : '';

// Update the th-info section:
<div class="th-info">
  <a href="/tags/${node.id}" class="th-name">${escapeHtml(node.name)}</a>
  ${multiParentBadge}
  ${metaText ? `<div class="th-meta">${metaText}</div>` : ''}
</div>
```

**Step 3: Add hover highlighting for same tags**

Add to `attachHierarchyEventHandlers()`:

```javascript
// Highlight all instances of a tag on hover
container.querySelectorAll('.th-node').forEach(node => {
  node.addEventListener('mouseenter', () => {
    const tagId = node.dataset.tagId;
    container.querySelectorAll(`.th-node[data-tag-id="${tagId}"]`).forEach(n => {
      n.classList.add('th-highlighted');
    });
  });

  node.addEventListener('mouseleave', () => {
    container.querySelectorAll('.th-node.th-highlighted').forEach(n => {
      n.classList.remove('th-highlighted');
    });
  });
});
```

**Step 4: Test multi-parent indicators**

Test manually:
1. Create a tag with 2+ parents
2. Badge should show "2 parents" etc.
3. Hover over the tag - all instances should highlight

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add multi-parent visual indicators and hover highlighting"
```

---

## Task 6: Add Drag-and-Drop Infrastructure

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add drag-and-drop CSS**

```css
/* Drag and drop */
.th-node[draggable="true"] {
  cursor: grab;
}

.th-node.dragging {
  opacity: 0.5;
}

.th-node.drag-over > .th-node-content {
  background: rgba(13, 110, 253, 0.2);
  outline: 2px dashed var(--bs-primary, #137cbd);
  outline-offset: -2px;
}

.th-node.drag-invalid > .th-node-content {
  background: rgba(220, 53, 69, 0.2);
  outline: 2px dashed var(--bs-danger, #dc3545);
  outline-offset: -2px;
}

.th-root-drop-zone {
  min-height: 40px;
  border: 2px dashed var(--bs-border-color, #444);
  border-radius: 4px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--bs-secondary, #6c757d);
  transition: all 0.2s;
}

.th-root-drop-zone.drag-over {
  border-color: var(--bs-primary, #137cbd);
  background: rgba(13, 110, 253, 0.1);
  color: var(--bs-primary, #137cbd);
}
```

**Step 2: Update renderTreeNode to make nodes draggable**

In `renderTreeNode()`, update the th-node div:

```javascript
return `
  <div class="th-node ${isRoot ? 'th-root' : ''}"
       data-tag-id="${node.id}"
       draggable="true">
```

**Step 3: Add root drop zone in renderHierarchyPage**

In `renderHierarchyPage()`, add the drop zone before the tree:

```javascript
<div class="th-root-drop-zone" id="th-root-drop-zone">
  Drop here to make root tag
</div>
<div class="th-tree">
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add drag-and-drop visual infrastructure"
```

---

## Task 7: Implement Drag-and-Drop Event Handlers

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`

**Step 1: Add drag state variables**

```javascript
let draggedTagId = null;
let draggedFromParentId = null;
```

**Step 2: Add drag event handlers in attachHierarchyEventHandlers**

```javascript
// Drag and drop handlers
container.querySelectorAll('.th-node').forEach(node => {
  node.addEventListener('dragstart', (e) => {
    draggedTagId = node.dataset.tagId;
    draggedFromParentId = node.closest('.th-children')?.dataset.parentId || null;
    node.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedTagId);
  });

  node.addEventListener('dragend', () => {
    node.classList.remove('dragging');
    draggedTagId = null;
    draggedFromParentId = null;
    // Clear all drag-over states
    container.querySelectorAll('.drag-over, .drag-invalid').forEach(el => {
      el.classList.remove('drag-over', 'drag-invalid');
    });
  });

  node.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (!draggedTagId || node.dataset.tagId === draggedTagId) return;

    const targetId = node.dataset.tagId;
    const wouldCircle = wouldCreateCircularRef(targetId, draggedTagId);

    node.classList.remove('drag-over', 'drag-invalid');
    node.classList.add(wouldCircle ? 'drag-invalid' : 'drag-over');
  });

  node.addEventListener('dragleave', () => {
    node.classList.remove('drag-over', 'drag-invalid');
  });

  node.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    node.classList.remove('drag-over', 'drag-invalid');

    if (!draggedTagId || node.dataset.tagId === draggedTagId) return;

    const targetId = node.dataset.tagId;
    if (wouldCreateCircularRef(targetId, draggedTagId)) {
      showToast('Cannot create circular reference', 'error');
      return;
    }

    // Add target as parent of dragged tag
    await addParent(draggedTagId, targetId);
  });
});

// Root drop zone handler
const rootDropZone = container.querySelector('#th-root-drop-zone');
if (rootDropZone) {
  rootDropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (draggedTagId) {
      rootDropZone.classList.add('drag-over');
    }
  });

  rootDropZone.addEventListener('dragleave', () => {
    rootDropZone.classList.remove('drag-over');
  });

  rootDropZone.addEventListener('drop', async (e) => {
    e.preventDefault();
    rootDropZone.classList.remove('drag-over');

    if (!draggedTagId) return;

    // If dragged from a specific parent, just remove that parent
    if (draggedFromParentId) {
      await removeParent(draggedTagId, draggedFromParentId);
    } else {
      // Make completely root
      await makeRoot(draggedTagId);
    }
  });
}
```

**Step 3: Test drag-and-drop**

Test manually:
1. Drag a tag onto another tag - should add as child
2. Drag a tag to root zone - should remove from parent
3. Try to drag to create circular ref - should show invalid state and reject

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): implement drag-and-drop event handlers"
```

---

## Task 8: Add Keyboard Shortcuts

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add CSS for selected state**

```css
/* Keyboard selection */
.th-node.th-selected > .th-node-content {
  outline: 2px solid var(--bs-primary, #137cbd);
  outline-offset: -2px;
  background: rgba(13, 110, 253, 0.1);
}

.th-node.th-copied > .th-node-content {
  outline: 2px dashed var(--bs-success, #198754);
  outline-offset: -2px;
}
```

**Step 2: Add keyboard state variables**

```javascript
let selectedTagId = null;
let copiedTagId = null;
```

**Step 3: Add click-to-select handler in attachHierarchyEventHandlers**

```javascript
// Click to select (for keyboard operations)
container.querySelectorAll('.th-node-content').forEach(content => {
  content.addEventListener('click', (e) => {
    // Don't select if clicking on a link or toggle
    if (e.target.closest('a') || e.target.closest('.th-toggle')) return;

    const node = content.closest('.th-node');
    const tagId = node?.dataset.tagId;
    if (!tagId) return;

    // Clear previous selection
    container.querySelectorAll('.th-node.th-selected').forEach(n => {
      n.classList.remove('th-selected');
    });

    // Select this node
    node.classList.add('th-selected');
    selectedTagId = tagId;
  });
});
```

**Step 4: Add keyboard event handler**

```javascript
// Keyboard shortcuts
function handleHierarchyKeyboard(e) {
  // Only handle if hierarchy page is active
  if (!document.querySelector('.tag-hierarchy-container')) return;

  // Ctrl+C - copy selected tag
  if (e.ctrlKey && e.key === 'c' && selectedTagId) {
    e.preventDefault();
    copiedTagId = selectedTagId;

    // Visual feedback
    const container = document.querySelector('.tag-hierarchy-container');
    container?.querySelectorAll('.th-node.th-copied').forEach(n => {
      n.classList.remove('th-copied');
    });
    container?.querySelectorAll(`.th-node[data-tag-id="${copiedTagId}"]`).forEach(n => {
      n.classList.add('th-copied');
    });

    showToast('Tag copied - select target and press Ctrl+V to add as child');
  }

  // Ctrl+V - paste (add copied tag as child of selected)
  if (e.ctrlKey && e.key === 'v' && copiedTagId && selectedTagId && copiedTagId !== selectedTagId) {
    e.preventDefault();

    if (wouldCreateCircularRef(selectedTagId, copiedTagId)) {
      showToast('Cannot create circular reference', 'error');
      return;
    }

    addParent(copiedTagId, selectedTagId);
  }

  // Delete/Backspace - remove selected tag from its current parent
  if ((e.key === 'Delete' || e.key === 'Backspace') && selectedTagId) {
    // Don't handle if typing in an input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    e.preventDefault();
    const selectedNode = document.querySelector(`.th-node.th-selected[data-tag-id="${selectedTagId}"]`);
    const parentId = selectedNode?.closest('.th-children')?.dataset.parentId;

    if (parentId) {
      removeParent(selectedTagId, parentId);
    } else {
      showToast('Tag is already a root');
    }
  }

  // Escape - clear selection
  if (e.key === 'Escape') {
    selectedTagId = null;
    copiedTagId = null;
    const container = document.querySelector('.tag-hierarchy-container');
    container?.querySelectorAll('.th-node.th-selected, .th-node.th-copied').forEach(n => {
      n.classList.remove('th-selected', 'th-copied');
    });
  }
}

// Register keyboard handler globally
document.addEventListener('keydown', handleHierarchyKeyboard);
```

**Step 5: Test keyboard shortcuts**

Test manually:
1. Click a tag to select it (should show outline)
2. Press Ctrl+C (should show "copied" state)
3. Click another tag, press Ctrl+V (should add as child)
4. Select a tag under a parent, press Delete (should remove from parent)
5. Press Escape (should clear selection)

**Step 6: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add keyboard shortcuts for hierarchy editing"
```

---

## Task 9: Add Toast Notification System

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Step 1: Add toast CSS**

```css
/* Toast notifications */
.th-toast-container {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 10002;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.th-toast {
  padding: 12px 16px;
  border-radius: 4px;
  background: var(--bs-body-bg, #1a1d1e);
  border: 1px solid var(--bs-border-color, #444);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  animation: th-toast-in 0.3s ease;
  max-width: 350px;
}

.th-toast.success {
  border-color: var(--bs-success, #198754);
  background: rgba(25, 135, 84, 0.1);
}

.th-toast.error {
  border-color: var(--bs-danger, #dc3545);
  background: rgba(220, 53, 69, 0.1);
}

@keyframes th-toast-in {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes th-toast-out {
  from {
    opacity: 1;
    transform: translateY(0);
  }
  to {
    opacity: 0;
    transform: translateY(-20px);
  }
}
```

**Step 2: Add showToast function**

```javascript
function showToast(message, type = 'success') {
  // Create container if needed
  let container = document.querySelector('.th-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'th-toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `th-toast ${type}`;
  toast.textContent = message;

  container.appendChild(toast);

  // Auto-remove after 3 seconds
  setTimeout(() => {
    toast.style.animation = 'th-toast-out 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
```

**Step 3: Verify toast is working**

This function was already called in earlier tasks. Verify it displays properly.

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add toast notification system"
```

---

## Task 10: Fix Tree Node Parent Context

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`

**Step 1: Update buildTagTree to track parent context**

The current tree structure doesn't track which parent a node is shown under (for multi-parent tags). We need to add this for the context menu to know which parent to remove.

Update `buildTagTree()`:

```javascript
function buildTagTree(tags) {
  const tagMap = new Map();
  tags.forEach(tag => {
    tagMap.set(tag.id, {
      ...tag,
      childNodes: []
    });
  });

  const roots = [];

  tags.forEach(tag => {
    const node = tagMap.get(tag.id);

    if (tag.parents.length === 0) {
      roots.push({ ...node, parentContextId: null });
    } else {
      tag.parents.forEach(parent => {
        const parentNode = tagMap.get(parent.id);
        if (parentNode) {
          // Create a copy with parent context
          parentNode.childNodes.push({ ...node, parentContextId: parent.id });
        }
      });
    }
  });

  // Sort
  const sortByName = (a, b) => a.name.localeCompare(b.name);
  roots.sort(sortByName);

  function sortChildren(node) {
    if (node.childNodes.length > 0) {
      node.childNodes.sort(sortByName);
      node.childNodes.forEach(sortChildren);
    }
  }
  roots.forEach(sortChildren);

  return roots;
}
```

**Step 2: Update renderTreeNode to use parentContextId**

```javascript
function renderTreeNode(node, isRoot = false) {
  // ... existing code ...

  // Add parent context for context menu
  const parentAttr = node.parentContextId ? `data-parent-context="${node.parentContextId}"` : '';

  return `
    <div class="th-node ${isRoot ? 'th-root' : ''}" data-tag-id="${node.id}" ${parentAttr} draggable="true">
```

**Step 3: Update context menu to use parentContextId**

Update the contextmenu handler:

```javascript
node.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  const tagId = node.dataset.tagId;
  const parentId = node.dataset.parentContext || null;
  showContextMenu(e.clientX, e.clientY, tagId, parentId);
});
```

**Step 4: Test parent context tracking**

Test:
1. Create tag C with parents A and B
2. Right-click C under A - should show "Remove from A"
3. Right-click C under B - should show "Remove from B"

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): track parent context in tree nodes for correct removal"
```

---

## Task 11: Integration Testing and Final Cleanup

**Files:**
- Review: `plugins/tagManager/tag-manager.js`
- Review: `plugins/tagManager/tag-manager.css`

**Step 1: Full functionality test**

Test all features end-to-end:
1. Navigate to Tag Hierarchy page
2. Test context menu operations:
   - Add parent via search
   - Add child via search
   - Remove from parent
   - Make root
3. Test drag-and-drop:
   - Drag tag onto another to add as child
   - Drag to root zone to remove parent
   - Verify circular reference prevention
4. Test keyboard shortcuts:
   - Click to select
   - Ctrl+C to copy
   - Ctrl+V to paste as child
   - Delete to remove from parent
   - Escape to clear selection
5. Test multi-parent indicators:
   - Create tag with multiple parents
   - Verify badge shows
   - Verify hover highlights all instances
6. Test toast notifications appear correctly

**Step 2: Clean up any console.log statements**

Review code for debug logs that should be removed or converted to console.debug.

**Step 3: Final commit if needed**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "chore(tagManager): cleanup and polish hierarchy editing"
```

**Step 4: Create PR**

```bash
gh pr create --base feature/tag-manager-backlog --title "feat(tagManager): P5 - Tag hierarchy editing" --body "$(cat <<'EOF'
## Summary

Adds editing capabilities to the Tag Hierarchy view:

- **Context menu** (right-click): Add parent, Add child, Remove from parent, Make root
- **Drag-and-drop**: Drag tag onto another to add as child, drag to root zone to remove parent
- **Keyboard shortcuts**: Ctrl+C/V for copy-paste hierarchy, Delete to remove from parent
- **Multi-parent indicators**: Badge showing parent count, hover highlights all instances
- **Circular reference prevention**: Visual feedback and rejection of invalid operations

## Test Plan

- [ ] Context menu appears on right-click with correct options
- [ ] Add parent/child search dialog works
- [ ] Remove from parent removes only that relationship
- [ ] Make root removes all parents
- [ ] Drag-and-drop adds child relationship
- [ ] Drag to root zone removes from dragged-from parent
- [ ] Circular references are prevented with visual feedback
- [ ] Multi-parent badge shows correct count
- [ ] Hover highlights all instances of multi-parent tag
- [ ] Keyboard shortcuts work (Ctrl+C/V, Delete, Escape)
- [ ] Toast notifications appear for all operations

## Integration Notes

This PR is independent of P2-P4 branches. When merged to `feature/tag-manager-backlog`:
- Works with existing hierarchy view structure
- Uses existing `updateTag` GraphQL mutation
- No conflicts expected with other P branches

EOF
)"
```

---

## Summary

This plan implements tag hierarchy editing with:

| Feature | Implementation |
|---------|---------------|
| Context menu | Right-click menu with Add parent/child, Remove from parent, Make root |
| Drag-and-drop | Drag onto tag = add as child, drag to root zone = remove parent |
| Keyboard | Ctrl+C/V copy-paste, Delete remove from parent, Escape clear |
| Multi-parent indicators | Badge + hover highlighting |
| Circular prevention | Check ancestry, show visual feedback, reject invalid ops |

All changes are in `plugins/tagManager/tag-manager.js` and `plugins/tagManager/tag-manager.css`.
