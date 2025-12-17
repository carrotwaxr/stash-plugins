# Tag Manager

Match and sync local tags with stash-box tags. Bulk cleanup your tag library with smart matching.

## Features

- **Multi-endpoint support** - Select from any configured stash-box endpoint (StashDB, FansDB, etc.)
- **Tag caching** - Caches fetched tags locally for fast subsequent searches
- **Paginated tag list** - Browse unmatched tags 25 at a time
- **Smart matching** - Layered search: exact name, alias, fuzzy, synonyms
- **One-click accept** - Quick accept for high-confidence matches
- **Field-by-field merge** - Choose what to keep vs. what to update
- **Manual search** - Search the stash-box directly for edge cases
- **Stash-box linking** - Adds `stash_ids` to tags for future syncing
- **Tag Hierarchy View** - Browse your tags in a visual tree structure showing parent/child relationships

## Requirements

- Stash 0.30+ (requires `stash_ids` field on tags)
- At least one stash-box endpoint configured in Stash (Settings → Metadata Providers → Stash-Box Endpoints)
- Python 3.8+ with `thefuzz` package

## Installation

1. Install via plugin source or copy the `tagManager` folder to your Stash plugins directory
2. Install Python dependency: `pip install thefuzz`
3. Reload plugins in Stash
4. Ensure you have at least one stash-box configured in Settings → Metadata Providers

## Usage

1. Navigate to the Tags page and click the tag icon button, or go to `/plugins/tag-manager` in your Stash UI
2. Select your stash-box endpoint from the dropdown
3. The plugin will load cached tags (or fetch them if no cache exists)
4. Click **Find Matches for Page** to search all visible tags
5. For each match:
   - **Accept** - Opens diff dialog with smart defaults
   - **More** - View all potential matches and search manually
6. In the diff dialog, choose what to update for each field:
   - **Keep** - Keep your current value
   - **Keep + Add stash-box alias** - Keep your name but add the stash-box name as an alias
   - **StashDB** - Use the stash-box value (your old name is auto-added as an alias)

### Tag Hierarchy

1. Navigate to the Tags page and click the sitemap icon button, or go to `/plugins/tag-hierarchy`
2. Browse your tags in a tree view showing parent/child relationships
3. Click arrows to expand/collapse branches
4. Use "Expand All" / "Collapse All" buttons for quick navigation
5. Toggle "Show images" to show/hide tag thumbnails

## Tag Caching

Fetching all tags from a stash-box takes 20-40 seconds. To improve UX, the plugin caches tags locally:

- **Cache location**: `plugins/tagManager/cache/` directory
- **Cache expiry**: 24 hours (auto-refreshes on next use)
- **Manual refresh**: Click "Refresh Cache" to force a fresh fetch
- **Per-endpoint**: Each stash-box has its own cache file

The cache status shows:
- **Green**: Valid cache with tag count and age
- **Yellow**: Cache expired (will refresh automatically)
- **Gray**: No cache yet (will fetch on first use)

## Plugin Settings

Go to **Settings > Plugins > Tag Manager**:

| Setting | Description | Default |
|---------|-------------|---------|
| Enable Fuzzy Search | Use fuzzy matching for typos | `true` |
| Enable Synonym Search | Use custom synonym mappings | `true` |
| Fuzzy Threshold | Minimum score (0-100) for fuzzy matches | `80` |
| Page Size | Tags per page | `25` |

**Note**: Stash-box endpoints are configured in Settings → Metadata Providers → Stash-Box Endpoints, not in the plugin settings.

## Smart Defaults

The diff dialog uses smart defaults based on your data:
- If local field is **empty** → defaults to stash-box value
- If local field has **content** → defaults to keeping your value

## Match Types

| Type | Color | Description |
|------|-------|-------------|
| exact | Green | Exact name match |
| alias | Blue | Matched via stash-box alias |
| fuzzy | Yellow | Fuzzy string match (typos, plurals) |
| synonym | Purple | Matched via custom synonym mapping |

## Custom Synonyms

Edit `synonyms.json` to add custom mappings:

```json
{
  "synonyms": {
    "Your Local Tag": ["StashDB Tag 1", "StashDB Tag 2"]
  }
}
```

## Troubleshooting

### Debug Logging

The plugin logs extensively at different levels:
- **Info**: High-level operations (cache hits, tag counts)
- **Debug**: Detailed operation info (endpoints, matches found)
- **Trace**: Very detailed (individual requests, response sizes)

Check Stash's log output (Settings → Logs) for troubleshooting.

### Common Issues

1. **"No Stash-Box Configured"**: Configure a stash-box in Settings → Metadata Providers
2. **Cache takes too long**: First fetch is slow (~30s); subsequent loads use cache
3. **No matches found**: Try clicking "Refresh Cache" to update the tag list

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
  tagManager.yml      # Plugin manifest
  tag_manager.py      # Python backend entry point
  stashdb_api.py      # StashDB GraphQL client
  matcher.py          # Tag matching logic
  log.py              # Logging utilities
  tag-manager.js      # JavaScript UI
  tag-manager.css     # UI styles
  synonyms.json       # Custom synonym mappings
  cache/              # Tag cache files (auto-created)
  tests/              # Test suite
```
