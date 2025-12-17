# Missing Scenes

Discover scenes from StashDB (or other stash-box instances) that you don't have in your local Stash library. View missing scenes for performers, studios, and tags, with optional Whisparr integration for automated downloading and cleanup.

## Features

- **Performer, Studio & Tag Support**: Find missing scenes for any performer, studio, or tag linked to a stash-box
- **Visual Grid Display**: Browse missing scenes with thumbnails, titles, dates, and performer info
- **Direct StashDB Links**: Click any scene to view it on StashDB
- **Multi-Endpoint Support**: Works with StashDB, FansDB, or any configured stash-box endpoint
- **Whisparr Integration** (Optional): Add missing scenes directly to Whisparr for automated downloading
- **Auto-Cleanup** (Optional): Automatically remove scenes from Whisparr when they get tagged in Stash
- **Scan Task**: Trigger Stash scans for newly downloaded scenes

## Requirements

- Stash v0.25.0 or later (requires `runPluginOperation` GraphQL mutation)
- At least one stash-box endpoint configured (Settings → Metadata Providers → Stash-box Endpoints)
- Performers/Studios/Tags must be linked to stash-box (use the Tagger to link them)

## Usage

1. Navigate to any **Performer**, **Studio**, or **Tag** detail page
2. Click the **Missing Scenes** button in the header
3. The plugin will:
   - Query the configured stash-box for all scenes featuring that performer/studio/tag
   - Compare against your local Stash library
   - Display scenes you don't have

### Tag Support (Stash 0.30+)

Starting with Stash 0.30, tags can have Stash ID associations. Missing Scenes now supports discovering scenes by tag:

1. Navigate to any Tag detail page
2. If the tag is linked to a stash-box (e.g., StashDB), you'll see the "Missing Scenes" button
3. Click to discover scenes with that tag that you don't have locally

**Multiple Endpoints:** If a tag is linked to multiple configured stash-boxes, you'll see a dropdown to select which endpoint to search.

## Settings

Configure in **Settings → Plugins → Missing Scenes**:

| Setting | Description |
|---------|-------------|
| **Stash-Box Endpoint** | Which stash-box to query. Leave empty to use the first configured endpoint (usually StashDB). Enter the full GraphQL URL (e.g., `https://stashdb.org/graphql`). |
| **Whisparr URL** | Optional. URL to your Whisparr instance (e.g., `http://localhost:6969`). |
| **Whisparr API Key** | Optional. API key from Whisparr Settings → General → Security. |
| **Quality Profile ID** | Whisparr quality profile ID (default: 1). |
| **Root Folder** | Whisparr root folder path for downloaded scenes. |
| **Search on Add** | Automatically search for scenes when adding to Whisparr. |
| **Auto-cleanup Whisparr** | Automatically remove scenes from Whisparr when they get tagged in Stash (receive a StashDB ID). |
| **Unmonitor Instead of Delete** | When auto-cleanup is enabled, unmonitor scenes instead of deleting them. |
| **Scan Path** | Path to scan for new downloaded scenes (e.g., `/data/unsorted`). Used by the "Scan for New Scenes" task. |

## Whisparr Integration

When Whisparr is configured:
- Each missing scene shows an "Add to Whisparr" button
- "Add All to Whisparr" button appears to bulk-add all missing scenes
- **Status Tracking**: Scenes show detailed status from Whisparr:
  - **Downloaded**: Already downloaded and available
  - **Downloading**: Currently downloading with progress percentage
  - **Queued**: Waiting to start downloading
  - **Stalled**: Download stalled (with error message)
  - **Waiting**: In Whisparr but not yet searching

**Note**: Whisparr v3 API is supported. The plugin uses `stash:{scene_id}` as the foreign ID format.

## Automation Features

### Auto-Cleanup Hook

When **Auto-cleanup Whisparr** is enabled, the plugin automatically removes scenes from Whisparr when they get tagged in Stash with a StashDB ID. This happens via the `Scene.Update.Post` hook.

**How it works:**
1. You download a scene via Whisparr
2. The scene gets imported into Stash
3. You tag the scene with its StashDB ID (manually or via the Tagger)
4. The hook detects the scene now has a stash_id and removes it from Whisparr

**Options:**
- **Delete**: Completely removes the scene from Whisparr (default)
- **Unmonitor**: Keeps the scene in Whisparr but marks it as unmonitored (prevents re-downloading)

### Tasks

Two tasks are available in **Settings → Tasks → Plugin Tasks**:

| Task | Description |
|------|-------------|
| **Scan for New Scenes** | Triggers a Stash metadata scan on the configured scan path. Use this after Whisparr downloads new scenes to import them into Stash. |
| **Cleanup Whisparr** | Batch removes all scenes from Whisparr that are now tagged in Stash. Useful for initial cleanup or periodic maintenance. |

### Recommended Workflow

1. **Configure Whisparr** in plugin settings (URL, API key, root folder)
2. **Set the scan path** to where Whisparr downloads scenes (e.g., `/data/unsorted`)
3. **Enable Auto-cleanup** to automatically remove scenes from Whisparr when tagged
4. **Add missing scenes** to Whisparr from performer/studio pages
5. **Run "Scan for New Scenes"** periodically (or set up a scheduled task) to import downloads
6. **Tag imported scenes** with StashDB IDs using the Tagger
7. Scenes are automatically cleaned up from Whisparr

## How It Works

1. **Get Entity**: Fetches the performer/studio/tag from your local Stash
2. **Find Stash ID**: Looks up the stash-box ID for that entity
3. **Query Stash-Box**: Fetches all scenes from the stash-box (paginated, up to 1000 scenes)
4. **Get Local Scenes**: Fetches all your local scene stash IDs
5. **Compare**: Filters out scenes you already have
6. **Display**: Shows missing scenes sorted by release date (newest first)

## Troubleshooting

### "Performer/Studio/Tag is not linked to StashDB"
The entity needs a stash_id for the configured stash-box endpoint. Use the Tagger (Scenes → Tagger) to match and link the performer/studio/tag.

### "No stash-box endpoints configured"
Go to Settings → Metadata Providers → Stash-box Endpoints and add at least one endpoint (e.g., StashDB).

### Results seem incomplete
The plugin fetches up to 1000 scenes (10 pages of 100). For performers/studios/tags with more scenes, the oldest scenes may be excluded.

## Technical Details

- **UI Plugin**: JavaScript + CSS injected on performer/studio/tag pages
- **Backend**: Python script called via `runPluginOperation` GraphQL mutation
- **No External Dependencies**: Uses only Python standard library
- **Pagination**: Fetches 100 scenes per page from stash-box
- **Caching**: Scene stash IDs are fetched fresh each time (no caching)

## License

MIT License
