# Studio Manager

Manage studio hierarchy with a visual tree editor. View and edit parent-child studio relationships.

## Features

- **Studio Hierarchy** - Visual tree view of studio parent-child relationships
- **Drag and Drop** - Drag studios to set parent relationships
- **Context Menu** - Right-click for quick actions (view, edit, remove parent)
- **Keyboard Shortcuts** - Delete key to remove parent
- **Pending Changes** - Review and save multiple changes at once

## Requirements

- Stash v0.30+

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

Shortcuts are off while you type in a text field.

### Saving Changes

Edits are queued as pending changes. Nothing is written until you save.
1. Make your edits (drag and drop, context menu, keyboard).
2. Review the pending changes in the panel at the bottom. Click the **x** on a row to drop that one change and keep the rest.
3. Click **Save Changes** to apply them, or **Cancel** to discard them all.

While a save runs, editing is locked and the button reads "Saving…".

The plugin saves parent removals first, then new parents, shallowest first. A change that would create a cycle is refused before any request is sent. This includes a parent whose own chain runs into an existing cycle.

If a change fails, it stays pending with the error on its row. The others are still saved. A toast reports "N saved, M failed". Fix the problem and save again.

### Navigation and Unsaved Changes

Studio names, the context menu (View Studio, Edit Studio) and the toolbar button open pages inside Stash without a full reload. If you have pending changes, the plugin asks before it leaves the page.

The plugin can't intercept Stash's own navbar or the browser's back button. If you leave that way, your pending changes stay in memory. When you come back to the hierarchy page, the plugin restores them and shows "N unsaved changes restored". Changes that no longer apply, such as a studio that was deleted, are dropped.

A page reload or closing the tab loses pending changes. The browser warns you first while changes are pending.

## Known Limits

- Drag and drop works with a mouse only.
- The whole tree is rendered at once. A very large library can be slow.
- Studios in an existing parent cycle stay visible. The studio with the smallest id in each cycle is shown at the top level, with the rest of the cycle beneath it. The plugin doesn't mark them yet.

## Changelog

### 0.1.1
- Studios in an existing parent cycle are no longer hidden.
- Removing one pending change keeps the others. Cancel restores the tree without a refetch. Stats and the context menu reflect pending changes, and the ancestors of a moved studio are expanded.
- Saves lock editing, run in a safe order, refuse cycles, and keep failed changes pending with the error shown.
- Navigation stays inside Stash and respects a sub-path. The plugin warns before you lose unsaved changes and restores them when you return.
- Only Delete removes a parent (not Backspace), and shortcuts are off while you type.
- No more page-wide observer or piled-up event listeners.

### 0.1.0
- First release.

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
