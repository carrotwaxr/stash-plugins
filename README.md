# Carrot Waxxer's Stash Plugins

Plugins for extending [Stash](https://github.com/stashapp/stash), the open-source media organizer.

## Installation

Add this repository as a plugin source in Stash:

1. Go to **Settings → Plugins → Available Plugins**
2. Click **Add Source**
3. Enter URL: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
4. Click **Reload**
5. Browse available plugins under "Carrot Waxxer"

Stash shows each plugin's current version in that list. What changed in each release is in [CHANGELOG.md](CHANGELOG.md).

## Python prerequisites

Most of these plugins run a Python script, using the Python that Stash finds. Stash doesn't install Python packages for you.

| Plugin | Python packages |
|---|---|
| mcMetadata | `stashapp-tools` |
| Tag Manager | `thefuzz` and `python-Levenshtein`, optional (better fuzzy matching) |
| Missing Scenes, Scene Matcher, Performer Image Search | none (standard library only) |
| Studio Manager | none (no Python) |

To keep these packages separate from your system Python, create a virtual environment and point Stash at it:

```bash
python3 -m venv ~/.stash/venv
~/.stash/venv/bin/pip install stashapp-tools thefuzz python-Levenshtein
```

Then set **Settings → System → Application Paths → Python executable path** to the venv's Python (for example `~/.stash/venv/bin/python`, or `venv\Scripts\python.exe` on Windows). In the official Docker image, run those commands inside the container and use a path under `/root/.stash` so the venv persists.

## Available Plugins

### mcMetadata

Generate NFO metadata files for Jellyfin, Emby and Plex, organize and rename video files, and export performer images.

**Features:**
- NFO generation with scene metadata (title, performers, studio, tags, date, rating)
- File organization with customizable path templates
- Performer image export to your media server's People folder
- Processing conditions: limit processing to Organized scenes, StashDB-linked scenes, required tags or path globs
- Dry run mode for previewing changes
- Bulk task and a per-scene update hook

[Documentation](plugins/mcMetadata/README.md)

### Performer Image Search

Search multiple image sources from a performer's page and set an image with one click.

**Features:**
- Sources: Babepedia, PornPics, FreeOnes, EliteBabes, Boobpedia, JavDatabase and DuckDuckGo, each of which can be turned off
- Preview images, with keyboard navigation, before setting one
- Filter by aspect ratio (portrait, landscape, square)
- Customizable search suffix

[Source](plugins/performerImageSearch/)

### Missing Scenes

Discover scenes on StashDB (or another stash-box) that you don't have in your library, with optional Whisparr integration.

**Features:**
- Missing scenes for any performer, studio or tag linked to a stash-box
- A browse page of missing scenes across your library, filterable by favorite performers, studios and tags
- Works with StashDB, FansDB, ThePornDB and other stash-box endpoints
- Whisparr integration with live status (downloading, queued, stalled)
- Auto-cleanup: remove scenes from Whisparr once they're tagged in Stash
- Scan task for newly downloaded scenes

[Documentation](plugins/missingScenes/README.md)

### Scene Matcher

Find StashDB matches for untagged scenes using their linked performers and studio. Adds a "Match" button to the Tagger.

**Features:**
- Searches StashDB by the scene's title and its linked performers and studio
- Results scored by title similarity, matching performers and studio, and how close the duration is
- Scenes you don't own are listed first
- Hands the chosen match to Stash's own Tagger for saving

[Documentation](plugins/sceneMatcher/README.md)

### Tag Manager

Match and sync local tags with stash-box tags, and clean up your tag library.

**Features:**
- Layered matching: exact name, alias, fuzzy and synonyms
- Field-by-field merge dialog (name, description, aliases)
- Browse stash-box tags by category and bulk import them, with in-app resolution when a name or alias is already taken
- Tag hierarchy view with drag-and-drop parent/child editing
- Sync Scene Tags task: copy tags from StashDB to your matched scenes
- Tag blacklist to exclude tags from matching and sync
- "Leave parent tags alone" setting for people who keep their own hierarchy
- Multiple stash-box endpoints

[Documentation](plugins/tagManager/README.md)

### Studio Manager

Manage studio hierarchy with visual tree editing.

**Features:**
- Tree view of studio parent/child relationships
- Drag and drop to set a parent
- Context menu for quick actions
- Pending changes panel for reviewing edits before saving

[Documentation](plugins/studioManager/README.md)

## Support

- **Issues**: [GitHub Issues](https://github.com/carrotwaxr/stash-plugins/issues)
- **Community**: [Stash Discord](https://discord.gg/stashapp) | [Stash Discourse](https://discourse.stashapp.cc/)

## Publishing

Every push to `main` publishes. The Deploy workflow runs the tests, then `build_site.sh` builds the plugin index and zips and publishes them to GitHub Pages.

- Zips are built from the committed tree and hold only runtime files. Tests, samples and `.env` files stay in the repo.
- A plugin's index version is `<manifest version>-<sha>`, where the sha is the last commit that changed a shipped file. Test-only commits don't offer users an update.
- The zips are reproducible, so rebuilding the same commit produces the same sha256.

To preview a build locally: `./build_site.sh /tmp/site`.

## License

[MIT](LICENSE)
