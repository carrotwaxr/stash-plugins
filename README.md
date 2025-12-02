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

### mcMetadata (v1.2.1)

Generate NFO metadata files for Jellyfin/Emby, organize/rename video files, and export performer images.

**Features:**
- NFO generation with scene metadata (title, performers, studio, tags, date)
- File organization with customizable path templates
- Performer image export to media server People folders
- Dry run mode for previewing changes
- Bulk operations and per-scene hooks

[Documentation](plugins/mcMetadata/README.md)

### Performer Image Search (v1.1.0)

Search multiple image sources directly from performer pages and set images with one click.

**Features:**
- Search Babepedia, PornPics, FreeOnes, EliteBabes, Boobpedia, and Bing
- Preview images before setting
- Filter by aspect ratio (portrait, landscape, square)
- Customizable search suffix

[Documentation](plugins/performerImageSearch/README.md)

## Support

- **Issues**: [GitHub Issues](https://github.com/carrotwaxr/stash-plugins/issues)
- **Community**: [Stash Discord](https://discord.gg/stashapp) | [Stash Discourse](https://discourse.stashapp.cc/)

## License

MIT License
