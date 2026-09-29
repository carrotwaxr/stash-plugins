# Tag Manager

Match and sync local tags with stash-box endpoints. Bulk cleanup your tag library with smart matching, browse and import tags from StashDB, and manage tag hierarchy.

## Features

- **Tag Matching** - Smart layered search (exact, alias, fuzzy, synonym) to match local tags with StashDB
- **Browse & Import** - Browse StashDB tags by category and bulk import new tags
- **Tag Hierarchy** - Visual tree view with drag-and-drop editing for parent/child relationships
- **Scene Tag Sync** - Batch task to add stash-box tags to matched scenes, without re-adding tags you removed
- **Tag Blacklist** - Filter unwanted tags using literal strings or regex patterns, with an on-page editor
- **Multi-endpoint Support** - Works with StashDB, FansDB, and other stash-box instances

> **Note**: While multi-endpoint support exists, the plugin is primarily tested with a single stash-box (StashDB). Using multiple endpoints simultaneously may produce unexpected behavior.

For detailed usage instructions, see the [User Guide](USERGUIDE.md).

## Requirements

- Stash v0.28+ (v0.30+ recommended for full `stash_ids` support)
- At least one stash-box endpoint configured in Stash (Settings → Metadata Providers → Stash-Box Endpoints)
- Python 3.9+ (no packages required; `thefuzz` and `python-Levenshtein` are optional, see Installation)

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

### Step 2: Install Optional Python Packages

