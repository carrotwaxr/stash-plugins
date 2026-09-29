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
  let isSaving = false;       // a save is running: editing is locked

  // Drag state
  let draggedStudioId = null;

  // Context menu state
  let contextMenuStudioId = null;
  let contextMenuCloser = null;   // { menu, close } while a menu is open

  // Lifecycle: bumped on every mount and unmount; async work started under an
  // older value must not render, toast or touch the page.
  let mountToken = 0;
  let restoredCount = 0;          // changes re-applied on the last mount (banner)
  let titleTimers = [];
  let leaveGuardOn = false;

  /**
   * Set page title with retry to overcome Stash's title management.
   * The retries are cleared on unmount (clearTitleTimers).
   */
  function setPageTitle(title) {
    const doSet = () => { document.title = title; };
    clearTitleTimers();
    doSet();
    for (const ms of [50, 200, 500]) titleTimers.push(setTimeout(doSet, ms));
  }

  function clearTitleTimers() {
    titleTimers.forEach(id => clearTimeout(id));
    titleTimers = [];
  }

  /**
   * An app path under Stash's <base href> (e.g. "/stash/"). Accepts paths with
   * or without a leading slash; never doubles slashes.
   */
  function stashPath(path) {
    const baseEl = document.querySelector('base');
    let base = (baseEl && baseEl.getAttribute('href')) || '/';
    if (/^([a-z][a-z\d+.-]*:)?\/\//i.test(base)) { // absolute or protocol-relative
      try { base = new URL(base, window.location.href).pathname; } catch (e) { /* keep the raw href */ }
    }
    let prefix = base.replace(/\/+$/, '');
    if (prefix && !prefix.startsWith('/')) prefix = `/${prefix}`;
    const rel = String(path == null ? '' : path).replace(/^\/+/, '');
    return `${prefix}/${rel}`;
  }

  function unsavedMessage(n) {
    return `You have ${n} unsaved change${n === 1 ? '' : 's'}. Leave anyway?`;
  }

  /**
   * Navigate inside Stash's single-page app (react-router listens for popstate).
   * With pending changes, asks first; the hierarchy page itself is exempt because
   * it restores them.
   */
  function navigateTo(path) {
    if (pendingChanges.length > 0 && path !== HIERARCHY_ROUTE_PATH &&
        !window.confirm(unsavedMessage(pendingChanges.length))) {
      return;
    }
    const url = stashPath(path);
    try {
      window.history.pushState({}, '', url);
      // history v4 ignores a popstate whose state is undefined, so pass one.
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    } catch (e) {
      console.warn('[studioManager] In-app navigation failed; loading the page instead:', e);
      window.location.href = url;
    }
  }

  function beforeUnloadGuard(e) {
    e.preventDefault();
    e.returnValue = '';
  }

  /**
   * beforeunload is set exactly while changes are pending.
   */
  function syncLeaveGuard() {
    const want = pendingChanges.length > 0;
    if (want && !leaveGuardOn) window.addEventListener('beforeunload', beforeUnloadGuard);
    if (!want && leaveGuardOn) window.removeEventListener('beforeunload', beforeUnloadGuard);
    leaveGuardOn = want;
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
   * Effective parent map: original parents with pending changes applied.
   * Returns a new Map of studio id -> parent id (or null). Never mutates its inputs.
   * @param {Map} originalParentMap studio id -> parent id or null (see enterEditMode)
   * @param {Array} pending items {type: 'set-parent'|'remove-parent', studioId, parentId, ...}
   */
  function effectiveParentMap(originalParentMap, pending) {
    const effective = new Map(originalParentMap);
    for (const change of pending || []) {
      if (change.type === 'set-parent') {
        effective.set(change.studioId, change.parentId);
      } else if (change.type === 'remove-parent') {
        effective.set(change.studioId, null);
      }
    }
    return effective;
  }

  /**
   * Ids from the parent of `id` up to the root. Stops at a cycle (no id repeats,
   * and `id` itself only appears if it is on a cycle).
   */
  function ancestorsOf(id, parentMap) {
    const result = [];
    const visited = new Set([id]);
    let current = parentMap.get(id);
    while (current && !visited.has(current)) {
      result.push(current);
      visited.add(current);
      current = parentMap.get(current);
    }
    return result;
  }

  /**
   * True if making newParentId the parent of studioId is unsafe: newParentId is the
   * studio itself or one of its descendants, or the walk up from newParentId meets an
   * existing cycle (Stash's own check would recurse forever on that).
   */
  function wouldCreateCycle(studioId, newParentId, parentMap) {
    if (!newParentId) return false;
    const visited = new Set();
    let current = newParentId;
    while (current) {
      if (current === studioId) return true;
      if (visited.has(current)) return true; // existing cycle upstream
      visited.add(current);
      current = parentMap.get(current);
    }
    return false;
  }

  /**
   * Find the cycles in a studio list. Returns arrays of ids, one per cycle
   * (a self-parent is a cycle of one).
   */
  function findCycles(parentOf) {
    const state = new Map(); // id -> 1 (on current path) | 2 (done)
    const cycles = [];
    for (const start of parentOf.keys()) {
      if (state.has(start)) continue;
      const path = [];
      let current = start;
      while (current && parentOf.has(current) && !state.has(current)) {
        state.set(current, 1);
        path.push(current);
        current = parentOf.get(current);
      }
      if (current && state.get(current) === 1) {
        cycles.push(path.slice(path.indexOf(current)));
      }
      path.forEach(id => state.set(id, 2));
    }
    return cycles;
  }

  /** studio id -> parent id, only for parents present in the list (else null). */
  function parentsInList(studios) {
    const ids = new Set(studios.map(s => s.id));
    const parentOf = new Map();
    for (const s of studios) {
      const p = s.parent_studio?.id || null;
      parentOf.set(s.id, p && ids.has(p) ? p : null);
    }
    return parentOf;
  }

  /**
   * Ids of studios on a parent cycle, plus all their descendants. Returns a Set.
   */
  function findCycleMembers(studios) {
    const parentOf = parentsInList(studios);
    const members = new Set();
    findCycles(parentOf).forEach(cycle => cycle.forEach(id => members.add(id)));
    if (members.size === 0) return members;
    const memo = new Map();
    function reaches(id) {
      const path = [];
      let current = id;
      let result = false;
      while (current) {
        if (members.has(current)) { result = true; break; }
        if (memo.has(current)) { result = memo.get(current); break; }
        path.push(current);
        current = parentOf.get(current);
      }
      path.forEach(p => memo.set(p, result));
      return result;
    }
    for (const id of parentOf.keys()) {
      if (reaches(id)) members.add(id);
    }
    return members;
  }

  /** Compare ids numerically when both are numeric, else as strings. */
  function compareIds(a, b) {
    const na = Number(a), nb = Number(b);
    if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
    return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
  }

  /**
   * Build tree structure from flat studio list.
   * Returns the array of root nodes ({...studio, childNodes, inCycle}); the array also
   * carries a non-enumerable `totalStudios` (number of nodes reachable from the roots).
   * An orphan whose parent is missing is a root. Each parent cycle is broken at its
   * smallest id, which becomes a pseudo-root; cycle members and their descendants are
   * flagged inCycle, so no studio disappears.
   */
  function buildStudioTree(studios) {
    const parentOf = parentsInList(studios);
    const inCycle = findCycleMembers(studios);
    const breakAt = new Set();
    for (const cycle of findCycles(parentOf)) {
      breakAt.add(cycle.slice().sort(compareIds)[0]);
    }

    const studioMap = new Map();
    studios.forEach(studio => {
      studioMap.set(studio.id, {
        ...studio,
        childNodes: [],
        inCycle: inCycle.has(studio.id)
      });
    });

    const roots = [];
    studios.forEach(studio => {
      const node = studioMap.get(studio.id);
      const parentId = parentOf.get(studio.id);
      if (!parentId || breakAt.has(studio.id)) {
        roots.push(node);
      } else {
        studioMap.get(parentId).childNodes.push(node);
      }
    });

    // Sort roots and children by name
    const sortByName = (a, b) => (a.name || '').localeCompare(b.name || '');
    roots.sort(sortByName);

    let total = 0;
    function sortChildren(node) {
      total++;
      node.childNodes.sort(sortByName);
      node.childNodes.forEach(sortChildren);
    }
    roots.forEach(sortChildren);

    Object.defineProperty(roots, 'totalStudios', { value: total, enumerable: false });
    return roots;
  }

  /**
   * Order pending changes for saving: every remove-parent first, then set-parent
   * changes by the studio's depth in the final tree, shallowest first (ties keep
   * insertion order). `parentMap` is the final effective map (or the original map;
   * pending is applied on top either way). Returns a new array.
   */
  function orderForSave(pending, parentMap) {
    const finalMap = effectiveParentMap(parentMap, pending);
    const removes = pending.filter(c => c.type === 'remove-parent');
    const depth = c => ancestorsOf(c.studioId, finalMap).length;
    const sets = pending
      .map((c, index) => ({ c, index }))
      .filter(({ c }) => c.type === 'set-parent')
      .sort((a, b) => depth(a.c) - depth(b.c) || a.index - b.index)
      .map(({ c }) => c);
    return [...removes, ...sets];
  }

  /**
   * Ids of studios that are their own ancestor (on a loop), not their descendants.
   */
  function studiosOnCycle(parentMap) {
    const on = new Set();
    for (const start of parentMap.keys()) {
      const seen = new Set([start]);
      let current = parentMap.get(start);
      while (current != null && !seen.has(current)) {
        seen.add(current);
        current = parentMap.get(current);
      }
      if (current === start) on.add(start);
    }
    return on;
  }

  /**
   * Parent map of the server state, with the edit snapshot when one exists.
   */
  function baseParentMap() {
    if (originalParentMap.size > 0) return originalParentMap;
    const base = new Map();
    for (const studio of hierarchyStudios) {
      base.set(studio.id, studio.parent_studio?.id || null);
    }
    return base;
  }

  /**
   * The displayed studios: copies of hierarchyStudios (the server state, never
   * mutated by edits) with parent_studio and child_studios taken from the
   * effective parent map (original parents plus pending changes).
   */
  function derivedStudios() {
    const effective = effectiveParentMap(baseParentMap(), pendingChanges);
    const byId = new Map(hierarchyStudios.map(s => [s.id, s]));
    const kids = new Map();
    for (const studio of hierarchyStudios) {
      const pid = effective.get(studio.id);
      if (pid && byId.has(pid)) {
        if (!kids.has(pid)) kids.set(pid, []);
        kids.get(pid).push({ id: studio.id, name: studio.name });
      }
    }
    return hierarchyStudios.map(studio => {
      const pid = effective.get(studio.id);
      const parent = pid ? byId.get(pid) : null;
      return {
        ...studio,
        parent_studio: pid ? { ...(studio.parent_studio || {}), id: pid, name: parent ? parent.name : studio.parent_studio?.name } : null,
        child_studios: kids.get(studio.id) || []
      };
    });
  }

  /**
   * Recompute the tree and stats from the derived studios; re-render if the page is mounted.
   */
  function refreshView() {
    const studios = derivedStudios();
    hierarchyTree = buildStudioTree(studios);
    hierarchyStats = getTreeStats(studios);
    const container = document.querySelector('.studio-hierarchy-container');
    if (container) renderHierarchyPage(container);
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
    const base = new Map();
    for (const studio of hierarchyStudios) {
      base.set(studio.id, studio.parent_studio?.id || null);
    }
    return wouldCreateCycle(studioId, potentialParentId, effectiveParentMap(base, pendingChanges));
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
    toast.textContent = message; // .sh-toast is white-space: pre-line
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
    if (contextMenuCloser) {
      document.removeEventListener('click', contextMenuCloser.close);
      document.removeEventListener('contextmenu', contextMenuCloser.close);
      contextMenuCloser.cancelled = true;
      contextMenuCloser = null;
    }
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

    const studio = derivedStudios().find(s => s.id === studioId);
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
          navigateTo(`/studios/${studioId}`);
          break;
        case 'edit':
          navigateTo(`/studios/${studioId}/edit`);
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

    // Close on any click or right-click elsewhere. Registered next tick so the
    // event that opened the menu does not close it; skipped if it was closed already.
    const closer = { close: () => hideContextMenu(), cancelled: false };
    contextMenuCloser = closer;
    setTimeout(() => {
      if (closer.cancelled) return;
      document.addEventListener('click', closer.close);
      document.addEventListener('contextmenu', closer.close);
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
   * Add a pending change. A change that returns a studio to its original parent
   * just drops its pending entry.
   */
  function addPendingChange(type, studioId, studioName, parentId, parentName) {
    enterEditMode();

    // Replace any existing change for this studio
    pendingChanges = pendingChanges.filter(c => c.studioId !== studioId);

    const originalParent = originalParentMap.get(studioId) || null;
    const target = type === 'set-parent' ? parentId : null;
    if (target !== originalParent) {
      pendingChanges.push({ type, studioId, studioName, parentId, parentName });
    }

    renderChangesPanel();
  }

  /**
   * Leave edit mode and forget pending changes (no refetch: the server state is intact)
   */
  function cancelPendingChanges() {
    if (isSaving) return;
    pendingChanges = [];
    isEditMode = false;
    originalParentMap.clear();
    renderChangesPanel();
    refreshView();
  }

  /**
   * Remove a pending change by index; the others stay applied
   */
  function removePendingChange(index) {
    if (isSaving) return;
    pendingChanges.splice(index, 1);
    if (pendingChanges.length === 0) {
      isEditMode = false;
      originalParentMap.clear();
    }
    renderChangesPanel();
    refreshView();
  }

  /**
   * Render the pending changes panel
   */
  function renderChangesPanel() {
    syncLeaveGuard();
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
        <div class="sh-change-item${change.error ? ' sh-change-failed' : ''}">
          <span class="sh-change-text">${text}${change.error ? `<span class="sh-change-error">${escapeHtml(change.error)}</span>` : ''}</span>
          <button class="sh-change-remove" data-index="${index}" ${isSaving ? 'disabled' : ''}>&times;</button>
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
        <button class="btn btn-secondary" id="sh-cancel-changes" ${isSaving ? 'disabled' : ''}>Cancel</button>
        <button class="btn btn-primary" id="sh-save-changes" ${isSaving ? 'disabled' : ''}>${isSaving ? 'Saving…' : 'Save Changes'}</button>
      </div>
    `;

    // Attach handlers
    panel.querySelectorAll('.sh-change-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        removePendingChange(parseInt(btn.dataset.index));
      });
    });

    panel.querySelector('#sh-cancel-changes')?.addEventListener('click', () => {
      cancelPendingChanges();
    });

    panel.querySelector('#sh-save-changes')?.addEventListener('click', savePendingChanges);
  }

  /**
   * Save pending changes in a safe order (see orderForSave). Editing is locked
   * meanwhile. Failed changes stay pending with their error; the data is then
   * refetched and the failures re-applied on top of it.
   */
  async function savePendingChanges() {
    if (isSaving || pendingChanges.length === 0) return;
    isSaving = true;
    const token = mountToken;
    const live = () => token === mountToken; // false once the page was unmounted
    renderChangesPanel();

    const total = pendingChanges.length;
    const failed = [];
    let saved = 0;
    try {
      // Parent map as the server evolves while we save
      const running = new Map(baseParentMap());
      for (const change of orderForSave(pendingChanges, running)) {
        delete change.error;
        const parentId = change.type === 'set-parent' ? change.parentId : null;
        if (parentId && wouldCreateCycle(change.studioId, parentId, running)) {
          change.error = 'Parent chain contains a cycle; fix that first';
          failed.push(change);
          continue;
        }
        try {
          await updateStudioParent(change.studioId, parentId);
          running.set(change.studioId, parentId);
          saved++;
        } catch (err) {
          change.error = err.message || String(err);
          failed.push(change);
        }
      }

      if (live()) {
        if (failed.length === 0) {
          showToast(`${saved} saved`, 'success');
        } else {
          const lines = failed.map(c => `"${c.studioName}": ${c.error}`);
          showToast(`${saved} saved, ${failed.length} failed\n${lines.join('\n')}`, 'error', 8000);
        }
      }

      pendingChanges = pendingChanges.filter(c => failed.includes(c));
      const reloaded = await reloadHierarchy();
      if (!reloaded) {
        // Keep the view consistent without a refetch: successes become the baseline
        originalParentMap = running;
      }
      if (pendingChanges.length === 0) {
        isEditMode = false;
        originalParentMap.clear();
      }
    } finally {
      isSaving = false;
      if (live()) {
        renderChangesPanel();
        refreshView();
      } else {
        syncLeaveGuard();
      }
    }
  }

  /**
   * Refetch the studios and re-snapshot the baseline from them. Returns false
   * (after showing an error) when the fetch fails, leaving state untouched.
   */
  async function reloadHierarchy() {
    const token = mountToken;
    try {
      const studios = await fetchAllStudiosWithHierarchy();
      if (token !== mountToken) return false; // unmounted meanwhile: touch nothing
      hierarchyStudios = studios;
      originalParentMap = new Map();
      for (const s of studios) originalParentMap.set(s.id, s.parent_studio?.id || null);
      return true;
    } catch (e) {
      if (token !== mountToken) return false;
      console.error('[studioManager] Failed to reload hierarchy:', e);
      showToast('Failed to reload hierarchy; pending changes were kept', 'error');
      return false;
    }
  }

  /**
   * Set a studio's parent (queue as pending change)
   */
  function setParent(studioId, newParentId) {
    if (isSaving) return;
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

    // Make the moved studio visible: expand every ancestor in the edited tree
    const effective = effectiveParentMap(baseParentMap(), pendingChanges);
    for (const id of ancestorsOf(studioId, effective)) expandedNodes.add(id);
    refreshView();
  }

  /**
   * Remove a studio's parent (make it a root studio)
   */
  function removeParent(studioId) {
    if (isSaving) return;
    const studio = derivedStudios().find(s => s.id === studioId);

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

    refreshView();
  }

  /**
   * Render a single tree node (recursive)
   */
  function renderTreeNode(node, isRoot = false, onCycle = new Set()) {
    const hasChildren = node.childNodes.length > 0;
    const cycleBadge = onCycle.has(node.id)
      ? '<span class="sh-cycle-badge" title="This studio is its own ancestor. Give one studio in the loop a different parent, or none.">cycle</span>'
      : '';
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
      const childNodes = node.childNodes.map(child => renderTreeNode(child, false, onCycle)).join('');
      childrenHtml = `<div class="sh-children ${isExpanded ? 'sh-expanded' : ''}" data-parent-id="${node.id}">${childNodes}</div>`;
    }

    // Toggle icon
    const toggleIcon = hasChildren
      ? (isExpanded ? '&#9660;' : '&#9654;')
      : '';

    return `
      <div class="sh-node ${isRoot ? 'sh-root' : ''} ${node.id === selectedStudioId ? 'sh-selected' : ''} ${cycleBadge ? 'sh-in-cycle' : ''}" data-studio-id="${node.id}" draggable="true">
        <div class="sh-node-content">
          <span class="sh-toggle ${hasChildren ? '' : 'sh-leaf'}" data-studio-id="${node.id}">${toggleIcon}</span>
          ${imageHtml}
          <div class="sh-info">
            <a class="sh-name" href="${escapeHtml(stashPath(`/studios/${node.id}`))}" data-sh-route="/studios/${escapeHtml(node.id)}">${escapeHtml(node.name)}</a>${cycleBadge}
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
    const onCycle = studiosOnCycle(effectiveParentMap(baseParentMap(), pendingChanges));
    const treeHtml = hierarchyTree.map(root => renderTreeNode(root, true, onCycle)).join('');
    const cycleHtml = onCycle.size > 0
      ? `<div class="sh-cycle-warning">${onCycle.size} studio${onCycle.size === 1 ? ' is' : 's are'} in a parent cycle (marked "cycle"). Stash can't save a new parent under them until one studio in each loop gets a different parent, or none.</div>`
      : '';
    if (pendingChanges.length === 0) restoredCount = 0;
    const bannerHtml = restoredCount > 0
      ? `<div class="sh-restored-banner">${restoredCount} unsaved change${restoredCount === 1 ? '' : 's'} restored</div>`
      : '';

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
        ${bannerHtml}
        ${cycleHtml}
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

    // The selected studio may be gone (or hidden) after a re-render
    if (selectedStudioId && !container.querySelector('.sh-node.sh-selected')) {
      selectedStudioId = null;
    }
  }

  /**
   * The element for a delegated event: the closest ancestor of the target matching sel.
   */
  function closestFrom(e, sel) {
    const t = e.target;
    if (!t) return null;
    if (typeof t.closest === 'function') return t.closest(sel);
    return t.parentElement && typeof t.parentElement.closest === 'function' ? t.parentElement.closest(sel) : null;
  }

  function clearDragMarks(container) {
    container.querySelectorAll('.drag-over, .drag-invalid').forEach(n => {
      n.classList.remove('drag-over', 'drag-invalid');
    });
  }

  /**
   * Attach the page's event handlers once per mount, delegated on the container
   * (its content is replaced on every render). Returns a function that removes them.
   */
  function attachHierarchyEventHandlers(container) {
    const handlers = {
      click(e) {
        // Toggle expand/collapse on arrow click
        const toggle = closestFrom(e, '.sh-toggle');
        if (toggle) {
          const studioId = toggle.dataset.studioId;
          if (!studioId) return;
          const childrenContainer = container.querySelector(`.sh-children[data-parent-id="${studioId}"]`);
          if (!childrenContainer) return;
          if (expandedNodes.has(studioId)) {
            expandedNodes.delete(studioId);
            childrenContainer.classList.remove('sh-expanded');
            toggle.innerHTML = '&#9654;';
          } else {
            expandedNodes.add(studioId);
            childrenContainer.classList.add('sh-expanded');
            toggle.innerHTML = '&#9660;';
          }
          return;
        }

        // Studio link: in-app navigation (modified clicks stay with the browser)
        const link = closestFrom(e, 'a[data-sh-route]');
        if (link) {
          if (e.defaultPrevented || (e.button && e.button !== 0)) return;
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          navigateTo(link.getAttribute('data-sh-route'));
          return;
        }

        if (closestFrom(e, '#sh-expand-all')) {
          container.querySelectorAll('.sh-children').forEach(el => {
            el.classList.add('sh-expanded');
            const parentId = el.dataset.parentId;
            if (parentId) expandedNodes.add(parentId);
          });
          container.querySelectorAll('.sh-toggle:not(.sh-leaf)').forEach(el => {
            el.innerHTML = '&#9660;';
          });
          return;
        }
        if (closestFrom(e, '#sh-collapse-all')) {
          container.querySelectorAll('.sh-children').forEach(el => {
            el.classList.remove('sh-expanded');
            const parentId = el.dataset.parentId;
            if (parentId) expandedNodes.delete(parentId);
          });
          container.querySelectorAll('.sh-toggle:not(.sh-leaf)').forEach(el => {
            el.innerHTML = '&#9654;';
          });
          return;
        }

        // Select a node; a click anywhere else clears the selection
        const node = closestFrom(e, '.sh-node');
        container.querySelectorAll('.sh-node.sh-selected').forEach(n => {
          n.classList.remove('sh-selected');
        });
        if (node) {
          node.classList.add('sh-selected');
          selectedStudioId = node.dataset.studioId;
        } else {
          selectedStudioId = null;
        }
      },

      change(e) {
        if (e.target && e.target.id === 'sh-show-images') {
          showImages = e.target.checked;
          container.querySelectorAll('.sh-image, .sh-image-placeholder').forEach(el => {
            el.classList.toggle('sh-hidden', !showImages);
          });
        }
      },

      contextmenu(e) {
        const node = closestFrom(e, '.sh-node');
        if (!node) return;
        e.preventDefault();
        showContextMenu(e.clientX, e.clientY, node.dataset.studioId);
      },

      dragstart(e) {
        const node = closestFrom(e, '.sh-node');
        if (!node) return;
        draggedStudioId = node.dataset.studioId;
        node.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedStudioId);
      },

      dragend(e) {
        const node = closestFrom(e, '.sh-node');
        if (node) node.classList.remove('dragging');
        draggedStudioId = null;
        clearDragMarks(container);
      },

      dragover(e) {
        const zone = closestFrom(e, '#sh-root-drop-zone');
        if (zone) {
          e.preventDefault();
          if (draggedStudioId) zone.classList.add('drag-over');
          return;
        }
        const node = closestFrom(e, '.sh-node');
        if (!node) return;
        e.preventDefault();
        if (!draggedStudioId || node.dataset.studioId === draggedStudioId) return;
        const wouldCircle = wouldCreateCircularRef(node.dataset.studioId, draggedStudioId);
        node.classList.remove('drag-over', 'drag-invalid');
        node.classList.add(wouldCircle ? 'drag-invalid' : 'drag-over');
      },

      dragleave(e) {
        const zone = closestFrom(e, '#sh-root-drop-zone');
        if (zone) { zone.classList.remove('drag-over'); return; }
        const node = closestFrom(e, '.sh-node');
        if (node) node.classList.remove('drag-over', 'drag-invalid');
      },

      drop(e) {
        const zone = closestFrom(e, '#sh-root-drop-zone');
        if (zone) {
          e.preventDefault();
          zone.classList.remove('drag-over');
          if (draggedStudioId) removeParent(draggedStudioId);
          return;
        }
        const node = closestFrom(e, '.sh-node');
        if (!node) return;
        e.preventDefault();
        node.classList.remove('drag-over', 'drag-invalid');
        if (!draggedStudioId || node.dataset.studioId === draggedStudioId) return;

        const targetId = node.dataset.studioId;
        if (wouldCreateCircularRef(targetId, draggedStudioId)) {
          showToast('Cannot create circular reference', 'error');
          return;
        }
        setParent(draggedStudioId, targetId);
      },
    };

    for (const [type, fn] of Object.entries(handlers)) container.addEventListener(type, fn);
    return () => {
      for (const [type, fn] of Object.entries(handlers)) container.removeEventListener(type, fn);
    };
  }

  /**
   * Keyboard handler
   */
  function handleHierarchyKeyboard(e) {
    if (!document.querySelector('.studio-hierarchy-container')) return;

    // Never act while the user is typing
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;

    // Delete - remove parent from selected studio
    if (e.key === 'Delete' && selectedStudioId) {
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
   * After a remount: re-apply the pending changes still held in module state to
   * freshly fetched data. Changes whose studio is gone, that became no-ops, or
   * that would now form a cycle are dropped. Sets the "restored" banner count.
   */
  function restorePendingChanges() {
    const original = new Map();
    for (const s of hierarchyStudios) original.set(s.id, s.parent_studio?.id || null);
    const byId = new Map(hierarchyStudios.map(s => [s.id, s]));
    const effective = new Map(original);
    const kept = [];
    for (const change of pendingChanges) {
      const studio = byId.get(change.studioId);
      const target = change.type === 'set-parent' ? change.parentId : null;
      if (!studio || (target && !byId.has(target))) continue;
      if (target === original.get(change.studioId)) continue;
      if (target && wouldCreateCycle(change.studioId, target, effective)) continue;
      effective.set(change.studioId, target);
      kept.push({ ...change, studioName: studio.name, parentName: target ? byId.get(target).name : null });
    }
    pendingChanges = kept;
    isEditMode = kept.length > 0;
    originalParentMap = kept.length > 0 ? original : new Map();
    restoredCount = kept.length;
  }

  /**
   * Studio Hierarchy Page React component
   */
  function StudioHierarchyPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);

    React.useEffect(() => {
      const token = ++mountToken;
      const live = () => token === mountToken;
      const container = containerRef.current;

      document.addEventListener('keydown', handleHierarchyKeyboard);
      const detachHandlers = container ? attachHierarchyEventHandlers(container) : () => {};

      async function init() {
        if (!container) return;

        setPageTitle("Studio Hierarchy | Stash");
        container.innerHTML = '<div class="studio-hierarchy"><div class="sh-loading">Loading studios...</div></div>';

        try {
          const studios = await fetchAllStudiosWithHierarchy();
          if (!live()) return;
          hierarchyStudios = studios;
          console.debug(`[studioManager] Loaded ${hierarchyStudios.length} studios`);

          expandedNodes.clear();
          selectedStudioId = null;
          restorePendingChanges();
          renderChangesPanel();
          refreshView();
        } catch (e) {
          if (!live()) return;
          console.error("[studioManager] Failed to load hierarchy:", e);
          container.innerHTML = `<div class="studio-hierarchy"><div class="sh-loading">Error: ${escapeHtml(e.message)}</div></div>`;
        }
      }

      init();

      return () => {
        mountToken++; // in-flight fetches and saves must not render or toast any more
        document.removeEventListener('keydown', handleHierarchyKeyboard);
        detachHandlers();
        clearTitleTimers();
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
    // Only run on Studios list page (with or without a trailing slash)
    if (!/\/studios\/?$/.test(window.location.pathname)) {
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
    btn.addEventListener('click', () => navigateTo(HIERARCHY_ROUTE_PATH));

    // Insert button
    insertionPoint.parentNode.insertBefore(btn, insertionPoint.nextSibling);
    console.debug('[studioManager] Nav button injected on Studios page');
  }

  /**
   * Inject the button now and on every Stash location change (the toolbar renders
   * shortly after the route changes, hence the short retries).
   */
  function setupNavButtonInjection() {
    injectNavButton();
    const onLocation = () => {
      injectNavButton();
      for (const ms of [100, 500, 1000]) setTimeout(injectNavButton, ms);
    };
    PluginApi.Event.addEventListener('stash:location', onLocation);
    // Retry on initial load
    for (const ms of [100, 500, 1000, 2000]) setTimeout(injectNavButton, ms);
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();

  console.log('[studioManager] Plugin loaded');

  // Test hook: only active when a test harness sets window.__STUDIO_MANAGER_TEST__.
  if (typeof window !== 'undefined' && window.__STUDIO_MANAGER_TEST__) {
    window.__STUDIO_MANAGER_TEST__.exports = {
      effectiveParentMap, ancestorsOf, wouldCreateCycle, findCycleMembers,
      buildStudioTree, orderForSave, wouldCreateCircularRef, getTreeStats,
      derivedStudios, savePendingChanges, reloadHierarchy, addPendingChange, removePendingChange, cancelPendingChanges,
      setParent, removeParent, renderChangesPanel, showContextMenu,
      renderHierarchyPage, handleHierarchyKeyboard, navigateTo, stashPath, injectNavButton,
    };
    window.__STUDIO_MANAGER_TEST__.getState = () => ({
      hierarchyStudios, hierarchyTree, hierarchyStats, expandedNodes, selectedStudioId,
      pendingChanges, isEditMode, originalParentMap,
    });
    window.__STUDIO_MANAGER_TEST__.setState = (patch) => {
      if ('hierarchyStudios' in patch) hierarchyStudios = patch.hierarchyStudios;
      if ('hierarchyTree' in patch) hierarchyTree = patch.hierarchyTree;
      if ('hierarchyStats' in patch) hierarchyStats = patch.hierarchyStats;
      if ('expandedNodes' in patch) expandedNodes = patch.expandedNodes;
      if ('selectedStudioId' in patch) selectedStudioId = patch.selectedStudioId;
      if ('pendingChanges' in patch) pendingChanges = patch.pendingChanges;
      if ('isEditMode' in patch) isEditMode = patch.isEditMode;
      if ('originalParentMap' in patch) originalParentMap = patch.originalParentMap;
    };
  }
})();
