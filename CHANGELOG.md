# Changelog

Changes to each plugin, newest first. Stash shows the installed and available version of each plugin under **Settings → Plugins**. Older history for mcMetadata and Tag Manager is in their READMEs.

## Missing Scenes

### 1.5.0
- Stash-box failures, rate limits and truncated results now show as errors or partial results with **Retry from here**. Missing Scenes no longer says you have everything when a request failed or hit the page limit.
- The scene index is kept in Stash's config dir (`plugin_data/missingScenes/`), so it survives plugin updates.
- ThePornDB responses are parsed tolerantly, and browsing several favorites merges up to 10 requests. Excluded tags are not applied to ThePornDB.
- Whisparr: v3 only, and only for StashDB scenes (other boxes show a hint instead of the Add button). The URL accepts `host:port` and a URL Base. New **Test Whisparr Connection** task, clearer errors, and a banner when Whisparr can't be read. Auto-cleanup never deletes an entry that is downloading or whose ID doesn't match. **Search on Add** is off by default.
- New Trending sort (it only includes scenes with activity in the last 7 days) and site links on scene cards.
- New fingerprint index: the **Build Fingerprint Index** task and button count untagged or other-box scenes matched by phash, oshash or md5 as owned. The **Ignore Fingerprint Matches** setting turns this off.
- The scan path must be inside a Stash library, and can hold several `;`-separated paths.

### 1.4.1
- Certificates are now verified for StashDB, ThePornDB and other stash-boxes, and for Whisparr. A new **Whisparr: Skip TLS Verification** setting covers an HTTPS Whisparr with a self-signed certificate.
- The stats bar escapes performer, studio and stash-box names.

## Scene Matcher

