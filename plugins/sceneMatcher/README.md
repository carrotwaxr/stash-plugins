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
2. Click "Match" on a scene. The first phase is two separate text searches: one for the cleaned title (the scene's title, or its filename when it has none, with release tags, dates and sizes removed), then one for the studio name plus the first two performer names, whether or not they are linked to the box.
3. If the results are not right, click the deep search. It needs a performer or the studio linked to the box. With both linked, it first asks for scenes from the linked studio with the linked performers. When that finds fewer than 10 scenes, or only performers or only the studio are linked, it also asks for every scene with the linked performers and every scene from the linked studio. With two or more linked performers, each performer query runs twice, for scenes with all of them and for scenes with any of them, and the results are merged.
4. Results are scored and sorted best first. Scenes you already have locally are listed after the ones you do not.
5. Click Select on a result. Scene Matcher finds the Tagger row of the scene you matched (by its own scene link), puts the stash-box scene ID in that row's search box and runs the Tagger's own search. The Tagger then handles the rest (creating performers, studios, and so on).

## Scoring

- **Title:** compared with the cleaned title (or filename), after removing the studio and performer names and stop words from both. The similarity weighs how much of the stash-box title is found in yours against how much of yours it accounts for (their harmonic mean), and words match despite small typos. So "Hot Day" against "Jane Doe - Hot Day" is a full match when Jane Doe is one of the scene's performers, but "Massage" against "Stepsister Massage Surprise" is only partial. Similarity of 0.9 or more adds 10; 0.5 or more adds 5.
- **Studio:** +3 when it is the linked studio.
- **Performers:** +2 for each linked performer in the scene.
- **Date:** +3 for the same day, +1 for the same month (when the stash-box only has a month) or a date one day apart. The scene's own date is tried first, then a date in the filename, then one in the title. A year-only date earns nothing.
  - Dates in names are read as `YYYY-MM-DD`, `YYYY.MM.DD`, `YYYY_MM_DD` or `YYYYMMDD`, and as `DD.MM.YYYY` or `MM.DD.YYYY` only when just one of those is a real date.
  - Two-digit dates are read year first, `YY.MM.DD`, the scene-release convention: `Studio.24.01.15.Title` is 15 January 2024, and `15.01.24` is read as 24 January 2015, not 15 January 2024.
- **Duration:** the total is multiplied by 0.5 to 1.0 by how close the durations are.

## Limits and truncation

The deep search returns at most **Max Results** scenes (default 50, 10 to 500). Fetching is also capped at 10 pages for performer searches and 25 for studio searches (`stashbox_max_pages_performer`, `stashbox_max_pages_studio`). Queries ask for the newest scenes first. When Max Results cuts the list, the modal says "Showing the top N of M candidates". When a page cap stopped the fetching, it says only the newest scenes were searched and how many the box has. The stash IDs in your library (for the In Stash badges) are cached on the server for 5 minutes in `<Stash config dir>/plugin_data/sceneMatcher/`.

## Errors

- A rejected API key names the stash-box and points you to Settings > Metadata Providers.
- Rate limits (429) honour the `Retry-After` header, up to 60 seconds. Without one, the plugin waits the Rate Limit Pause setting. One request waits at most 90 seconds in all.
- Each search phase has a 100 second budget for all its requests and waits. When it runs out, the phase stops and shows what it found, with a warning.
- If some queries fail and others succeed, the successful results are shown with a warning.
- If Stash can't list the stash IDs in your library, the results still show, without the In Stash badges, and a warning says so.
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
| `stashbox_request_timeout` | 30 | Seconds to wait for one response, cut to what is left of the phase's 100 seconds. |
| `stashbox_rate_limit_pause` | 60.0 | Seconds to wait after a 429 with no `Retry-After`, cut so one request waits at most 90 seconds. |
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
- Clear errors for bad API keys, rate limits and partial results; each search phase stops within 100 seconds with what it found, and the browser times out at 120 seconds with Retry.
- Local scene IDs are cached for 5 minutes in `plugin_data/sceneMatcher/`.
- Select fills the matched scene's own Tagger row and runs the Tagger search.
- One observer, and partial stash-box dates shown as `2024` or `May 2024`.

### 1.1.1
- Certificates are now verified for stash-box requests.
