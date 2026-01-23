# Studio Manager Plugin Design

## Overview

A Stash plugin providing a Studio Hierarchy page for visualizing and editing parent-child relationships between studios. Similar to the existing Tag Manager plugin but with a reduced feature set focused on hierarchy management.

## Scope

**In scope:**
- Studio Hierarchy page with tree visualization
- Full editing support (drag-drop, context menu, keyboard)
- Button injection on Studios page
- README documentation

**Out of scope (for now):**
- Main Studio Manager page (matching/sync features)
- StashDB integration
- Python backend

## Plugin Structure

```
plugins/studioManager/
├── studioManager.yml      # Plugin manifest
├── studio-manager.js      # UI code
├── studio-manager.css     # Styling
└── README.md              # Documentation
```

## Button Injection

- **Location:** Studios page toolbar (`/studios`)
- **Icon:** Tree/hierarchy icon
- **Tooltip:** "Studio Hierarchy"
- **Position:** After view mode controls (matching Tag Manager placement)
- **Action:** Navigate to `/plugins/studio-hierarchy`

**Implementation:** Watch for URL changes, find `.filtered-list-toolbar`, insert button with retry delays for async DOM updates.

## Hierarchy Page

### Route

`/plugins/studio-hierarchy`

### Layout

**Header:**
- Title: "Studio Hierarchy"
- Controls: Expand All, Collapse All, Show/Hide Images toggle
- Stats: Total studios, root studios, max depth

**Tree area:**
- Expandable/collapsible nodes
- Each node displays:
  - Expand/collapse arrow (if has children)
  - Studio image (with placeholder if none)
  - Studio name (clickable link to Stash)
  - Metadata: scene count, image count, gallery count, child studio count

**Root drop zone:**
- Located at bottom of tree
- Label: "Drop here to remove parent"

**Pending changes panel:**
- Fixed at bottom, appears when edits exist
- Lists changes: "Set [Child] parent to [Parent]" or "Remove parent from [Studio]"
- Save and Cancel buttons

### Editing Interactions

**Drag and drop:**
- Drag studio onto another to set as child
- Drag onto root drop zone to remove parent
- Visual feedback: green for valid, red for circular reference
- Prevents circular references (can't drop parent onto descendant)

**Context menu (right-click):**
- View Studio
- Edit Studio
- Remove Parent (if has parent)
- Expand/Collapse All Children

**Keyboard:**
- Delete: Remove parent from selected studio

**Selection:**
- Click to select node
- Selection used for keyboard actions

## Data Model

Studios use single-parent hierarchy (simpler than tags' multi-parent model):
- Each studio has at most one `parent_studio`
- Each studio can have multiple `child_studios`
- No duplicate nodes in tree (unlike tags)

### GraphQL Queries

**Fetch all studios:**
```graphql
query FindStudios {
  findStudios(filter: { per_page: -1 }) {
    studios {
      id
      name
      image_path
      scene_count
      image_count
      gallery_count
      parent_studio { id }
      child_studios { id }
    }
  }
}
```

**Update parent:**
```graphql
mutation StudioUpdate($input: StudioUpdateInput!) {
  studioUpdate(input: $input) {
    id
    parent_studio { id }
  }
}
```

### Tree Building

1. Create map of studios by ID
2. Identify roots (studios with no parent_studio)
3. Build childNodes arrays from child_studios references
4. Single-parent means straightforward tree (no multi-parent complexity)

## Styling

Adapt Tag Manager CSS with renamed classes:
- `.th-*` → `.sh-*` (studio hierarchy)
- Same tree structure, node layout, animations
- Same drag-drop feedback styles
- Same context menu and pending changes panel
- Same toast notifications

## Documentation

**Plugin README:** Usage instructions, features, keyboard shortcuts

**Repo README:** Add Studio Manager to plugin list with brief description

## Implementation Reference

Primary reference: `plugins/tagManager/tag-manager.js`

Key functions to adapt:
- `injectNavButtons()` → target `/studios` instead of `/tags`
- `TagHierarchyPage` → `StudioHierarchyPage`
- `buildTagTree()` → `buildStudioTree()` (simpler single-parent logic)
- `fetchAllTagsWithHierarchy()` → `fetchAllStudiosWithHierarchy()`
- `updateTagParents()` → `updateStudioParent()` (single parent_id)
- `wouldCreateCircularRef()` → adapt for single-parent model
