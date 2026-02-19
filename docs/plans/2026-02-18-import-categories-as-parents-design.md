# Design: Import Categories as Parent Tags (Issue #98)

## Problem

When bulk-importing tags from the Browse StashDB tab, parent relationships based on StashDB categories are not assigned. Users must manually create category tags and mass-edit parent relationships — an 11-step process per category.

## Solution

Add a **Category Parent Preview** dialog to the Browse tab import flow. When selected tags have StashDB categories, show the resolved parent mappings before import and assign parents automatically during the import operation.

## UI Flow

1. User selects tags in Browse tab (any view: category, search, All)
2. Clicks **"Import Selected"**
3. If any selected tags have StashDB categories → show **Category Parent Preview** dialog:
   - Lists each unique category among selected tags
   - Shows auto-resolved parent tag per category (editable)
   - **Change** button per row opens existing parent search modal
   - **"Remember mappings"** checkbox (default: checked)
   - **"Import with Parents"** button to proceed
   - **"Import without Parents"** button to skip parent assignment
4. If no selected tags have categories → import proceeds as today (no dialog)

## Parent Resolution Order

For each unique category name:

1. **Saved mapping** — `categoryMappings[categoryName]`, verify tag still exists locally
2. **Exact name match** — local tag with matching name (case-insensitive)
3. **Create new** — flag as "will create" using category name

When creating a new parent tag, set its description from the StashDB category description (if available). When assigning an existing tag as parent, backfill description only if the existing tag's description is empty.

## Import Behavior

For each selected StashDB tag:

- **New tags**: Include `parent_ids: [resolvedParentId]` in the TagCreate mutation
- **Existing tags being linked**: After linking stash_id, append resolved parent to `parent_ids` if not already present (preserves existing parents)
- **Tags without category**: Import as-is (unchanged behavior)

Post-import: update `categoryMappings` with new/changed mappings if "Remember" is checked.

## Result Summary

Status message includes parent info:

> Created 15 tags, linked 3 existing, set parents for 18 (2 categories)

## Out of Scope

- Match tab flow changes (already has category integration)
- Bulk re-parent existing library tool (separate feature)
- StashDB group-level import (ACTION/SCENE/PEOPLE are informational)
- Category sync/change detection (separate ticket)
