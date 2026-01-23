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

  console.log('[studioManager] Plugin loaded');
})();
