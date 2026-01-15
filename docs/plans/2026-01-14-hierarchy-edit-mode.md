# Tag Hierarchy Edit Mode

## Problem

The current hierarchy editing experience saves changes immediately on every action (context menu, drag-drop, keyboard shortcut). This creates two issues:

1. **Accidental changes** - A stray drag or misclick immediately modifies the database
2. **No review before commit** - Users can't see the full picture of changes before they're persisted

## Solution

Add an edit mode pattern where changes are queued locally and only committed when the user explicitly clicks "Save".

## User Flow

```
View Mode (default)
    │
    ├─ Right-click "Add parent" ─┐
    ├─ Drag tag onto another ────┼──► Edit Mode activates automatically
    ├─ Ctrl+V paste ─────────────┘
    │
    ▼
Edit Mode
    │
    ├─ Changes panel appears (bottom of hierarchy view)
    ├─ "Save" and "Cancel" buttons visible
    ├─ Tree updates locally to show pending state
    ├─ Can continue making more changes
    │
    ├─ Click "Save" ──► All changes sent to server ──► Back to View Mode
    └─ Click "Cancel" ──► Discard all changes ──► Back to View Mode
```

## Changes Panel

A panel at the bottom of the hierarchy view showing pending changes:

```
┌─ Pending Changes (3) ──────────────────────────────┐
│                                                     │
│  • Added "Action" as parent of "Fight"        [x]  │
│  • Removed "Scene Type" as parent of "Solo"   [x]  │
│  • Added "Outdoor" as child of "Location"     [x]  │
│                                                     │
│                          [Cancel]  [Save Changes]  │
└─────────────────────────────────────────────────────┘
```

- Each change has an [x] button to remove just that pending change
- Clear description of what changed (action + tag names)
- Count in header updates as edits are made

## Data Model

```javascript
let pendingChanges = [];
let originalParentMap = new Map(); // tagId -> Set of parentIds (snapshot at edit start)
let isEditMode = false;

// Change object structure:
{
  type: 'add-parent' | 'remove-parent',
  tagId: string,
  tagName: string,
  parentId: string,
  parentName: string,
  timestamp: number
}
```

## Behavior Details

### Entering Edit Mode

- First mutation attempt triggers `enterEditMode()`
- Snapshot current parent relationships into `originalParentMap`
- Show changes panel (initially empty but visible to indicate edit mode)
- Queue the triggering change instead of calling GraphQL

### While in Edit Mode

- All changes queue into `pendingChanges[]`
- Tree updates locally to reflect pending state (user sees the effect)
- Opposite changes cancel out (add parent A, then remove parent A = no net change)
- Circular reference prevention still applies (checked against pending state)

### Save

- Compute final parent state for each modified tag
- Send mutations to server (batch where possible)
- On success: clear pending changes, exit edit mode, full tree refresh
- On error: show which changes failed, stay in edit mode to allow retry/adjustment

### Cancel

- Discard `pendingChanges`
- Restore tree display to original state
- Exit edit mode

### Navigation Away

- If pending changes exist when switching tabs, prompt: "You have unsaved changes. Discard?"

## Implementation Plan

### Phase 1: State Management

1. Add state variables (`pendingChanges`, `originalParentMap`, `isEditMode`)
2. Create `enterEditMode()` - snapshots current state, shows panel
3. Create `exitEditMode(save: boolean)` - either commits or discards
4. Create `addPendingChange()` - queues change, handles cancellation of opposite changes

### Phase 2: Changes Panel UI

5. Create `renderChangesPanel()` - pending changes list with Save/Cancel buttons
6. Add CSS for the panel (fixed bottom position, change items, buttons)
7. Wire up individual change removal ([x] buttons)

### Phase 3: Modify Existing Actions

8. Update `addParent()` / `removeParent()` to queue changes when in edit mode (instead of immediate mutation)
9. Update local tree rendering to reflect pending changes visually
10. Add unsaved changes warning on tab navigation

### Phase 4: Save Logic

11. Implement batch save - compute final state per tag, send mutations
12. Handle partial failures gracefully (show errors, allow retry)

## Scope

- ~200-300 lines of new code
- Modifications to ~5 existing functions (`addParent`, `removeParent`, `renderHierarchyPage`, tab switching logic)
- New CSS for changes panel (~30-50 lines)