Tag Manager needs no Python packages to run, and Scene Tag Sync needs nothing extra. `thefuzz` and `python-Levenshtein` are optional: they only improve fuzzy tag matching. Without them Tag Manager falls back to basic matching. To install them, open a terminal/command prompt and run:

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
| Fuzzy Match Threshold | Minimum score (0-100) for fuzzy matches. 0 is allowed | 80 |
| Tags Per Page | Number of tags shown per page (at least 1) | 25 |
| Scene Tag Sync - Dry Run | Preview sync without making changes. Checks at most 200 scenes | Enabled |
| Default to Stash-Box Name | In the merge dialog, pick the stash-box name instead of keeping the local name | Off |
| Default to Stash-Box Description | In the merge dialog, pick the stash-box description instead of keeping the local one | Off |
| Leave Parent Tags Alone | Import/match tags flat: don't create or assign category parent tags, keeping your own hierarchy untouched | Off |
| Category Mappings (Internal) | Saved category to parent tag choices, kept per stash-box. Managed automatically, don't edit by hand | `{}` |
| Tag Blacklist | Patterns to exclude from matching and sync. Separate with `,` or `;`, or use the Blacklist button on the Match tab. See the [User Guide](USERGUIDE.md#tag-blacklist) | Empty |

The backend reads these settings from Stash's saved plugin config, so a change applies after you save it in Stash.

### Default Settings File

Stash's plugin settings have no defaults of their own. `assets/default_settings.json` holds the default for each setting. Both the UI and the backend read it. Don't edit it to change your own settings: use the Settings page. If you add a setting to `tagManager.yml`, add it to this file too. A test checks that every setting is listed.

## Troubleshooting

### Fuzzy Matching Is Basic

`thefuzz` is optional. Without it Tag Manager uses basic matching. To get better fuzzy matching, install it for the Python that Stash uses:

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

### Cache Takes Too Long / Fetch Errors

The first fetch from StashDB downloads 20,000+ tags in pages of 1,000 and takes about 5 seconds. Later loads use the local cache. If a stash-box rejects the page size, Tag Manager falls back to pages of 100, which is slower. A failed or partial fetch is never cached. If you get errors:

1. Read the error shown in the UI. It comes from the stash-box.
2. If it says the stash-box rejected the API key (HTTP 401 or 403), check the API key in Settings → Metadata Providers.
3. Try "Refresh Cache" again. The stash-box may be busy.

### Scene Tag Sync Errors

- Ensure scenes have stash-box IDs (use Stash's Tagger first)
- Start with "Dry Run" enabled to preview changes
- An error naming a stash-box and HTTP 401 or 403 means that stash-box rejected the API key. Check it in Settings → Metadata Providers.
- If tags you removed from scenes keep coming back, or the history file can't be read, run the "Reset Scene Tag Sync History" task
- Check Stash logs for detailed error messages

### SSL/Certificate Errors (Windows)

If you see SSL errors:

1. Ensure Python is up to date
2. Try: `pip install --upgrade certifi`

## Development

### Running Tests

```bash
cd plugins/tagManager

# Python unit tests (offline)
python -m pytest

# JavaScript tests
for f in tests/test_*.js; do node "$f"; done

# Integration tests against StashDB (requires an API key)
STASH_PLUGINS_INTEGRATION=1 STASHDB_API_KEY=your-key python -m pytest tests/test_integration.py
```

Tests that talk to a real Stash, StashDB or Whisparr skip unless `STASH_PLUGINS_INTEGRATION=1` is set. Point them at a test instance, never production.

### File Structure

```
tagManager/
├── tagManager.yml         # Plugin manifest
├── tag_manager.py         # Python backend (search, cache, sync)
├── stashdb_api.py         # Stash-box GraphQL client
├── stash_client.py        # Client for the local Stash server
├── sync_history.py        # Scene sync history (SQLite)
├── plugin_data.py         # Location of runtime state
├── log.py                 # Plugin logging
├── stashdb_scene_sync.py  # Scene tag sync logic
├── matcher.py             # Tag matching algorithms
├── blacklist.py           # Blacklist pattern matching
├── tag_cache.py           # Local tag lookup cache
├── tag-manager.js         # JavaScript UI
├── tag-manager.css        # UI styles
├── synonyms.json          # Custom synonym mappings
├── assets/                # Files served to the UI (default_settings.json)
├── requirements.txt       # Optional Python packages
└── tests/                 # Test suite
```

### Where Runtime State Is Stored

Caches and sync history live in Stash's config folder, not the plugin folder, so plugin updates don't wipe them:

```
<Stash config dir>/plugin_data/tagManager/
├── tag_cache/             # Stash-box tag caches
└── sync_history.sqlite    # Scene Tag Sync history
```

The `cache/` folder inside the plugin folder from older versions is no longer used. You can delete it.

## Changelog

### v0.7.0

- **Requirements**: Python 3.9+ with no required packages. `thefuzz` and `python-Levenshtein` are optional. `stashapp-tools` is no longer used (fixes #129).
- **Faster, clearer tag fetches**: 1,000 tags per page, so a full StashDB fetch takes about 5 seconds instead of 30-40+. Falls back to 100 per page if a stash-box rejects it. Failed or partial fetches are never cached. Stash-box errors show in the UI, and HTTP 401/403 tells you to check the API key. Requests send a `User-Agent`, which fixes HTTP 403 errors from ThePornDB and JAVStash. ThePornDB fetches now get every tag instead of the first 100.
- **State moved to Stash's config folder** (`plugin_data/tagManager/`). Plugin updates no longer wipe caches. The old `cache/` folder can be deleted.
- **Settings are read by the backend** from Stash's saved plugin config. The saved blacklist now applies to searches.
- **Scene Tag Sync**: syncs each scene against every linked stash-box that has an API key, adds tags in a way that keeps tags added during a long sync, and remembers matched tags so tags you removed are not added back. New "Reset Scene Tag Sync History" task. The first live sync after upgrading can re-add tags you removed before 0.7.0 one last time. Dry run checks at most 200 scenes and previews the history-aware result. A rejected API key stops the sync with an error naming the stash-box. Respects each stash-box's max requests per minute.
- **Blacklist**: `/regex/flags` syntax, patterns separated by newlines, `,` or `;`, and a Blacklist editor button on the Match tab. It now applies to the best match, Accept, "More matches" (Select now applies the tag you clicked), manual and backend searches, Import All and Scene Tag Sync.
- **Accept/Apply dialog**: saved category mappings show as "(saved mapping)" and are pre-selected. Parent controls are hidden with "Leave Parent Tags Alone". The pre-selected `Create "<category>"` option now really creates the parent tag. Names and aliases are checked for conflicts first, and Apply is disabled while it runs.
- **Category mappings are stored per stash-box endpoint.** Existing mappings move under StashDB automatically. A failed settings load can no longer overwrite them.
- **Merging tags** asks for confirmation and shows what moves. On Stash 0.31+ the merge and the update happen in one transaction. Parents and children of the merged tag now carry over, and the parent you pick in the dialog is added.
- **Import conflicts dialog**: failed reverse merges clean up, other rows are re-checked after each action, "strip" lists dropped aliases, replacing a stash ID asks first, and the dialog stays open with a "Done" button.
- **Import**: shows progress and has a Cancel button. Import All skips blacklisted and already-linked tags and is much faster on large libraries.
- **Tag Hierarchy**: edit mode resets when you leave the page, saving re-reads current parents and keeps failed changes pending, alias search works, large trees render lazily, and keyboard shortcuts only act when the tree has focus.
- **Navigation** works when Stash is served under a sub-path and no longer reloads the page.
- Fuzzy Match Threshold and Tags Per Page are parsed safely. 0 is a valid threshold.

### v0.6.1

- The backend now looks up the stash-box URL and API key in Stash's own configuration instead of accepting them from the browser. An endpoint that isn't configured in Stash is rejected.
- Only the `assets/` folder is served to the browser, instead of the whole plugin directory.

### v0.6.0

- **Resolve import conflicts in the UI** (#125): when an imported tag's name or alias is already used by a local tag, the import no longer fails silently to the console. A "Resolve Tag Conflicts" dialog now lets you merge into the existing tag, strip the clashing alias and import anyway, open the conflicting tag to fix it by hand, do a confirm-gated reverse merge, or skip. The import summary reports conflicts resolved and skipped.

### v0.5.0

- **Category mappings now persist reliably** (#122): config writes are serialized and verified after saving, so a selected parent tag no longer reverts to "create new." A failed save now surfaces a toast instead of silently dropping.
- **Tag list refreshes without a full page reload** (#124): returning to the tab (e.g. after fixing a conflicting tag elsewhere) and finishing a merge/import now re-fetch from Stash automatically.
- **New "Leave Parent Tags Alone" setting** (#126): import/match tags flat without creating or assigning category parent tags, for users who maintain their own nested hierarchy.
- Hardened plugin config reads (empty/cleared numeric settings and malformed config no longer error).

## License

MIT License - See repository root for details.
