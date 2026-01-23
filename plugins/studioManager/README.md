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
