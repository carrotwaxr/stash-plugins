# Scene Matcher

Find StashDB matches for untagged scenes using known performer and studio associations.

## Overview

Scene Matcher adds a "Match" button to Stash's Tagger UI for scenes that are not yet linked to the stash-box you are tagging against. It helps when the built-in search by filename or fingerprint finds nothing, by using what Stash already knows about the scene: its title, its linked performers and its linked studio.

## Which stash-box is searched

The box selected in the Tagger's source dropdown. If none is selected there, the **Stash-Box Endpoint** setting is used, then the first configured stash-box.

- If the Tagger's source is a scraper rather than a stash-box, the Match buttons are hidden.
- The button shows only for scenes that are not linked to that box, and its label names the box.

## How It Works

1. Open the Tagger (bulk or single scene view).
2. Click "Match" on a scene. The first phase is a text search on the cleaned title (filename or title with noise removed) plus the names of the linked performers and studio.
3. If the results are not right, click the deep search. It queries the box for scenes with all of the linked performers first, then any of them, plus scenes from the linked studio.
4. Results are scored and sorted best first. Scenes you already have locally are listed after the ones you do not.
5. Click Select on a result. Scene Matcher fills that row's search box with the scene and runs the Tagger's own search. The Tagger then handles the rest (creating performers, studios, and so on).

## Scoring

- **Title:** based on the cleaned filename or title. A title contained in the other counts, stop words are ignored, and studio and performer names are removed before comparing. Similarity of 0.9 or more adds 10; 0.5 or more adds 5.
- **Studio:** +3 when it is the linked studio.
- **Performers:** +2 for each linked performer in the scene.
- **Date:** +3 for the same day, +1 for the same month (when the stash-box only has a month) or a date one day apart. The scene's own date is tried first, then a date in the filename. A year-only date earns nothing.
- **Duration:** the total is multiplied by 0.5 to 1.0 by how close the durations are.

## Limits and truncation

The deep search returns at most **Max Results** scenes (default 50, 10 to 500). Fetching is also capped at 10 pages for performer searches and 25 for studio searches (`stashbox_max_pages_performer`, `stashbox_max_pages_studio`). When a cap cuts the list, the modal says so. The local scene lists are cached on the server for 5 minutes in `<Stash config dir>/plugin_data/sceneMatcher/`.

## Errors

- A rejected API key names the stash-box and points you to Settings > Metadata Providers.
- Rate limits (429) honour the `Retry-After` header, up to 60 seconds; the plugin never spends more than 90 seconds waiting on one request.
- If some queries fail and others succeed, the successful results are shown with a warning.
- The browser gives up after 120 seconds and shows a Retry button.

## Requirements

- Stash with at least one stash-box configured (e.g., StashDB).
- Scenes with performers or a studio linked to that stash-box, or a usable title.

## Settings

Settings > Plugins > Scene Matcher. All are optional.

| Setting | Default | Meaning |
| --- | --- | --- |
| `stashBoxEndpoint` | empty | Fallback stash-box (GraphQL URL) when the Tagger has none selected. Empty means the first configured box. |
| `maxResults` | 50 | Most deep-search results (10 to 500). |
| `stashbox_request_delay` | 0.5 | Seconds between paginated requests. |
| `stashbox_max_retries` | 3 | Retries for a failed request. |
| `stashbox_initial_retry_delay` | 1.0 | Seconds before the first retry. |
| `stashbox_max_retry_delay` | 30.0 | Longest wait between retries. |
| `stashbox_retry_backoff_multiplier` | 2.0 | Growth of the wait between retries. |
| `stashbox_request_timeout` | 30 | Seconds to wait for one response. |
| `stashbox_rate_limit_pause` | 60.0 | Seconds to wait after a 429 with no `Retry-After`. |
| `stashbox_per_page` | 100 | Scenes per page. |
| `stashbox_max_pages_performer` | 10 | Page cap for performer searches. |
| `stashbox_max_pages_studio` | 25 | Page cap for studio searches. |

## Why Use This?

The built-in Tagger searches by filename or video fingerprint. These don't always work:
- Renamed files with non-standard naming
- Files that don't have matching fingerprints on StashDB
- Scenes from compilations or rips

If you've already tagged the performers or studio, Scene Matcher leverages that information to find the right match.

## Development

```bash
cd plugins/sceneMatcher
python -m pytest
```

The tests run offline.

## Changelog

### 1.2.0
- Searches the stash-box selected in the Tagger, and hides the Match buttons for a scraper source. The button names the box and shows only for scenes not linked to it.
- Two phases: a quick text search, then a deep search by linked performers and studio.
- Better scoring: cleaned titles, a date signal, and a duration multiplier.
- New `maxResults` setting and page caps; truncated lists are reported.
- Clear errors for bad API keys, rate limits and partial results; a 120 second timeout with Retry.
- Local scene IDs are cached for 5 minutes in `plugin_data/sceneMatcher/`.
- Select fills the row's search box and runs the Tagger search.
- One observer, and partial stash-box dates shown as `2024` or `May 2024`.

### 1.1.1
- Certificates are now verified for stash-box requests.
