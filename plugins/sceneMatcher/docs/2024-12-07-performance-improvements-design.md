# Scene Matcher Performance Improvements Design

## Problem Statement

Scene Matcher is slow and misses potential matches because:
1. Performer/studio queries are limited to 500 results (5 pages × 100)
2. Popular performers/studios have thousands of scenes
3. Duration filter (±60s) is too strict, excludes valid matches
4. All searches run sequentially before showing results

## Design Goals

- Show results faster via progressive loading
- Find more matches by using multiple search strategies
- Don't exclude scenes, rank by confidence instead

## Search Architecture

### Two-Phase Progressive Search

```
Phase 1 (Fast - parallel text searches):
├── Search: cleaned scene title
├── Search: "{studio} {performer1} {performer2}"
└── Return results immediately → UI shows them

Phase 2 (Thorough - runs after Phase 1 shown):
├── If performer + studio both linked:
│   └── Combined filter query (performer AND studio)
├── Else:
│   ├── Performer query (10 pages max)
│   └── Studio query (10 pages max)
└── Results merged into UI with "loading more..." indicator
```

### Implementation: Multiple Plugin Calls

JS makes two sequential calls to keep Python simple:

```javascript
// Phase 1 - fast text searches
const phase1 = await runPluginOperation({ operation: "find_matches_fast", ... });
renderResults(phase1.results);
showLoadingMore();

// Phase 2 - thorough performer/studio queries
const phase2 = await runPluginOperation({ operation: "find_matches_thorough", ... });
mergeAndRerenderResults(phase1.results, phase2.results);
hideLoadingMore();
```

## Scoring System

Replace hard duration filter with scoring. All matches shown, ranked by confidence:

```
Score components:
├── Title match:
│   ├── Exact (≥90% similarity):    +10 points
│   └── Partial (≥50% similarity):  +5 points
├── Studio match:                   +3 points
├── Per matching performer:         +2 points each
└── Duration proximity multiplier:  0.5x to 1.0x
    ├── Within 30 sec:   1.0x
    ├── Within 1 min:    0.9x
    ├── Within 2 min:    0.8x
    ├── Within 5 min:    0.6x
    ├── Within 10 min:   0.3x
    └── Beyond 10 min:   0.1x (penalized but not excluded)
```

### Sort Order

1. Not in library first (finding new matches is the goal)
2. Higher score first
3. More recent release date first

## Title Cleaning

For filename-based titles, clean up before searching:
- Strip file extension
- Replace `.` and `_` with spaces
- Strip common resolution tags (`1080p`, `720p`, `4K`, `WEB`, `XXX`, etc.)
- Let StashDB's fuzzy search handle the rest

## UI Changes

### Progressive Loading Flow

```
User clicks "Match" button
    ↓
Modal opens immediately with spinner
"Searching StashDB..."
    ↓
Phase 1 results arrive (1-2 seconds)
    ↓
Results grid populates
Status bar: "Found 8 matches, loading more..."
    ↓
Phase 2 results stream in
New cards appear/insert based on score
    ↓
All searches complete
Status bar: "Found 23 matches"
```

### Visual Indicators

- Duration warning: subtle indicator if >5 min difference from local
- Keep existing badges: title match, studio match, performer count

### Stats Bar

Show what was searched:
- `Searching: "Studio Name" + 2 performers + title`

## Backend Changes (Python)

### New Functions

1. `clean_title(filename)` - Strip extensions, dots, resolution tags
2. `build_search_query(studio, performers)` - Construct "{studio} {performer1} {performer2}"
3. `query_stashdb_by_title(title)` - StashDB text search
4. `query_stashdb_combined(performers, studio)` - Combined filter query

### New Operations

1. `find_matches_fast` - Phase 1: parallel text searches, returns quickly
2. `find_matches_thorough` - Phase 2: performer/studio queries with higher page limits

### Page Limit Changes

- Performer queries: 5 → 10 pages (1000 results max)
- Studio queries: 5 → 10 pages (1000 results max)
- Combined queries: 10 pages

## Frontend Changes (JavaScript)

### State Management

```javascript
let isLoadingMore = false;
let phase1Results = [];
let phase2Results = [];
```

### Progressive Rendering

- Render Phase 1 results immediately
- Show "Loading more results..." indicator
- Merge Phase 2 results, re-sort, re-render
- Hide loading indicator when complete

## Deduplication

All results merged by StashDB scene ID:
- Same scene from multiple searches appears once
- Scene found by multiple strategies gets noted (higher confidence)

## Migration

- Keep backward compatibility with existing `find_matches` operation
- New operations are additive
- Can deprecate old operation later
