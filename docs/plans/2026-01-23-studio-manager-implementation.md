# Studio Manager Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a Studio Manager plugin with a Studio Hierarchy page for visualizing and editing parent-child studio relationships.

**Architecture:** Pure frontend plugin (no Python backend). Single JavaScript file handles route registration, button injection, GraphQL queries, tree rendering, and edit operations. CSS adapted from Tag Manager with renamed classes.

**Tech Stack:** Vanilla JavaScript, Stash PluginApi (React, routes), GraphQL, CSS

---

## Task 1: Create Plugin Manifest

**Files:**
- Create: `plugins/studioManager/studioManager.yml`

**Step 1: Create the plugin manifest file**

```yaml
name: Studio Manager
description: Manage studio hierarchy with visual tree editing. View and edit parent-child studio relationships.
version: 0.1.0
url: https://github.com/carrotwaxr/stash-plugins

ui:
  javascript:
    - studio-manager.js
  css:
    - studio-manager.css
```

**Step 2: Verify file exists**

Run: `cat plugins/studioManager/studioManager.yml`
Expected: File contents displayed

**Step 3: Commit**

```bash
git add plugins/studioManager/studioManager.yml
git commit -m "feat(studioManager): add plugin manifest"
```

---

## Task 2: Create CSS Stylesheet (Adapted from Tag Manager)

**Files:**
- Create: `plugins/studioManager/studio-manager.css`
- Reference: `plugins/tagManager/tag-manager.css` (lines 604-1455 for `.th-*` classes)

**Step 1: Create CSS file with hierarchy styles**

Adapt Tag Manager's `.th-*` classes to `.sh-*` (studio hierarchy). Include:
- Container and header styles (`.studio-hierarchy-container`, `.studio-hierarchy`, `.studio-hierarchy-header`)
- Tree structure (`.sh-tree`, `.sh-node`, `.sh-node-content`, `.sh-children`)
- Node elements (`.sh-toggle`, `.sh-image`, `.sh-info`, `.sh-name`, `.sh-meta`)
- Interactive states (`.sh-selected`, `.sh-highlighted`, `.dragging`, `.drag-over`, `.drag-invalid`)
- Context menu (`.sh-context-menu`, `.sh-context-menu-item`)
- Pending changes panel (`.sh-changes-panel`, `.sh-changes-list`, `.sh-change-item`)
- Toast notifications (`.sh-toast-container`, `.sh-toast`)
- Root drop zone (`.sh-root-drop-zone`)
- Stats display (`.sh-stats`)

**Step 2: Verify CSS file exists and has expected classes**

Run: `grep -c '\.sh-' plugins/studioManager/studio-manager.css`
Expected: Output shows count of `.sh-` class definitions (should be 40+)

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.css
git commit -m "feat(studioManager): add hierarchy page styles"
```

---

## Task 3: Create JavaScript - Core Structure and Utilities

**Files:**
- Create: `plugins/studioManager/studio-manager.js`

**Step 1: Create JS file with IIFE wrapper, constants, and utility functions**

```javascript
(function () {
  "use strict";

  const PLUGIN_ID = "studioManager";
  const HIERARCHY_ROUTE_PATH = "/plugins/studio-hierarchy";

  // State
  let hierarchyStudios = [];  // All studios from API
  let hierarchyTree = [];     // Tree structure for rendering
  let hierarchyStats = {};    // Computed statistics
  let expandedNodes = new Set();
  let showImages = true;
  let selectedStudioId = null;
  let pendingChanges = [];
  let isEditMode = false;
  let originalParentMap = new Map();

  // Drag state
  let draggedStudioId = null;

  // Context menu state
  let contextMenuStudioId = null;

  /**
   * Set page title with retry to overcome Stash's title management
   */
  function setPageTitle(title) {
    const doSet = () => { document.title = title; };
    doSet();
    setTimeout(doSet, 50);
    setTimeout(doSet, 200);
    setTimeout(doSet, 500);
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /**
   * Get the GraphQL endpoint URL for local Stash
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request to local Stash
   */
  async function graphqlRequest(query, variables = {}) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });

    if (!response.ok) {
      throw new Error(`GraphQL request failed: ${response.status}`);
    }

    const result = await response.json();
    if (result.errors?.length > 0) {
      throw new Error(result.errors[0].message);
    }

    return result.data;
  }

  // ... rest of implementation in subsequent tasks

  console.log('[studioManager] Plugin loaded');
})();
```

**Step 2: Verify file structure**

Run: `head -50 plugins/studioManager/studio-manager.js`
Expected: Shows IIFE wrapper, constants, and utility functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add core JS structure and utilities"
```

---

## Task 4: Add GraphQL Functions

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add GraphQL query and mutation functions**

Add after the utility functions, before the closing of the IIFE:

