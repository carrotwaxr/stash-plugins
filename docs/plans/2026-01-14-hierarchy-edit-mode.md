# Tag Hierarchy Edit Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add edit mode to tag hierarchy so changes are queued locally and only committed on explicit Save.

**Architecture:** Auto-enter edit mode on first change, show pending changes panel, queue mutations locally, batch-save on confirm.

**Tech Stack:** Vanilla JavaScript, CSS, Stash GraphQL API

---

## Problem

The current hierarchy editing experience saves changes immediately on every action (context menu, drag-drop, keyboard shortcut). This creates two issues:

1. **Accidental changes** - A stray drag or misclick immediately modifies the database
2. **No review before commit** - Users can't see the full picture of changes before they're persisted

## Solution

Add an edit mode pattern where changes are queued locally and only committed when the user explicitly clicks "Save".

---

## Task 1: Add Edit Mode State Variables

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2171-2182` (after existing state vars)

**Step 1: Add state variables after line 2182**

Add these variables after `let copiedTagId = null;`:

```javascript
// Edit mode state
let isEditMode = false;
let pendingChanges = [];
let originalParentMap = new Map(); // tagId -> array of parent ids (snapshot at edit start)
```

**Step 2: Verify by searching**

Run: Search for `isEditMode` to confirm added.

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add edit mode state variables"
```

---

## Task 2: Create enterEditMode Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after state variables, around line 2186)

**Step 1: Add enterEditMode function**

```javascript
/**
 * Enter edit mode - snapshot current state and show changes panel
 */
function enterEditMode() {
  if (isEditMode) return;

  isEditMode = true;
  pendingChanges = [];

  // Snapshot current parent relationships
  originalParentMap.clear();
  for (const tag of hierarchyTags) {
    originalParentMap.set(tag.id, tag.parents?.map(p => p.id) || []);
  }

  // Show the changes panel
  renderChangesPanel();
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add enterEditMode function"
```

---

## Task 3: Create addPendingChange Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after enterEditMode)

**Step 1: Add addPendingChange function**

```javascript
/**
 * Add a pending change, handling cancellation of opposite changes
 */
function addPendingChange(type, tagId, tagName, parentId, parentName) {
  // Check for opposite change that would cancel this out
  const oppositeType = type === 'add-parent' ? 'remove-parent' : 'add-parent';
  const oppositeIdx = pendingChanges.findIndex(c =>
    c.type === oppositeType && c.tagId === tagId && c.parentId === parentId
  );

  if (oppositeIdx !== -1) {
    // Remove the opposite change (they cancel out)
    pendingChanges.splice(oppositeIdx, 1);
  } else {
    // Check if this exact change already exists
    const existingIdx = pendingChanges.findIndex(c =>
      c.type === type && c.tagId === tagId && c.parentId === parentId
    );

    if (existingIdx === -1) {
      pendingChanges.push({
        type,
        tagId,
        tagName,
        parentId,
        parentName,
        timestamp: Date.now()
      });
    }
  }

  // Re-render the changes panel
  renderChangesPanel();
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add addPendingChange with cancellation logic"
```

---

## Task 4: Create removePendingChange Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after addPendingChange)

**Step 1: Add removePendingChange function**

```javascript
/**
 * Remove a specific pending change by index
 */
function removePendingChange(index) {
  if (index >= 0 && index < pendingChanges.length) {
    pendingChanges.splice(index, 1);

    // If no changes left, exit edit mode
    if (pendingChanges.length === 0) {
      exitEditMode(false);
    } else {
      renderChangesPanel();
      // Re-render tree to update visual state
      applyPendingChangesToTree();
    }
  }
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add removePendingChange function"
```

---

## Task 5: Create exitEditMode Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after removePendingChange)

**Step 1: Add exitEditMode function**

```javascript
/**
 * Exit edit mode - either save or discard changes
 */
async function exitEditMode(save) {
  if (!isEditMode) return;

  if (save && pendingChanges.length > 0) {
    await savePendingChanges();
  }

  isEditMode = false;
  pendingChanges = [];
  originalParentMap.clear();

  // Remove the changes panel
  const panel = document.getElementById('th-changes-panel');
  if (panel) panel.remove();

  // Refresh from server to ensure consistent state
  await refreshHierarchy();
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add exitEditMode function"
```

---

## Task 6: Create renderChangesPanel Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after exitEditMode)

**Step 1: Add renderChangesPanel function**

