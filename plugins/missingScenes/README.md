# Missing Scenes

Discover scenes from StashDB (or another stash-box, or ThePornDB) that you don't have in your local Stash library. View missing scenes for performers, studios and tags, browse everything your favorites have released, and optionally add scenes to Whisparr for automated downloading and cleanup.

## Features

- **Performer, Studio & Tag Support**: Find missing scenes for any performer, studio, or tag linked to a stash-box
- **Browse Page**: Missing scenes across all your favorite performers, studios and tags
- **Visual Grid Display**: Thumbnails, titles, dates, performers, and one link per site the scene is listed on
- **Multi-Endpoint Support**: StashDB, FansDB, any configured stash-box, and ThePornDB
- **Trending Sort**: Most active scenes first
- **Fingerprint Index**: Scenes you have without a stash ID still count as owned when they match by fingerprint
- **Honest Errors**: Stash-box failures, rate limits and truncated results are shown, not reported as "all found"
- **Whisparr Integration** (Optional, v3, StashDB only): Add missing scenes to Whisparr
- **Auto-Cleanup** (Optional): Remove scenes from Whisparr when they get tagged in Stash
- **Scan Task**: Trigger Stash scans for newly downloaded scenes

## Requirements

- Stash v0.25.0 or later (requires `runPluginOperation` GraphQL mutation)
- At least one stash-box endpoint configured (Settings > Metadata Providers > Stash-box Endpoints)
- Performers/Studios/Tags must be linked to the stash-box (use the Tagger to link them)

## Usage

1. Navigate to any **Performer**, **Studio**, or **Tag** detail page
2. Click the **Missing Scenes** button in the header
3. The plugin will:
   - Query the stash-box for all scenes featuring that performer/studio/tag
   - Compare against your local Stash library
   - Display scenes you don't have

Use the sort control to order by date, title, or **Trending**. Each card links to the scene on every site the stash-box lists for it.

### Tag Support (Stash 0.30+)

Starting with Stash 0.30, tags can have Stash ID associations. On a Tag page linked to a stash-box, click **Missing Scenes** to discover scenes with that tag that you don't have locally. If a tag is linked to several configured stash-boxes, pick the endpoint from the dropdown.

### Browse Page

On the Scenes page, click **Missing Scenes** in the toolbar to open the Browse page (`/plugins/missing-scenes`). It lists missing scenes for your favorite performers, studios and tags (up to **Favorite Limit** of each type), with sorting and an endpoint dropdown.

### Trending

Trending is stash-box's own ordering. It only includes scenes that had fingerprint activity in the last 7 days, so an empty Trending list does not mean you have every scene. Switch to Release Date to see the full list. Trending is always most active first; the direction control does not apply.

### ThePornDB

ThePornDB endpoints work like a stash-box. Differences:

- ThePornDB has no way to filter by several IDs in one request. Browsing several favorites sends one request per favorite (up to 10 per page) and merges the results by scene. When there are more favorites than that, the page tells you the list was limited.
- **Excluded Tags** are not applied to ThePornDB.
- Odd or missing fields in ThePornDB responses are tolerated; one bad scene is skipped with a warning in the Stash log rather than failing the page. A reply with no scene list at all is an error.
- ThePornDB can't list scenes by tag, so a Tag page with ThePornDB selected shows that as an error.
- A studio whose ThePornDB link names no ThePornDB site shows "this studio isn't on ThePornDB". When browsing favorite studios, those are skipped; if none are on ThePornDB, that is an error.

### Errors and partial results

- A stash-box failure is shown as an error, not as "you have everything".
- When a request fails part-way, or the page limit (50 stash-box pages per request) is reached, the scenes found so far are shown with a **Retry from here** button that continues from where it stopped.
- A rejected key shows a hint to check the API key in Settings > Metadata Providers.
- On rate limits (HTTP 429) the plugin waits for the stash-box's `Retry-After` (within a 60 second budget per request), or 10 seconds when none is given, then tells you to try again shortly if it is still limited. Raise **Request Delay** if this happens often.

## Fingerprint Index

A scene you own may have no stash ID for the stash-box (not tagged yet, or tagged on a different stash-box). By default such scenes would show as missing. The fingerprint index fixes that:

