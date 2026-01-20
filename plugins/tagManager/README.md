# Tag Manager

Match and sync local tags with stash-box endpoints. Bulk cleanup your tag library with smart matching, browse and import tags from StashDB, and manage tag hierarchy.

## Features

- **Tag Matching** - Smart layered search (exact, alias, fuzzy, synonym) to match local tags with StashDB
- **Browse & Import** - Browse StashDB tags by category and bulk import new tags
- **Tag Hierarchy** - Visual tree view with drag-and-drop editing for parent/child relationships
- **Scene Tag Sync** - Batch task to sync tags from StashDB to all matched scenes
- **Tag Blacklist** - Filter unwanted tags using literal strings or regex patterns
- **Multi-endpoint Support** - Works with StashDB, FansDB, and other stash-box instances

> **Note**: While multi-endpoint support exists, the plugin is primarily tested with a single stash-box (StashDB). Using multiple endpoints simultaneously may produce unexpected behavior.

For detailed usage instructions, see the [User Guide](USERGUIDE.md).

## Requirements

- Stash v0.28+ (v0.30+ recommended for full `stash_ids` support)
- At least one stash-box endpoint configured in Stash (Settings → Metadata Providers → Stash-Box Endpoints)
- Python 3.8+ with required packages (see Installation)

## Installation

### Step 1: Install the Plugin

**Option A: Via Stash Plugin Source (Recommended)**
1. In Stash, go to **Settings → Plugins → Available Plugins**
2. Click **Add Source**
3. Enter URL: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
4. Click **Reload**
5. Find "Tag Manager" under "Carrot Waxxer" and click Install

**Option B: Manual Installation**
1. Download or clone this repository
2. Copy the `tagManager` folder to your Stash plugins directory:
   - **Windows**: `C:\Users\<username>\.stash\plugins\`
   - **macOS**: `~/.stash/plugins/`
   - **Linux**: `~/.stash/plugins/`

### Step 2: Install Python Dependencies

Tag Manager requires Python packages for fuzzy string matching. Open a terminal/command prompt and run:

**Windows (Command Prompt or PowerShell):**
```cmd
pip install thefuzz python-Levenshtein
```

**macOS / Linux:**
```bash
pip install thefuzz python-Levenshtein
```

> **Troubleshooting pip:**
> - If you have multiple Python versions, use `pip3` instead of `pip`
> - If pip isn't in your PATH, try `python -m pip install ...` or `python3 -m pip install ...`
> - On Windows, you can also try `py -m pip install ...`

### Step 3: Configure Stash-Box

1. Go to Stash → Settings → Metadata Providers → Stash-Box Endpoints
2. Add your stash-box (e.g., StashDB at `https://stashdb.org/graphql`)
3. Enter your API key (get one from your stash-box account settings)

### Step 4: Reload and Verify

1. Go to Settings → Plugins and click "Reload Plugins"
2. Navigate to the Tags page - you should see new icon buttons for Tag Manager

## Quick Start

1. **Match Tags**: Go to Tags page → Click the tag icon → Select your stash-box → Click "Find Matches for Page"
2. **Browse StashDB**: Switch to "Browse StashDB" tab → Select a category → Check tags to import → Click "Import Selected"
3. **View Hierarchy**: Click the sitemap icon → Browse your tag tree → Right-click to edit relationships

## Plugin Settings

Go to **Settings → Plugins → Tag Manager**:

| Setting | Description | Default |
|---------|-------------|---------|
| Enable Fuzzy Search | Use fuzzy matching for typos | Enabled |
| Enable Synonym Search | Use custom synonym mappings | Enabled |
| Fuzzy Match Threshold | Minimum score (0-100) for fuzzy matches | 80 |
| Tags Per Page | Number of tags shown per page | 25 |
| Scene Tag Sync - Dry Run | Preview sync without making changes | Enabled |
| Tag Blacklist | Patterns to exclude from matching | Empty |

## Troubleshooting

### "thefuzz not installed" or Fuzzy Matching Disabled

The Python packages aren't installed correctly. Verify installation:

```bash
# Check which Python Stash is using
python --version
python3 --version

# Install for the correct Python version
python3 -m pip install thefuzz python-Levenshtein

# On Windows, you may need to run as administrator
# Or try: py -m pip install thefuzz python-Levenshtein
```

### "No Stash-Box Configured"

1. Go to Settings → Metadata Providers → Stash-Box Endpoints
2. Add your stash-box endpoint URL and API key
3. Reload plugins and try again

### Plugin Not Appearing

1. Check that the `tagManager` folder is in the correct plugins directory
2. Verify folder structure: `plugins/tagManager/tagManager.yml` should exist
3. Check Stash logs (Settings → Logs) for error messages
4. Reload plugins in Settings → Plugins

### Cache Takes Too Long / Timeout Errors

First fetch from StashDB downloads ~20,000+ tags and takes 20-40 seconds. Subsequent loads use the local cache. If you get timeout errors:

1. Try clicking "Refresh Cache" again
2. Check your internet connection
3. StashDB may be temporarily overloaded - wait and retry

### Scene Tag Sync Errors

- Ensure scenes have StashDB IDs (use Stash's Tagger first)
- Start with "Dry Run" enabled to preview changes
- Check Stash logs for detailed error messages

### SSL/Certificate Errors (Windows)

If you see SSL errors:

1. Ensure Python is up to date
2. Try: `pip install --upgrade certifi`

## Development

### Running Tests

```bash
cd plugins/tagManager

# Unit tests (no API key needed)
python -m unittest discover tests -v

# Integration tests (requires API key)
STASHDB_API_KEY=your-key python -m unittest tests.test_integration -v
```

### File Structure

```
tagManager/
├── tagManager.yml         # Plugin manifest
├── tag_manager.py         # Python backend (search, cache, sync)
├── stashdb_api.py         # StashDB GraphQL client
├── stashdb_scene_sync.py  # Scene tag sync logic
├── matcher.py             # Tag matching algorithms
├── blacklist.py           # Blacklist pattern matching
├── tag_cache.py           # Local tag lookup cache
├── tag-manager.js         # JavaScript UI
├── tag-manager.css        # UI styles
├── synonyms.json          # Custom synonym mappings
├── requirements.txt       # Python dependencies
├── cache/                 # Tag cache files (auto-created)
└── tests/                 # Test suite
```

## License

MIT License - See repository root for details.
