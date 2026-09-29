# sceneMatcher 1.2.0 plan

Goal: ship sceneMatcher 1.2.0, closing #161, #162 and #163.
Approach: backend first. The endpoint comes from the Tagger's source and is resolved against the configured boxes. The local-ID cache moves server-side, and errors come back as fields. Candidate queries report truncation, and scoring works on a cleaned title with a date signal. Then a harness that loads the real JS, and the UI fixes on top of it: the gate, listeners, request tokens, the handoff, and partial dates.
Run with: /fluffer:plan-run

## Context every task needs

- Worktree: `/home/carrot/code/stash-plugins/.worktrees/scenematcher-1.2`, branch `fix/scenematcher-1.2.0`, based on `main` (2b306a8). Never touch the main checkout or other worktrees. **Never use `git stash`**, because the stack is shared with other sessions; compare with `git show HEAD:<path>` instead.
- Plugin dir: `plugins/sceneMatcher`. Python tests: `cd plugins/sceneMatcher && python -m pytest -q` (root `pytest.ini`). Current state: 55 passed, all in `tests/test_unit.py`, all testing real code.
- Tests are offline and use mocks. Patch `scene_matcher.stash_graphql`, `scene_matcher.get_stashbox_config` and `urllib.request.urlopen` (for `stashbox_api`); never contact Stash or a stash-box. Write each test first and see it fail.
- Tickets: `gh issue view <n> --json body --jq .body`. Finding ids F1-F13 are in the "sceneMatcher" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path. **Its line numbers are stale**: use the code map at the end of this plan. F11-F13 are unlabelled in the audit; this plan's ids follow the tickets.
- **Conventions:**
  - Data functions return data or raise.
  - Responses carry `error`, `warnings[]`, `partial: true` and `auth_error: true` fields that the UI renders.
  - Never `print()` outside the JSON reply; use `log`.
  - Runtime state goes in `<server_connection.Dir>/plugin_data/sceneMatcher/`; copy `plugins/tagManager/plugin_data.py` and change the plugin id.
  - Standard library only (plus the optional `certifi` already used).
- Reference code in tagManager 0.7.0, which is on main:
  - `plugins/tagManager/tag_manager.py` `resolve_stashbox`, which resolves an endpoint sent by the UI against the configured boxes and never calls an arbitrary URL;
  - `plugins/tagManager/stashdb_api.py` `USER_AGENT`;
  - `plugins/tagManager/tests/harness.js` plus the `window.__TAG_MANAGER_TEST__` hook at the end of `tag-manager.js`.
- **Upstream Stash facts (v0.30+):**
  - **Tagger source:** the Tagger source select is `select#scraper`, with option values `stashbox:<endpoint>` or `scraper:<id>`. React sets its initial value without firing `change`. The saved choice is also in `configuration.ui.taggerConfig.selectedEndpoint`.
  - **Rows and pills:** each Tagger row is a `.search-item`. It renders `span.stash-id-pill[data-endpoint=<box name>]` for every endpoint the scene is linked to, and search-result rows inside the same `.search-item` render pills too.
  - **Search input:** the input is `input.text-input` (no `type` attribute).
  - **Enter key:** Stash listens for `onKeyPress`. A synthetic `keypress` needs `key: "Enter"`, `keyCode: 13`, `charCode: 13` and `which: 13`, or React 17 drops it.
  - **Stash-box dates:** stash-box dates may be `YYYY`, `YYYY-MM` or `YYYY-MM-DD`.
- **CI:** `.github/workflows/test.yml` JS job. If main's loop is still `plugins/tagManager/tests/test_*.js` when Task 7 runs, generalize it to `plugins/*/tests/test_*.js`. The missingScenes 1.5.0 branch makes the same change; whichever lands second rebases.
- stash-test is shared with other sessions. Only Task 13 deploys, and only plugin files.

## Backend

### Task 1: endpoint from the UI, resolved and normalized; a User-Agent (#161 F2 backend, F12 backend)

