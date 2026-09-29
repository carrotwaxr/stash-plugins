# missingScenes 1.5.0 plan

Goal: ship missingScenes 1.5.0, closing #154-#160, #135 and #115.
Approach: first move state into Stash's config dir and delete the dead legacy path, so every later change touches one code path. Then make failures visible: typed Whisparr errors and a connection test, stash-box errors and partial results instead of "You have all available scenes!", tolerant ThePornDB parsing. Then the UI (a harness that loads the real JS), Whisparr limited to StashDB scenes, Trending sort, site links, and a fingerprint index that counts untagged or other-box scenes as owned.
Run with: /fluffer:plan-run

## Context every task needs

- Worktree: `/home/carrot/code/stash-plugins/.worktrees/missingscenes-1.5`, branch `fix/missingscenes-1.5.0`, based on `main` (2b306a8). Never touch the main checkout or other worktrees. **Never use `git stash`**, because the stack is shared with other sessions; compare with `git show HEAD:<path>` instead.
- Plugin dir: `plugins/missingScenes`. Python tests: `cd plugins/missingScenes && python -m pytest -q` (root `pytest.ini`; `conftest.py` points `CACHE_DIR` at `tmp_path`). Current state: 83 passed, 2 skipped.
- Tests are offline and use mocks; never contact Stash, stash-boxes or Whisparr. Write each test first and see it fail.
- Tickets: `gh issue view <n> --json body --jq .body` (#154-#160). Finding ids (F1-F16) are in the "missingScenes v1.4.0" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path. A code map with line numbers is at the end of this plan.
- **Conventions:**
  - Data functions return data or raise.
  - Responses carry an `error` field (plus `partial: true` and `auth_error` where relevant) that the UI renders.
  - Never `print()` outside the JSON reply; use `log`.
  - Runtime state goes in `<server_connection.Dir>/plugin_data/missingScenes/`.
  - Standard library only.
  - The stash-box client (`stashbox_api.py`) is a per-plugin copy. Changes here don't need to be mirrored into sceneMatcher.
- Whisparr is v3. Its `stashId` field and `stash:<id>` lookup term are StashDB-specific.
- **Settings:** `missingScenes.yml` (STRING, NUMBER or BOOLEAN), with defaults in code. The manifest lint runs from the worktree root: `python .github/scripts/lint_manifests.py`.
- stash-test is shared with other sessions. Only Task 15 deploys, and only plugin files.

## Foundation

### Task 1: state in Stash's config dir; a sturdier local stash_id cache (F7)

Files: create `plugins/missingScenes/plugin_data.py`; modify `plugins/missingScenes/missing_scenes.py` (cache functions l.54-128 and `get_or_build_cache` l.1026-1109, `main`); test `plugins/missingScenes/tests/test_cache.py` (create `plugins/missingScenes/tests/` with an empty `__init__.py`; pytest collects `tests/`)

Test first:
- `data_dir({"Dir": tmp})` gives `tmp/plugin_data/missingScenes` and creates it. With no `Dir`, or when it can't be created, it falls back to `<plugin dir>/data`, then to a temp dir, and never raises.
- The cache file lives under the data dir.
- A TTL-expired cache is rebuilt.
- A corrupt cache file triggers a rebuild, not a crash.
- Two writers use unique temp files, and the file is replaced atomically.
- A new endpoint gets a new cache key.
- The build uses `per_page=1000`.
- `invalidate_cache(endpoint)` removes the file.
- `stash_graphql` returning None during a build raises a clear error instead of an AttributeError.
- md5 is called with `usedforsecurity=False` where supported.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_cache.py`, which fails.

Change:
- Add `plugin_data.py`, modelled on tagManager's: `data_dir`, `configure` and `current_dir`, never raising. `main()` calls `plugin_data.configure(server_connection)` first.
- `CACHE_DIR` becomes `plugin_data.current_dir()`; keep `conftest.py`'s monkeypatch working.
- Temp files are unique (`tempfile.mkstemp` in the same dir), then `os.replace`.
- Add `invalidate_cache(endpoint)` and a `refresh_index` operation that invalidates and rebuilds.
- Build pages are 1000.
- Remove the duplicate `_get_cache_info`.
- Add `plugins/missingScenes/data/` to the root `.gitignore`.
- Update `.github/workflows/test.yml` only if the new `tests/` dir needs it (pytest already runs from the plugin dir).

Run: `cd plugins/missingScenes && python -m pytest -q`, which passes.

Commit: `fix(missingScenes): keep the scene index in Stash's config dir; sturdier cache`

### Task 2: remove the dead legacy path and the tests that test nothing (F16)

Files: modify `plugins/missingScenes/missing_scenes.py`, `plugins/missingScenes/stashbox_api.py`, `plugins/missingScenes/missing-scenes.js` (legacy `updateStats` branch), `plugins/missingScenes/missingScenes.yml` (settings only the legacy path read), `plugins/missingScenes/test_missing_scenes.py`

Delete (from the code map, after verifying zero callers with grep):
- `find_missing_scenes`, and the `main` branch that calls it when neither `page_size` nor `cursor` is sent. Unpaginated calls now use the paginated path with the default page size.
- `query_stashdb_performer_scenes`/`_studio_`/`_tag_scenes` and `_tpdb_fetch_all_pages`.
- `stashbox_api.query_scenes_by_performer`, `query_scenes_by_studio`, `query_scenes_by_tag`, `paginated_query`, `query_performer_name`, `search_scenes_by_text`, `query_scenes_combined` and `query_scenes_by_performers`.
- `whisparr_get_existing_stash_ids`, the unused `studio_name` parameter of `add_to_whisparr`, and the unused `PLUGIN_ID` in missing-scenes.js and browse.js.
- The yml settings `stashbox_max_pages_performer` and `stashbox_max_pages_studio`. Keep `stashbox_request_delay`: Task 6 wires it in.
- The tautological test classes: `TestWhisparrPayload`, `TestStashIdMatching`, `TestErrorHandling`, `TestSceneSorting`, `TestGraphQLQueryConstruction`, `TestEndpointSelection`, `TestEndpointMatching`. They re-implement logic inline and call no plugin code, and later tasks add real tests for these behaviours. Also delete the manual `run_all_tests` runner.

Check: `grep -n` shows no remaining references to any removed name; `cd plugins/missingScenes && python -m pytest -q` passes (fewer tests, all real); `node --check` passes on the three JS files; `python .github/scripts/lint_manifests.py` passes.

Commit: `refactor(missingScenes): remove the dead legacy code path and tests that tested nothing`

## Whisparr

### Task 3: typed Whisparr errors, URL normalization, a lookup that checks the id (F1, F9, F10)

Files: modify `plugins/missingScenes/missing_scenes.py` (all `whisparr_*` helpers l.680-1019, `whisparr_delete_scene`, `whisparr_unmonitor_scene`); test `plugins/missingScenes/tests/test_whisparr.py`

Test first (patch `urllib.request.urlopen`):
- `normalize_whisparr_url` table:
  - strips whitespace;
  - defaults the scheme to `http://`;
  - drops a trailing `/`, `/api`, `/api/v3` (and anything after it), and `#...`;
  - keeps a URL Base path such as `http://h:6969/whisparr`.
- `WhisparrError` carries `status`, `url` (with no API key in it) and a body snippet. A 400 with a JSON body exposes Whisparr's validation messages.
- The helpers raise instead of returning None/`[]`: get-by-stashId, lookup, trigger-search, get-all, queue.
- `whisparr_get_scene_by_stash_id` returns only an entry whose `stashId` equals the requested id. Given a list whose first item has a different id, it returns None.
- The status map tolerates queue items with `status`, `errorMessage` or `size` set to null.
- `whisparr_unmonitor_scene` raises a clear error when the GET finds nothing.
- The status map is cached for 60s in the data dir, and a fetch error is returned as `whisparr_error` rather than an empty map.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_whisparr.py`, which fails.

Change: implement the above. Callers catch `WhisparrError` and put `{error, whisparr_error}` into their responses. Nothing masks a failure as "not found".

Run: `cd plugins/missingScenes && python -m pytest -q`, which passes.

Commit: `fix(missingScenes): typed Whisparr errors and a lookup that checks the scene id`

### Task 4: Whisparr connection test (#154)

Files: modify `plugins/missingScenes/missing_scenes.py` (a `test_whisparr` operation and a `test_whisparr` task mode), `plugins/missingScenes/missingScenes.yml` (a task "Test Whisparr Connection"); test `plugins/missingScenes/tests/test_whisparr.py`

Test first: `test_whisparr_connection(settings)` calls `system/status`, `rootfolder` and `qualityprofile`, and returns `{ok, app, version, root_folders, quality_profiles, problems[]}`. The problems it must name, one test each:
- the app isn't Whisparr (e.g. Radarr or Sonarr, or Stash answering on that port, which returns HTML): "wrong service";
- a v2 version: "Whisparr v3 is required";
- 404 on `/api/v3`: "check URL Base";
- 401: "API key rejected";
- a connection refused or timeout: "can't reach";
- the configured root folder isn't among Whisparr's root folders;
- the configured quality profile id doesn't exist (list the valid ones).

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_whisparr.py`, which fails.

Change: implement the function, the operation and the task (the task logs the result). Use the normalized URL.

Run: `cd plugins/missingScenes && python -m pytest -q` and the manifest lint. Both pass.

Commit: `feat(missingScenes): Whisparr connection test`

### Task 5: Whisparr only for StashDB scenes; safe add and auto-cleanup (#156)

Files: modify `plugins/missingScenes/missing_scenes.py` (`add_to_whisparr` l.2122-2206, `handle_scene_update_hook` l.2268-2368, `task_cleanup_whisparr` l.2436-2495); test `plugins/missingScenes/tests/test_whisparr_flows.py`

Test first:
- `add_to_whisparr` with a non-StashDB `endpoint` returns an error explaining Whisparr matches StashDB IDs only, and makes no Whisparr call.
- When the StashDB scene is found and added, the response reports whether the search was triggered, and a failed trigger is reported.
- A lookup error returns the Whisparr error text, not "not found".
- The hook uses the StashDB endpoint (the configured box whose endpoint is StashDB), not the first configured box.
- The hook only acts when `hookContext.inputFields` includes `stash_ids`.
- The hook skips an entry that's in Whisparr's download queue, with a log.
- The hook never deletes when the looked-up entry's `stashId` doesn't match.
- `unmonitorOnly` is honoured.
- The hook invalidates the local stash_id cache for StashDB.
- `task_cleanup_whisparr` builds its local set from the cache (the `stash_id_endpoint` filter), not a full library scan. It skips queued items and returns `success: false` with errors when Whisparr can't be read.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_whisparr_flows.py`, which fails.

Change: implement the above. The `add_to_whisparr` operation takes an `endpoint` arg.

Run: `cd plugins/missingScenes && python -m pytest -q`, which passes.

Commit: `fix(missingScenes): Whisparr only for StashDB scenes; safe auto-cleanup`

## Stash-box and ThePornDB results

### Task 6 (hard): failures and truncation shown, not "all found" (#155: F2, F8, F12, F13)

Files: modify `plugins/missingScenes/missing_scenes.py` (`fetch_until_full` l.1258-1397, `find_missing_scenes_paginated` l.1557-1806, `browse_stashdb` l.1871-2100), `plugins/missingScenes/stashbox_api.py` (`graphql_request_with_retry` l.120-250, `query_scenes_page`, `query_scenes_browse`); test `plugins/missingScenes/tests/test_fetch_errors.py`

Test first:
- A failed first page returns `{error, scenes: []}`, not a successful empty result. A failed later page returns the scenes so far with `partial: true`, an `error` and a cursor to retry from the failed page.
- Hitting the 50-page cap before the page fills returns a cursor (`has_more: true`).
- A fill that lands exactly at the last page doesn't emit a cursor when the stash-box reports no more pages.
- `request_delay` (setting `stashbox_request_delay`, default 0.5s) sleeps between pages. Patch `time.sleep` and assert the calls.
- A 429 with `Retry-After: 5` waits 5s. A `Retry-After` over the time budget (60s total per request) returns partial with a rate-limit error instead of sleeping.
- An auth error (401/403) returns `auth_error: true` with a message naming the box.
- `queryScenes: null` in a GraphQL response is an error, not an AttributeError.
- The cursor is validated: `entity_stash_id` must match, and page and offset must be ints. A bad cursor gives a clear error.
- `missing_count_estimate` is clamped to 0 or more, and is omitted when the first page failed.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_fetch_errors.py`, which fails.

Change:
- `stashbox_api` raises `StashBoxAPIError` with `status_code` and an `is_auth_error` property, and reads `Retry-After`.
- `query_scenes_page`/`query_scenes_browse` raise instead of returning None.
- The two loops catch the error and return partial or error responses as specified.

Run: `cd plugins/missingScenes && python -m pytest -q`, which passes.

Commit: `fix(missingScenes): show stash-box failures and truncation instead of "all found"`

### Task 7: tolerate ThePornDB response variants (#157: F5, F14)

Files: modify `plugins/missingScenes/theporndb_api.py` (`transform_scene` l.147-241, `_fetch_scenes` l.431-467, `query_scenes_browse` l.317-362), `plugins/missingScenes/missing_scenes.py` (`format_scene` l.1809-1868); test `plugins/missingScenes/tests/test_theporndb.py`

Test first, shape-fuzz tests, each producing a valid scene or a skipped scene with a warning, never an exception:
- string `posters`, and a list of strings;
- a poster with `width: null`;
- `performers: null`, and a string performer;
- a string `parent`, a string `site`, string tags;
- `directors` as a list of objects (the director becomes a name string);
- `data: null`, `meta: null`, a non-dict response.

Also: one bad scene in a page skips only that scene. `format_scene` handles `width: null` and `performer: null`. Browse with several favorite performers or studios uses all of them where the API supports arrays; research the TPDB API for `performers[]`/`sites[]` array parameters and record the finding in the commit. Otherwise browse iterates favorites in a stable order and merges, and the response says `favorites_limited: true`. TPDB browse reports `excluded_tags_applied` truthfully.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_theporndb.py`, which fails.

Change: implement `isinstance` coercion per field, a per-scene `try/except` in `_fetch_scenes`, `(w or 0)` guards, and the browse behaviour.

Run: `cd plugins/missingScenes && python -m pytest -q`, which passes.

Commit: `fix(missingScenes): tolerate ThePornDB response variants`

## UI

### Task 8: test harness that loads the real JS, and CI for it

Files: create `plugins/missingScenes/tests/harness.js`, `plugins/missingScenes/tests/test_real_files.js`; modify `.github/workflows/test.yml` (the JS job loops `plugins/*/tests/test_*.js` instead of only tagManager); modify the three JS files only to add a test hook (`window.__MISSING_SCENES_TEST__` exports, as in tagManager)

Test first: `test_real_files.js` loads `missing-scenes-core.js`, `missing-scenes.js` and `missing-scenes-browse.js` into one vm context with stubbed `window`, `document`, `fetch`, `PluginApi` and timers, modelled on `plugins/tagManager/tests/harness.js`, which you can copy and adapt. It asserts:
- all three load without throwing;
- the exported functions are reachable;
- a static "no undefined calls" check passes (copy tagManager's `test_real_file.js` approach).

Run: `node plugins/missingScenes/tests/test_real_files.js`, which fails (no harness).

Change: add the harness and hooks, and update the CI loop so it runs every `plugins/*/tests/test_*.js`. The loop must still fail on any non-zero exit.

Run: `for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done` from the worktree root, which passes, including tagManager's 21 files.

Commit: `test(missingScenes): harness that loads the real UI files; CI runs every plugin's JS tests`

### Task 9: UI shows errors, partial results and retry; no stale results (#155 UI)

Depends on Tasks 6, 8.

Files: modify `plugins/missingScenes/missing-scenes.js` (`performSearch` l.662-718, `updateStats` l.340-410, Load More l.471-484, `showError`), `plugins/missingScenes/missing-scenes-browse.js` (`performSearch` l.254-306, render l.100-160); test `plugins/missingScenes/tests/test_ui_states.js`

Test first, through the harness:
- A response with `error` and no scenes renders the error with a Retry button, not "You have all available scenes!". Browse behaves the same.
- A `partial: true` response renders the scenes plus a warning and a "Retry from here" that resends the returned cursor.
- `auth_error` renders the API-key hint.
- A response from a superseded request (a sort, filter or endpoint change, or the modal closing) is ignored. Use a request token.
- Browse controls stay usable during a load, and a change made during a load starts a new request that supersedes the old one.
- The estimate display never shows a negative number. Browse stats say "missing" only for the missing estimate, not the stash-box total.
- The final Load More isn't shown when `has_more` is false.

Run: `node plugins/missingScenes/tests/test_ui_states.js`, which fails.

Change: implement a request token per view, error, partial and retry rendering, and correct estimate and stats text.

Run: every `plugins/*/tests/test_*.js`, which passes.

Commit: `fix(missingScenes): show errors and partial results; ignore stale responses`

### Task 10: Whisparr in the UI: StashDB only, clear errors, connection banner (#154, #156 UI)

Depends on Tasks 3, 4, 5, 8.

Files: modify `plugins/missingScenes/missing-scenes-core.js` (`createSceneCard` l.199-363, `handleAddToWhisparr` l.145-188, `addToWhisparr` l.129-135), `plugins/missingScenes/missing-scenes.js`, `plugins/missingScenes/missing-scenes-browse.js` (pass `onWhisparrAdd`); test `plugins/missingScenes/tests/test_whisparr_ui.js`

Test first:
- `addToWhisparr` sends `endpoint`.
- On a non-StashDB endpoint, the card shows no Add button and shows a "Whisparr needs StashDB" hint.
- A failed add shows Whisparr's error message in the status line, on both the modal and browse.
- A success with `search_triggered: false` says the scene was added without a search.
- `whisparr_error` from a status-map fetch shows a banner ("Whisparr status unavailable: <error>. Run Test Whisparr Connection.") instead of silently showing Add on every card.

Run: `node plugins/missingScenes/tests/test_whisparr_ui.js`, which fails.

Change: implement the above.

Run: every `plugins/*/tests/test_*.js`, which passes.

Commit: `fix(missingScenes): Whisparr actions only for StashDB, with clear errors`

### Task 11: Trending sort and site links on cards (#159)

Depends on Task 8.

Files: modify `plugins/missingScenes/stashbox_api.py` (`query_scenes_page` sort set l.737-740), `plugins/missingScenes/missing_scenes.py` (`format_scene`: keep all `urls` with site names), `plugins/missingScenes/missing-scenes.js` (sort select l.124-136), `plugins/missingScenes/missing-scenes-core.js` (`createSceneCard`); tests `plugins/missingScenes/tests/test_sorting.py`, `plugins/missingScenes/tests/test_cards.js`

Test first:
- `query_scenes_page` accepts `TRENDING` and sends it.
- `format_scene` returns `urls: [{url, site}]`, where `site` is the stash-box site name, or the host when missing.
- The modal sort select includes Trending.
- The direction labels read "Descending"/"Ascending" for Title. Keep "Newest/Oldest First" for date sorts.
- A card with urls renders one link per distinct site, labelled with the site name, opening in a new tab with `rel="noopener noreferrer"`. Card clicks on a link don't also open the stash-box page.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_sorting.py` and `node plugins/missingScenes/tests/test_cards.js`, which fail.

Change: implement. Check that the stash-box accepts TRENDING with a performer or studio filter by reading the stash-box schema: `SceneSortEnum` includes `TRENDING`; the schema is in `/home/carrot/code/stash-box/graphql/schema/` if present, else note it. Map it for ThePornDB, which already maps `trending`.

Run: both test commands and all JS tests, which pass.

Commit: `feat(missingScenes): Trending sort and site links on scene cards`

## Ownership by fingerprint

### Task 12 (hard): count untagged and other-box scenes as owned, by fingerprint (#160)

Depends on Tasks 1, 6.

Design, settled here because #160 left it open:
- The owned set for endpoint E becomes the union of two sets:
  - the local scenes' E stash_ids (today);
  - E scene ids matched by fingerprint for local scenes that have no stash_id for E, whether untagged or tagged on another box.
- A fingerprint index per endpoint is stored as a SQLite file (standard library) in the data dir. It holds, per local scene, the updated_at seen and the matched E scene ids.
- A task, "Build Fingerprint Index", and an operation for the UI:
  - query local scenes with `stash_id_endpoint {endpoint: E, modifier: IS_NULL}` (verify the modifier works per endpoint; if not, fetch all and filter);
  - take `files { fingerprints { type value } duration }`;
  - skip scenes whose updated_at hasn't changed since the last index run;
  - batch 40 scenes per `findScenesBySceneFingerprints` call (phash, oshash, md5; model the query on `plugins/tagManager/stashdb_api.py find_scenes_by_fingerprints`), with the same rate limiting and `Retry-After` handling as Task 6;
  - store the results.
- The missing and browse views union the index into `local_ids`, and report `owned_by_fingerprint: N` in the response.
- A setting `countFingerprintMatchesAsOwned` (BOOLEAN, default true) lets users turn it off. The index is only used if it has been built.

Files: create `plugins/missingScenes/fingerprint_index.py`; modify `plugins/missingScenes/stashbox_api.py` (`find_scenes_by_fingerprints`), `plugins/missingScenes/missing_scenes.py` (the union in both views, a `build_fingerprint_index` task and operation, the setting), `plugins/missingScenes/missingScenes.yml` (the task and the setting), `plugins/missingScenes/missing-scenes.js` + `missing-scenes-browse.js` (show "N counted as owned by fingerprint" and a "Build fingerprint index" button when no index exists); tests `plugins/missingScenes/tests/test_fingerprint_index.py`, `plugins/missingScenes/tests/test_ui_states.js` (extend)

Test first:
- the index build batches 40 and stores matches;
- an unchanged scene isn't re-queried on a second run;
- a changed scene is;
- a stash-box error mid-build keeps what was stored and reports partial;
- a scene matched by fingerprint is excluded from the missing list, and `owned_by_fingerprint` counts it;
- the setting off means no union;
- no index means no union, and the response says `fingerprint_index: false`;
- the UI renders the count, and the build button when there's no index.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_fingerprint_index.py` and the JS test, which fail.

Change: implement.

Run: all Python and JS tests and the manifest lint, which pass.

Commit: `feat(missingScenes): count scenes matched by fingerprint as owned`

## Release

### Task 13: scan task validation; settings that match their docs (F15)

Files: modify `plugins/missingScenes/missing_scenes.py` (`task_scan_for_new_scenes` l.2371-2433, settings reads), `plugins/missingScenes/missingScenes.yml`; test `plugins/missingScenes/tests/test_tasks.py`

Test first:
- The scan task rejects a path that isn't inside any Stash library (`configuration.general.stashes[].path`), with a clear error.
- It accepts `;`-separated multiple paths.
- It invalidates the StashDB cache after starting the scan.
- `whisparrSearchOnAdd` defaults to OFF, as the code does. Fix the yml text ("Off by default") rather than changing behaviour, since turning it on would start downloads for users who never set it.
- `whisparrUrl` and `whisparrRootFolder` are `.strip()`ed.
- The redundant `import json as json_module` is gone.

Run: `cd plugins/missingScenes && python -m pytest -q tests/test_tasks.py`, which fails.

Change: implement.

Run: all tests and the manifest lint, which pass.

Commit: `fix(missingScenes): validate the scan path; settings match their descriptions`

### Task 14: docs, version 1.5.0, changelog

Depends on Tasks 1-13.

Files: modify `plugins/missingScenes/missingScenes.yml` (`version: 1.5.0`, descriptions), `plugins/missingScenes/README.md`, `CHANGELOG.md` (root, the Missing Scenes section)

Check (docs):
- **README:**
  - Whisparr is optional and v3 only. Say which Docker image and tag, the URL forms (host:port, URL Base), the connection test and troubleshooting (#135, #115).
  - Whisparr works only with StashDB scenes.
  - Correct the caching and page-limit claims: the index lives in Stash's config dir, and there's a Refresh index button and a TTL.
  - The Browse page, ThePornDB support, Trending, site links, the fingerprint index and its setting.
  - A settings table covering every yml key.
  - A v1.5.0 changelog entry.
- **Root CHANGELOG:** a `### 1.5.0` entry.
- No em-dash characters.

Run: the manifest lint, all Python and JS tests, and a relative-link check over the READMEs (inline script, no network). All pass.

Commit: `docs(missingScenes): 1.5.0 docs and changelog`

### Task 15: deploy to stash-test and exercise; read-only Whisparr check

Depends on Task 14.

Files: none changed.

Check:
- Deploy with rsync, excluding `tests`, `__pycache__`, `data`, `.env`, `test_*.py` and `conftest.py`, then `reloadPlugins`. The key is `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it.
- Through `runPluginOperation`:
  - `find_missing` for a StashDB-linked performer, and for a ThePornDB one;
  - `browse_stashdb`, then `refresh_index`;
  - `build_fingerprint_index`, with its timing and count;
  - `test_whisparr`, pointed only at the user's Whisparr (`WHISPARR_URL`/`WHISPARR_API_KEY` in `/home/carrot/code/.env`). It is read-only: `system/status`, `rootfolder` and `qualityprofile` are GETs. Set the stash-test plugin config's Whisparr fields temporarily and restore them afterwards. No adds, no cleanup.
- Browser pass on stash-test (the user logs in if needed):
  - the modal on a performer: errors, partial results, sort to Trending, site links, the fingerprint count;
  - the browse page;
  - the Whisparr button hidden on a ThePornDB endpoint.
- Snapshot the missingScenes plugin config first and restore it after.

Commit: none.

## Code map

This was mapped on 2026-09-28 against main 3e7409a (unchanged in 2b306a8 for this plugin); line numbers are approximate.

- **`missing_scenes.py`**
  - **Connection and settings:**
    - `get_stash_connection` 142-171 uses the session cookie and `0.0.0.0`→`localhost`, and falls back to `localhost:9999`.
    - `get_input_data` 174-179 duplicates the stdin read.
    - `stash_graphql` 182-209 logs GraphQL errors and returns `data` (possibly None).
    - Settings are read in `main` 2513-2527; `configure_tls` is called at 2529.
  - **Dispatch (`main` 2502-2718):** the hook (2532), scan (2551), cleanup (2557), and the operations `find_missing` (2568; legacy branch when there's no `page_size`/`cursor`), `browse_stashdb` (2603), `add_to_whisparr` (2625), `get_endpoints` (2634), `get_all_endpoints` (2677).
  - **Cache:**
    - `CACHE_TTL_SECONDS` is at 54 and `CACHE_DIR` at 55.
    - `_get_cache_filepath` 57-60, `_read_cache_from_disk` 62-79, `_write_cache_to_disk` 81-96 (fixed `.tmp` name).
    - `_get_cache_info` appears twice (99-106 and 1112-1119).
    - `get_or_build_cache` 1026-1109 uses `per_page=100`, crashes with AttributeError when `stash_graphql` returns None, and computes `build_start` twice.
  - **Whisparr helpers 680-1019:**
    - `whisparr_request` re-raises; its error body is only logged.
    - `get_scene_by_stash_id` returns `result[0]` with no id check.
    - Most helpers swallow errors into None/[].
    - `get_status_map` breaks on null queue fields.
    - `whisparr_delete_scene` is at 2209-2235 and `whisparr_unmonitor_scene` at 2238-2261.
  - **`add_to_whisparr` 2122-2206:** gives "not found in TPDB/Whisparr lookup" at 2176-2182 when the lookup returns None, ignores the trigger-search result, and reads `whisparrSearchOnAdd` with a default of False at 2141.
  - **Hook `handle_scene_update_hook` 2268-2368:** picks the first configured box or the setting, does no inputFields check, no queue check, and uses `result[0]`.
  - **Tasks:** `task_scan_for_new_scenes` 2371-2433 (non-empty check only); `task_cleanup_whisparr` 2436-2495 (full scan via `get_local_scene_stash_ids` 323-373, always `success: true`).
  - **Paging constants:** 1194-1197 (`MAX_PAGES_PER_REQUEST=50`, `PAGE_SIZE_MAX=100`, `PAGE_SIZE_DEFAULT=50`).
  - **`fetch_until_full` 1258-1397:** `if not result: break` gives a silent dead end; the cursor is only emitted when the page is full.
  - **`find_missing_scenes_paginated` 1557-1806:** cursor validation at 1627-1630, the unclamped estimate at 1783-1785, the status map at 1751-1756.
  - **`browse_stashdb` 1871-2100:** the same loop; sort validation at 1895; excluded tags are applied server-side only.
  - **`format_scene` 1809-1868:** no `(w or 0)` at 1817; `perf.get("performer", {})` at 1826; keeps only `urls[0].url` at 1844-1846.
  - **`count_local_scenes_for_entity` 1122-1186:** uses `depth -1` for studio and tag.
- **`stashbox_api.py`**
  - `get_config` is at 86-117.
  - **`graphql_request_with_retry` 120-250:** a 429 sleeps 60s × 3, with no `Retry-After`; GraphQL errors are logged and `data` returned.
  - `SCENE_FIELDS` 329-366 includes `urls { url site { name } }`.
  - `query_scenes_page` ~700-817: sorts DATE/TITLE/CREATED_AT/UPDATED_AT, returns None on error.
  - `query_scenes_browse` ~820-924: includes TRENDING, returns None on error, and `data.get("queryScenes",{})` breaks on null.
- **`theporndb_api.py`**
  - `rest_request` 44-140 returns None on errors.
  - `transform_scene` 147-241 crashes on string posters, performers, parent, site or tags; `directors[0]` is a dict.
  - `query_scenes_browse` 317-362 uses only the first performer and studio, and ignores tags.
  - `_map_sort` 369-382; `_fetch_scenes` 431-467 has no per-scene try.
- **JS**
  - **`missing-scenes-core.js`:** `graphqlRequest` 22-42, `runPluginOperation` 47-78, `addToWhisparr` 129-135 (no endpoint), `handleAddToWhisparr` 145-188, `escapeHtml`, `createSceneCard` 199-363.
  - **`missing-scenes.js`:** the sort select at 124-136, `updateStats` 340-410, `renderResults` 415-460 ("You have all available scenes!" at 420-428), Load More 471-484, `handleAddAll` 497-556, `performSearch` 662-718 (no request token; appends stale results).
  - **`missing-scenes-browse.js`:** the sort select at 82-88 (with TRENDING), stats at 100-160, Whisparr cards with no `onWhisparrAdd` at 238-242, `performSearch` 254-306 (its `isLoading` guard drops changes; controls inert while loading).
- **Tests:** `test_missing_scenes.py`. The real test classes are `TestFormatScene`, `TestLocalStashIdCache`, `TestQueryScenesPage`, `TestCacheBuildingFunction`, `TestCountLocalScenesForEntity`, `TestGetFavoriteStashIds*`, `TestScenePassesFavoriteFilters`, `TestFetchUntilFull`, `TestCursorEncoding`, `TestBrowseStashdb` and `TestQueryScenesBrowse`. The tautological ones are listed in Task 2.
