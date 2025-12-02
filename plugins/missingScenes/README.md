# Missing Scenes

Discover scenes from StashDB (or other stash-box instances) that you don't have in your local Stash library.

## Features

- **Performer & Studio Support**: Find missing scenes for any performer or studio linked to a stash-box
- **Visual Grid Display**: Browse missing scenes with thumbnails, titles, dates, and performer info
- **Direct StashDB Links**: Click any scene to view it on StashDB
- **Multi-Endpoint Support**: Works with StashDB, FansDB, or any configured stash-box endpoint
- **Whisparr Integration** (Optional): Add missing scenes directly to Whisparr for automated downloading

## Requirements

- Stash v0.25.0 or later (requires `runPluginOperation` GraphQL mutation)
- At least one stash-box endpoint configured (Settings → Metadata Providers → Stash-box Endpoints)
- Performers/Studios must be linked to stash-box (use the Tagger to link them)

## Usage

1. Navigate to any **Performer** or **Studio** detail page
2. Click the **Missing Scenes** button in the header
3. The plugin will:
   - Query the configured stash-box for all scenes featuring that performer/studio
   - Compare against your local Stash library
   - Display scenes you don't have

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

## Whisparr Integration

When Whisparr is configured:
- Each missing scene shows an "Add to Whisparr" button
- "Add All to Whisparr" button appears to bulk-add all missing scenes
- Scenes already in Whisparr are marked and disabled

**Note**: Whisparr v3 API is supported. The plugin uses `stash:{scene_id}` as the foreign ID format.

## How It Works

1. **Get Entity**: Fetches the performer/studio from your local Stash
2. **Find Stash ID**: Looks up the stash-box ID for that entity
3. **Query Stash-Box**: Fetches all scenes from the stash-box (paginated, up to 1000 scenes)
4. **Get Local Scenes**: Fetches all your local scene stash IDs
5. **Compare**: Filters out scenes you already have
6. **Display**: Shows missing scenes sorted by release date (newest first)

## Troubleshooting

### "Performer/Studio is not linked to StashDB"
The entity needs a stash_id for the configured stash-box endpoint. Use the Tagger (Scenes → Tagger) to match and link the performer/studio.

### "No stash-box endpoints configured"
Go to Settings → Metadata Providers → Stash-box Endpoints and add at least one endpoint (e.g., StashDB).

### Results seem incomplete
The plugin fetches up to 1000 scenes (10 pages of 100). For performers/studios with more scenes, the oldest scenes may be excluded.

## Technical Details

- **UI Plugin**: JavaScript + CSS injected on performer/studio pages
- **Backend**: Python script called via `runPluginOperation` GraphQL mutation
- **No External Dependencies**: Uses only Python standard library
- **Pagination**: Fetches 100 scenes per page from stash-box
- **Caching**: Scene stash IDs are fetched fresh each time (no caching)

## License

MIT License