```javascript
/**
 * Render the pending changes panel at the bottom of the hierarchy view
 */
function renderChangesPanel() {
  // Remove existing panel
  let panel = document.getElementById('th-changes-panel');
  if (panel) panel.remove();

  const container = document.querySelector('.tag-hierarchy');
  if (!container) return;

  panel = document.createElement('div');
  panel.id = 'th-changes-panel';
  panel.className = 'th-changes-panel';

  const changesHtml = pendingChanges.map((change, idx) => {
    const action = change.type === 'add-parent'
      ? `Added "${escapeHtml(change.parentName)}" as parent of "${escapeHtml(change.tagName)}"`
      : `Removed "${escapeHtml(change.tagName)}" from "${escapeHtml(change.parentName)}"`;
    return `
      <div class="th-change-item">
        <span class="th-change-text">${action}</span>
        <button class="th-change-remove" data-index="${idx}" title="Remove this change">&times;</button>
      </div>
    `;
  }).join('');

  panel.innerHTML = `
    <div class="th-changes-header">
      <span>Pending Changes (${pendingChanges.length})</span>
    </div>
    <div class="th-changes-list">
      ${changesHtml || '<div class="th-no-changes">No changes yet</div>'}
    </div>
    <div class="th-changes-actions">
      <button class="btn btn-secondary" id="th-cancel-changes">Cancel</button>
      <button class="btn btn-primary" id="th-save-changes" ${pendingChanges.length === 0 ? 'disabled' : ''}>Save Changes</button>
    </div>
  `;

  container.appendChild(panel);

  // Attach event handlers
  panel.querySelector('#th-cancel-changes')?.addEventListener('click', () => exitEditMode(false));
  panel.querySelector('#th-save-changes')?.addEventListener('click', () => exitEditMode(true));
  panel.querySelectorAll('.th-change-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.index, 10);
      removePendingChange(idx);
    });
  });
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add renderChangesPanel function"
```

---

## Task 7: Add CSS for Changes Panel

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (at end of file)

**Step 1: Add CSS styles**

```css
/* Edit mode - changes panel */
.th-changes-panel {
  position: sticky;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--bs-dark, #212529);
  border-top: 2px solid var(--bs-primary, #0d6efd);
  padding: 12px 16px;
  z-index: 100;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.3);
}

.th-changes-header {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--bs-light, #f8f9fa);
}

.th-changes-list {
  max-height: 150px;
  overflow-y: auto;
  margin-bottom: 12px;
}

.th-change-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  background: var(--bs-gray-800, #343a40);
  border-radius: 4px;
  margin-bottom: 4px;
}

.th-change-text {
  flex: 1;
  font-size: 0.9rem;
}

.th-change-remove {
  background: none;
  border: none;
  color: var(--bs-gray-500, #adb5bd);
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.th-change-remove:hover {
  color: var(--bs-danger, #dc3545);
}

.th-no-changes {
  color: var(--bs-gray-500, #adb5bd);
  font-style: italic;
  padding: 8px;
}

.th-changes-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "feat(hierarchy): add CSS for changes panel"
```

---

## Task 8: Create savePendingChanges Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after renderChangesPanel)

**Step 1: Add savePendingChanges function**

```javascript
/**
 * Save all pending changes to the server
 */
async function savePendingChanges() {
  if (pendingChanges.length === 0) return;

  // Compute final parent state for each modified tag
  const tagUpdates = new Map(); // tagId -> Set of final parent ids

  // Start with original parents
  for (const change of pendingChanges) {
    if (!tagUpdates.has(change.tagId)) {
      const original = originalParentMap.get(change.tagId) || [];
      tagUpdates.set(change.tagId, new Set(original));
    }
  }

  // Apply changes
  for (const change of pendingChanges) {
    const parentSet = tagUpdates.get(change.tagId);
    if (change.type === 'add-parent') {
      parentSet.add(change.parentId);
    } else {
      parentSet.delete(change.parentId);
    }
  }

  // Send mutations
  const errors = [];
  for (const [tagId, parentSet] of tagUpdates) {
    try {
      await updateTagParents(tagId, Array.from(parentSet));
    } catch (err) {
      const tag = hierarchyTags.find(t => t.id === tagId);
      errors.push(`Failed to update "${tag?.name || tagId}": ${err.message}`);
    }
  }

  if (errors.length > 0) {
    showToast(`Some changes failed:\n${errors.join('\n')}`, 'error');
  } else {
    showToast(`Saved ${pendingChanges.length} change${pendingChanges.length !== 1 ? 's' : ''}`);
  }
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add savePendingChanges function"
```

