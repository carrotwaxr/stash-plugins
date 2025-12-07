# Carrot Waxxer's Stash Plugins

Plugins for extending [Stash](https://github.com/stashapp/stash), the open-source media organizer.

## Installation

Add this repository as a plugin source in Stash:

1. Go to **Settings → Plugins → Available Plugins**
2. Click **Add Source**
3. Enter URL: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
4. Click **Reload**
5. Browse available plugins under "Carrot Waxxer"

## Available Plugins

### mcMetadata (v1.2.2)

Generate NFO metadata files for Jellyfin/Emby, organize/rename video files, and export performer images.

**Features:**
- NFO generation with scene metadata (title, performers, studio, tags, date)
- File organization with customizable path templates
- Performer image export to media server People folders
- Dry run mode for previewing changes
- Bulk operations and per-scene hooks

[Documentation](plugins/mcMetadata/README.md)

### Performer Image Search (v1.2.2)

Search multiple image sources directly from performer pages and set images with one click.

**Features:**
- Search Babepedia, PornPics, FreeOnes, EliteBabes, Boobpedia, JavDatabase, and Bing
- Preview images before setting
- Filter by aspect ratio (portrait, landscape, square)
- Customizable search suffix

[Documentation](plugins/performerImageSearch/README.md)

### Missing Scenes (v1.2.0)

Discover scenes from StashDB that you don't have in your local library, with optional Whisparr integration for automated downloading and cleanup.

**Features:**
- Find missing scenes for any performer or studio linked to StashDB
- Visual grid with thumbnails, titles, dates, and performer info
- Direct links to view scenes on StashDB
- Multi-endpoint support (StashDB, FansDB, etc.)
- Whisparr integration with real-time status tracking (downloading, queued, stalled, etc.)
- Auto-cleanup: Automatically remove scenes from Whisparr when tagged in Stash
- Scan task: Trigger Stash scans for newly downloaded scenes

[Documentation](plugins/missingScenes/README.md)

### Scene Matcher (v1.0.0)

Find StashDB matches for untagged scenes using known performer and studio associations. Adds a "Match" button to the Tagger UI.

**Features:**
- Search StashDB for scenes by linked performers and/or studio
- Results scored by relevance (matching performers + studio)
- Unowned scenes prioritized over scenes already in your library
- Seamless handoff to Stash's native Tagger for saving

[Documentation](plugins/sceneMatcher/README.md)

## Support

- **Issues**: [GitHub Issues](https://github.com/carrotwaxr/stash-plugins/issues)
- **Community**: [Stash Discord](https://discord.gg/stashapp) | [Stash Discourse](https://discourse.stashapp.cc/)

## License

MIT License
