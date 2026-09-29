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

These also work when Stash is served under a sub-path, such as behind a reverse proxy.

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

A saved mapping shows as "&lt;tag&gt; (saved mapping)" and is pre-selected. If the mapped tag was deleted, the mapping is dropped. Mappings are stored per stash-box, so each stash-box has its own. With "Leave Parent Tags Alone" on, the parent controls are hidden.

When there is no existing parent, match or saved mapping, `Create "<category>"` is pre-selected. Apply then creates the category parent tag. Before creating a parent tag or saving a mapping, Apply checks the name and aliases for conflicts. The Apply button is disabled while it runs.

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

### Merging Tags

When you merge one tag into another, Tag Manager first asks you to confirm. The confirmation shows how many scenes, child tags and parent tags will move, and says the source tag is deleted. Parents and children of the merged tag carry over to the destination.

- **Stash 0.31 and later**: the merge and the updates to the destination tag (aliases, stash IDs, description, parents and children) happen in one transaction. If anything fails, nothing changes.
- **Stash 0.30**: Tag Manager merges first, then updates the destination. If the update fails, the merge has already happened. The error explains what to fix by hand.

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

### Import Progress and Import All

While tags are imported you see "Importing i / N" and a **Cancel** button. Cancel stops after the current tag. **Import All** skips blacklisted tags and tags that are already linked. Clicks are ignored while an import runs.

### What Happens When You Import

1. A new local tag is created with:
   - Name from StashDB
   - Description from StashDB
   - Aliases from StashDB
   - `stash_id` link to StashDB
2. **No parent relationships are set** - You'll need to organize hierarchy separately
3. **No scenes are modified** - Tags are just created, not applied to anything

### Resolving Conflicts (#125)

Sometimes an imported tag's name or one of its aliases is already used by a local
tag, so it can't be created as-is. The import still completes for everything else,
then a **"Resolve Tag Conflicts"** dialog lists each conflict with these choices:

- **Merge into "&lt;tag&gt;"** - Link the StashDB entity to the existing tag (adds the
  `stash_id`, plus the imported name and any non-conflicting aliases). Nothing is
  deleted. One button appears per conflicting tag.