1. Run **Build Fingerprint Index** in Settings > Tasks > Plugin Tasks. It looks up your scenes that have no stash ID for each configured stash-box by phash, oshash and md5. The **Build fingerprint index** button in the modal does the same for the current stash-box only.
2. A stash-box scene that one of your scenes matches by fingerprint counts as owned. The stats show "N counted as owned by fingerprint".
3. Matches are checked against duration to avoid false positives.
4. Later builds only look up new or changed scenes. Scenes that matched nothing are checked again after 30 days.

On a large library the first build takes a while, so the task is the better way to run it. To count only scenes linked by stash ID, turn on **Ignore Fingerprint Matches**.

## Settings

Configure in **Settings > Plugins > Missing Scenes**:

| Setting | Key | Description |
|---------|-----|-------------|
| **Default Stash-Box Endpoint** | `stashBoxEndpoint` | Endpoint selected by default in the UI dropdown. Empty uses the first configured endpoint. Enter the full GraphQL URL (e.g., `https://stashdb.org/graphql`). |
| **Whisparr URL** | `whisparrUrl` | Optional. Whisparr address, e.g. `192.168.1.10:6969` or `http://whisparr:6969/whisparr`. Empty disables Whisparr. |
| **Whisparr API Key** | `whisparrApiKey` | Optional. From Whisparr Settings > General > Security. |
| **Whisparr Quality Profile ID** | `whisparrQualityProfile` | Optional. Quality profile ID for added scenes (default 1). |
| **Whisparr Root Folder** | `whisparrRootFolder` | Optional. Root folder path; must match a root folder in Whisparr. |
| **Whisparr: Skip TLS Verification** | `whisparrSkipTlsVerify` | Only for an HTTPS Whisparr with a self-signed certificate. Other services are always verified. |
| **Search on Add** | `whisparrSearchOnAdd` | Search for the scene when adding to Whisparr. Off by default, so adding a scene doesn't start a download. |
| **Auto-cleanup Whisparr** | `enableAutoCleanup` | Remove scenes from Whisparr when they get tagged in Stash with a StashDB ID. |
| **Unmonitor Instead of Delete** | `unmonitorOnly` | With auto-cleanup, unmonitor the scene instead of deleting it. |
| **Scan Path for New Scenes** | `scanPath` | Path for the "Scan for New Scenes" task. Must be inside a Stash library. Separate several paths with `;`. |
| **Request Delay (seconds)** | `stashbox_request_delay` | Delay between paginated stash-box requests (default 0.5). Increase if you see rate limit errors. |
| **Max Retries** | `stashbox_max_retries` | Retries for failed stash-box requests (default 3). |
| **Excluded Tags** | `excludedTags` | Comma-separated stash-box tag UUIDs to leave out of results. Not applied to ThePornDB. |
| **Favorite Limit** | `favoriteLimit` | Maximum favorites per type used on the Browse page (default 100). |
| **Ignore Fingerprint Matches** | `ignoreFingerprintMatches` | Off by default: fingerprint matches count as owned. On: only scenes linked by stash ID count. |

## Whisparr Integration

Whisparr is optional. Missing Scenes works fully without it.

### Requirements

- **Whisparr v3 only.** v2 does not work. v3 is also called "eros" (the Eros edition of Whisparr).
- **StashDB scenes only.** Whisparr v3 stores StashDB scene IDs, so the **Add to Whisparr** button appears only on StashDB scenes. On other stash-boxes and on ThePornDB the button is replaced by a hint ("Whisparr needs StashDB") and **Add All to Whisparr** is hidden.

For Docker, use the v3 image: with hotio's Whisparr image ([hotio.dev/containers/whisparr](https://hotio.dev/containers/whisparr/)) that is the `v3` tag (also published as `eros`). The `v2` tag will not work. For other images see the [Servarr wiki](https://wiki.servarr.com/whisparr).

### Whisparr URL

Enter the address you use to reach Whisparr from the Stash server (not a `localhost` that means something else inside a container). These forms are accepted:

- `192.168.1.10:6969` or `whisparr:6969` (host:port; `http://` is added)
- `http://192.168.1.10:6969`
- `https://whisparr.example.com`
- With a URL Base (Whisparr Settings > General > URL Base), include it: `http://192.168.1.10:6969/whisparr`
- A trailing `/` or `/api` is removed for you

### Test Whisparr Connection

Run **Test Whisparr Connection** in Settings > Tasks > Plugin Tasks and read the Stash log (Settings > Logs). It checks the URL, API key, version, root folder and quality profile. On success it logs the Whisparr version and how many root folders and quality profiles it found. Otherwise each problem is logged as a warning.

### Troubleshooting Whisparr

If scenes appear but nothing is added to Whisparr (issues #135 and #115), run the connection test first. Its messages:

| Message | Fix |
|---------|-----|
| `Whisparr URL is empty` / `Whisparr API Key is empty` | Fill in the setting. |
| `API key rejected (HTTP 401)` | Copy the key again from Whisparr Settings > General > Security. |
| `Not found at .../api/v3 (HTTP 404); check URL Base` | Whisparr uses a URL Base. Add it to the URL (`http://host:6969/whisparr`). |
| `... is the wrong service: it did not answer with JSON` | The address is not Whisparr itself (a proxy login page, or another app on that port). |
| `... is the wrong service: it is <app>, not Whisparr` | The port belongs to Radarr, Sonarr or another app. |
| `Can't reach Whisparr at ...` | Wrong address or port, Whisparr is down, or Stash can't reach it (inside Docker use the container name or the host's LAN IP, not `localhost`). |
| `Whisparr v3 is required; this is v2...` | Run the v3 (eros) image. |
| `Root folder '...' is not in Whisparr` | Use one of the listed root folders exactly. |
| `Quality profile ... does not exist in Whisparr` | Use one of the listed profile IDs. |

Other things to know:

- When the scene list can't read Whisparr's status, a banner says "Whisparr status unavailable" with the reason. Run the connection test.
- Add failures show Whisparr's own error message.
- The Whisparr status cache lasts 60 seconds.

### Status Tracking

Scenes show their state in Whisparr:
- **Downloaded**: Already downloaded and available
- **Downloading**: Currently downloading, with progress percentage
- **Queued**: Waiting to start downloading
- **Stalled**: Download stalled (with error message)
- **Waiting**: In Whisparr but not yet searching

The plugin uses `stash:{scene_id}` as the foreign ID format. Adding a scene doesn't start a download unless **Search on Add** is on.

## Automation Features

### Auto-Cleanup Hook

When **Auto-cleanup Whisparr** is enabled, the plugin removes scenes from Whisparr when they get tagged in Stash with a StashDB ID, via the `Scene.Update.Post` hook.

1. You download a scene via Whisparr
2. The scene gets imported into Stash
3. You tag the scene with its StashDB ID (manually or via the Tagger)
4. The hook removes it from Whisparr

Safety rules: cleanup only acts on StashDB IDs (a scene tagged on another stash-box is left alone). It never removes an entry that is still downloading, and never one whose stored StashDB ID doesn't match the scene's.

**Options:**
- **Delete**: Removes the scene from Whisparr (default)
- **Unmonitor** (`unmonitorOnly`): Keeps the scene in Whisparr but unmonitored (prevents re-downloading)

### Tasks

Available in **Settings > Tasks > Plugin Tasks**:

| Task | Description |
|------|-------------|
| **Scan for New Scenes** | Triggers a Stash scan on the configured scan path(s). Every path must be inside a Stash library or the scan is refused; separate several paths with `;`. |
| **Cleanup Whisparr** | Batch removes all scenes from Whisparr that are now tagged in Stash, with the same safety rules as the hook. |
| **Test Whisparr Connection** | Checks the Whisparr settings and logs what is wrong. |
| **Build Fingerprint Index** | Builds the fingerprint index for every configured stash-box. |

### Recommended Workflow

