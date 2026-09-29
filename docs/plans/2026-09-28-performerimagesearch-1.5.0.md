# performerImageSearch 1.5.0 plan

Goal: ship performerImageSearch 1.5.0, closing #164 and #165.
Approach: give the scrapers one fetch seam with a per-source deadline, so each source reports ok, empty, partial, error or timeout instead of an empty list. Tests can then use offline HTML fixtures. Next, fix what each source returns (FreeOnes full-size images, DuckDuckGo off by default and retried) and delete the dead code. Last, the UI, on a harness that loads the real JS: per-source status chips, filters on real dimensions without rebuilding the grid, preview and keyboard fixes, and stale-search guards. Add a README.
Run with: /fluffer:plan-run

## Context every task needs

- Worktree: `/home/carrot/code/stash-plugins/.worktrees/pis-1.5`, branch `fix/performerimagesearch-1.5.0`, based on `main` (2b306a8). Never touch the main checkout or other worktrees. **Never use `git stash`**, because the stack is shared with other sessions; compare with `git show HEAD:<path>` instead.
- Plugin dir: `plugins/performerImageSearch`. Python tests: `cd plugins/performerImageSearch && python -m pytest -q`. Current state: 36 passed, 9 deselected. The deselected 9 are the live-network tests in `test_image_search.py`: the root `pytest.ini` has `-m "not network"`. **Never run the network tests.**
- New tests are offline. Serve fixtures through the `_fetch` seam from Task 1, or patch `urllib.request.urlopen`. Fixtures go under `plugins/performerImageSearch/tests/fixtures/`, because `build_site.sh` excludes `**/tests/**`; anything outside `tests/` ships in the zip.
- **Fixture content rule:** fixtures are small hand-trimmed HTML or JSON snippets that keep only the markup the parsers need, with a fake performer name (`Jane Example`) and fake image paths. Never commit real pages, real names or real image URLs.
- Tickets: `gh issue view <n> --json body --jq .body`. Finding ids are in the "performerImageSearch" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path. **Its `image_search.py` line numbers are stale by +63** (the #168 host allowlist); use the code map at the end of this plan. P7-P11 follow the tickets' usage.
- **Conventions:**
  - Data functions return data or raise.
  - Responses carry `error`, `partial` and `warnings` fields that the UI renders.
  - Never `print()` outside the JSON reply; use `log`.
  - Standard library only.
- Keep the #168 host allowlist (`is_allowed_image_url`, `drop_disallowed_hosts`) working. Its tests are in `test_image_hosts.py`.
- When the HTTP request is cancelled, Stash kills the plugin's Python process. A client-side timeout therefore frees the server.
- **CI:** `.github/workflows/test.yml` JS job. If main's loop is still `plugins/tagManager/tests/test_*.js` when Task 5 runs, generalize it to `plugins/*/tests/test_*.js`. Other release branches make the same change; whichever lands second rebases.

## Backend

### Task 1 (hard): one fetch seam, per-source deadlines, errors reported (#164 P1, P5)

Files: modify `plugins/performerImageSearch/image_search.py` (every scraper 213-1000, `search_single_source` 1040-1093, `drop_disallowed_hosts` 100-112, `HEADERS` 32-36, imports 19-29); test `plugins/performerImageSearch/tests/test_sources.py` plus `tests/fixtures/`, and create `plugins/performerImageSearch/tests/__init__.py` if the other test dirs use one (match the repo)

Test first:
- `_fetch(url, deadline)` is the only place requests are made. Grep for `urlopen(` and `Request(` outside it; the count must be zero.
  - It sends the shared headers, with a current browser User-Agent string.
  - The socket timeout is `min(10, deadline - now)`.
  - It raises `SourceTimeout` when the deadline has passed, and `SourceBlocked` on 403 or 429, or on a Cloudflare challenge page (`cf-chl`, `Just a moment...`).
  - It raises `SourceHTTPError(status)` on other non-200 responses, and `SourceNotFound` on 404.
- Each scraper returns results or raises.
  - A 404 on the performer page is "not found": an empty list, no error.
  - A failed gallery page (after the index page worked) is skipped, and the source returns what it has with `partial` and a warning. Implement this with a small `SourceResult(results, warnings, partial)` or a tuple.
  - PornPics and EliteBabes index-page failures are errors, not debug logs.
- Gallery pages are fetched in parallel: a `ThreadPoolExecutor` with 4 workers, shut down with `wait=False, cancel_futures=True` when the deadline passes.
  - Gallery ids are sorted before the cap, so which galleries get fetched is deterministic. That replaces `list(set(...))`.
  - Each source has a 25s budget, the constant `SOURCE_BUDGET_SECONDS`.
  - A fixture with 20 galleries, where one hangs past the deadline, returns the other 19 with `partial` and a timeout warning within the budget. Use a fake clock or short budgets in the test.
- `search_single_source` returns `{results, status, error?, warnings?}`:
  - `status` is one of `ok`, `empty`, `partial`, `error`, `blocked` or `timeout`;
  - an unknown source is `error`: "Unknown source".
  - `drop_disallowed_hosts` returns the dropped count, and a non-zero count adds a warning ("N results from unexpected hosts were dropped").
- Offline fixtures for every source parse to the expected results: Babepedia, FreeOnes (a list page plus a gallery page), PornPics, EliteBabes, Boobpedia, JavDatabase (2 pages) and DuckDuckGo (the vqd page plus the JSON).
  - To write them, fetch each source's page for a common performer once (read-only GETs, from the dev machine).
  - Trim each page to the markup the regexes use, and replace the name and paths per the fixture rule.
  - Do not commit the raw pages.
- Fix the Babepedia docstring (223-224), which claims failures reach the UI.

Run: `cd plugins/performerImageSearch && python -m pytest -q tests/test_sources.py`, which fails.

Change: implement the above.

Run: `cd plugins/performerImageSearch && python -m pytest -q`, which passes.

Commit: `fix(performerImageSearch): per-source errors, time limits and parallel galleries`

### Task 2: each source returns only its own full-size images (#165 P2)

Depends on Task 1.

Task 1's live look (2026-09-29) found what each page really offers:
- FreeOnes galleries show only 4 unlocked photos as links: `<a data-id href=".../1440x0/filters:quality(85)/...jpg" data-size="1440x2160">` wrapping a `/350x350/center/middle/...` crop. A JSON-LD `ImageGallery.associatedMedia` block lists every photo, with the full `1440x0` URL, a `fit-in/0x230` thumbnail and the original width and height. Its URLs are JSON-escaped (`https:\/\/`). `tests/fixtures/freeones_gallery.html` has a trimmed copy of that block.
- EliteBabes galleries return related-gallery previews and `/content/lists/` images, possibly of other performers, alongside the gallery's own photos. On the model page, the first gallery-link pattern no longer matches; only the fallback finds galleries.
- JavDatabase idol pages return "similar idol" card thumbnails (`/idolimages/thumb/<other>.webp`), and every `/vertical/...jpg` image is a sponsored ad (`rel="sponsored"`). `/covers/full/` never appears on the page, so the `/covers/thumb/` to `/covers/full/` rewrite is unverified.

Files: modify `plugins/performerImageSearch/image_search.py` (`search_freeones`, `search_elitebabes`, `search_javdatabase`); extend `tests/fixtures/` and `tests/test_sources.py`

Test first:
- FreeOnes: a gallery page's results come from the JSON-LD block (unescaped). `image` is the full-size URL, `thumbnail` the small one, and `width`/`height` are the original size. A photo with only a square crop is skipped. The pattern never spans a `srcset` value.
- EliteBabes: only the gallery's own photos come back (the `<a href=".../content/<id>/<file>.jpg" data-width data-height>` links, whose `<id>` is the gallery's), each with its `data-width`/`data-height`. Related-gallery previews and `/content/lists/` images are excluded. The model page's gallery links are found by the pattern that matches today's markup, which has no class attribute.
- JavDatabase: similar-idol thumbnails (any idol slug other than the page's own) and `rel="sponsored"` or `/vertical/` images are excluded. Covers are returned only in a form the page actually links. Verify whether `/covers/full/<id>` exists with one read-only HEAD or GET; if it doesn't, return the thumb as both `thumbnail` and `image` and say so in the commit message.
- Fixtures: extend the three sources' fixtures with the markup that must be excluded (related galleries, lists images, similar idols, a sponsored vertical ad), per the fixture rule. You may fetch each page once more, read-only, to see that markup, then delete the raw copy.

Run: `cd plugins/performerImageSearch && python -m pytest -q tests/test_sources.py`, which fails.

Change: implement the above.

Run: `cd plugins/performerImageSearch && python -m pytest -q`, which passes.

Commit: `fix(performerImageSearch): full-size images, and only the performer's own, from each source`

### Task 3: DuckDuckGo off by default, retried once; dead code gone (#165 P4, P11)

Depends on Task 1.

Files: modify `plugins/performerImageSearch/image_search.py` (`search_duckduckgo_images` 823-1000, dead code: `SIZE_THRESHOLDS` 39-43, `ASPECT_THRESHOLDS` 46-50, `normalize_name_for_url` 115-118, `get_image_dimensions` 121-161, `filter_by_size_and_layout` 164-210, the HTML fallback 874-900, the JSON-regex fallback 945-962, `_is_small_image_url` 1003-1037, the Bing branch 1068-1071, `search_all_sources` 1096-1146, `main` 1149-1228), `plugins/performerImageSearch/performerImageSearch.yml` (the DuckDuckGo description), `plugins/performerImageSearch/test_image_search.py` (drop the unused `import re`, and any test of removed functions); test `plugins/performerImageSearch/tests/test_sources.py`, plus `tests/test_main.py` for the `main()` contract

Test first:
- DuckDuckGo on a 403 or a missing vqd waits 2s (patch `time.sleep`), fetches a fresh vqd and retries once. A second failure raises `SourceBlocked` ("DuckDuckGo rate-limited this search; try again later").
- **`main()` contract:**
  - with stdin `{"args": {"mode": "search", "query": q, "performerName": n, "source": s}}`, it prints `{"output": {"results", "query", "source", "status", ...}}`;
  - a missing `source` gives `{"error": "source is required"}`;
  - an unknown mode, or no query, gives `{"error": ...}` as today;
  - an exception gives `output.error` with `status: error`.
- Each removed name has no references left (grep).
- `main()` prints its reply and flushes before any abandoned gallery thread can delay exit: after writing the JSON it calls `sys.stdout.flush()` and `os._exit(0)`, so the reply is never held past the source budget by a stuck socket. Test this with a subprocess run of `image_search.py`, using a fixture-served source that never answers.

Run: `cd plugins/performerImageSearch && python -m pytest -q`, which fails on the new tests.

Change: implement the above.
- The yml description for DuckDuckGo becomes: "Web image search with SafeSearch off. It often gets rate-limited, so it is off by default."
- The JS default change is in Task 6.

Run: `cd plugins/performerImageSearch && python -m pytest -q` and `python .github/scripts/lint_manifests.py` (from the worktree root). Both pass.

Commit: `fix(performerImageSearch): retry DuckDuckGo once; remove dead code`

## UI

### Task 4: test harness that loads the real JS, and CI for it

Files: create `plugins/performerImageSearch/tests/harness.js`, `plugins/performerImageSearch/tests/test_real_file.js`; modify `plugins/performerImageSearch/performer-image-search.js` (a `window.__PERFORMER_IMAGE_SEARCH_TEST__` export block at the end); modify `.github/workflows/test.yml` (see the context section)

Test first: `test_real_file.js` loads the file into a vm context with stubbed `window`, `document`, `fetch`, `PluginApi` (including `PluginApi.Event.addEventListener`), timers and `location`. Model it on `plugins/tagManager/tests/harness.js`, which you can copy and adapt. It asserts:
- the file loads without throwing;
- the exports are reachable;
- the static "no undefined calls" check passes. Copy tagManager's `test_real_file.js` approach.

Run: `node plugins/performerImageSearch/tests/test_real_file.js`, which fails.

Change: add the harness, the export block and the CI loop.

Run: `for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done` from the worktree root, which passes.

Commit: `test(performerImageSearch): harness that loads the real UI file; CI runs it`

### Task 5: per-source status, a client timeout, no stale results (#164 UI, #165 P10)

Depends on Tasks 1, 4.

Files: modify `plugins/performerImageSearch/performer-image-search.js` (`searchImages` 144-182, `searchSource` 444-478, `pisSearch` 483-525, `updateFilterStatus` 219-248, `renderModal` 322-406, `hideModal` 301-317, `graphqlRequest` 60-89), `plugins/performerImageSearch/performer-image-search.css` (chip styles); test `plugins/performerImageSearch/tests/test_status.js`

Test first:
- **Chips:** each enabled source has a status chip: `pending` → `ok (n)`, `empty`, `partial (n)`, `error`, `blocked` or `timeout`. The chip's title shows the error or warnings. A response with `results` and `error` together keeps the results.
- **Completion:** the status line says "All sources finished" once no source is pending, whatever the image loading state.
- **Timeout:** each plugin call aborts after 45s with `AbortController`, and marks the source `timeout`.
- **Stale searches:** a search generation token is checked before every state change. Starting a new search, or opening another performer, while old requests are pending means:
  - the old responses change nothing (not `allResults`, `pendingSources` or `isLoading`);
  - a modal closed during the settings fetch doesn't come back.
- **Keys:** Escape closes the main modal when no preview is open, and `hideModal` removes that listener.

Run: `node plugins/performerImageSearch/tests/test_status.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(performerImageSearch): show each source's result; ignore superseded searches`

### Task 6: filters on real dimensions without rebuilding; preview and keyboard fixes (#165 P3, P6, P8, P9)

Depends on Task 5.

Files: modify `plugins/performerImageSearch/performer-image-search.js` (`DEFAULTS` 7-18, `getPluginSettings` 94-136, `applyFilters` 187-214, `pisImageLoaded` 530-546, `renderResults` 551-599, `pisShowPreview` 604-644, navigation 649-677, `handlePreviewKeydown` 411-422, `pisClosePreview` 682-689, `pisConfirmImage` 694-732); test `plugins/performerImageSearch/tests/test_grid.js`

Test first:
- **Aspect filter:** Portrait is `ratio < 0.9`, Square is `0.9 <= ratio <= 1.1`, and Landscape is `ratio > 1.1`. Test 0.9 and 1.1 exactly.
  - Dimensions come from the backend's `width`/`height` when present. Otherwise they come from the full image's size once it has loaded in the preview, then from the thumbnail's natural size. An `onerror` records "unknown" and counts as loaded.
  - Unknown dimensions pass every filter.
- **Grid:** new results from a source are appended as nodes, and a filter change toggles visibility. Assert that the existing `<img>` nodes are the same objects after a filter change and after another source's results arrive.
- **Preview:**
  - it stores the previewed result; prev and next recompute its index in the current filtered list;
  - when that result has been filtered out, prev and next go to the nearest visible neighbour.
  - A full image that fails to load shows the thumbnail with the notice "Full-size image unavailable; showing the thumbnail". A flag prevents looping.
  - When both fail, it shows "Image unavailable" and Confirm is disabled.
- **Keys:** while the modal is open, a capture-phase `keydown` listener on `window` handles the arrow keys and Escape. It calls `preventDefault` and `stopPropagation`, so Stash's Mousetrap hotkeys don't fire. Removing the modal removes the listener. Test that a bubble-phase `document` listener registered first doesn't see the event.
- **Defaults:**
  - Source defaults are per source: DuckDuckGo defaults to off, the others to on.
  - A settings fetch failure uses those defaults and doesn't turn everything on.
  - `defaultLayout` accepts `All`/`Any` in any case.

Run: `node plugins/performerImageSearch/tests/test_grid.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(performerImageSearch): filters on real sizes, stable grid, preview and keyboard fixes`

## Release

### Task 7: README, docs, version 1.5.0

Depends on Tasks 1-6.

Files: create `plugins/performerImageSearch/README.md`; modify `plugins/performerImageSearch/performerImageSearch.yml` (`version: 1.5.0`, descriptions), the root `README.md` (the performerImageSearch row links the new README), `CHANGELOG.md` (root, the Performer Image Search section)

Check:
- **README:**
  - The sources: what each is good for, and which performers each covers.
  - Each setting, with its default.
  - The layout values and their exact bounds.
  - The per-source status chips and what each state means.
  - That editing the query box only changes DuckDuckGo results; site sources search by the performer's name.
  - Network requirements: sources behind Cloudflare may block cloud, VPN or datacenter addresses (P7).
  - That DuckDuckGo is off by default now.
  - Troubleshooting, and a v1.5.0 changelog entry.
- **Root CHANGELOG:** a `### 1.5.0` entry, which says that DuckDuckGo is now off by default even for users who never changed the toggle.
- No em-dash characters.

Run: `python .github/scripts/lint_manifests.py`, all Python and JS tests, and a relative-link check over the READMEs (inline script, no network). All pass.

Commit: `docs(performerImageSearch): README and 1.5.0 changelog`

### Task 8: deploy to stash-test and exercise

Depends on Task 7.

Files: none changed.

Check:
- Deploy with rsync, excluding `tests`, `__pycache__`, `.pytest_cache` and `test_*.py`, then `reloadPlugins`. The key is `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it.
- For one stash-test performer, run `runPluginOperation` once per source. Record each source's status, count and time, and confirm each finishes within the budget.
- Browser pass on that performer's page (the user logs in if needed):
  - the chips reach a final state;
  - the filters don't rebuild the grid;
  - in the preview, the arrow keys don't change Stash's page, and Escape closes the preview, then the modal.
- **Do not click Confirm:** it would change the performer's image. If you want to exercise Confirm, snapshot the performer's image and restore it afterwards.

Commit: none.

## Code map

This was mapped on 2026-09-28 against main 2b306a8; line numbers are approximate.

- **`image_search.py` (1232 lines)**
  - **Setup:**
    - Imports 19-29 (no explicit `urllib.error`).
    - `HEADERS` 32-36 (a Chrome/120 User-Agent).
    - Dead `SIZE_THRESHOLDS` 39-43 and `ASPECT_THRESHOLDS` 46-50.
  - **Host allowlist (from #168):** `SOURCE_IMAGE_HOSTS`/`OPEN_WEB_SOURCES` 55-65, `_is_public_host` 70-81, `is_allowed_image_url` 84-97, `drop_disallowed_hosts` 100-112 (drops silently).
  - **Dead helpers:** `normalize_name_for_url` 115-118, `get_image_dimensions` 121-161, `filter_by_size_and_layout` 164-210.
  - **Scrapers.** Each uses regex parsing, the shared `HEADERS`, a 10s timeout per socket operation, serial gallery fetches and swallowed exceptions.
    - `search_babepedia` 213-287: 1 request.
    - `search_freeones` 290-392: a list page plus up to 20 galleries; the "full" image equals the thumbnail at 344-358.
    - `search_pornpics` 395-509: plus 20 galleries; `list(set())` at 440; an index failure only logs at Debug (444-445).
    - `search_elitebabes` 512-616: up to 10 galleries; `list(set())` at 546; Debug-only failure at 549-550.
    - `search_boobpedia` 619-692.
    - `search_javdatabase` 695-820: pages `?ipage=2..5`.
    - `search_duckduckgo_images` 823-1000: vqd regexes at 862-872, the HTML fallback at 874-900, the size/layout map at 905-913 (never sent), the JSON-regex fallback at 945-962, the 300px minimum at 980-982, and one outer `except` at 995-998.
  - **Dispatch:**
    - `_is_small_image_url` 1003-1037 is dead.
    - `search_single_source` 1040-1093: the Bing branch is at 1068-1071, and an unknown source returns `[]` at 1072.
    - `search_all_sources` 1096-1146 is legacy.
  - **`main` 1149-1228:** it strips suffixes from the query when `performerName` is empty (1177-1180). The no-`source` path is at 1201-1208, and the exception output at 1217-1226.
- **`performer-image-search.js` (861 lines, one IIFE, no tests)**
  - **Setup:** `DEFAULTS` 7-18 (all on, including DDG at 17), `ALL_SOURCES` 21-29, `ASPECT_THRESHOLDS` 32-36, state 39-55.
  - **Settings and fetch:**
    - `getGraphQLUrl`/`graphqlRequest` 60-89 have no timeout.
    - `getPluginSettings` 94-136: `!== false` at 119, and the catch at 133 enables everything.
    - `searchImages` 144-182 throws on `output.error`, which drops any results sent with it (173-175).
  - **Filters and status:**
    - `applyFilters` 187-214 uses `ratio >= min && ratio < max` at 208.
    - `updateFilterStatus` 219-248: "All sources complete" (227-228) is never shown, and "loading x/y" (233-234) never ends.
    - `setPerformerImage` 253-276.
  - **Modal:**
    - `showModal` 281-296, `hideModal` 301-317.
    - `renderModal` 322-406 is async; Enter is handled at 387-395 and the layout change at 398-405.
    - `handlePreviewKeydown` 411-422 is a bubble-phase `document` listener with no `preventDefault`.
  - **Search:**
    - `addResultsFromSource` 427-439.
    - `searchSource` 444-478 changes `pendingSources` at 455 and 473 without a token.
    - `pisSearch` 483-525.
  - **Grid:**
    - `pisImageLoaded` 530-546 re-renders every 10th load; the `loadedCount` check is at 540.
    - `renderResults` 551-599 rebuilds `innerHTML`; `onerror` at 589 records nothing.
  - **Preview:**
    - `pisShowPreview` 604-644: the index is stored once at 610, the thumbnail fallback at 632-637 loops, and the listener is added at 642.
    - Navigation 649-677; `pisClosePreview` 682-689.
    - `pisConfirmImage` 694-732 reloads the page after 1s.
  - **Page integration:** `showStatus`/`escapeHtml` 742-761; `getPerformerIdFromUrl`, `getPerformerNameFromPage` and `addSearchButton` 766-841 (an unbounded 500ms retry); `init` 846-855 (`stash:location`).
- **Other files:**
  - **`performer-image-search.css`:** tiles are `aspect-ratio: 1; object-fit: cover` (186-216); `.pis-error` 218-234; status 258-275.
  - **`performerImageSearch.yml`:** version 1.4.1; the settings are `defaultSearchSuffix`, `defaultLayout` and seven `enable*` toggles.
  - **Tests:**
    - `test_image_search.py` has 9 live-network tests (`pytestmark = network`) and an unused `import re`.
    - `test_image_hosts.py` is offline and has about 35 cases.
    - There are no JS tests and no README.