### 1.2.0
- Searches the stash-box selected in the Tagger, then the `stashBoxEndpoint` setting, then the first configured box. The Match button names the box, shows only for scenes not linked to it, and is hidden when the Tagger's source is a scraper.
- Two phases: quick text searches (the cleaned title, then the studio and performer names), then a deep search by linked performers (all of them first, then any) and studio.
- Scoring uses the cleaned title, a date bonus (the scene's date, then a date in the filename) and the duration. The new `maxResults` setting (default 50) and page caps limit the deep search, and a cut list is reported.
- Bad API keys name the box and point to Settings > Metadata Providers. Rate limits honour `Retry-After`, partial results are shown, and a 120 second timeout offers Retry.
- Every setting the plugin reads is now declared in the manifest. Local scene IDs are cached for 5 minutes in `<Stash config dir>/plugin_data/sceneMatcher/`.
- Select fills the row's search box and runs the Tagger search. Partial stash-box dates show as `2024` or `May 2024`.

### 1.1.1
- Certificates are now verified for stash-box requests.

## Performer Image Search

### 1.5.0
- **DuckDuckGo is now off by default, even if you never changed the toggle.** It often rate-limits searches. Turn on **Enable DuckDuckGo Images** in the plugin settings to use it again.
- Each source shows a status chip in the modal: ok, empty, partial, error, blocked or timeout. Hover a chip to see the error. Sources have a 25 second limit and load gallery pages in parallel.
- FreeOnes returns full-size images instead of square crops. EliteBabes returns only the gallery's own photos. JavDatabase returns only the performer's own images, without similar-idol thumbnails or sponsored ads.
- DuckDuckGo retries once when blocked, then reports that it is rate-limited.
- The layout filter uses real image sizes. Portrait is below 0.9, Square is 0.9 to 1.1 and Landscape is above 1.1. Images of unknown size pass every filter.
- Preview: arrow keys and Escape no longer trigger Stash's hotkeys. If the full image fails, the thumbnail shows with a notice, and Confirm is disabled when neither loads.
- Editing the query box changes only DuckDuckGo results. Site sources search by the performer's name.

### 1.4.1
- Only images hosted by the source that found them can be set as a performer image. DuckDuckGo results must be on a public host.

## Tag Manager

### 0.7.0
- Python 3.9+ with no required packages. `thefuzz` is optional. `stashapp-tools` is no longer used (fixes #129).
- A full StashDB tag fetch takes about 5 seconds instead of 30-40+. Stash-box errors show in the UI, and a rejected API key (HTTP 401/403) says so. Requests send a `User-Agent`, which fixes HTTP 403 from ThePornDB and JAVStash. ThePornDB tag fetches now get every tag instead of the first 100.
- Caches and sync history moved to `<Stash config dir>/plugin_data/tagManager/`, so plugin updates no longer wipe them. The old `cache/` folder can be deleted.
- Scene Tag Sync uses every linked stash-box that has an API key, keeps tags added during a long sync, and doesn't add back tags you removed. New "Reset Scene Tag Sync History" task. The first live sync after upgrading can re-add tags you removed before 0.7.0 one last time.
- Blacklist: `/regex/flags` syntax, `,` and `;` separators, a Blacklist editor on the Match tab, and it now applies to searches, Import All and sync.
- Accept/Apply: saved category mappings are pre-selected, and `Create "<category>"` now really creates the parent. Category mappings are stored per stash-box.
- Merging tags asks for confirmation. On Stash 0.31+ the merge is one transaction. Parents and children carry over, plus the parent picked in the dialog.
- Import shows progress and can be cancelled. The conflicts dialog re-checks rows after each action.
- Tag Hierarchy, navigation under a sub-path and numeric settings fixes.

### 0.6.1
- The stash-box URL and API key are looked up in Stash's configuration rather than taken from the browser. An endpoint Stash doesn't have is rejected.
- Only the plugin's `assets/` folder is served to the browser.

## mcMetadata

### 1.6.0
- Renames are safer. Names are sanitized, capped at 255 bytes each, and can't escape the base path, which must be inside a Stash library. **Max Filepath Length** is clamped to 40-800 and trims tags, then whole performer names, only when a path is too long. A destination that's already taken is never overwritten: the move is skipped with a warning, and two files of one scene get a numbered suffix such as ` (2)`. Case-only renames work on case-insensitive file systems.
- Subtitles, funscripts and other sidecar files now move with the video. The new **Move Sidecar Files** setting is on by default.
- Bulk runs no longer skip scenes, and the hook ignores mcMetadata's own "mark organized" update.
- NFO files are always valid XML. **NFO Exclude Fields** also accepts `thumb`, `poster`, `actor` and `tag`. New settings: **NFO File Name**, **Poster File Name**, **Backdrop File Name** and **NFO Rating Field**. NFOs now include `<director>` and stash-box `<uniqueid>` entries.
- New Plex mode for Plex Media Server 1.43.1+. It follows Plex's documented format and hasn't been checked against a live Plex yet.
- Jellyfin folder-per-movie names work, and updating a performer re-exports that performer's image.
- Bad settings fall back to defaults with a warning. An invalid **Organized Condition** now processes no scenes until you fix it.
- The log file appends and rotates at 5 MB. Image downloads use your Stash login, skip default performer images, and refuse redirects. Saved images are readable by other users, such as a media server (they were saved as owner-only).

## Studio Manager

### 0.1.1
- Studios in an existing parent cycle are no longer hidden. The smallest id of each cycle is shown at the top level with the rest beneath it, each marked "cycle", with a warning above the tree. A parent whose chain runs into a cycle is refused.
- Removing one pending change keeps the others, and Cancel no longer refetches. Stats and the context menu reflect pending changes, and a moved studio's ancestors are expanded.
- Saves lock editing while running, remove parents first and then set new parents shallowest first, and refuse cycles before any request. Failed changes stay pending with the error on the row, and the toast reads "N saved, M failed". A failed reload keeps pending changes.
- The studio link, context menu View/Edit and the toolbar button navigate inside Stash, including under a sub-path. Leaving with unsaved changes asks first, the browser warns before a reload, and pending changes are restored when you return.
- Only Delete removes a parent (not Backspace), and no shortcuts fire while you type. Event listeners no longer pile up, and there is no page-wide MutationObserver.
- The Stash floor is v0.30.

## Repository

- Tests run on every pull request, and a publish only happens after they pass.
- Plugin zips contain only runtime files and are reproducible. A commit that only changes tests no longer offers an update.
