# Tag Hierarchy View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Tag Hierarchy/Tree View page to the tagManager plugin that displays all tags in a collapsible tree structure based on parent/child relationships.

**Architecture:** Extend the existing tagManager plugin with a second route (`/plugins/tag-hierarchy`) and a new React component. Fetch all tags with parent/child relationships via GraphQL, build a tree structure client-side, and render with collapsible nodes. Reuse existing CSS patterns and button injection logic.

**Tech Stack:** JavaScript (Stash PluginApi React), CSS, Stash GraphQL API

---

## Design Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Multiple parents | Show tag under each parent (appears multiple times in tree) |
| Root level | Only orphan tags (tags with no parents) at top level |
| Images | 64px thumbnails, letterboxed/pillarboxed, with "Show images" toggle |
| Collapse behavior | All collapsed by default, with Expand All / Collapse All buttons |
| Metadata | Name + scene count + child count (e.g., "Action (47 scenes, 12 sub-tags)") |
| Scene counts | Direct only (no recursive/descendant counts) |
| Filtering | None for v1 |
| Navigation | Separate button to the right of existing Tag Manager button |
| Icon | Sitemap-style hierarchy icon |
| Page titles | "Tag Matcher | Stash" and "Tag Hierarchy | Stash" |

---

## Task 1: Add Page Title to Existing Tag Manager Page

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:957-962`

**Step 1: Add document.title in TagManagerPage init**

In the `TagManagerPage` component's `useEffect`, add the title after the debug log:

```javascript
React.useEffect(() => {
  async function init() {
    if (!containerRef.current) return;

    console.debug("[tagManager] Initializing...");
    document.title = "Tag Matcher | Stash";  // ADD THIS LINE
    containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading configuration...</div></div>';
```

**Step 2: Verify manually**

1. Navigate to `/plugins/tag-manager` in Stash
2. Verify browser tab shows "Tag Matcher | Stash"

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add page title to Tag Matcher page"
```

---

## Task 2: Create Sitemap Icon Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (add after `createTagManagerIcon` function, around line 1045)

**Step 1: Add the icon function**

Add this function after `createTagManagerIcon()`:

```javascript
/**
 * Create the Tag Hierarchy nav button SVG icon
 * Uses a sitemap icon to represent hierarchy/tree view
 */
function createHierarchyIcon() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 576 512');
  svg.setAttribute('class', 'svg-inline--fa fa-icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.style.width = '1em';
  svg.style.height = '1em';

  // FontAwesome "sitemap" icon path
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('fill', 'currentColor');
  path.setAttribute('d', 'M208 80c0-26.5 21.5-48 48-48h64c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-8v40H464c30.9 0 56 25.1 56 56v32h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-64c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-32c0-4.4-3.6-8-8-8H312v40h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-64c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-40H112c-4.4 0-8 3.6-8 8v32h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48H48c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-32c0-30.9 25.1-56 56-56h152v-40h-8c-26.5 0-48-21.5-48-48V80z');
  svg.appendChild(path);

  return svg;
}
```

**Step 2: Verify icon renders**

Temporarily add a test in browser console or wait until button injection task.

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add sitemap icon for hierarchy view"
```

---

## Task 3: Add Route Constant and Register Hierarchy Route

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:5` (add constant)
- Modify: `plugins/tagManager/tag-manager.js:1018-1021` (update registerRoute)

**Step 1: Add route constant**

After `const ROUTE_PATH = "/plugins/tag-manager";` add:

```javascript
const HIERARCHY_ROUTE_PATH = "/plugins/tag-hierarchy";
```

**Step 2: Create placeholder TagHierarchyPage component**

Add before the `registerRoute` function:

```javascript
/**
 * Tag Hierarchy page component (placeholder)
 */
function TagHierarchyPage() {
  const React = PluginApi.React;
  const containerRef = React.useRef(null);

  React.useEffect(() => {
    if (!containerRef.current) return;
    document.title = "Tag Hierarchy | Stash";
    containerRef.current.innerHTML = '<div class="tag-hierarchy"><div class="th-loading">Tag Hierarchy - Coming Soon</div></div>';
  }, []);

  return React.createElement('div', {
    ref: containerRef,
    className: 'tag-hierarchy-container'
  });
}
```

**Step 3: Update registerRoute to register both routes**

```javascript
function registerRoute() {
  PluginApi.register.route(ROUTE_PATH, TagManagerPage);
  PluginApi.register.route(HIERARCHY_ROUTE_PATH, TagHierarchyPage);
  console.log('[tagManager] Routes registered:', ROUTE_PATH, HIERARCHY_ROUTE_PATH);
}
```

**Step 4: Verify manually**

1. Navigate to `/plugins/tag-hierarchy` in Stash
2. Verify placeholder page loads with "Tag Hierarchy | Stash" title

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): register tag hierarchy route with placeholder"
```

---

## Task 4: Inject Hierarchy Button on Tags Page

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (update `injectTagManagerButton` function)

**Step 1: Rename function and update to inject both buttons**

Rename `injectTagManagerButton` to `injectNavButtons` and update to inject both buttons:

```javascript
/**
 * Inject Tag Manager and Tag Hierarchy buttons into Tags list page toolbar
 */
function injectNavButtons() {
  // Only run on Tags list page
  if (!window.location.pathname.endsWith('/tags')) {
    return;
  }

  // Check if we already injected the buttons
  if (document.querySelector('#tm-nav-button')) {
    return;
  }

  // Find the toolbar
  const toolbar = document.querySelector('.filtered-list-toolbar');
  if (!toolbar) {
    console.debug('[tagManager] Toolbar not found yet');
    return;
  }

  // Strategy 1: Find zoom-slider-container (always present after view mode buttons)
  let insertionPoint = toolbar.querySelector('.zoom-slider-container');

  // Strategy 2: Find display-mode-select button (dropdown version in some layouts)
  if (!insertionPoint) {
    insertionPoint = toolbar.querySelector('.display-mode-select');
  }

  // Strategy 3: Find the last btn-group with icon buttons
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
    console.debug('[tagManager] No suitable insertion point found in toolbar');
    return;
  }

  // Create Tag Manager button
  const tmBtn = document.createElement('button');
  tmBtn.id = 'tm-nav-button';
  tmBtn.className = 'btn btn-secondary';
  tmBtn.title = 'Tag Matcher';
  tmBtn.style.marginLeft = '0.5rem';
  tmBtn.appendChild(createTagManagerIcon());
  tmBtn.addEventListener('click', () => {
    window.location.href = ROUTE_PATH;
  });

  // Create Tag Hierarchy button
  const thBtn = document.createElement('button');
  thBtn.id = 'th-nav-button';
  thBtn.className = 'btn btn-secondary';
  thBtn.title = 'Tag Hierarchy';
  thBtn.style.marginLeft = '0.25rem';
  thBtn.appendChild(createHierarchyIcon());
  thBtn.addEventListener('click', () => {
    window.location.href = HIERARCHY_ROUTE_PATH;
  });

  // Insert both buttons after the insertion point
  insertionPoint.parentNode.insertBefore(tmBtn, insertionPoint.nextSibling);
  tmBtn.parentNode.insertBefore(thBtn, tmBtn.nextSibling);
  console.debug('[tagManager] Nav buttons injected on Tags page');
}
```

**Step 2: Update setupNavButtonInjection to use new function name**

Replace all occurrences of `injectTagManagerButton` with `injectNavButtons` in `setupNavButtonInjection`:

```javascript
function setupNavButtonInjection() {
  // Try to inject immediately
  injectNavButtons();

  // Watch for URL changes (SPA navigation)
  let lastUrl = window.location.href;
  const observer = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      // Wait a bit for DOM to update after navigation
      setTimeout(injectNavButtons, 100);
      setTimeout(injectNavButtons, 500);
      setTimeout(injectNavButtons, 1000);
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // Also try on initial load with delays (for refresh on Tags page)
  setTimeout(injectNavButtons, 100);
  setTimeout(injectNavButtons, 500);
  setTimeout(injectNavButtons, 1000);
  setTimeout(injectNavButtons, 2000);
}
```

**Step 3: Verify manually**

1. Navigate to `/tags` in Stash
2. Verify two buttons appear in toolbar (tag icon + sitemap icon)
3. Click sitemap icon, verify it navigates to `/plugins/tag-hierarchy`

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): inject hierarchy button on tags page"
```

---

## Task 5: Add CSS for Tag Hierarchy Page

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (append at end of file)

**Step 1: Add hierarchy-specific styles**

Append to `tag-manager.css`:

```css
/* ============================================
   Tag Hierarchy View Styles
   ============================================ */

.tag-hierarchy-container {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}

.tag-hierarchy {
  background: var(--bs-body-bg, #1a1a2e);
  border-radius: 8px;
  padding: 20px;
}

.tag-hierarchy-header {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.tag-hierarchy-header h2 {
  margin: 0;
  flex: 1;
}

.tag-hierarchy-controls {
  display: flex;
  gap: 10px;
  align-items: center;
}

.tag-hierarchy-controls button {
  padding: 6px 12px;
  border-radius: 4px;
  border: 1px solid var(--bs-border-color, #444);
  background: var(--bs-secondary-bg, #2a2a4a);
  color: var(--bs-body-color, #fff);
  cursor: pointer;
  font-size: 0.85em;
}

.tag-hierarchy-controls button:hover {
  background: var(--bs-tertiary-bg, #3a3a5a);
}

.tag-hierarchy-controls label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.9em;
  cursor: pointer;
}

/* Tree structure */
.th-tree {
  margin-top: 10px;
}

.th-node {
  margin-left: 20px;
  border-left: 1px solid var(--bs-border-color, #444);
  padding-left: 15px;
}

.th-node.th-root {
  margin-left: 0;
  border-left: none;
  padding-left: 0;
}

.th-node-content {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 4px;
  margin-bottom: 2px;
}

.th-node-content:hover {
  background: var(--bs-tertiary-bg, #3a3a5a);
}

/* Expand/collapse toggle */
.th-toggle {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 3px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--bs-secondary-color, #aaa);
}

.th-toggle:hover {
  background: var(--bs-secondary-bg, #2a2a4a);
}

.th-toggle.th-leaf {
  visibility: hidden;
}

/* Tag image thumbnail */
.th-image {
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  background: #000;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.th-image img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.th-image.th-hidden {
  display: none;
}

/* No image placeholder */
.th-image-placeholder {
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  background: var(--bs-secondary-bg, #2a2a4a);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--bs-secondary-color, #666);
  font-size: 24px;
}

.th-image-placeholder.th-hidden {
  display: none;
}

/* Tag info */
.th-info {
  flex: 1;
  min-width: 0;
}

.th-name {
  font-weight: 500;
  color: var(--bs-link-color, #6ea8fe);
  text-decoration: none;
}

.th-name:hover {
  text-decoration: underline;
}

.th-meta {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #aaa);
  margin-top: 2px;
}

/* Children container */
.th-children {
  display: none;
}

.th-children.th-expanded {
  display: block;
}

/* Stats bar */
.th-stats {
  display: flex;
  gap: 15px;
  margin-bottom: 15px;
  padding: 10px 15px;
  background: var(--bs-secondary-bg, #2a2a4a);
  border-radius: 4px;
  font-size: 0.9em;
}

.th-stats .stat {
  color: var(--bs-secondary-color, #aaa);
}

.th-stats .stat strong {
  color: var(--bs-body-color, #fff);
}

/* Loading state */
.th-loading {
  text-align: center;
  padding: 40px;
  color: var(--bs-secondary-color, #aaa);
}

/* Empty state */
.th-empty {
  text-align: center;
  padding: 40px;
  color: var(--bs-secondary-color, #aaa);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add CSS styles for tag hierarchy view"
```

---

## Task 6: Implement GraphQL Query for Tags with Hierarchy

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (add new function after existing GraphQL functions)

**Step 1: Add fetchAllTagsWithHierarchy function**

Add after the existing `fetchLocalTags` function:

```javascript
/**
 * Fetch all tags with hierarchy information (parents, children)
 */
async function fetchAllTagsWithHierarchy() {
  const query = `
    query AllTagsWithHierarchy {
      allTags {
        id
        name
        image_path
        scene_count
        parent_count
        child_count
        parents {
          id
        }
        children {
          id
        }
      }
    }
  `;

  const result = await graphqlRequest(query);
  return result.allTags || [];
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add GraphQL query for tags with hierarchy"
```

---

## Task 7: Implement Tree Building Logic

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (add tree building functions)

**Step 1: Add tree building functions**

Add after `fetchAllTagsWithHierarchy`:

```javascript
/**
 * Build a tree structure from flat tag list
 * Tags with multiple parents appear under each parent
 * @param {Array} tags - Flat array of tags with parent/children info
 * @returns {Array} - Array of root nodes (tags with no parents)
 */
function buildTagTree(tags) {
  // Create a map for quick lookup
  const tagMap = new Map();
  tags.forEach(tag => {
    tagMap.set(tag.id, {
      ...tag,
      childNodes: []
    });
  });

  // Find root tags (no parents) and build children arrays
  const roots = [];

  tags.forEach(tag => {
    const node = tagMap.get(tag.id);

    if (tag.parents.length === 0) {
      // Root tag
      roots.push(node);
    } else {
      // Add this tag as a child to each of its parents
      tag.parents.forEach(parent => {
        const parentNode = tagMap.get(parent.id);
        if (parentNode) {
          parentNode.childNodes.push(node);
        }
      });
    }
  });

  // Sort roots and all children alphabetically by name
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

/**
 * Get stats from tag tree
 */
function getTreeStats(tags) {
  const totalTags = tags.length;
  const rootTags = tags.filter(t => t.parents.length === 0).length;
  const tagsWithChildren = tags.filter(t => t.child_count > 0).length;
  const tagsWithParents = tags.filter(t => t.parent_count > 0).length;

  return {
    totalTags,
    rootTags,
    tagsWithChildren,
    tagsWithParents
  };
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add tree building logic for tag hierarchy"
```

---

## Task 8: Implement Tag Hierarchy Page Rendering

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (replace placeholder TagHierarchyPage)

**Step 1: Replace TagHierarchyPage with full implementation**

Replace the placeholder `TagHierarchyPage` function with:

```javascript
/**
 * Tag Hierarchy page state
 */
let hierarchyTags = [];
let hierarchyTree = [];
let hierarchyStats = {};
let showImages = true;
let expandedNodes = new Set();

/**
 * Render a single tree node
 */
function renderTreeNode(node, isRoot = false) {
  const hasChildren = node.childNodes.length > 0;
  const isExpanded = expandedNodes.has(node.id);

  // Build scene/child count text
  const metaParts = [];
  if (node.scene_count > 0) {
    metaParts.push(`${node.scene_count} scene${node.scene_count !== 1 ? 's' : ''}`);
  }
  if (node.child_count > 0) {
    metaParts.push(`${node.child_count} sub-tag${node.child_count !== 1 ? 's' : ''}`);
  }
  const metaText = metaParts.length > 0 ? metaParts.join(', ') : '';

  // Image HTML
  const imageHtml = node.image_path
    ? `<div class="th-image ${showImages ? '' : 'th-hidden'}">
         <img src="${escapeHtml(node.image_path)}" alt="${escapeHtml(node.name)}" loading="lazy">
       </div>`
    : `<div class="th-image-placeholder ${showImages ? '' : 'th-hidden'}">
         <span>?</span>
       </div>`;

  // Children HTML (recursive)
  let childrenHtml = '';
  if (hasChildren) {
    const childNodes = node.childNodes.map(child => renderTreeNode(child, false)).join('');
    childrenHtml = `<div class="th-children ${isExpanded ? 'th-expanded' : ''}" data-parent-id="${node.id}">${childNodes}</div>`;
  }

  // Toggle icon
  const toggleIcon = hasChildren
    ? (isExpanded ? '&#9660;' : '&#9654;')  // Down arrow / Right arrow
    : '';

  return `
    <div class="th-node ${isRoot ? 'th-root' : ''}" data-tag-id="${node.id}">
      <div class="th-node-content">
        <span class="th-toggle ${hasChildren ? '' : 'th-leaf'}" data-tag-id="${node.id}">${toggleIcon}</span>
        ${imageHtml}
        <div class="th-info">
          <a href="/tags/${node.id}" class="th-name">${escapeHtml(node.name)}</a>
          ${metaText ? `<div class="th-meta">${metaText}</div>` : ''}
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
    <div class="tag-hierarchy">
      <div class="tag-hierarchy-header">
        <h2>Tag Hierarchy</h2>
        <div class="tag-hierarchy-controls">
          <button id="th-expand-all">Expand All</button>
          <button id="th-collapse-all">Collapse All</button>
          <label>
            <input type="checkbox" id="th-show-images" ${showImages ? 'checked' : ''}>
            Show images
          </label>
        </div>
      </div>
      <div class="th-stats">
        <span class="stat"><strong>${hierarchyStats.totalTags}</strong> total tags</span>
        <span class="stat"><strong>${hierarchyStats.rootTags}</strong> root tags</span>
        <span class="stat"><strong>${hierarchyStats.tagsWithChildren}</strong> with sub-tags</span>
        <span class="stat"><strong>${hierarchyStats.tagsWithParents}</strong> with parents</span>
      </div>
      <div class="th-tree">
        ${treeHtml || '<div class="th-empty">No tags found</div>'}
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
  // Toggle expand/collapse on node click
  container.querySelectorAll('.th-toggle').forEach(toggle => {
    toggle.addEventListener('click', (e) => {
      const tagId = e.target.dataset.tagId;
      if (!tagId) return;

      const childrenContainer = container.querySelector(`.th-children[data-parent-id="${tagId}"]`);
      if (!childrenContainer) return;

      if (expandedNodes.has(tagId)) {
        expandedNodes.delete(tagId);
        childrenContainer.classList.remove('th-expanded');
        e.target.innerHTML = '&#9654;';  // Right arrow
      } else {
        expandedNodes.add(tagId);
        childrenContainer.classList.add('th-expanded');
        e.target.innerHTML = '&#9660;';  // Down arrow
      }
    });
  });

  // Expand All button
  const expandAllBtn = container.querySelector('#th-expand-all');
  if (expandAllBtn) {
    expandAllBtn.addEventListener('click', () => {
      container.querySelectorAll('.th-children').forEach(el => {
        el.classList.add('th-expanded');
        const parentId = el.dataset.parentId;
        if (parentId) expandedNodes.add(parentId);
      });
      container.querySelectorAll('.th-toggle:not(.th-leaf)').forEach(el => {
        el.innerHTML = '&#9660;';
      });
    });
  }

  // Collapse All button
  const collapseAllBtn = container.querySelector('#th-collapse-all');
  if (collapseAllBtn) {
    collapseAllBtn.addEventListener('click', () => {
      container.querySelectorAll('.th-children').forEach(el => {
        el.classList.remove('th-expanded');
      });
      container.querySelectorAll('.th-toggle:not(.th-leaf)').forEach(el => {
        el.innerHTML = '&#9654;';
      });
      expandedNodes.clear();
    });
  }

  // Show images toggle
  const showImagesCheckbox = container.querySelector('#th-show-images');
  if (showImagesCheckbox) {
    showImagesCheckbox.addEventListener('change', (e) => {
      showImages = e.target.checked;
      container.querySelectorAll('.th-image, .th-image-placeholder').forEach(el => {
        el.classList.toggle('th-hidden', !showImages);
      });
    });
  }
}