```javascript
  /**
   * Fetch all studios with hierarchy information
   */
  async function fetchAllStudiosWithHierarchy() {
    const query = `
      query FindStudios {
        findStudios(filter: { per_page: -1 }) {
          count
          studios {
            id
            name
            image_path
            scene_count
            image_count
            gallery_count
            parent_studio {
              id
            }
            child_studios {
              id
            }
          }
        }
      }
    `;

    const result = await graphqlRequest(query);
    return result?.findStudios?.studios || [];
  }

  /**
   * Update a studio's parent
   */
  async function updateStudioParent(studioId, parentId) {
    const query = `
      mutation StudioUpdate($input: StudioUpdateInput!) {
        studioUpdate(input: $input) {
          id
          name
          parent_studio {
            id
            name
          }
        }
      }
    `;

    const result = await graphqlRequest(query, {
      input: {
        id: studioId,
        parent_id: parentId
      }
    });

    return result?.studioUpdate;
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "async function fetch\|async function update" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for fetchAllStudiosWithHierarchy and updateStudioParent

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add GraphQL query and mutation functions"
```

---

## Task 5: Add Tree Building and Statistics Functions

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add tree building functions**

Add after GraphQL functions:

```javascript
  /**
   * Build tree structure from flat studio list
   * Simpler than Tag Manager since studios have single parent
   */
  function buildStudioTree(studios) {
    const studioMap = new Map();

    // First pass: create nodes
    studios.forEach(studio => {
      studioMap.set(studio.id, {
        ...studio,
        childNodes: []
      });
    });

    // Second pass: build parent-child relationships
    const roots = [];
    studios.forEach(studio => {
      const node = studioMap.get(studio.id);
      const parentId = studio.parent_studio?.id;

      if (!parentId) {
        // Root studio
        roots.push(node);
      } else {
        // Add to parent's children
        const parentNode = studioMap.get(parentId);
        if (parentNode) {
          parentNode.childNodes.push(node);
        } else {
          // Parent not found, treat as root
          roots.push(node);
        }
      }
    });

    // Sort roots and children by name
    const sortByName = (a, b) => a.name.localeCompare(b.name);
    roots.sort(sortByName);

    function sortChildren(node) {
      node.childNodes.sort(sortByName);
      node.childNodes.forEach(sortChildren);
    }
    roots.forEach(sortChildren);

    return roots;
  }

  /**
   * Calculate hierarchy statistics
   */
  function getTreeStats(studios) {
    const rootCount = studios.filter(s => !s.parent_studio?.id).length;
    const withChildren = studios.filter(s => s.child_studios?.length > 0).length;
    const withParent = studios.filter(s => s.parent_studio?.id).length;

    // Calculate max depth
    const studioMap = new Map(studios.map(s => [s.id, s]));
    let maxDepth = 0;

    function getDepth(studioId, seen = new Set()) {
      if (seen.has(studioId)) return 0; // Prevent infinite loop
      seen.add(studioId);

      const studio = studioMap.get(studioId);
      if (!studio?.parent_studio?.id) return 0;
      return 1 + getDepth(studio.parent_studio.id, seen);
    }

    studios.forEach(s => {
      const depth = getDepth(s.id);
      if (depth > maxDepth) maxDepth = depth;
    });

    return {
      totalStudios: studios.length,
      rootStudios: rootCount,
      studiosWithChildren: withChildren,
      studiosWithParent: withParent,
      maxDepth: maxDepth
    };
  }

  /**
   * Check if adding parentId as parent of studioId would create a cycle
   */
  function wouldCreateCircularRef(potentialParentId, studioId) {
    if (potentialParentId === studioId) return true;

    // Build effective parent map considering pending changes
    const effectiveParent = new Map();

    for (const studio of hierarchyStudios) {
      effectiveParent.set(studio.id, studio.parent_studio?.id || null);
    }

    // Apply pending changes
    for (const change of pendingChanges) {
      if (change.type === 'set-parent') {
        effectiveParent.set(change.studioId, change.parentId);
      } else if (change.type === 'remove-parent') {
        effectiveParent.set(change.studioId, null);
      }
    }

    // Walk up from potentialParentId, check if we hit studioId
    let current = potentialParentId;
    const visited = new Set();

    while (current) {
      if (current === studioId) return true;
      if (visited.has(current)) break; // Existing cycle, stop
      visited.add(current);
      current = effectiveParent.get(current);
    }

    return false;
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function buildStudioTree\|function getTreeStats\|function wouldCreateCircularRef" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for all three functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add tree building and statistics functions"
```

---

## Task 6: Add Toast and Context Menu Functions

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add UI helper functions**

Add after tree functions:

```javascript
  /**
   * Show a toast notification
   */
  function showToast(message, type = 'info', duration = 3000) {
    let container = document.querySelector('.sh-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'sh-toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `sh-toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  /**
   * Hide context menu
   */
  function hideContextMenu() {
    const menu = document.querySelector('.sh-context-menu');
    if (menu) menu.remove();
    contextMenuStudioId = null;
  }

  /**
   * Show context menu at position
   */
  function showContextMenu(x, y, studioId) {
    hideContextMenu();
    contextMenuStudioId = studioId;

    const studio = hierarchyStudios.find(s => s.id === studioId);
    if (!studio) return;

    const hasParent = !!studio.parent_studio?.id;
    const hasChildren = studio.child_studios?.length > 0;

    const menu = document.createElement('div');
    menu.className = 'sh-context-menu';
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    menu.innerHTML = `
      <div class="sh-context-menu-item" data-action="view">View Studio</div>
      <div class="sh-context-menu-item" data-action="edit">Edit Studio</div>
      <div class="sh-context-menu-separator"></div>
      <div class="sh-context-menu-item ${hasParent ? '' : 'disabled'}" data-action="remove-parent">Remove Parent</div>
      ${hasChildren ? `
        <div class="sh-context-menu-separator"></div>
        <div class="sh-context-menu-item" data-action="expand-children">Expand All Children</div>
        <div class="sh-context-menu-item" data-action="collapse-children">Collapse All Children</div>
      ` : ''}
    `;

    document.body.appendChild(menu);

    // Adjust position if menu goes off screen
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
      menu.style.left = `${window.innerWidth - rect.width - 10}px`;
    }
    if (rect.bottom > window.innerHeight) {
      menu.style.top = `${window.innerHeight - rect.height - 10}px`;
    }

    // Handle menu clicks
    menu.addEventListener('click', (e) => {
      const action = e.target.dataset.action;
      if (!action || e.target.classList.contains('disabled')) return;

      switch (action) {
        case 'view':
          window.location.href = `/studios/${studioId}`;
          break;
        case 'edit':
          window.location.href = `/studios/${studioId}/edit`;
          break;
        case 'remove-parent':
          removeParent(studioId);
          break;
        case 'expand-children':
          expandAllChildren(studioId);
          break;
        case 'collapse-children':
          collapseAllChildren(studioId);
          break;
      }
      hideContextMenu();
    });

    // Close on click outside
    setTimeout(() => {
      document.addEventListener('click', hideContextMenu, { once: true });
    }, 0);
  }

  /**
   * Expand all children of a node
   */
  function expandAllChildren(studioId) {
    const container = document.querySelector('.studio-hierarchy-container');
    if (!container) return;

    function expandNode(id) {
      expandedNodes.add(id);
      const childContainer = container.querySelector(`.sh-children[data-parent-id="${id}"]`);
      if (childContainer) {
        childContainer.classList.add('sh-expanded');
        const toggle = container.querySelector(`.sh-toggle[data-studio-id="${id}"]`);
        if (toggle) toggle.innerHTML = '&#9660;';

        // Recursively expand children
        childContainer.querySelectorAll(':scope > .sh-node').forEach(node => {
          const childId = node.dataset.studioId;
          if (childId) expandNode(childId);
        });
      }
    }

    expandNode(studioId);
  }

  /**
   * Collapse all children of a node
   */
  function collapseAllChildren(studioId) {
    const container = document.querySelector('.studio-hierarchy-container');
    if (!container) return;

    function collapseNode(id) {
      expandedNodes.delete(id);
      const childContainer = container.querySelector(`.sh-children[data-parent-id="${id}"]`);
      if (childContainer) {
        childContainer.classList.remove('sh-expanded');
        const toggle = container.querySelector(`.sh-toggle[data-studio-id="${id}"]`);
        if (toggle) toggle.innerHTML = '&#9654;';

        // Recursively collapse children
        childContainer.querySelectorAll(':scope > .sh-node').forEach(node => {
          const childId = node.dataset.studioId;
          if (childId) collapseNode(childId);
        });
      }
    }

    collapseNode(studioId);
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function showToast\|function showContextMenu\|function hideContextMenu" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for all three functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add toast and context menu functions"
```

---

## Task 7: Add Pending Changes Management

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add change tracking functions**

Add after context menu functions:

```javascript
  /**
   * Enter edit mode - snapshot current state
   */
  function enterEditMode() {
    if (isEditMode) return;

    isEditMode = true;
    pendingChanges = [];

    // Snapshot current parent relationships
    originalParentMap.clear();
    for (const studio of hierarchyStudios) {
      originalParentMap.set(studio.id, studio.parent_studio?.id || null);
    }
  }

  /**
   * Add a pending change
   */
  function addPendingChange(type, studioId, studioName, parentId, parentName) {
    enterEditMode();

    // Check if this change cancels out an existing one
    const existingIndex = pendingChanges.findIndex(c =>
      c.studioId === studioId &&
      ((c.type === 'set-parent' && type === 'remove-parent') ||
       (c.type === 'remove-parent' && type === 'set-parent'))
    );

    if (existingIndex >= 0) {
      // Check if this returns to original state
      const originalParent = originalParentMap.get(studioId);
      if ((type === 'set-parent' && parentId === originalParent) ||
          (type === 'remove-parent' && originalParent === null)) {
        pendingChanges.splice(existingIndex, 1);
        renderChangesPanel();
        return;
      }
    }

    // Remove any existing change for this studio
    const existingChangeIndex = pendingChanges.findIndex(c => c.studioId === studioId);
    if (existingChangeIndex >= 0) {
      pendingChanges.splice(existingChangeIndex, 1);
    }

    // Check if this is actually a change from original
    const originalParent = originalParentMap.get(studioId);
    if (type === 'set-parent' && parentId === originalParent) {
      renderChangesPanel();
      return; // No actual change
    }
    if (type === 'remove-parent' && originalParent === null) {
      renderChangesPanel();
      return; // Already a root
    }

    pendingChanges.push({
      type,
      studioId,
      studioName,
      parentId,
      parentName
    });

    renderChangesPanel();
  }

  /**
   * Remove a pending change by index
   */
  function removePendingChange(index) {
    pendingChanges.splice(index, 1);
    if (pendingChanges.length === 0) {
      isEditMode = false;
      originalParentMap.clear();
    }
    renderChangesPanel();

    // Re-render tree to reflect removed change
    const container = document.querySelector('.studio-hierarchy-container');
    if (container) {
      reloadHierarchy(container);
    }
  }

  /**
   * Render the pending changes panel
   */
  function renderChangesPanel() {
    let panel = document.querySelector('.sh-changes-panel');

    if (pendingChanges.length === 0) {
      if (panel) panel.remove();
      return;
    }

    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'sh-changes-panel';
      document.body.appendChild(panel);
    }

    const changesHtml = pendingChanges.map((change, index) => {
      const text = change.type === 'set-parent'
        ? `Set "${escapeHtml(change.studioName)}" parent to "${escapeHtml(change.parentName)}"`
        : `Remove parent from "${escapeHtml(change.studioName)}"`;

      return `
        <div class="sh-change-item">
          <span class="sh-change-text">${text}</span>
          <button class="sh-change-remove" data-index="${index}">&times;</button>
        </div>
      `;
    }).join('');

    panel.innerHTML = `
      <div class="sh-changes-header">
        <strong>${pendingChanges.length} pending change${pendingChanges.length !== 1 ? 's' : ''}</strong>
      </div>
      <div class="sh-changes-list">
        ${changesHtml}
      </div>
      <div class="sh-changes-actions">
        <button class="btn btn-secondary" id="sh-cancel-changes">Cancel</button>
        <button class="btn btn-primary" id="sh-save-changes">Save Changes</button>
      </div>
    `;

    // Attach handlers
    panel.querySelectorAll('.sh-change-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        removePendingChange(parseInt(btn.dataset.index));
      });
    });

    panel.querySelector('#sh-cancel-changes')?.addEventListener('click', () => {
      pendingChanges = [];
      isEditMode = false;
      originalParentMap.clear();
      renderChangesPanel();

      const container = document.querySelector('.studio-hierarchy-container');
      if (container) {
        reloadHierarchy(container);
      }
    });

    panel.querySelector('#sh-save-changes')?.addEventListener('click', savePendingChanges);
  }

  /**
   * Save all pending changes to server
   */
  async function savePendingChanges() {
    if (pendingChanges.length === 0) return;

    const saveBtn = document.querySelector('#sh-save-changes');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';
    }

    const errors = [];

    for (const change of pendingChanges) {
      try {
        const parentId = change.type === 'set-parent' ? change.parentId : null;
        await updateStudioParent(change.studioId, parentId);
      } catch (err) {
        errors.push(`Failed to update "${change.studioName}": ${err.message}`);
      }
    }

    if (errors.length > 0) {
      showToast(`Some changes failed:\n${errors.join('\n')}`, 'error', 5000);
    } else {
      showToast(`Saved ${pendingChanges.length} change${pendingChanges.length !== 1 ? 's' : ''}`, 'success');
    }

    // Reset state
    pendingChanges = [];
    isEditMode = false;
    originalParentMap.clear();
    renderChangesPanel();

    // Reload data
    const container = document.querySelector('.studio-hierarchy-container');
    if (container) {
      reloadHierarchy(container);
    }
  }

  /**
   * Reload hierarchy data and re-render
   */
  async function reloadHierarchy(container) {
    try {
      hierarchyStudios = await fetchAllStudiosWithHierarchy();
      hierarchyTree = buildStudioTree(hierarchyStudios);
      hierarchyStats = getTreeStats(hierarchyStudios);
      renderHierarchyPage(container);
    } catch (e) {
      console.error('[studioManager] Failed to reload hierarchy:', e);
      showToast('Failed to reload hierarchy', 'error');
    }
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function enterEditMode\|function addPendingChange\|function savePendingChanges" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for all three functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add pending changes management"
```

---

## Task 8: Add Parent Modification Functions

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add setParent and removeParent functions**

Add after pending changes functions:

```javascript
  /**
   * Set a studio's parent (queue as pending change)
   */
  function setParent(studioId, newParentId) {
    if (studioId === newParentId) {
      showToast('Cannot set studio as its own parent', 'error');
      return;
    }

    if (wouldCreateCircularRef(newParentId, studioId)) {
      showToast('Cannot create circular reference', 'error');
      return;
    }

    const studio = hierarchyStudios.find(s => s.id === studioId);
    const parent = hierarchyStudios.find(s => s.id === newParentId);

    if (!studio || !parent) {
      showToast('Studio not found', 'error');
      return;
    }

    addPendingChange('set-parent', studioId, studio.name, newParentId, parent.name);
    showToast(`Will set "${studio.name}" parent to "${parent.name}"`);

    // Update local state for immediate visual feedback
    studio.parent_studio = { id: newParentId };
    hierarchyTree = buildStudioTree(hierarchyStudios);

    const container = document.querySelector('.studio-hierarchy-container');
    if (container) {
      renderHierarchyPage(container);
    }
  }

  /**
   * Remove a studio's parent (make it a root studio)
   */
  function removeParent(studioId) {
    const studio = hierarchyStudios.find(s => s.id === studioId);

    if (!studio) {
      showToast('Studio not found', 'error');
      return;
    }

    if (!studio.parent_studio?.id) {
      showToast('Studio is already a root', 'error');
      return;
    }

    addPendingChange('remove-parent', studioId, studio.name, null, null);
    showToast(`Will remove parent from "${studio.name}"`);

    // Update local state for immediate visual feedback
    studio.parent_studio = null;
    hierarchyTree = buildStudioTree(hierarchyStudios);

    const container = document.querySelector('.studio-hierarchy-container');
    if (container) {
      renderHierarchyPage(container);
    }
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function setParent\|function removeParent" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for both functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add parent modification functions"
```

---

## Task 9: Add Tree Rendering Functions

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add tree node rendering function**

Add after parent modification functions:

```javascript
  /**
   * Render a single tree node (recursive)
   */
  function renderTreeNode(node, isRoot = false) {
    const hasChildren = node.childNodes.length > 0;
    const isExpanded = expandedNodes.has(node.id);

    // Build metadata
    const metaParts = [];
    if (node.scene_count > 0) {
      metaParts.push(`${node.scene_count} scene${node.scene_count !== 1 ? 's' : ''}`);
    }
    if (node.image_count > 0) {
      metaParts.push(`${node.image_count} image${node.image_count !== 1 ? 's' : ''}`);
    }
    if (node.gallery_count > 0) {
      metaParts.push(`${node.gallery_count} galler${node.gallery_count !== 1 ? 'ies' : 'y'}`);
    }
    if (node.childNodes.length > 0) {
      metaParts.push(`${node.childNodes.length} sub-studio${node.childNodes.length !== 1 ? 's' : ''}`);
    }
    const metaText = metaParts.length > 0 ? metaParts.join(', ') : '';

    // Image
    const imageHtml = node.image_path
      ? `<div class="sh-image ${showImages ? '' : 'sh-hidden'}">
           <img src="${escapeHtml(node.image_path)}" alt="${escapeHtml(node.name)}" loading="lazy">
         </div>`
      : `<div class="sh-image-placeholder ${showImages ? '' : 'sh-hidden'}">
           <span>?</span>
         </div>`;

    // Children (recursive)
    let childrenHtml = '';
    if (hasChildren) {
      const childNodes = node.childNodes.map(child => renderTreeNode(child, false)).join('');
      childrenHtml = `<div class="sh-children ${isExpanded ? 'sh-expanded' : ''}" data-parent-id="${node.id}">${childNodes}</div>`;
    }

    // Toggle icon
    const toggleIcon = hasChildren
      ? (isExpanded ? '&#9660;' : '&#9654;')
      : '';

    return `
      <div class="sh-node ${isRoot ? 'sh-root' : ''}" data-studio-id="${node.id}" draggable="true">
        <div class="sh-node-content">
          <span class="sh-toggle ${hasChildren ? '' : 'sh-leaf'}" data-studio-id="${node.id}">${toggleIcon}</span>
          ${imageHtml}
          <div class="sh-info">
            <a class="sh-name" href="/studios/${node.id}">${escapeHtml(node.name)}</a>
            <div class="sh-meta">${metaText}</div>
          </div>
        </div>
        ${childrenHtml}
      </div>
    `;
  }

  /**
   * Render the full hierarchy page
   */
  function renderHierarchyPage(container) {
    const treeHtml = hierarchyTree.map(root => renderTreeNode(root, true)).join('');

    container.innerHTML = `
      <div class="studio-hierarchy">
        <div class="studio-hierarchy-header">
          <h2>Studio Hierarchy</h2>
          <div class="studio-hierarchy-controls">
            <button id="sh-expand-all" class="btn btn-secondary">Expand All</button>
            <button id="sh-collapse-all" class="btn btn-secondary">Collapse All</button>
            <label>
              <input type="checkbox" id="sh-show-images" ${showImages ? 'checked' : ''}>
              Show images
            </label>
          </div>
        </div>
        <div class="sh-stats">
          <span class="stat"><strong>${hierarchyStats.totalStudios}</strong> total studios</span>
          <span class="stat"><strong>${hierarchyStats.rootStudios}</strong> root studios</span>
          <span class="stat"><strong>${hierarchyStats.studiosWithChildren}</strong> with sub-studios</span>
          <span class="stat"><strong>${hierarchyStats.maxDepth}</strong> max depth</span>
        </div>
        <div class="sh-root-drop-zone" id="sh-root-drop-zone">
          Drop here to make root studio
        </div>
        <div class="sh-tree">
          ${treeHtml || '<div class="sh-empty">No studios found</div>'}
        </div>
      </div>
    `;

    // Attach event handlers
    attachHierarchyEventHandlers(container);
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function renderTreeNode\|function renderHierarchyPage" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for both functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add tree rendering functions"
```

---

## Task 10: Add Event Handlers

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add event handler attachment function**

Add after rendering functions:

```javascript
  /**
   * Attach event handlers for hierarchy page
   */
  function attachHierarchyEventHandlers(container) {
    // Toggle expand/collapse on arrow click
    container.querySelectorAll('.sh-toggle').forEach(toggle => {
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const studioId = e.target.dataset.studioId;
        if (!studioId) return;

        const childrenContainer = container.querySelector(`.sh-children[data-parent-id="${studioId}"]`);
        if (!childrenContainer) return;

        if (expandedNodes.has(studioId)) {
          expandedNodes.delete(studioId);
          childrenContainer.classList.remove('sh-expanded');
          e.target.innerHTML = '&#9654;';
        } else {
          expandedNodes.add(studioId);
          childrenContainer.classList.add('sh-expanded');
          e.target.innerHTML = '&#9660;';
        }
      });
    });

    // Expand All button
    container.querySelector('#sh-expand-all')?.addEventListener('click', () => {
      container.querySelectorAll('.sh-children').forEach(el => {
        el.classList.add('sh-expanded');
        const parentId = el.dataset.parentId;
        if (parentId) expandedNodes.add(parentId);
      });
      container.querySelectorAll('.sh-toggle:not(.sh-leaf)').forEach(el => {
        el.innerHTML = '&#9660;';
      });
    });

    // Collapse All button
    container.querySelector('#sh-collapse-all')?.addEventListener('click', () => {
      container.querySelectorAll('.sh-children').forEach(el => {
        el.classList.remove('sh-expanded');
        const parentId = el.dataset.parentId;
        if (parentId) expandedNodes.delete(parentId);
      });
      container.querySelectorAll('.sh-toggle:not(.sh-leaf)').forEach(el => {
        el.innerHTML = '&#9654;';
      });
    });

    // Show/hide images toggle
    container.querySelector('#sh-show-images')?.addEventListener('change', (e) => {
      showImages = e.target.checked;
      container.querySelectorAll('.sh-image, .sh-image-placeholder').forEach(el => {
        el.classList.toggle('sh-hidden', !showImages);
      });
    });

    // Node selection and context menu
    container.querySelectorAll('.sh-node').forEach(node => {
      // Click to select
      node.addEventListener('click', (e) => {
        if (e.target.closest('.sh-toggle') || e.target.closest('.sh-name')) return;
        e.stopPropagation();

        // Clear previous selection
        container.querySelectorAll('.sh-node.sh-selected').forEach(n => {
          n.classList.remove('sh-selected');
        });

        // Select this node
        node.classList.add('sh-selected');
        selectedStudioId = node.dataset.studioId;
      });

      // Right-click for context menu
      node.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showContextMenu(e.clientX, e.clientY, node.dataset.studioId);
      });

      // Drag start
      node.addEventListener('dragstart', (e) => {
        e.stopPropagation();
        draggedStudioId = node.dataset.studioId;
        node.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedStudioId);
      });

      // Drag end
      node.addEventListener('dragend', () => {
        node.classList.remove('dragging');
        draggedStudioId = null;
        container.querySelectorAll('.drag-over, .drag-invalid').forEach(n => {
          n.classList.remove('drag-over', 'drag-invalid');
        });
      });

      // Drag over
      node.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!draggedStudioId || node.dataset.studioId === draggedStudioId) return;

        const targetId = node.dataset.studioId;
        const wouldCircle = wouldCreateCircularRef(targetId, draggedStudioId);

        node.classList.remove('drag-over', 'drag-invalid');
        node.classList.add(wouldCircle ? 'drag-invalid' : 'drag-over');
      });

      // Drag leave
      node.addEventListener('dragleave', (e) => {
        e.stopPropagation();
        node.classList.remove('drag-over', 'drag-invalid');
      });

      // Drop
      node.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.remove('drag-over', 'drag-invalid');

        if (!draggedStudioId || node.dataset.studioId === draggedStudioId) return;

        const targetId = node.dataset.studioId;
        if (wouldCreateCircularRef(targetId, draggedStudioId)) {
          showToast('Cannot create circular reference', 'error');
          return;
        }

        setParent(draggedStudioId, targetId);
      });
    });

    // Root drop zone
    const rootDropZone = container.querySelector('#sh-root-drop-zone');
    if (rootDropZone) {
      rootDropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (draggedStudioId) {
          rootDropZone.classList.add('drag-over');
        }
      });

      rootDropZone.addEventListener('dragleave', () => {
        rootDropZone.classList.remove('drag-over');
      });

      rootDropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        rootDropZone.classList.remove('drag-over');

        if (draggedStudioId) {
          removeParent(draggedStudioId);
        }
      });
    }

    // Clear selection when clicking outside
    container.addEventListener('click', (e) => {
      if (!e.target.closest('.sh-node')) {
        container.querySelectorAll('.sh-node.sh-selected').forEach(n => {
          n.classList.remove('sh-selected');
        });
        selectedStudioId = null;
      }
    });
  }

  /**
   * Keyboard handler
   */
  function handleHierarchyKeyboard(e) {
    if (!document.querySelector('.studio-hierarchy-container')) return;

    // Delete - remove parent from selected studio
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedStudioId) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      e.preventDefault();
      removeParent(selectedStudioId);
    }

    // Escape - clear selection
    if (e.key === 'Escape') {
      selectedStudioId = null;
      hideContextMenu();
      const container = document.querySelector('.studio-hierarchy-container');
      container?.querySelectorAll('.sh-node.sh-selected').forEach(n => {
        n.classList.remove('sh-selected');
      });
    }
  }
