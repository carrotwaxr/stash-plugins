# Tag Manager

Match and sync local tags with StashDB tags. Bulk cleanup your tag library with smart matching.

## Features

- **Paginated tag list** - Browse unmatched tags 25 at a time
- **Smart matching** - Layered search: exact name, alias, fuzzy, synonyms
- **One-click accept** - Quick accept for high-confidence matches
- **Field-by-field merge** - Choose what to keep vs. what to update
- **Manual search** - Search StashDB directly for edge cases
- **StashDB linking** - Adds `stash_ids` to tags for future syncing

## Requirements

- Stash 0.30+ (requires `stash_ids` field on tags)
- StashDB API key
- Python 3.8+ with `thefuzz` package

## Installation

1. Install via plugin source or copy the `tagManager` folder to your Stash plugins directory
2. Install Python dependency: `pip install thefuzz`
3. Reload plugins in Stash
4. Configure your StashDB API key in Settings > Plugins > Tag Manager

## Configuration

Go to **Settings > Plugins > Tag Manager**:

| Setting | Description | Default |
|---------|-------------|---------|
| StashDB Endpoint | GraphQL URL | `https://stashdb.org/graphql` |
| StashDB API Key | Your API key (required) | - |
| Enable Fuzzy Search | Use fuzzy matching for typos | `true` |
| Enable Synonym Search | Use custom synonym mappings | `true` |
| Fuzzy Threshold | Minimum score (0-100) for fuzzy matches | `80` |
| Page Size | Tags per page | `25` |

## Usage

1. Navigate to `/plugin/tag-manager` in your Stash UI
2. Click **Find Matches for Page** to search all visible tags
3. For each match:
   - **Accept** - Opens diff dialog with smart defaults
   - **More** - View all potential matches and search manually
4. In the diff dialog, choose what to update for each field:
   - **Keep** - Keep your current value
   - **StashDB** - Use the StashDB value
   - **Merge** (aliases only) - Combine both sets

## Smart Defaults

The diff dialog uses smart defaults based on your data:
- If local field is **empty** -> defaults to StashDB value
- If local field has **content** -> defaults to keeping your value

## Match Types

| Type | Color | Description |
|------|-------|-------------|
| exact | Green | Exact name match |
| alias | Blue | Matched via StashDB alias |
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
  tests/              # Test suite
```
