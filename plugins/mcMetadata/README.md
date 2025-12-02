# mcMetadata Plugin for [Stash](https://github.com/stashapp/stash)

**Version**: 1.2.0

This plugin is for users who manage their collection with Stash but serve content via Jellyfin or Emby. Instead of relying on those media servers' scrapers, mcMetadata leverages your Stash database to generate `.nfo` metadata files and performer images that Jellyfin/Emby can use.

## Features

- **NFO Generation**: Creates `.nfo` files with scene metadata (title, performers, studio, tags, date, rating)
- **File Organization**: Renames and moves video files based on customizable templates
- **Performer Images**: Exports performer images to your media server's People metadata folder
- **Dry Run Mode**: Preview all changes before committing them
- **Bulk Operations**: Process your entire library or update scenes one at a time via hooks

## Installation

### From Stash Plugin Index (Recommended)

1. Go to **Settings → Plugins → Available Plugins**
2. Add source: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
3. Find "mcMetadata" under "Carrot Waxxer" and click Install
4. Reload plugins

### Manual Installation

1. Download or clone this repository to your Stash plugins directory
2. Reload plugins in Stash

## Configuration

All settings are configured through Stash's UI at **Settings → Plugins → mcMetadata**.

### General Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Dry Run Mode** | Boolean | On | Preview changes without making them. Check logs to see what would happen. |
| **Enable Scene Update Hook** | Boolean | Off | Automatically process scenes when you update them. |

### File Renamer Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Enable File Renamer** | Boolean | Off | Move/rename video files based on the path template |
| **Renamer Base Path** | String | - | Base directory for renamed files (must be in a Stash library) |
| **Renamer Path Template** | String | `$Studio/$Title - $Performers $ReleaseDate [$Resolution]` | Template for file paths. See variables below. |
| **Max Filepath Length** | Number | 250 | Maximum total path length (adjust for your OS) |
| **Skip Files Already in Path** | Boolean | Off | Don't rename files already in the renamer base path |
| **Mark Scenes as Organized** | Boolean | On | Set the Organized flag after renaming |
| **Multi-File Mode** | String | `all` | How to handle scenes with multiple files: `all`, `primary_only`, or `skip` |

### NFO Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Skip Existing NFO Files** | Boolean | Off | Don't overwrite NFO files that already exist |

### Actor Image Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Enable Actor Images** | Boolean | Off | Copy performer images to media server metadata folder |
| **Media Server Type** | String | `jellyfin` | Target media server: `jellyfin` or `emby` |
| **Actor Metadata Path** | String | - | Path to media server's People metadata folder |

## Template Variables

Use these variables in your **Renamer Path Template**:

| Variable | Description |
|----------|-------------|
| `$Studio` | Scene's studio name |
| `$Studios` | Full studio hierarchy as nested directories |
| `$Title` | Scene title |
| `$StashID` | Scene's Stash ID |
| `$ReleaseDate` | Scene's release date (YYYY-MM-DD) |
| `$ReleaseYear` | Year from release date |
| `$Resolution` | Video resolution (480p, 720p, 1080p, 1440p, 4K, 8K) |
| `$Quality` | Video quality (LOW, SD, HD, FHD, 2K, QHD, UHD, FUHD) |
| `$Performers` | All performer names (space-separated) |
| `$FemalePerformers` | Female performer names only |
| `$MalePerformers` | Male performer names only |
| `$Tags` | All scene tags (space-separated) |

**Uniqueness Requirement**: Templates must contain either:
- `$StashID`, OR
- (`$Studio` or `$Studios`) AND `$Title` AND `$ReleaseDate`

## Usage

### Running Bulk Tasks

1. Go to **Settings → Tasks → Plugin Tasks**
2. Select:
   - **Bulk Update Scenes**: Process all scenes with StashIDs
   - **Bulk Update Performers**: Copy all performer images to media server

### Using the Hook

1. Enable **"Enable Scene Update Hook"** in plugin settings
2. When you update a scene in Stash, the plugin will automatically:
   - Rename/move the video file (if renamer enabled)
   - Generate/update the NFO file
   - Copy performer images (if enabled)

### Recommended Workflow

1. **First Run**: Enable Dry Run Mode, configure your settings, run "Bulk Update Scenes"
2. **Check Logs**: Go to Settings → Logs (Debug level) to see what would happen
3. **Execute**: Disable Dry Run Mode, run "Bulk Update Scenes" again
4. **Ongoing**: Enable the hook for automatic updates on scene changes

## Media Server Setup

### Jellyfin

- **Actor Metadata Path**: `<jellyfin-config>/data/metadata/People/`
- **Folder Structure**: `People/J/Jane Doe/folder.jpg` (uses A-Z subfolders)

### Emby

- **Actor Metadata Path**: `<emby-config>/metadata/People/`
- **Folder Structure**: `People/Jane Doe/folder.jpg` (no subfolders)

## Troubleshooting

Enable debug logging at **Settings → Logs** and set Log Level to Debug. The plugin logs detailed information prefixed with `[DRY RUN]` when in dry run mode.

Common issues:
- **Files not moving**: Check that the destination path is within a Stash library
- **NFO not parsing**: Ensure your media server is set to read local NFO files
- **Performer images not showing**: Verify the actor metadata path is correct for your media server

## Requirements

- Stash v0.24.0 or later
- Python 3.9+ (bundled with Stash)
- `stashapp-tools>=0.2.59` (installed automatically)

## Changelog

### v1.2.0
- Fixed Emby actor folder structure (no A-Z subfolders)
- Fixed XML escaping for special characters in titles (ampersands, etc.)
- Added comprehensive unit tests

### v1.1.0
- Migrated settings from `settings.ini` to Stash's native plugin settings UI
- Added "Enable Scene Update Hook" setting
- Removed toggle tasks (now controlled via settings UI)

### v1.0.0
- Replaced direct SQLite manipulation with GraphQL `moveFiles` mutation
- Added multi-file scene handling (all/primary_only/skip modes)
- Added `nfo_skip_existing` setting
- Fixed pagination bug in bulk operations
- Replaced Python 3.10+ syntax for broader compatibility
- Improved error handling and progress reporting

## License

MIT License - See [LICENSE](LICENSE) file