Files: modify `plugins/sceneMatcher/scene_matcher.py` (`get_scene_context` endpoint pick ~611-633, every `stash_id.endpoint ==` compare, `main`), `plugins/sceneMatcher/stashbox_api.py` (request headers in `graphql_request_with_retry` ~146-152); create `plugins/sceneMatcher/conftest.py` if shared fixtures help; test `plugins/sceneMatcher/tests/test_endpoints.py`

Test first:
- `normalize_endpoint(url)` gives `(url or "").strip().rstrip("/").lower()`, and is used on both sides of every endpoint compare. That covers the configured box, the setting, the UI arg, and local `stash_ids[].endpoint`.
- `resolve_endpoint(requested, boxes, setting)` covers:
  - the UI's `endpoint` arg, when it matches a configured box after normalizing, wins;
  - else the plugin setting `stashBoxEndpoint`, normalized, and it also accepts the URL without `/graphql`;
  - else the first box.
  - An `endpoint` arg that matches no configured box returns an error naming the configured boxes, and never makes a request to it.
- `get_scene_context` with a scene linked only to ThePornDB, when the target is StashDB, is not rejected as "already has an ID". It is rejected only when linked to the resolved endpoint.
- `stashdb_url` (the site base for links) follows upstream's rule: the text before `/graphql` (`re.match(r"(https?://.*?/)graphql", url)`). It is separate from the cache key.
- Every stash-box request sends `User-Agent: stash-plugins-sceneMatcher/<version>`, where the version comes from `sceneMatcher.yml`, read once as tagManager does, or a constant kept in sync by Task 12.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_endpoints.py`, which fails.

Change: implement the above. The `find_matches_fast` and `find_matches_thorough` ops accept an optional `endpoint` arg. Responses include `endpoint` (the resolved GraphQL URL) and `endpoint_name` (the box name) so the UI can label things.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes.

Commit: `fix(sceneMatcher): search the endpoint the Tagger selected; normalize endpoints`

### Task 2: null-studio crash; a server-side local-ID cache that hits (#162 F3, F4)

Depends on Task 1.

Files: create `plugins/sceneMatcher/plugin_data.py` (copied from tagManager's, id `sceneMatcher`); modify `plugins/sceneMatcher/scene_matcher.py` (`format_results` ~700-731, `get_local_scene_stash_ids` ~165-213, the cache compare ~776 and ~891, `main`); test `plugins/sceneMatcher/tests/test_cache.py`

Test first:
- `format_results` with a stash-box result whose `studio` is null, when the local scene's studio is linked (`studio_stash_id` set), doesn't raise and scores studio 0.
- `local_stash_ids(endpoint)` builds the set with `findScenes(scene_filter: {stash_id_endpoint: {endpoint: E, modifier: NOT_NULL}})`, 1000 per page. Verify the filter shape against Stash's schema: `StashIDCriterionInput {endpoint, stash_id, modifier}`. It returns only that endpoint's ids, compared normalized.
- The set is cached in the data dir for 5 minutes, keyed by the normalized endpoint (md5 with `usedforsecurity=False`), and written with a unique temp file plus `os.replace`.
- A second call within the TTL does no Stash query.
- An empty set is a valid cache hit, not a miss.
- A corrupt file rebuilds.
- `stash_graphql` returning None raises a clear error.
- The ops no longer return `local_stash_ids`, and ignore the `cached_local_stash_ids`/`cache_endpoint` args. Old UI builds that still send them keep working.
- `main` calls `plugin_data.configure(server_connection)` first.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_cache.py`, which fails.

Change: implement the above. Remove the unused `cache_hit` parameter of `format_results`.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes.

Commit: `fix(sceneMatcher): no crash on a studio-less result; a local-ID cache that hits`

### Task 3: stash-box errors reported, not read as "no matches" (#162 F8 backend)

Depends on Task 1.