---

## Task 9: Modify addParent to Queue Changes

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2444-2459` (addParent function)

**Step 1: Replace addParent function**

Replace the existing `addParent` function with:

```javascript
/**
 * Add a parent to a tag
 */
async function addParent(tagId, newParentId) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  const parent = hierarchyTags.find(t => t.id === newParentId);
  if (!tag || !parent) return;

  // Enter edit mode if not already
  enterEditMode();

  // Queue the change
  addPendingChange('add-parent', tagId, tag.name, newParentId, parent.name);

  // Update local state for immediate visual feedback
  applyPendingChangesToTree();

  showToast(`Queued: Add "${tag.name}" as child of "${parent.name}"`);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): modify addParent to queue changes in edit mode"
```

---

## Task 10: Modify addChild to Queue Changes

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2464-2479` (addChild function)

**Step 1: Replace addChild function**

Replace the existing `addChild` function with:

```javascript
/**
 * Add a child to a tag (by adding the target as the child's parent)
 */
async function addChild(parentId, childId) {
  const child = hierarchyTags.find(t => t.id === childId);
  const parent = hierarchyTags.find(t => t.id === parentId);
  if (!child || !parent) return;

  // Enter edit mode if not already
  enterEditMode();

  // Queue the change (same as addParent, just different perspective)
  addPendingChange('add-parent', childId, child.name, parentId, parent.name);

  // Update local state for immediate visual feedback
  applyPendingChangesToTree();

  showToast(`Queued: Add "${child.name}" as child of "${parent.name}"`);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): modify addChild to queue changes in edit mode"
```

---

## Task 11: Modify removeParent to Queue Changes

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2552-2568` (removeParent function)

**Step 1: Replace removeParent function**

Replace the existing `removeParent` function with:

```javascript
/**
 * Remove a specific parent from a tag
 */
async function removeParent(tagId, parentIdToRemove) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  const parent = hierarchyTags.find(t => t.id === parentIdToRemove);
  if (!tag || !parent) return;

  // Enter edit mode if not already
  enterEditMode();

  // Queue the change
  addPendingChange('remove-parent', tagId, tag.name, parentIdToRemove, parent.name);

  // Update local state for immediate visual feedback
  applyPendingChangesToTree();

  showToast(`Queued: Remove "${tag.name}" from "${parent.name}"`);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): modify removeParent to queue changes in edit mode"
```

---

## Task 12: Modify makeRoot to Queue Changes

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2573-2589` (makeRoot function)

**Step 1: Replace makeRoot function**

Replace the existing `makeRoot` function with:

```javascript
/**
 * Make a tag a root by removing all its parents
 */
async function makeRoot(tagId) {
  const tag = hierarchyTags.find(t => t.id === tagId);
  if (!tag) return;

  if (!tag.parents || tag.parents.length === 0) {
    showToast('Tag is already a root');
    return;
  }

  // Enter edit mode if not already
  enterEditMode();

  // Queue removal of each parent
  for (const parent of tag.parents) {
    addPendingChange('remove-parent', tagId, tag.name, parent.id, parent.name);
  }

  // Update local state for immediate visual feedback
  applyPendingChangesToTree();

  showToast(`Queued: Make "${tag.name}" a root tag`);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): modify makeRoot to queue changes in edit mode"
```

---

## Task 13: Create applyPendingChangesToTree Function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (after savePendingChanges)

**Step 1: Add applyPendingChangesToTree function**

```javascript
/**
 * Apply pending changes to local tree state and re-render
 */
function applyPendingChangesToTree() {
  // Create a working copy of tags with pending changes applied
  const workingTags = hierarchyTags.map(tag => {
    // Get original parents
    const originalParents = originalParentMap.get(tag.id) || tag.parents?.map(p => p.id) || [];
    const parentSet = new Set(originalParents);

    // Apply pending changes for this tag
    for (const change of pendingChanges) {
      if (change.tagId === tag.id) {
        if (change.type === 'add-parent') {
          parentSet.add(change.parentId);
        } else {
          parentSet.delete(change.parentId);
        }
      }
    }

    // Convert back to parent objects
    const newParents = Array.from(parentSet).map(pid => {
      const parentTag = hierarchyTags.find(t => t.id === pid);
      return parentTag ? { id: pid, name: parentTag.name } : { id: pid, name: 'Unknown' };
    });

    return { ...tag, parents: newParents };
  });

  // Rebuild and re-render tree
  hierarchyTree = buildTagTree(workingTags);
  const container = document.querySelector('.tag-hierarchy-container');
  if (container) {
    renderHierarchyPage(container);
    // Re-attach the changes panel after re-render
    if (isEditMode) {
      renderChangesPanel();
    }
  }
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add applyPendingChangesToTree for local preview"
```

