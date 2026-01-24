# Scene File Deduper - Design Document

## Purpose

A local Flask web tool to manage multi-file scenes in Stash. After using Stash's Duplicate Checker to merge duplicate scenes, users end up with scenes that have multiple files attached. This tool provides a way to compare these files and keep only the best quality version(s).

## Architecture

- Python Flask app (same pattern as duplicate-performer-finder)
- Stash GraphQL API for querying scenes and deleting files
- Single-page UI with tag exclusion filter and scene cards
- Each scene card shows side-by-side file comparison

## Data Model

### Scene Query
Query all scenes where `file_count > 1`, excluding scenes with specified tags.

### File Information Displayed
For each file:
- Resolution (width x height)
- Video codec (H.264/H.265/AV1)
- Audio codec
- Bitrate
- File size
- Frame rate
- Duration
- File path

### Scene Metadata Displayed
- Title
- Performers
- Tags
- Studio

## User Interface

### Filter Bar
- Tag exclusion: Text input with autocomplete, multi-select
- Fetches all tags from Stash for autocomplete suggestions
- Selected tags shown as removable chips

### Scene Cards
Each multi-file scene displayed as a card containing:
- Scene header: title, performers, studio, tags
- File comparison: side-by-side cards for each file
- Each file card shows media info and action buttons

### File Actions
- **"Keep This"** button: Sets this file as primary, deletes all other files
- **"Delete"** button: Deletes just this single file
  - If deleting the primary file: first sets another file as primary, then deletes

### Post-Action Behavior
- Scene card removed from list after action
- Toast notification shows success/error
- If scene now has only 1 file, it's no longer a multi-file scene

## API Operations

### Queries
- `findScenes` with `file_count > 1` filter, excluding specified tags
- `allTags` for autocomplete suggestions

### Mutations
- `sceneUpdate` with `primary_file_id` to change primary file
- `deleteFiles` to delete non-primary files

### Delete Flow
1. If deleting non-primary file: call `deleteFiles` directly
2. If deleting primary file (or "Keep This"):
   - Call `sceneUpdate` to set new primary
   - Call `deleteFiles` to delete the others

## File Structure

```
scripts/scene-file-deduper/
├── app.py                 # Flask application
├── stash_client.py        # GraphQL client (can share base with performer tool)
├── requirements.txt       # Flask, requests, python-dotenv
├── .env.example          # Template for credentials
├── templates/
│   └── report.html       # Main UI template
└── static/
    └── style.css         # Styling
```

## Tech Stack

- Python 3.10+
- Flask
- Requests (for GraphQL)
- python-dotenv (for .env loading)
- Vanilla JS (for UI interactions, autocomplete)

## Error Handling

- Connection errors: Show error message, allow retry
- Delete failures: Show error toast, don't remove scene from list
- Primary file protection: Handle gracefully by setting new primary first

## Branch

`feature/scene-file-deduper`