Files: modify `plugins/sceneMatcher/stashbox_api.py` (`graphql_request_with_retry` ~120-250, `paginated_query` ~253-322, `search_scenes_by_text` ~536-562), `plugins/sceneMatcher/scene_matcher.py` (`find_matches_fast` ~734-808, `find_matches_thorough` ~811-918); test `plugins/sceneMatcher/tests/test_errors.py`

Test first (patch `urllib.request.urlopen`, and patch `time.sleep` and assert the calls):
- An HTTP 401 or 403 raises `StashBoxAPIError` with `is_auth_error` true.
- A 200 response carrying GraphQL `errors` and `data: null` raises `StashBoxAPIError`, which covers stash-box's "not authorized" when no API key is set. It gets `is_auth_error` when the message says unauthorized or forbidden.
- A 429 with `Retry-After: 5` sleeps 5s. A missing `Retry-After` uses `rate_limit_pause`. A `Retry-After` over 60s, or a total wait over 90s per request, raises a rate-limit error instead of sleeping.
- A read timeout (`TimeoutError`/`socket.timeout`) is retried like a connection error.
- A non-JSON body (e.g. a Cloudflare page) raises with a snippet.
- `search_scenes_by_text` no longer swallows errors.
- `paginated_query` returns `(scenes, total, error)`: a later-page failure keeps the earlier pages and returns the error.
- `find_matches_fast`:
  - when one of its two searches fails, it returns the other's results plus `warnings: [<message>]`;
  - when both fail, it returns `error`;
  - on an auth failure it returns `auth_error: true` with a message naming the box and pointing to Settings > Metadata Providers.
- `find_matches_thorough`: a page failure returns the scenes so far plus `partial: true` and `warnings`.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_errors.py`, which fails.

Change: implement the above.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes.

Commit: `fix(sceneMatcher): report stash-box auth errors, rate limits and partial results`

### Task 4: candidate queries that say when they're truncated (#163 F7)

Depends on Task 3.

Files: modify `plugins/sceneMatcher/stashbox_api.py` (`query_scenes_combined` ~565-611, `query_scenes_by_performers` ~614-656, `query_scenes_by_studio` ~493-533, `get_config` ~86-117), `plugins/sceneMatcher/scene_matcher.py` (`find_matches_thorough` ~851-885, wrappers ~289-333); test `plugins/sceneMatcher/tests/test_candidates.py`

Test first:
- With 2+ linked performers, the performer query sends `modifier: INCLUDES_ALL` first. When that returns fewer than `MIN_COMBINED_RESULTS_THRESHOLD` (10) scenes, it adds an `INCLUDES` query and merges, deduped by id. With 1 performer it sends `INCLUDES`. The combined query (performers plus studio) follows the same rule.
- Page caps come from settings:
  - performer and combined queries use `stashbox_max_pages_performer`, default 10, which is today's effective value;
  - studio queries use `stashbox_max_pages_studio`, default 25.
  - The wrappers stop hard-coding `max_pages=10` (and the studio wrapper's ignored `max_pages` parameter goes away).
- The fallback threshold is checked on the raw query count, before phase-1 ids are excluded.
- Phase 2 sorts the scored results and returns the top N, with setting `maxResults` (NUMBER), default 50, clamped to 10-500. It adds `truncated: true` and `total_candidates` when N or the page cap cut anything. Use the `total` from `paginated_query`.
- `get_config` allows `max_retries` of 0.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_candidates.py`, which fails.

Change: implement the above.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes.

Commit: `fix(sceneMatcher): AND across performers first; report truncated candidate lists`

### Task 5 (hard): score against a cleaned title, with a date signal (#163 F9)

Depends on Task 1.

Files: modify `plugins/sceneMatcher/scene_matcher.py` (`clean_title` ~221-265, `normalize_title` ~389-398, `tokenize`, `token_similarity`, `title_similarity`, `score_scene` ~553-593, `format_results` ~711-715); test `plugins/sceneMatcher/tests/test_scoring.py`