- **Strip alias & import** - Create the new tag anyway, dropping only the
  conflicting alias(es). _(Hidden when the conflict is on the name itself, since the
  name can't be reused.)_
- **Open "&lt;tag&gt;"** - Open the conflicting tag in a new browser tab so you can edit
  or delete it by hand, then re-run the import later.
- **Merge "&lt;tag&gt;" into this** - The destructive reverse: create the new tag and
  absorb the existing one into it (reassigns its scenes, then deletes it). Asks for
  confirmation and shows how many scenes will move. _(Hidden on name conflicts.)_
- **Skip** / **Skip all remaining** - Leave the conflict unresolved. Anything left
  unresolved when you close the dialog is counted as skipped.

Other things to know about this dialog:

- Only one action runs at a time.
- After each action the other rows are re-checked. A row whose conflict is gone gets an **Import** button.
- **Strip alias & import** lists the aliases it dropped.
- If a reverse merge fails, the new tag it created is removed again.
- If an action would replace an existing stash ID, you are asked first.
- The dialog stays open with the results and a **Done** button.

The import summary reports how many conflicts were resolved and how many were skipped.

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
- **Search** finds tags by name or alias
- Large trees render collapsed branches only when you expand them

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

These only act when the tree has focus. Copy and paste in text boxes works as normal. Edit mode resets when you leave the page.

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

When you save, Tag Manager re-reads each tag's current parents first. A change that fails stays pending so you can retry it.

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
4. Skips tags that are blacklisted, tags you removed from the scene earlier, and tags with no local match
5. Adds the remaining tags to the scene (existing tags are preserved)

### Which Stash-Box Each Scene Uses

A scene can be linked to more than one stash-box. Each scene is synced against every configured stash-box it is linked to that has an API key, not just the first. Log messages name the stash-box. Tag Manager keeps to each stash-box's "max requests per minute" setting, and never sends more than 2 requests per second.

### Removed Tags Stay Removed

Tag Manager remembers which stash-box tags it matched for each scene. If you remove one of those tags from a scene, later syncs don't add it back. Tags the stash-box adds later are still added.

Some tags are not remembered, so they can still be added later:
- Stash-box tags with no local match are added once you have a matching local tag.
- Blacklisted tags are added once you take them off the blacklist.

The first live sync after upgrading to 0.7.0 has no history yet. It can add back tags you removed before 0.7.0 one last time.

The history is stored in `sync_history.sqlite` (see [Tag Caching](#tag-caching)). To forget it, run **Settings → Tasks → Plugin Tasks → Tag Manager → Reset Scene Tag Sync History**. The next sync considers every tag again, so it adds back tags you removed. The task also deletes the file, so it fixes a history file that can't be read.

### Running the Sync

1. Go to **Settings → Tasks → Plugin Tasks**
2. Find **Tag Manager → Sync Scene Tags from StashDB**
3. Click **Run**

### Dry Run Mode (Recommended)

By default, "Dry Run" is enabled in plugin settings. In dry run mode:
- The sync runs but doesn't save any changes
- You see a preview of what would happen
- Checks at most 200 scenes in total
- The preview takes the history into account, so it shows what a real sync would add now
- Check Stash logs for detailed output

To run for real:
1. Go to Settings → Plugins → Tag Manager
2. Disable "Scene Tag Sync - Dry Run"
3. Run the sync task again

### What Gets Modified

- **Scene tags are ADDED** - Missing stash-box tags are added to the scene. Tags added to a scene while a long sync runs are kept
- **Tags are never removed** - Your existing scene tags stay intact
- **Only matched tags are added** - StashDB tags without a local match are skipped

### Sync Statistics

After sync completes, check Stash logs for:
- Total scenes processed
- Scenes updated vs. no changes needed
- Tags added total
- Unmatched tags skipped (tags on StashDB with no local equivalent)

### Sync Errors

If a stash-box answers with HTTP 401 or 403, it rejected the API key. The sync stops with an error naming that stash-box. Check the API key in Settings → Metadata Providers. An HTTP 403 can also come from a stash-box that blocks requests without a `User-Agent`. Tag Manager sends one with every request, so this should not happen.

The task returns an error when every scene failed.

---

## Tag Blacklist

The blacklist filters unwanted tags from matching and sync operations.

### Configuring the Blacklist

There are two ways to edit it:

- **On the Match tab**: click the **Blacklist** button. A text box opens. Click **Save** to store it in the plugin settings.
- **In Stash**: go to **Settings → Plugins → Tag Manager** and edit **Tag Blacklist**. This is a single-line box, so separate patterns with `,` or `;`.

### Syntax

Write one pattern per line, or separate patterns with `,` or `;`.

#### Literal Strings (Case-Insensitive)
```
Unwanted Tag
Another Bad Tag
```
Plain text matches the whole tag name, ignoring case.

#### Regex Patterns (`/regex/`)
```
/^\d+p$/
/Available$/
/^test/i
```
- `/^\d+p$/` - Matches resolution tags like "720p", "1080p"
- `/Available$/` - Matches tags ending with "Available"
- `/^test/i` - Matches tags starting with "test"

Rules for regex patterns:
- Regexes are always case-insensitive.
- Flags after the closing slash are allowed: `i`, `m` and `s`. Other flags are ignored, with a warning in the log.
- The older form without a closing slash (`/^\d+p$`) still works. The rest of the line is the regex.
- Commas and semicolons inside `/.../` don't split the pattern.
- An invalid regex is skipped, with a warning in the log.

### Where Blacklist Applies

- **Match tab** - Blacklisted tags are not offered as a row's best match or by Accept, and are left out of the "More matches" list, manual searches and backend searches. In "More matches", Select applies the tag you clicked.
- **Import All** - Blacklisted tags are skipped
- **Scene Tag Sync** - Blacklisted stash-box tags are not added to scenes
- **Browse tab** - Blacklisted tags are still visible, so you can import them one by one

The blacklist is read from the saved plugin settings, so save it before you run a search or a sync.

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

- **Location**: `<Stash config dir>/plugin_data/tagManager/tag_cache/`. Plugin updates don't wipe it. The old `cache/` folder inside the plugin folder is no longer used and can be deleted
- **Expiry**: 24 hours
- **Per-endpoint**: Each stash-box has its own cache file

### Cache Status Indicator

The cache status shows in the top-right:
- **Green** - Valid cache with tag count and age
- **Yellow** - Cache expired (will auto-refresh)
- **Gray** - No cache yet

### Manual Cache Management

- **Refresh Cache** - Forces a fresh fetch from the stash-box and replaces the cache
- To remove a cache by hand, delete its file from `<Stash config dir>/plugin_data/tagManager/tag_cache/`

### First-Time Fetch

The initial StashDB fetch takes about 5 seconds. Tags are fetched 1,000 per page. If a stash-box rejects that, Tag Manager falls back to 100 per page, which takes longer. A failed or partial fetch is never cached. If the stash-box returns an error, it shows in the UI. Later loads use the cache and are nearly instant.

### Other Stored Data

Scene Tag Sync history is stored in `<Stash config dir>/plugin_data/tagManager/sync_history.sqlite`.

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

Fuzzy matching uses the optional `thefuzz` library (based on Levenshtein distance). Without it, Tag Manager uses basic matching:
- Catches typos: "Bondge" → "Bondage"
- Catches plurals: "Tattoo" → "Tattoos"
- Catches minor variations: "Blow Job" → "Blowjob"

Adjust the threshold in plugin settings (0-100, default 80). Higher = stricter matching.