1. **Configure Whisparr** in plugin settings (URL, API key, root folder), then run **Test Whisparr Connection**
2. **Set the scan path** to where Whisparr downloads scenes (e.g., `/data/unsorted`)
3. **Enable Auto-cleanup**
4. **Add missing scenes** to Whisparr from performer/studio pages (StashDB)
5. **Run "Scan for New Scenes"** periodically (or schedule it) to import downloads
6. **Tag imported scenes** with StashDB IDs using the Tagger
7. Scenes are cleaned up from Whisparr

## How It Works

1. **Get Entity**: Fetches the performer/studio/tag from your local Stash
2. **Find Stash ID**: Looks up the stash-box ID for that entity
3. **Query Stash-Box**: Fetches the scenes from the stash-box, 100 per page, up to 50 pages per request
4. **Get Local Scenes**: Reads an index of your local stash IDs for that stash-box
5. **Compare**: Filters out scenes you already have, by stash ID and by fingerprint index
6. **Display**: Shows missing scenes sorted by the selected order (default: release date, newest first)

## Caching and State

State lives in Stash's config directory, under `plugin_data/missingScenes/` (not in the plugin folder, so updates don't erase it):

- **Local stash-ID index**: your scenes' stash IDs per stash-box, kept for 5 minutes, then rebuilt on the next search. A scan started by the plugin's Scan task clears it. The backend `refresh_index` operation rebuilds it on demand.
- **Whisparr status cache**: 60 seconds.
- **Fingerprint index**: one SQLite file per stash-box. See [Fingerprint Index](#fingerprint-index).

Scenes you just tagged may take up to 5 minutes to stop showing as missing.

## Troubleshooting

### "Performer/Studio/Tag is not linked to StashDB"
The entity needs a stash ID for the stash-box. Use the Tagger (Scenes > Tagger) to match and link it.

### "No stash-box endpoints configured"
Go to Settings > Metadata Providers > Stash-box Endpoints and add at least one endpoint (e.g., StashDB).

### An error or "partial results" message
The stash-box failed or rate-limited the request. Use **Retry from here**, and check the API key under Settings > Metadata Providers if it says the key was rejected.

### Scenes I own show as missing
They probably have no stash ID for this stash-box. Run **Build Fingerprint Index** (and leave **Ignore Fingerprint Matches** off), or tag them with the Tagger.

### Results seem incomplete
One request reads up to 50 stash-box pages (5000 scenes). If that limit is reached the plugin says so and offers **Retry from here** to continue. It never reports "all found" for a truncated list.

## Technical Details

- **UI Plugin**: JavaScript + CSS injected on performer/studio/tag pages and the Scenes page
- **Backend**: Python script called via `runPluginOperation` GraphQL mutation
- **No External Dependencies**: Uses only the Python standard library
- **Pagination**: 100 scenes per page from the stash-box

## Changelog

### 1.5.0
- Stash-box failures, rate limits and truncated results are shown as errors or partial results with **Retry from here**, instead of "all found".
- The scene index moved to Stash's config dir (`plugin_data/missingScenes/`) with a 5 minute TTL.
- ThePornDB: tolerant response parsing; browsing several favorites merges up to 10 requests.
- Whisparr: v3 only and StashDB scenes only; URL forms (host:port, URL Base) accepted; new **Test Whisparr Connection** task; clear error messages and a status banner; auto-cleanup never deletes a downloading entry or one whose ID doesn't match; **Search on Add** is off by default.
- New Trending sort and site links on scene cards.
- New fingerprint index (**Build Fingerprint Index** task and button); scenes matched by fingerprint count as owned. New **Ignore Fingerprint Matches** setting.
- The scan path is validated against your Stash libraries and can hold several `;`-separated paths.

### 1.4.1
- Certificates are verified for stash-box, ThePornDB and Whisparr requests; new **Whisparr: Skip TLS Verification** setting.

## Development

```bash
cd plugins/missingScenes
python -m pytest
```

Tests that talk to a real Stash, StashDB or Whisparr skip unless `STASH_PLUGINS_INTEGRATION=1` is set. Point them at a test instance, never production.

- `test_integration.py` reads `STASH_URL` and `STASH_API_KEY` from the environment or from a `.env` file here (see `.env.example`).
- `test_whisparr_status.py` reads `WHISPARR_URL` and `WHISPARR_API_KEY` from the environment.

## License

MIT License