Test first (use realistic release names; a table of cases):
- `clean_title` bugs that also corrupt the phase-1 search term:
  - `Brazzers.24.01.15.Jane.Doe.Hot.Day.XXX.1080p.MP4-WRB` → `Jane Doe Hot Day` (studio and date removed only when they are recognised, see below), and it keeps `Hot Day`.
  - Dotted dates `YY.MM.DD` and `YYYY.MM.DD` are stripped before separators become spaces.
  - The extension pattern no longer eats the last short word of a name that has already lost its extension (`...Hot.Day` keeps `Day`).
  - The release-group pattern only strips a trailing `-GROUP` after a resolution or source tag (`1080p`, `2160p`, `WEB`, `XXX`, `MP4`), so `jane-doe-hot-scene` keeps `scene`.
- `normalize_title` splits `_` like other separators.
- `extract_date(filename)` finds `YY.MM.DD`, `YYYY.MM.DD`, `YYYY-MM-DD` and `DD.MM.YYYY` only where unambiguous (a day over 12, or a 4-digit year first). It returns an ISO string or None.
- The title score compares the stash-box title against the cleaned local title, with the studio name and linked performer names removed as tokens. It uses a containment measure: the share of the stash-box title's tokens found in the local tokens, fuzzy-matched at 0.75. Stop words don't count (`the a an and of in on with to for`).
  - `score("Hot Day", cleaned "Jane Doe Hot Day")` reaches the ≥0.9 band.
  - A shared single stop word scores 0.
- The date bonus is +3 when the extracted local date equals the stash-box date, and +1 when only the year-month matches (stash-box `YYYY-MM`, or the day differs by 1 for time zones). When the local scene has its own `date` set, that is used before the filename.
- The existing `TestScoreScene` and `TestTitleSimilarity` expectations keep passing. When one encodes the old raw-filename behaviour on purpose, update it, and say which in the commit.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_scoring.py`, which fails.

Change: implement the above. `format_results` passes the cleaned title, the studio and performer names, and the date to `score_scene`, and each result gets `matches_date`.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes.

Commit: `fix(sceneMatcher): score on a cleaned title with a date signal`

### Task 6: partial dates, one sort, dead code gone (#163 F13 backend)

Depends on Task 5.

Files: modify `plugins/sceneMatcher/scene_matcher.py` (`result_sort_key` ~596-603, `find_matching_scenes` ~921-955 and the `find_matches` op, imports), `plugins/sceneMatcher/stashbox_api.py`; test `plugins/sceneMatcher/tests/test_unit.py` (extend `TestResultSorting`)

Test first:
- `result_sort_key` orders `2024-05-03` > `2024-05` > `2024` > `2023-12-31` > no date. A partial date sorts as the end of its period, so `2024` sorts after `2024-12-31`; say so in a comment.
- A malformed date (`"20x4"`) sorts with no date instead of raising.
- `test_null_dates_sort_last` asserts the full order.

Run: `cd plugins/sceneMatcher && python -m pytest -q tests/test_unit.py`, which fails.

Change:
- Implement the sort.
- Delete, after a grep shows no callers:
  - `query_performer_name`, `query_scenes_by_performer` (singular), `find_matching_scenes` and the `find_matches` op;
  - the unused `import urllib.error` and the redundant inner `import re`.

Run: `cd plugins/sceneMatcher && python -m pytest -q`, which passes, and `grep -n` shows no references to the removed names.

Commit: `fix(sceneMatcher): partial stash-box dates sort correctly; remove dead code`

## UI

### Task 7: test harness that loads the real JS, and CI for it

Files: create `plugins/sceneMatcher/tests/harness.js`, `plugins/sceneMatcher/tests/test_real_file.js`; modify `plugins/sceneMatcher/scene-matcher.js` (a `window.__SCENE_MATCHER_TEST__` export block at the end, exporting the functions later tasks test); modify `.github/workflows/test.yml` (see the context section)

Test first: `test_real_file.js` loads `scene-matcher.js` into a vm context with stubbed `window`, `document`, `fetch`, `MutationObserver`, `PluginApi`, timers and `location`. Model it on `plugins/tagManager/tests/harness.js`, which you can copy and adapt. It asserts:
- the file loads without throwing;
- the exports are reachable;
- the static "no undefined calls" check passes. Copy tagManager's `test_real_file.js` approach.

Run: `node plugins/sceneMatcher/tests/test_real_file.js`, which fails (no harness).

Change: add the harness, the export block and the CI loop.

Run: `for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done` from the worktree root, which passes, including tagManager's files.

Commit: `test(sceneMatcher): harness that loads the real UI file; CI runs it`

### Task 8: the Match button follows the selected endpoint (#161 F1, F2 UI, F12 UI)

Depends on Tasks 1, 7.

Files: modify `plugins/sceneMatcher/scene-matcher.js` (`sceneHasStashId` ~865-879, `addMatchButtons` ~906-944, `createMatchButton` ~884-901, `getSceneIdFromElement` ~844-860, the fetch helpers); test `plugins/sceneMatcher/tests/test_gate.js`

Test first:
- **Effective endpoint:** `effectiveEndpoint()` reads `select#scraper`'s value each time it's called, since React sets it without firing `change`.
  - `stashbox:<url>` gives `<url>`.
  - `scraper:<id>` gives null.
  - A missing select falls back to `configuration.ui.taggerConfig.selectedEndpoint`, then to the plugin setting.