```

**Step 2: Verify function exists**

Run: `grep -n "function attachHierarchyEventHandlers\|function handleHierarchyKeyboard" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for both functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add event handlers for hierarchy page"
```

---

## Task 11: Add React Component and Route Registration

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add React component and route registration**

Add after event handlers, before the final console.log:

```javascript
  /**
   * Studio Hierarchy Page React component
   */
  function StudioHierarchyPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);

    React.useEffect(() => {
      document.addEventListener('keydown', handleHierarchyKeyboard);

      async function init() {
        if (!containerRef.current) return;

        setPageTitle("Studio Hierarchy | Stash");
        containerRef.current.innerHTML = '<div class="studio-hierarchy"><div class="sh-loading">Loading studios...</div></div>';

        try {
          hierarchyStudios = await fetchAllStudiosWithHierarchy();
          console.debug(`[studioManager] Loaded ${hierarchyStudios.length} studios`);

          hierarchyTree = buildStudioTree(hierarchyStudios);
          hierarchyStats = getTreeStats(hierarchyStudios);

          // Reset UI state
          expandedNodes.clear();
          pendingChanges = [];
          isEditMode = false;
          originalParentMap.clear();

          renderHierarchyPage(containerRef.current);
        } catch (e) {
          console.error("[studioManager] Failed to load hierarchy:", e);
          containerRef.current.innerHTML = `<div class="studio-hierarchy"><div class="sh-loading">Error: ${escapeHtml(e.message)}</div></div>`;
        }
      }

      init();

      return () => {
        document.removeEventListener('keydown', handleHierarchyKeyboard);
        // Clean up any panels
        document.querySelector('.sh-changes-panel')?.remove();
        document.querySelector('.sh-toast-container')?.remove();
        hideContextMenu();
      };
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'studio-hierarchy-container'
    });
  }

  /**
   * Register plugin routes
   */
  function registerRoute() {
    PluginApi.register.route(HIERARCHY_ROUTE_PATH, StudioHierarchyPage);
    console.log('[studioManager] Route registered:', HIERARCHY_ROUTE_PATH);
  }
