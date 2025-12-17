# Tag Hierarchy View Design

## Problem Statement

Stash users have requested a way to visualize their tag hierarchy. Currently, parent/child tag relationships exist in the database but there's no dedicated view to see the full tree structure. Users must navigate to individual tag detail pages to see parents/children one level at a time.

## Solution Overview

Add a **Tag Hierarchy** page to the existing tagManager plugin that displays all tags in a collapsible tree view based on parent/child relationships. Accessible via a new button on the `/tags` toolbar, next to the existing Tag Matcher button.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Multiple parents | Show tag under each parent | Stash supports multiple parents; showing in all locations gives accurate view |
| Root level | Only orphan tags (no parents) | Natural hierarchy - tags with no parents are true roots |
| Images | 64px thumbnails, letterboxed | Balance between visibility and compactness; letterbox preserves aspect ratio |
| Image toggle | "Show images" checkbox | Allows compact view when images not needed |
| Collapse behavior | All collapsed by default | Better performance/UX for large collections |
| Expand controls | Expand All / Collapse All buttons | Quick navigation for exploring or resetting |
| Metadata | Name + scene count + child count | e.g., "Action (47 scenes, 12 sub-tags)" - key info at a glance |
| Scene counts | Direct only | Recursive counts would be inaccurate (double-counting) or expensive |
| Filtering | None for v1 | Keep simple; use Stash's main search for finding specific tags |
| Navigation | Separate button from Tag Matcher | Distinct features deserve distinct entry points |
| Icon | Sitemap-style | Universally recognized hierarchy symbol |
| Page titles | "Tag Matcher \| Stash", "Tag Hierarchy \| Stash" | Descriptive browser tab names |

## User Interface

### Entry Point

Two buttons on `/tags` toolbar:
1. **Tag icon** (existing) - Opens Tag Matcher at `/plugins/tag-manager`
2. **Sitemap icon** (new) - Opens Tag Hierarchy at `/plugins/tag-hierarchy`

### Tag Hierarchy Page Layout

```
+------------------------------------------------------------------+
| Tag Hierarchy                    [Expand All] [Collapse All]     |
|                                  [x] Show images                 |
+------------------------------------------------------------------+
| 127 total tags | 45 root tags | 23 with sub-tags | 82 with parents |
+------------------------------------------------------------------+
|                                                                  |
| > [img] Action (47 scenes, 12 sub-tags)                         |
| > [img] Comedy (23 scenes, 5 sub-tags)                          |
| v [img] Drama (89 scenes, 8 sub-tags)                           |
|   |  > [img] Crime Drama (15 scenes, 2 sub-tags)                |
|   |  > [img] Legal Drama (8 scenes)                             |
|   |  [img] Medical Drama (12 scenes)                            |
| [img] Documentary (34 scenes)                                    |
|                                                                  |
+------------------------------------------------------------------+
```

### Tree Node Structure

Each node displays:
- **Expand/collapse arrow** (if has children)
- **64px thumbnail** (letterboxed, toggleable)
- **Tag name** (clickable link to `/tags/{id}`)
- **Metadata** (scene count, sub-tag count)

### Interactions

| Action | Result |
|--------|--------|
| Click expand arrow | Toggle children visibility |
| Click tag name | Navigate to tag detail page |
| Click "Expand All" | Expand entire tree |
| Click "Collapse All" | Collapse entire tree |
| Toggle "Show images" | Show/hide all thumbnails |

## Technical Approach

### Data Flow

1. Page loads, fetches all tags via `allTags` GraphQL query
2. Query includes: `id`, `name`, `image_path`, `scene_count`, `parent_count`, `child_count`, `parents { id }`, `children { id }`
3. Client builds tree structure from flat list
4. Tags with no parents become root nodes
5. Tags with multiple parents appear under each parent

### Tree Building Algorithm

```
For each tag:
  - If no parents: add to roots array
  - For each parent: add tag to parent's childNodes array
Sort all levels alphabetically by name
```

### Performance Considerations

- **All collapsed by default**: Only root nodes rendered initially
- **Lazy image loading**: `loading="lazy"` on img tags
- **No recursive counts**: Avoids expensive depth queries
- **Single GraphQL request**: Fetch all tags once, build tree client-side

## Files Changed

| File | Change |
|------|--------|
| `tag-manager.js` | Add route, page component, tree logic, button injection |
| `tag-manager.css` | Add hierarchy-specific styles |

No new files required - extends existing plugin.

## Future Enhancements (Out of Scope for v1)

- Search/filter within tree
- Drag-and-drop to reparent tags
- Bulk operations on selected tags
- Export hierarchy as text/markdown
- Recursive scene counts (with caching)