- **Gate:** `gateScenes(ids, endpoint)` makes one GraphQL `findScenes(ids: [...])` query for the visible scene ids, selecting `id stash_ids { endpoint }`, and returns the ids with no stash_id for the endpoint, compared normalized. It is cached per (endpoint, id) until the row's `.stash-id-pill` set changes.
- **Rows:** `syncMatchButtons()` adds a button to rows whose scene isn't linked to the effective endpoint, and removes it from rows that are.
  - With a `scraper:` source, it removes every Match button.
  - Changing `select#scraper` re-gates.
  - A scene linked only to ThePornDB gets a button while the Tagger targets StashDB.
- **Row ids:** a row's scene id comes from its `a[href*="/scenes/"]` link, matched with `/\/scenes\/(\d+)/`. Drop the `dataset.sceneId` fallback.
- **Labels:** the button label and title name the box (`Match on <box name>`), not a hard-coded "StashDB".
- The UI sends `endpoint` to both ops.

Run: `node plugins/sceneMatcher/tests/test_gate.js`, which fails.

Change: implement the above. Keep `sync` idempotent, because the observer in Task 9 calls it often.

Run: all JS tests, which pass.

Commit: `fix(sceneMatcher): Match button follows the Tagger's selected endpoint`

### Task 9: one observer, request tokens, a client timeout (#162 F5, F6, F8 UI)

Depends on Task 8.

Files: modify `plugins/sceneMatcher/scene-matcher.js` (`waitForPage` ~1010-1040, `init` ~1045-1065, `waitForTaggerElements` ~985-1005, `handleMatchClick` ~787-839, `handleDeepSearchClick` ~746-782, `graphqlRequest` ~33-53, `renderResults`, `showError`); test `plugins/sceneMatcher/tests/test_lifecycle.js`

Test first:
- **Listeners:**
  - After init and 5 simulated navigations, exactly one `MutationObserver` is observing and no `popstate` listeners have been added by the plugin.
  - Navigation is detected with `PluginApi.Event.addEventListener("stash:location", ...)` when available, otherwise the single observer.
  - `syncMatchButtons` is debounced (150ms), so 20 mutations in a burst cause one sync.
  - Leaving the Tagger stops syncing.