/**
 * Tag Hierarchy page component
 */
function TagHierarchyPage() {
  const React = PluginApi.React;
  const containerRef = React.useRef(null);

  React.useEffect(() => {
    async function init() {
      if (!containerRef.current) return;

      document.title = "Tag Hierarchy | Stash";
      containerRef.current.innerHTML = '<div class="tag-hierarchy"><div class="th-loading">Loading tags...</div></div>';

      try {
        // Fetch all tags with hierarchy info
        hierarchyTags = await fetchAllTagsWithHierarchy();
        console.debug(`[tagManager] Loaded ${hierarchyTags.length} tags for hierarchy`);

        // Build tree structure
        hierarchyTree = buildTagTree(hierarchyTags);
        hierarchyStats = getTreeStats(hierarchyTags);
        console.debug(`[tagManager] Built tree with ${hierarchyTree.length} root nodes`);

        // Reset expand state
        expandedNodes.clear();

        // Render the page
        renderHierarchyPage(containerRef.current);
      } catch (e) {
        console.error("[tagManager] Failed to load tag hierarchy:", e);
        containerRef.current.innerHTML = `<div class="tag-hierarchy"><div class="th-loading">Error loading tags: ${escapeHtml(e.message)}</div></div>`;
      }
    }

    init();
  }, []);

  return React.createElement('div', {
    ref: containerRef,
    className: 'tag-hierarchy-container'
  });
}
```

**Step 2: Verify manually**

1. Navigate to `/plugins/tag-hierarchy`
2. Verify tree renders with root tags
3. Click expand arrows to expand/collapse
4. Test "Expand All" / "Collapse All" buttons
5. Toggle "Show images" checkbox
6. Click tag names to verify they link to `/tags/{id}`

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): implement tag hierarchy page with tree view"
```

