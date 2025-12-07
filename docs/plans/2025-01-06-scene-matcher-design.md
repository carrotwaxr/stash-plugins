# Scene Matcher Plugin Design

## Overview

Scene Matcher helps tag untagged scenes by leveraging known performer/studio associations. It adds a "Match" button to Stash's Tagger UI that searches StashDB for scenes matching the scene's linked performers and/or studio, then hands off to Stash's native Tagger for the actual save.

## Problem Statement

Users have many scenes that:
- Lack a StashDB ID (not tagged)
- Have performers and/or studios that ARE linked to StashDB
- Can't be found via filename search or fragment scrape

Currently, users must manually browse StashDB to find matches. Scene Matcher automates the discovery step while reusing Stash's existing Tagger UI for the complex save logic (creating performers, studios, tags on the fly).

## Core Workflow

1. User is on Scene Tagger page (single or bulk view)
2. Scene has performer(s) and/or studio with StashDB IDs, but scene itself lacks StashDB ID
3. User clicks "Match" button (appears next to Search/Search by Fragment)
4. Modal opens showing a grid of StashDB scenes filtered by those performers/studio
5. Results scored and sorted by relevance (more attribute matches = higher)
6. User clicks a scene in the grid
7. Plugin injects that scene's UUID into Stash's search input and triggers search
8. Stash's native Tagger UI takes over from there

## Technical Architecture

### Plugin Structure

```
plugins/sceneMatcher/
├── sceneMatcher.yml      # Plugin manifest
├── scene-matcher.js      # UI injection (button + modal)
├── scene-matcher.css     # Styling
├── scene_matcher.py      # Backend for StashDB queries
└── log.py                # Shared logging helper
```

### Data Flow

1. JS Frontend detects Tagger page, injects button next to each scene's search controls
2. Button click calls Python backend via `runPluginOperation`
3. Python backend:
   - Fetches scene's performers/studio from local Stash (with their stash_ids)
   - Queries StashDB for scenes matching those performers (OR query)
   - Queries StashDB for scenes matching that studio (separate query)
   - Merges, deduplicates, scores, and sorts results
   - Queries local Stash to flag which results user already has
   - Returns enriched results to JS
4. JS renders modal with scored grid
5. User clicks result
6. JS finds Tagger's search input, sets UUID, triggers search
7. Stash native UI renders result and handles save

### Query Strategy

**Local Scene Query:**
```graphql
query FindScene($id: ID!) {
    findScene(id: $id) {
        id
        title
        performers {
            id
            name
            stash_ids { endpoint, stash_id }
        }
        studio {
            id
            name
            stash_ids { endpoint, stash_id }
        }
    }
}
```

**StashDB Queries** (two separate, then merge):

Performers:
```graphql
queryScenes(input: {
    performers: { value: [id1, id2, ...], modifier: INCLUDES },
    page: 1, per_page: 100, sort: DATE, direction: DESC
})
```

Studio:
```graphql
queryScenes(input: {
    studios: { value: [studioId], modifier: INCLUDES },
    page: 1, per_page: 100, sort: DATE, direction: DESC
})
```

Pagination: Fetch up to 10 pages (1000 results) per query.

### Scoring Algorithm

```python
def score_scene(scene, performer_ids, studio_id):
    score = 0
    if studio_id and scene.studio.id == studio_id:
        score += 3
    for perf in scene.performers:
        if perf.id in performer_ids:
            score += 2
    return score
```

Final sort order: `(-not_in_local_stash, -score, -release_date)`

## UI Design

### Button Placement

Appears inline with Search/Search by Fragment buttons on each scene row in Tagger.

**Button States:**
- Enabled: Scene has ≥1 performer or studio with StashDB ID
- Disabled + tooltip: "No linked performers or studios"
- Hidden: Scene already has a StashDB ID

### Modal Design

- Header: "Scene Matcher - [Scene Title]"
- Stats bar: "Searching by: [Performer A], [Performer B], [Studio Name]"
- Grid of results:
  - Thumbnail
  - Title
  - Studio, Date, Duration
  - Performer names
  - Match score badge (e.g., "Studio + 2 Performers")
  - "Already in Stash" badge if applicable
- Click card to select and trigger Stash search

### Handoff to Stash

When user clicks a result:
1. Find scene's query input in DOM
2. Set input value to StashDB UUID
3. Trigger Search button click
4. Close modal

Key insight: StashDB's `searchScene` API already supports UUID lookup. Passing a valid UUID directly fetches that scene by ID.

## Edge Cases

1. **No usable attributes**: Button disabled with tooltip
2. **Large result sets**: Cap at 1000 results per query
3. **Scene already tagged**: Button hidden
4. **Multiple stash-box endpoints**: Respect plugin setting, fall back to first
5. **Performer/studio not linked**: Skip in query, use what's available
6. **No results found**: Friendly message in modal
7. **All results in Stash**: Still show with "Already in Stash" badge

## Error Handling

- StashDB unreachable: Show error in modal
- Invalid response: Log and show generic error
- No linked attributes: Graceful message explaining why

## Code Reuse

- CSS patterns from Missing Scenes plugin
- Modal structure and styling
- Python GraphQL helpers
- log.py logging utility

## Files to Create

1. `plugins/sceneMatcher/sceneMatcher.yml` - Plugin manifest
2. `plugins/sceneMatcher/scene-matcher.js` - UI injection
3. `plugins/sceneMatcher/scene-matcher.css` - Styling
4. `plugins/sceneMatcher/scene_matcher.py` - Backend logic
5. `plugins/sceneMatcher/log.py` - Logging helper