- **Request tokens:** clicking Match on scene A, then on scene B before A's phase 1 or phase 2 returns, renders only B's results. A's late responses are dropped, and "Select" uses B's row.
- **Loading state:** a Match click while a previous search is loading after its modal closed starts the new search instead of being ignored.
- **Timeout:** `graphqlRequest` aborts after 120s with `AbortController` and shows a timeout error with Retry.
- **Rendering:**
  - `warnings` render as a notice above the results;
  - `partial` renders the results plus "Some pages failed";
  - `auth_error` renders the API-key hint;
  - `error` renders the message with Retry, not "No matching scenes found".
  - `truncated` shows "Showing the top N of M".

Run: `node plugins/sceneMatcher/tests/test_lifecycle.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(sceneMatcher): one observer, no stale results, visible errors and timeouts`

### Task 10: a handoff to the Tagger that works (#162 F10, F11)

Depends on Task 9.

Files: modify `plugins/sceneMatcher/scene-matcher.js` (`handleSelectMatch` ~588-652, the selectors listed under F11 in the code map); test `plugins/sceneMatcher/tests/test_handoff.js`

Test first:
- `handleSelectMatch(sceneId, stashId)` re-queries the row by `a[href*="/scenes/<sceneId>"]` → `.closest(".search-item")`; the row captured at click time may be gone.
- It finds `input.text-input` in the row and sets the value through the native value setter, then dispatches `input`.
- It clicks the first `.input-group-append button` that isn't `.sm-match-button`, when that button isn't disabled.
- If the button is disabled, or there isn't one, it dispatches `keypress` with key `Enter`, `keyCode` 13, `charCode` 13 and `which` 13 on the input.
- A missing row or input shows an error toast instead of failing silently.
- F11 selectors are gone or fixed:
  - no `.tagger-scene`, `[class*="StashIDPill"]` or `input[type="text"]`;
  - no `a[href*='stashdb.org/scenes']` gate;
  - no `.tagger-container`-only page detection (use the `/scenes` route plus `select#scraper` or `.search-item`).
  - The hard-coded "StashDB" strings name the resolved box.

Run: `node plugins/sceneMatcher/tests/test_handoff.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(sceneMatcher): hand the chosen match to the Tagger reliably`

### Task 11: partial dates, sort parity and safe new windows in the UI (#163 F13 UI)

Depends on Tasks 6, 8.

Files: modify `plugins/sceneMatcher/scene-matcher.js` (`formatDate` ~166-182, `mergeResults` ~701-741, `createSceneCard` ~464-583); test `plugins/sceneMatcher/tests/test_cards.js`

Test first:
- `formatDate` shows `2024`, `May 2024` and `May 3, 2024` for the three stash-box date shapes, and never "Invalid Date".
- `mergeResults` orders phase 1 and phase 2 results with the same key as Python's `result_sort_key`: in-Stash last, score descending, then partial-aware date. Use the same fixture list as the Python test, and assert the same order.
- The card's stash-box link and `window.open` use `noopener,noreferrer`.
- The site base comes from the response's `stashdb_url`, not a hard-coded `https://stashdb.org`.

Run: `node plugins/sceneMatcher/tests/test_cards.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(sceneMatcher): partial dates and matching sort order in the UI`

## Release

### Task 12: settings declared, docs, version 1.2.0

Depends on Tasks 1-11.

Files: modify `plugins/sceneMatcher/sceneMatcher.yml` (`version: 1.2.0`, description, every honored setting), `plugins/sceneMatcher/README.md`, `CHANGELOG.md` (root, the Scene Matcher section), and the User-Agent version if Task 1 used a constant

Check:
- **yml:**
  - declares every setting the code reads: `stashBoxEndpoint`, `maxResults`, `stashbox_request_delay`, `stashbox_max_retries`, `stashbox_initial_retry_delay`, `stashbox_max_retry_delay`, `stashbox_retry_backoff_multiplier`, `stashbox_request_timeout`, `stashbox_rate_limit_pause`, `stashbox_per_page`, `stashbox_max_pages_performer` and `stashbox_max_pages_studio`;
  - each has a true description and the code default. Drop any the code no longer reads.
  - The description says it searches the box selected in the Tagger.
  - Use an inline script to diff the yml keys against the settings the code reads.