```

**Step 2: Verify functions exist**

Run: `grep -n "function StudioHierarchyPage\|function registerRoute" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for both functions

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add React component and route registration"
```

---

## Task 12: Add Button Injection

**Files:**
- Modify: `plugins/studioManager/studio-manager.js`

**Step 1: Add button injection functions**

Add after route registration, before final console.log:

```javascript
  /**
   * Create hierarchy icon SVG
   */
  function createHierarchyIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.setAttribute('fill', 'currentColor');
    svg.innerHTML = `
      <path d="M3 3h6v6H3V3zm0 12h6v6H3v-6zm12 0h6v6h-6v-6zm-2-6h2v4h4v2h-6v-6zm-4 0v6H7v-2h2v-4h2zm10-6h-6v6h6V3z"/>
    `;
    return svg;
  }

  /**
   * Inject navigation button into Studios page toolbar
   */
  function injectNavButton() {
    // Only run on Studios list page
    if (!window.location.pathname.endsWith('/studios')) {
      return;
    }

    // Check if already injected
    if (document.querySelector('#sh-nav-button')) {
      return;
    }

    // Find the toolbar
    const toolbar = document.querySelector('.filtered-list-toolbar');
    if (!toolbar) {
      console.debug('[studioManager] Toolbar not found yet');
      return;
    }

    // Strategy 1: Find zoom-slider-container
    let insertionPoint = toolbar.querySelector('.zoom-slider-container');

    // Strategy 2: Find display-mode-select
    if (!insertionPoint) {
      insertionPoint = toolbar.querySelector('.display-mode-select');
    }

    // Strategy 3: Find last btn-group with icons
    if (!insertionPoint) {
      const btnGroups = toolbar.querySelectorAll('.btn-group');
      for (const group of btnGroups) {
        const hasIcons = group.querySelector('.fa-icon') || group.querySelector('svg');
        if (hasIcons) {
          insertionPoint = group;
        }
      }
    }

    if (!insertionPoint) {
      console.debug('[studioManager] No suitable insertion point found');
      return;
    }

    // Create hierarchy button
    const btn = document.createElement('button');
    btn.id = 'sh-nav-button';
    btn.className = 'btn btn-secondary';
    btn.title = 'Studio Hierarchy';
    btn.style.marginLeft = '0.5rem';
    btn.appendChild(createHierarchyIcon());
    btn.addEventListener('click', () => {
      window.location.href = HIERARCHY_ROUTE_PATH;
    });

    // Insert button
    insertionPoint.parentNode.insertBefore(btn, insertionPoint.nextSibling);
    console.debug('[studioManager] Nav button injected on Studios page');
  }

  /**
   * Watch for navigation to Studios page and inject button
   */
  function setupNavButtonInjection() {
    injectNavButton();

    // Watch for URL changes (SPA navigation)
    let lastUrl = window.location.href;
    const observer = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(injectNavButton, 100);
        setTimeout(injectNavButton, 500);
        setTimeout(injectNavButton, 1000);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Retry on initial load
    setTimeout(injectNavButton, 100);
    setTimeout(injectNavButton, 500);
    setTimeout(injectNavButton, 1000);
    setTimeout(injectNavButton, 2000);
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();
```

**Step 2: Verify functions exist and initialization code is present**

Run: `grep -n "function injectNavButton\|function setupNavButtonInjection\|registerRoute();\|setupNavButtonInjection();" plugins/studioManager/studio-manager.js`
Expected: Shows line numbers for functions and initialization calls

**Step 3: Commit**

```bash
git add plugins/studioManager/studio-manager.js
git commit -m "feat(studioManager): add button injection for Studios page"
```

---

## Task 13: Create Plugin README

**Files:**
- Create: `plugins/studioManager/README.md`
- Reference: `plugins/tagManager/README.md`

**Step 1: Create README file**

```markdown
# Studio Manager

Manage studio hierarchy with a visual tree editor. View and edit parent-child studio relationships.

## Features

- **Studio Hierarchy** - Visual tree view of studio parent-child relationships
- **Drag and Drop** - Drag studios to set parent relationships
- **Context Menu** - Right-click for quick actions (view, edit, remove parent)
- **Keyboard Shortcuts** - Delete key to remove parent
- **Pending Changes** - Review and save multiple changes at once

## Requirements

- Stash v0.28+

## Installation

### Via Stash Plugin Source (Recommended)

1. In Stash, go to **Settings → Plugins → Available Plugins**
2. Click **Add Source**
3. Enter URL: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
4. Click **Reload**
5. Find "Studio Manager" under "Carrot Waxxer" and click Install

### Manual Installation

1. Download or clone this repository
2. Copy the `studioManager` folder to your Stash plugins directory:
   - **Windows**: `C:\Users\<username>\.stash\plugins\`
   - **macOS**: `~/.stash/plugins/`
   - **Linux**: `~/.stash/plugins/`

## Usage

### Accessing the Hierarchy Page

1. Navigate to the **Studios** page in Stash
2. Click the **hierarchy icon** button in the toolbar
3. Browse the studio tree structure

### Editing Relationships

**Drag and Drop:**
- Drag any studio onto another to set it as a child
- Drag onto the "Drop here to make root studio" zone to remove its parent

**Context Menu (Right-click):**
- **View Studio** - Open the studio page
- **Edit Studio** - Open the studio edit page
- **Remove Parent** - Make the studio a root studio
- **Expand/Collapse All Children** - Toggle all descendants

**Keyboard:**
- **Delete** - Remove parent from selected studio
- **Escape** - Clear selection

### Saving Changes

Changes are queued as "pending" until you save:
1. Make your edits (drag-drop, context menu, keyboard)
2. Review pending changes in the panel at the bottom
3. Click **Save Changes** to apply or **Cancel** to discard

## File Structure

```
studioManager/
├── studioManager.yml     # Plugin manifest
├── studio-manager.js     # JavaScript UI
├── studio-manager.css    # UI styles
└── README.md             # This file
```

## License

MIT License - See repository root for details.
```

**Step 2: Verify file exists**

Run: `head -20 plugins/studioManager/README.md`
Expected: Shows first 20 lines of README

**Step 3: Commit**

```bash
git add plugins/studioManager/README.md
git commit -m "docs(studioManager): add plugin README"
```

---

## Task 14: Update Repository README

**Files:**
- Modify: `README.md` (repo root)

**Step 1: Add Studio Manager to Available Plugins section**

Add after the Tag Manager entry (around line 82):

```markdown
### Studio Manager (v0.1.0)

Manage studio hierarchy with visual tree editing. View and edit parent-child studio relationships.

**Features:**
- Visual tree view of studio parent-child relationships
- Drag and drop to set parent relationships
- Context menu for quick actions
- Pending changes panel for reviewing edits before saving

[Documentation](plugins/studioManager/README.md)
```

**Step 2: Verify the entry was added**

Run: `grep -A 10 "Studio Manager" README.md`
Expected: Shows the new Studio Manager section

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add Studio Manager to plugin list"
```

---

## Task 15: Manual Testing

**Files:** None (testing only)

**Step 1: Reload plugins in Stash**

1. Open Stash in browser
2. Go to Settings → Plugins
3. Click "Reload Plugins"
4. Verify "Studio Manager" appears in the plugin list

**Step 2: Test button injection**

1. Navigate to Studios page
2. Verify hierarchy button appears in toolbar
3. Click button, verify navigation to `/plugins/studio-hierarchy`

**Step 3: Test hierarchy page**

1. Verify studios load and display in tree structure
2. Test expand/collapse buttons
3. Test show/hide images toggle
4. Test drag and drop (creates pending change)
5. Test context menu (right-click)
6. Test Delete key to remove parent
7. Test Save Changes and Cancel buttons

**Step 4: Commit any fixes if needed**

If bugs found, fix them and commit with descriptive message.

---

## Task 16: Final Commit - Complete Feature

**Files:** None (verification only)

**Step 1: Verify all files exist**

Run: `ls -la plugins/studioManager/`
Expected: Shows studioManager.yml, studio-manager.js, studio-manager.css, README.md

**Step 2: Verify git status is clean**

Run: `git status`
Expected: "nothing to commit, working tree clean" or only untracked files

**Step 3: View commit history**

Run: `git log --oneline -15`
Expected: Shows all feature commits for Studio Manager

---

## Summary

Tasks 1-14 create the complete Studio Manager plugin with:
- Plugin manifest (Task 1)
- CSS styles (Task 2)
- Core JS structure (Task 3)
- GraphQL queries (Task 4)
- Tree building (Task 5)
- Toast/context menu (Task 6)
- Pending changes (Task 7)
- Parent modification (Task 8)
- Tree rendering (Task 9)
- Event handlers (Task 10)
- React component (Task 11)
- Button injection (Task 12)
- Plugin README (Task 13)
- Repo README update (Task 14)

Task 15 is manual testing, Task 16 is verification.
