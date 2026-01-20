# Tag Manager User Guide

This guide covers all Tag Manager features in detail. For installation and quick start, see the [README](README.md).

## Table of Contents

- [Overview](#overview)
- [Accessing Tag Manager](#accessing-tag-manager)
- [Tag Matching (Match Tab)](#tag-matching-match-tab)
- [Browse & Import (Browse StashDB Tab)](#browse--import-browse-stashdb-tab)
- [Tag Hierarchy View](#tag-hierarchy-view)
- [Scene Tag Sync](#scene-tag-sync)
- [Tag Blacklist](#tag-blacklist)
- [Custom Synonyms](#custom-synonyms)
- [Tag Caching](#tag-caching)
- [Understanding Match Types](#understanding-match-types)

---

## Overview

Tag Manager helps you manage your Stash tag library by:

1. **Matching local tags to StashDB** - Link your tags to their StashDB equivalents for standardization
2. **Importing new tags** - Browse StashDB categories and import tags you don't have yet
3. **Managing hierarchy** - Organize tags into parent/child relationships
4. **Syncing scene tags** - Automatically add StashDB tags to scenes based on their StashDB metadata

### What Tag Manager Does NOT Do

- **Does not modify scenes during tag matching** - The Match tab only updates tag metadata (names, aliases, StashDB links). It never touches your scenes.
- **Scene tags are only modified by Scene Tag Sync** - This is a separate task you must explicitly run.

---

## Accessing Tag Manager

There are two ways to access Tag Manager:

### From the Tags Page

1. Navigate to your Tags page in Stash
2. Look for new icon buttons in the top-right area:
   - **Tag icon** - Opens Tag Manager (Match/Browse tabs)
   - **Sitemap icon** - Opens Tag Hierarchy view

### Direct URLs

- Tag Manager: `/plugins/tag-manager`
- Tag Hierarchy: `/plugins/tag-hierarchy`

---

## Tag Matching (Match Tab)

The Match tab helps you link your existing local tags to their StashDB equivalents.

### Workflow

1. **Select Stash-Box**: Choose your stash-box endpoint from the dropdown (usually StashDB)
2. **Load Cache**: The plugin loads cached StashDB tags (or fetches them if no cache exists)
3. **Filter Tags**: Use the filter buttons to show:
   - **Unmatched** - Tags without a StashDB link (default)
   - **Matched** - Tags already linked to StashDB
   - **All** - All tags
4. **Find Matches**: Click "Find Matches for Page" to search for matches for all visible tags
5. **Review Matches**: For each tag with matches:
   - **Accept** - Opens the diff dialog with smart defaults
   - **More** - Shows all potential matches and manual search option

### The Diff Dialog

When you click Accept or select a match, the diff dialog lets you choose what to update:

#### Name Options
- **Keep local** - Keep your current tag name
- **Keep + Add alias** - Keep your name, add StashDB name as an alias
- **Use StashDB** - Rename to StashDB name (your old name becomes an alias)

#### Description Options
- **Keep local** - Keep your current description
- **Use StashDB** - Replace with StashDB description

#### Aliases
- Check/uncheck individual aliases to include or exclude them
- Both local and StashDB aliases are merged by default

#### Category/Parent Tag
If the StashDB tag has a category (e.g., "Hair Color" for "Brown Hair"):
- Select an existing local tag to use as parent
- Or create a new parent tag with the category name
- Check "Remember this mapping" to auto-apply for future tags in the same category

### Smart Defaults

The dialog automatically selects sensible defaults:
- If your local field is **empty** → defaults to StashDB value
- If your local field has **content** → defaults to keeping your value
- If names differ → defaults to "Keep + Add alias" (preserves both)

### What Happens When You Apply

1. Tag metadata is updated (name, description, aliases as selected)
2. A `stash_id` link is added connecting your tag to StashDB
3. Parent tag relationship is set if you selected a category
4. **No scenes are modified** - only the tag itself changes

---

## Browse & Import (Browse StashDB Tab)

The Browse tab lets you explore StashDB tags by category and import ones you don't have.

### Workflow

1. **Switch to Browse Tab**: Click "Browse StashDB" tab
2. **Select Category**: Choose a category from the left sidebar (e.g., "Hair Color", "Body Type")
3. **Browse Tags**: See all StashDB tags in that category
4. **Select for Import**: Check the boxes next to tags you want to import
5. **Import**: Click "Import Selected" to create the tags locally

### Tag Status Indicators

- **Checkbox enabled** - Tag doesn't exist locally, can be imported
- **Checkbox disabled + "✓ Exists"** - Tag already exists locally (linked by StashDB ID)

### What Happens When You Import

1. A new local tag is created with:
   - Name from StashDB
   - Description from StashDB
   - Aliases from StashDB
   - `stash_id` link to StashDB
2. **No parent relationships are set** - You'll need to organize hierarchy separately
3. **No scenes are modified** - Tags are just created, not applied to anything

---

## Tag Hierarchy View

The Hierarchy view lets you visualize and edit parent/child relationships between tags.

### Viewing the Hierarchy

1. Click the sitemap icon on the Tags page (or go to `/plugins/tag-hierarchy`)
2. Browse tags in a tree structure:
   - **Root tags** (no parents) appear at the top level
   - **Child tags** appear nested under their parents
   - Tags with multiple parents appear under each parent

### Navigation

- **Click arrows** to expand/collapse branches
- **Expand All** / **Collapse All** buttons for quick navigation
- **Show images** toggle to show/hide tag thumbnails
- **Click a tag name** to select it (for keyboard operations)

### Statistics Bar

Shows counts for:
- Total tags
- Tags with sub-tags (children)
- Tags with parents

### Editing Hierarchy

Hierarchy editing uses an "edit mode" - changes are queued and saved together.

#### Right-Click Context Menu

Right-click any tag to see options:
- **Add parent...** - Search for and add a parent tag
- **Add child...** - Search for and add a child tag
- **Remove from "[parent]"** - Remove this tag from its current parent (only shown when tag is under a parent)

#### Drag and Drop

1. Drag a tag and drop it onto another tag
2. The dragged tag becomes a child of the drop target
3. If dragging from a specific parent, it's moved (old parent removed, new parent added)
4. If dragging a root tag, the parent is just added

#### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+C** | Copy selected tag |
| **Ctrl+V** | Paste - add copied tag as child of selected tag |
| **Delete** / **Backspace** | Remove selected tag from its current parent |
| **Escape** | Clear selection |

#### Pending Changes Panel

When you make changes, a panel appears at the bottom showing:
- List of pending changes
- **×** button to remove individual changes
- **Cancel** to discard all changes
- **Save Changes** to apply all changes to the database

### Circular Reference Protection

The plugin prevents creating circular references (e.g., A → B → C → A). If you try to create one, you'll see an error message and the change will be blocked.

---

## Scene Tag Sync

Scene Tag Sync is a batch task that adds StashDB tags to your scenes based on their StashDB metadata.

### Prerequisites

Before running Scene Tag Sync:
1. **Scenes must have StashDB IDs** - Use Stash's built-in Tagger to match scenes to StashDB first
2. **Tags should be linked to StashDB** - Use Tag Manager's Match tab to link your tags

### How It Works

1. Finds all scenes that have a StashDB ID
2. For each scene, queries StashDB for its tags
3. Matches each StashDB tag to a local tag using:
   - **StashDB link** - If local tag has same `stash_id`
   - **Name match** - If local tag name matches StashDB tag name
   - **Alias match** - If local tag alias matches StashDB tag name
4. Adds matched tags to the scene (existing tags are preserved)

### Running the Sync

1. Go to **Settings → Tasks → Plugin Tasks**
2. Find **Tag Manager → Sync Scene Tags from StashDB**
3. Click **Run**

### Dry Run Mode (Recommended)

By default, "Dry Run" is enabled in plugin settings. In dry run mode:
- The sync runs but doesn't save any changes
- You see a preview of what would happen
- Limited to 200 scenes for quick preview
- Check Stash logs for detailed output

To run for real:
1. Go to Settings → Plugins → Tag Manager
2. Disable "Scene Tag Sync - Dry Run"
3. Run the sync task again

### What Gets Modified

- **Scene tags are ADDED** - StashDB tags are merged with existing scene tags
- **Tags are never removed** - Your existing scene tags stay intact
- **Only matched tags are added** - StashDB tags without a local match are skipped

### Sync Statistics

After sync completes, check Stash logs for:
- Total scenes processed
- Scenes updated vs. no changes needed
- Tags added total
- Unmatched tags skipped (tags on StashDB with no local equivalent)

---

## Tag Blacklist

The blacklist filters unwanted tags from matching and sync operations.

### Configuring the Blacklist

1. Go to **Settings → Plugins → Tag Manager**
2. Find **Tag Blacklist** setting
3. Enter patterns, one per line

### Pattern Types

#### Literal Strings (Case-Insensitive)
```
Unwanted Tag
Another Bad Tag
```
Matches tags with exact names (ignoring case).

#### Regex Patterns (Prefix with /)
```
/^\d+p$/
/Available$/
/^Test/i
```
- `/^\d+p$/` - Matches resolution tags like "720p", "1080p"
- `/Available$/` - Matches tags ending with "Available"
- `/^Test/` - Matches tags starting with "Test"

### Where Blacklist Applies

- **Match tab** - Blacklisted tags hidden from search results
- **Scene Tag Sync** - Blacklisted StashDB tags are not added to scenes
- **Browse tab** - Blacklisted tags are still visible (for import flexibility)

---

## Custom Synonyms

Synonyms let you define manual mappings for tags that don't match automatically.

### Editing Synonyms

Edit `synonyms.json` in the plugin folder:

```json
{
  "synonyms": {
    "My Local Tag Name": ["StashDB Tag Name"],
    "Another Tag": ["StashDB Name 1", "StashDB Name 2"]
  }
}
```

### How Synonyms Work

When matching "My Local Tag Name":
1. Plugin checks exact name match (none found)
2. Plugin checks alias match (none found)
3. Plugin checks synonyms → finds "StashDB Tag Name"
4. Returns that StashDB tag as a "synonym" match

### When to Use Synonyms

- Tags with completely different names that should match
- Alternate spellings or conventions
- Cases where fuzzy matching doesn't work well

---

## Tag Caching

StashDB has 20,000+ tags. To avoid slow fetches every time, Tag Manager caches tags locally.

### Cache Behavior

- **Location**: `plugins/tagManager/cache/` directory
- **Expiry**: 24 hours
- **Per-endpoint**: Each stash-box has its own cache file

### Cache Status Indicator

The cache status shows in the top-right:
- **Green** - Valid cache with tag count and age
- **Yellow** - Cache expired (will auto-refresh)
- **Gray** - No cache yet

### Manual Cache Management

- **Refresh Cache** - Forces a fresh fetch from StashDB
- **Clear Cache** - Removes the cache file (next load will fetch fresh)

### First-Time Fetch

The initial fetch takes 20-40 seconds depending on your connection. Subsequent loads use the cache and are nearly instant.

---

## Understanding Match Types

When searching for matches, results are color-coded by match type:

| Type | Color | Score | Description |
|------|-------|-------|-------------|
| **Exact** | Green | 100 | Tag name matches exactly (case-insensitive) |
| **Alias** | Blue | 100 | Your tag name matches a StashDB alias |
| **Synonym** | Purple | 95 | Matched via custom synonym mapping |
| **Fuzzy** | Yellow | 80-99 | Similar name (typos, plurals, close variations) |

### Match Confidence

- **High confidence (90+)**: Usually safe to accept with defaults
- **Medium confidence (80-89)**: Review before accepting
- **Lower scores**: Shown in "More" dialog, may need manual selection

### Fuzzy Matching Details

Fuzzy matching uses the `thefuzz` library (based on Levenshtein distance):
- Catches typos: "Bondge" → "Bondage"
- Catches plurals: "Tattoo" → "Tattoos"
- Catches minor variations: "Blow Job" → "Blowjob"

Adjust the threshold in plugin settings (default: 80). Higher = stricter matching.