---

## Task 14: Update wouldCreateCircularRef for Pending State

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:2485-2505` (wouldCreateCircularRef function)

**Step 1: Update wouldCreateCircularRef to consider pending changes**

Replace the function with:

```javascript
/**
 * Check if making potentialParentId a parent of tagId would create a circular reference.
 * This happens if tagId is already an ancestor of potentialParentId.
 * Also considers pending changes that haven't been saved yet.
 */
function wouldCreateCircularRef(potentialParentId, tagId) {
  // Build effective parent map considering pending changes
  const effectiveParents = new Map();

  for (const tag of hierarchyTags) {
    const parents = new Set(tag.parents?.map(p => p.id) || []);
    effectiveParents.set(tag.id, parents);
  }

  // Apply pending changes
  for (const change of pendingChanges) {
    const parents = effectiveParents.get(change.tagId) || new Set();
    if (change.type === 'add-parent') {
      parents.add(change.parentId);
    } else {
      parents.delete(change.parentId);
    }
    effectiveParents.set(change.tagId, parents);
  }

  // Build a set of all ancestors of potentialParentId
  const ancestors = new Set();

  function collectAncestors(id) {
    const parents = effectiveParents.get(id);
    if (!parents) return;

    for (const parentId of parents) {
      if (ancestors.has(parentId)) continue; // Already visited
      ancestors.add(parentId);
      collectAncestors(parentId);
    }
  }

  collectAncestors(potentialParentId);

  // If tagId is an ancestor of potentialParentId, adding potentialParentId as parent of tagId
  // would create: tagId -> potentialParentId -> ... -> tagId (circular)
  return ancestors.has(tagId);
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): update circular ref check to consider pending changes"
```

---

## Task 15: Add Tab Navigation Warning

**Files:**
- Modify: `plugins/tagManager/tag-manager.js:1092-1100` (tab click handler)

**Step 1: Update tab click handler to warn about unsaved changes**

Find and replace the tab switching handler:

```javascript
// Tab switching
container.querySelectorAll('.tm-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const newTab = tab.dataset.tab;
    if (newTab !== activeTab) {
      // Warn if there are pending hierarchy changes
      if (isEditMode && pendingChanges.length > 0) {
        if (!confirm('You have unsaved hierarchy changes. Discard them?')) {
          return;
        }
        // Discard changes
        isEditMode = false;
        pendingChanges = [];
        originalParentMap.clear();
      }
      activeTab = newTab;
      renderPage(container);
    }
  });
});
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(hierarchy): add unsaved changes warning on tab navigation"
```

---

## Task 16: Test Locally

**Step 1: Start Stash and open Tag Manager**

Open the Tag Manager plugin in Stash and navigate to the Hierarchy tab.

**Step 2: Test auto-enter edit mode**

1. Right-click a tag and select "Add parent"
2. Verify changes panel appears at bottom
3. Verify the change is listed

**Step 3: Test change cancellation**

1. Add a parent to a tag
2. Remove the same parent
3. Verify the changes cancel out

**Step 4: Test individual change removal**

1. Make 2-3 changes
2. Click [x] on one change
3. Verify only that change is removed

**Step 5: Test Cancel button**

1. Make some changes
2. Click Cancel
3. Verify all changes discarded, tree restored

**Step 6: Test Save button**

1. Make some changes
2. Click Save Changes
3. Verify changes saved to server

**Step 7: Test tab navigation warning**

1. Make some changes
2. Try to switch tabs
3. Verify confirmation dialog appears

**Step 8: Commit final state**

```bash
git add -A
git commit -m "feat(hierarchy): complete edit mode implementation"
```

---

## Summary

This implementation adds an edit mode to the tag hierarchy that:

1. Auto-activates on first change (no explicit toggle needed)
2. Shows a pending changes panel at the bottom of the view
3. Queues all changes locally until Save is clicked
4. Allows removing individual pending changes
5. Warns when navigating away with unsaved changes
6. Updates circular reference detection to consider pending state
7. Batch-saves all changes on confirm

Key files modified:
- `plugins/tagManager/tag-manager.js` - All logic (~200 lines added)
- `plugins/tagManager/tag-manager.css` - Panel styling (~50 lines added)