- **README:**
  - The two phases: a text search from the title and linked names, then a deep search by linked performers and studio, which the user triggers.
  - Which endpoint is searched (the Tagger's source, then the setting, then the first box) and when the button shows.
  - Scoring (title, studio, performers, duration, date).
  - Truncation and `maxResults`.
  - Errors, and a settings table.
  - A v1.2.0 changelog entry.
- **Root CHANGELOG:** a `### 1.2.0` entry.
- No em-dash characters.

Run: `python .github/scripts/lint_manifests.py`, all Python and JS tests, and a relative-link check over the READMEs (inline script, no network). All pass.

Commit: `docs(sceneMatcher): 1.2.0 docs and changelog`

### Task 13: deploy to stash-test and exercise

Depends on Task 12.

Files: none changed.

Check:
- Deploy with rsync, excluding `tests`, `__pycache__`, `.pytest_cache`, `conftest.py` and `test_*.py`, then `reloadPlugins`. The key is `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it.
- Through `runPluginOperation` on a stash-test scene without a StashDB id: `find_matches_fast` then `find_matches_thorough` against StashDB. These are read-only stash-box queries. Check the timings, and that `warnings` is empty or explained.
- Browser pass on the Tagger (the user logs in if needed):
  - the button appears for a scene linked only to another box;
  - switching the source re-gates it;
  - a scraper source hides the buttons;
  - Match, then Select fills the row's search box and runs the Tagger search;
  - after 5 page changes, one observer is running (check with a console counter from the test hook).
- Change nothing in stash-test's library or config.

Commit: none.

## Code map

This was mapped on 2026-09-28 against main 2b306a8; line numbers are approximate.

- **`scene_matcher.py` (1056 lines)**
  - **Setup:**
    - `SSL_CONTEXT` (unverified, local Stash only) is at 20-23, and `MIN_COMBINED_RESULTS_THRESHOLD=10` at 27.
    - The `import urllib.error` at 13 is unused.
  - **Stash access:**
    - `get_stash_connection` 38-63 uses the session cookie and maps `0.0.0.0` to localhost.
    - `get_input_data` 66-71; `stash_graphql` 74-100 has a 30s timeout and returns `data` even when there are errors.
    - `get_stashbox_config` 103-121; `get_local_scene` 124-162.
  - **`get_local_scene_stash_ids` 165-213:** pages all scenes 100 at a time with no filter and compares endpoints exactly at 203.
  - **Search terms:**
    - `STRIP_PATTERNS`/`clean_title` 221-265: separators become spaces at 256 before the date pattern at 233 runs; the extension regex is at 248 and the release-group regex at 252.
    - `build_search_query` 268-282.
    - Wrappers 289-333: `query_stashdb_by_text` uses limit 25; combined and performers pass `max_pages=10`; studio (325-333) ignores `max_pages`.
  - **Result shaping and scoring:**
    - `format_scene` 340-386.
    - `normalize_title` 389-398, which keeps `_` and has a redundant `import re` at 394.
    - `tokenize` 401, `levenshtein_ratio` 408, `token_similarity` 440-485, `title_similarity` 488-519, `calculate_duration_score` 522-550.
    - `score_scene` 553-593: title ≥0.9 gives +10 and ≥0.5 gives +5; the studio earns +3 and each performer +2, both only when linked; the total is multiplied by `0.5+0.5*duration_score`.
    - `result_sort_key` 596-603.
  - **Scene context and results:**
    - `get_scene_context` 606-697: the endpoint pick is at 611-633, the "already has an ID" check at 641-643, and file info at 674-681.
    - `format_results` 700-731: `cache_hit` is unused, and the F3 crash is at 722-725.
  - **Search ops:**
    - `find_matches_fast` 734-808 (the cache compare is at 776 and `stashdb_url` is stripped at 798).
    - `find_matches_thorough` 811-918 (cache at 891, early `message` return at 829-842, fallback threshold at 864).
    - `find_matching_scenes` 921-955 is dead.
  - **`main` 962-1052:** reads settings 974-985; any exception becomes `{"error"}` at 1042-1044.
- **`stashbox_api.py` (656 lines)**
  - **Setup:** `create_ssl_context` 23-40, the verifying `SSL_CONTEXT` 44, `DEFAULT_CONFIG` 47-65, `RETRYABLE_STATUS_CODES` 68-74, `StashBoxAPIError` 77-83.
  - **`get_config` 86-117:** clamps ints to at least 1.
  - **`graphql_request_with_retry` 120-250:**
    - headers at 146-152 (no User-Agent);
    - GraphQL errors return `data` at 169-174;
    - a 429 sleeps 60s × 3 and ignores `Retry-After` (181-195);
    - backoff at 198-205; a certificate error raises at 218-224; the generic `except` at 242-244.
  - **`paginated_query` 253-322:** breaks on error without a flag at 289-298 and drops `total`.
  - `SCENE_FIELDS` 329-362.
  - **Dead:** `query_performer_name` 365-384 and `query_scenes_by_performer` 387-490.
  - **Live query functions:** `query_scenes_by_studio` 493-533, `search_scenes_by_text` 536-562 (swallows errors to `[]`), `query_scenes_combined` 565-611, `query_scenes_by_performers` 614-656.
- **`scene-matcher.js` (1069 lines, one IIFE, `init()` at 1068)**
  - **State and fetch helpers:**
    - State at 7-19, including the cache at 18-19.
    - `graphqlRequest` 33-53 has no timeout; `runPluginOperation` 58-91.
    - `findMatchesFast` 96-119 and `findMatchesThorough` 124-146 send and store the cache.
  - **Rendering:**
    - `formatDate` 166-182; `createModal` 196-261, with its Escape handler balanced.
    - "Search StashDB" is hard-coded in `createDeepSearchButton` 365-403 (at 377); "No matching scenes found on StashDB." in `renderResults` 408-459 (at 435).
    - `createSceneCard` 464-583: `window.open` without `noopener` at 579.
    - `handleSelectMatch` 588-652; `mergeResults` 701-741, with the JS sort at 719-738.
  - **Click handlers:** `handleDeepSearchClick` 746-782; `handleMatchClick` 787-839 (the `isLoading` guard at 788, the default URL at 807).
  - **Gate:**
    - `getSceneIdFromElement` 844-860; `sceneHasStashId` 865-879; `createMatchButton` 884-901.
    - `addMatchButtons` 906-944: selector `.search-item, .tagger-scene`; it only ever adds, and appends into `.input-group-append` at 934-937.
  - **Page lifecycle:**
    - `isTaggerPage` 949-979; `waitForTaggerElements` 985-1005 polls 20 × 250ms.
    - `waitForPage` 1010-1040 adds a new observer and a `popstate` listener on every call.
    - `init` 1045-1065 has a permanent URL observer.
- **F11 selectors to fix:**
  - `.tagger-scene` (908, 990), `[class*="StashIDPill"]` (867), `a[href*='stashdb.org/scenes']` (874), `input[type="text"]` (597);
  - `dataset.sceneId` (855), `.tagger-container` (969, 974);
  - hard-coded "StashDB" (377, 435, 437, 677, 888).
- **Settings read in code:**
  - `stashBoxEndpoint`, and `stashbox_` + `request_delay`, `max_retries`, `initial_retry_delay`, `max_retry_delay`, `retry_backoff_multiplier`, `request_timeout`, `rate_limit_pause`, `per_page`, `max_pages_studio` and `max_pages_performer` (effectively dead today).
  - Only the first three are declared in the yml.
- **Tests:** `tests/test_unit.py` has 55 tests, all testing real code. Nothing tests `clean_title`, `format_results`, `get_scene_context`, the ops, `main` or `stashbox_api`. There's no conftest and no JS tests.
