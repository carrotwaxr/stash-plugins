# StashDB Tag Search - Design Document

**Date:** 2026-01-25
**Branch:** feature/stashdb-tag-search
**Requested by:** smith113 (community feedback)

## Problem

When browsing StashDB tags in the Tag Manager plugin, users must navigate through categories to find tags they want to import. This is difficult when users don't know which parent category contains a specific tag.

## Solution

Add a search function to the Browse StashDB tab that filters tags across all categories by name and aliases.

## User Experience

### Search Input Location
- Positioned at the top of the main content area (above the tag list, right of sidebar)
- Placeholder text: "Search tags..."
- Includes a clear "×" button

### Default State (No Search)
Works exactly as today:
- Sidebar shows all categories with tag counts
- Clicking a category displays its tags in the main area

### Search Mode (User Types in Search Box)
- **Real-time filtering** with 200ms debounce
- **Sidebar collapses** to give more room to results
- **Flat results list** shows all matching tags across all categories
- Each result row displays:
  - Checkbox for selection
  - Tag name
  - Category badge (shows which category the tag belongs to)
  - Aliases preview (up to 3)
  - "✓ Exists" badge if already linked locally

### Search Matching
- Case-insensitive substring matching
- Searches both tag names and aliases
- Example: searching "MILF" finds tags with that name OR that alias

### Clearing Search
- Click "×" button in search input, or delete all text
- View returns to normal category browsing
- Previously selected category (if any) is restored

### Selection and Import
- Checkboxes work identically to category view
- Selected tags persist when toggling between search and category views
- "Import Selected" button and counter function unchanged

## Technical Implementation

### Changes to tag-manager.js

1. **New state variable:**
   ```javascript
   let browseSearchQuery = '';
   ```

2. **New function `filterTagsBySearch(query)`:**
   - Takes search query string
   - Filters `stashdbTags` array
   - Matches against `tag.name` and `tag.aliases` (case-insensitive)
   - Returns array of matching tags

3. **Modified `renderBrowseView()`:**
   - Add search input to main content header
   - If `browseSearchQuery` is non-empty:
     - Hide sidebar (add CSS class)
     - Call `renderSearchResults()` instead of `renderBrowseTagList()`

4. **New function `renderSearchResults()`:**
   - Renders flat list of search matches
   - Each row includes category badge
   - Reuses existing tag row rendering logic where possible

5. **Event handlers:**
   - `input` event on search box with 200ms debounce
   - `click` event on clear button

### Changes to tag-manager.css

1. **Search input styling:**
   - `.tm-browse-search` - container with search icon
   - `.tm-browse-search-input` - text input
   - `.tm-browse-search-clear` - × button

2. **Category badge styling:**
   - `.tm-tag-category-badge` - small pill showing category name

3. **Sidebar collapse:**
   - `.tm-browse-sidebar.collapsed` - hidden state for search mode

## Edge Cases

- **Empty results:** Show "No tags found matching '{query}'" message
- **Special characters:** Escape regex special chars in search query
- **Very long queries:** No artificial limit, but debounce prevents performance issues
- **Cache not loaded:** Search box disabled until cache loads (same as rest of UI)

## Out of Scope

- Fuzzy matching (exact substring match is sufficient)
- Search history or suggestions
- Filtering by other fields (description, etc.)
- Server-side search (not needed - tags are already cached locally)

## Testing

Manual testing checklist:
- [ ] Search finds tags by exact name
- [ ] Search finds tags by partial name
- [ ] Search finds tags by alias
- [ ] Search is case-insensitive
- [ ] Sidebar hides during search
- [ ] Category badge displays correctly
- [ ] Clear button returns to category view
- [ ] Selected tags persist across search/category toggle
- [ ] Import works from search results
- [ ] Empty results show appropriate message