---

## Task 9: Final Testing and Cleanup

**Files:**
- Review: `plugins/tagManager/tag-manager.js`
- Review: `plugins/tagManager/tag-manager.css`

**Step 1: Manual testing checklist**

- [ ] Navigate to `/tags` - both buttons visible in toolbar
- [ ] Tag Matcher button opens `/plugins/tag-manager` with title "Tag Matcher | Stash"
- [ ] Tag Hierarchy button opens `/plugins/tag-hierarchy` with title "Tag Hierarchy | Stash"
- [ ] Hierarchy page shows stats bar with correct counts
- [ ] Root tags (no parents) appear at top level
- [ ] Tags with children show expand arrow
- [ ] Clicking expand arrow shows children
- [ ] Tags appear under each parent (if multiple parents)
- [ ] "Expand All" expands entire tree
- [ ] "Collapse All" collapses entire tree
- [ ] "Show images" checkbox toggles 64px thumbnails
- [ ] Images letterbox/pillarbox correctly (no stretching)
- [ ] Tag names link to correct `/tags/{id}` pages
- [ ] Scene count and sub-tag count display correctly
- [ ] Dark theme colors look correct

**Step 2: Commit any fixes**

```bash
git add plugins/tagManager/
git commit -m "fix(tagManager): address testing feedback"
```

**Step 3: Final commit for feature**

If all tests pass:

```bash
git add plugins/tagManager/
git commit -m "feat(tagManager): complete tag hierarchy view implementation"
```

---

## Summary

This implementation adds:

1. **Page titles** - "Tag Matcher | Stash" and "Tag Hierarchy | Stash" for browser tabs
2. **Sitemap icon** - New button with hierarchy visual
3. **Second route** - `/plugins/tag-hierarchy` for tree view
4. **Dual button injection** - Both buttons on `/tags` toolbar
5. **GraphQL query** - Fetches all tags with parent/child relationships
6. **Tree builder** - Builds hierarchical structure, handles multiple parents
7. **Collapsible tree UI** - All collapsed by default, expand/collapse controls
8. **Image thumbnails** - 64px letterboxed, toggleable visibility
9. **Metadata display** - Scene count + sub-tag count per tag
10. **Stats bar** - Total tags, root tags, hierarchy stats

All code is contained in the existing `tag-manager.js` and `tag-manager.css` files - no new files needed.
