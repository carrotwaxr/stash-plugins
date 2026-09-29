# tagManager 0.7.0 plan

Goal: ship tagManager 0.7.0, closing #140-#146, #129, #122, #124 and #126.
Approach: first make the Python backend standard-library only, with typed errors and state kept outside the plugin folder. Then rebuild scene sync on top of that: one sync per endpoint, a SQLite history, and ADD-mode writes. On the JS side, build a `vm` harness that loads the real `tag-manager.js`, then fix each finding with tests that run through it. Last, one short scale window on stash-test.
Run with: /fluffer:plan-run

## Context every task needs

- Worktree: `/home/carrot/code/stash-plugins/.worktrees/tagmanager-0.7`, branch `fix/tagmanager-0.7.0`. It is stacked on `docs/readme-refresh` (PR #171). Never touch the main checkout.
- Plugin dir: `plugins/tagManager`.
  - Python tests: `cd plugins/tagManager && python -m pytest -q` (the root `pytest.ini` applies).
  - JS tests: `node plugins/tagManager/tests/<file>.js`, run from the worktree root.
  - CI (`.github/workflows/test.yml`) runs both, plus `node --check` and `python .github/scripts/lint_manifests.py`.
- Tickets hold the full requirements: `gh issue view <n> --json body --jq .body`. Finding ids (F1-F20) are defined in the "tagManager v0.6.0" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path.
- Stash floor is v0.30. Anything that needs v0.31 must feature-detect it and fall back.
- **Conventions:**
  - Data functions return data or raise.
  - Responses to the UI carry an `error` field (plus `auth_error: true` for 401/403) that the UI renders.
  - Never `print()` to stdout from plugin code; stdout is the plugin's JSON reply. Use `log.LogWarning` etc.
- Runtime state lives in `<server_connection["Dir"]>/plugin_data/tagManager/` (Stash's config dir), never in the plugin dir.
- **stash-test is shared with other sessions.** Tasks deploy code there, but only Task 20 may touch its database, and Task 20 must restore it exactly.
- **Measured facts:**
  - StashDB has 2,934 tags. `per_page=1000` returns in about 1.3s, against 0.84s for 100.
  - Prod has 19,263 scenes linked to a stash-box: 16,975 StashDB, 2,269 ThePornDB, 84 JAVStash, 18 FansDB.
  - Local tags average 7.5 per StashDB scene, max 45. Prod has 269 local tags.
  - `TagsMergeInput.values` exists from Stash v0.31.0. `StashBox.max_requests_per_minute` exists from v0.29.0.

## Python backend

### Task 1: standard-library client for the local Stash

Files: create `plugins/tagManager/stash_client.py`; test `plugins/tagManager/tests/test_stash_client.py`

Test first: in `test_stash_client.py`, patch `stash_client.urllib.request.urlopen` with a fake that records each request (URL, headers, decoded JSON body) and returns queued JSON responses. Tests:
- `test_url_and_cookie_from_server_connection`: `{"Scheme":"http","Host":"0.0.0.0","Port":9999,"SessionCookie":{"Value":"abc"}}` posts to `http://localhost:9999/graphql` with `Cookie: session=abc`.
- `test_api_key_header_preferred`: `LocalStash(conn, api_key="k")` sends `ApiKey: k` and no Cookie.
- `test_graphql_errors_raise`: a `{"errors":[...]}` reply raises `StashError`.
- `test_find_all_tags_paginates`: count=2500 with per_page=1000 makes 3 requests and returns 2500 tags.
- `test_iter_scenes_respects_limit`: with `limit=150` and per_page=100, it makes 2 requests and yields exactly 150.
- `test_add_scene_tags_uses_add_mode`: the body contains `bulkSceneUpdate` with `{"ids":["7"],"tag_ids":{"ids":["1","2"],"mode":"ADD"}}`.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_stash_client.py`, which fails with `ModuleNotFoundError: stash_client`.

Change: create `stash_client.py` with:
- `class StashError(Exception)`.
- `class LocalStash`:
  - `__init__(self, server_connection, api_key=None, timeout=60)`: host `0.0.0.0` becomes `localhost`. The TLS context has no verification, because this is the same host (copy the comment from `tag_manager.stash_graphql`).
  - `call(self, query, variables=None) -> dict`: raises `StashError` on HTTP or GraphQL errors.
  - `configuration(self) -> dict`: the `configuration` object with `general { apiKey stashBoxes { endpoint api_key name max_requests_per_minute } } plugins`.
  - `find_all_tags(self, per_page=1000) -> list`: fields `id name aliases stash_ids { endpoint stash_id }`; paginate on `count`.
  - `iter_scenes_with_stash_id(self, endpoint, per_page=100, limit=None)`: a generator using `scene_filter: {stash_id_endpoint: {endpoint, modifier: NOT_NULL, stash_id: ""}}`, sorted by `updated_at` ASC. Fields `id tags { id } stash_ids { endpoint stash_id } files { fingerprints { type value } }`. Stop fetching once `limit` scenes have been yielded.
  - `add_scene_tags(self, scene_id, tag_ids) -> None`: the `bulkSceneUpdate` mutation in ADD mode.

In `tag_manager.py`, replace `stash_graphql` and `STASHBOX_CONFIG_QUERY` with `LocalStash(server_connection).configuration()` in `main()`. Update `tests/test_hardening.py` if it imports either.

Run: `cd plugins/tagManager && python -m pytest -q`, which now passes, including the 6 new tests.

Commit: `feat(tagManager): standard-library client for the local Stash`

### Task 2: typed stash-box errors, User-Agent, bigger pages (independent of Task 1)

Files: modify `plugins/tagManager/stashdb_api.py`; test `plugins/tagManager/tests/test_stashdb_api_errors.py`; update `plugins/tagManager/tests/test_stashdb_api.py` where it expects `[]` on errors

Test first: in `test_stashdb_api_errors.py`, patch `stashdb_api.urllib.request.urlopen`. Tests:
- `test_user_agent_sent`: the header is `stash-plugins-tagManager/<version from tagManager.yml>`.
- `test_401_is_auth_error`: an `HTTPError` 401 raises `StashDBAPIError` with `status_code == 401` and `is_auth_error`.
- `test_403_body_in_message`: a 403 with body `error code: 1010` puts `1010` in `str(err)`.
- `test_graphql_errors_without_data_raise`: `{"errors":[{"message":"x"}],"data":null}` raises.
- `test_unauthorized_graphql_error_is_auth`: `{"errors":[{"message":"Not authorized"}],"data":null}` gives `is_auth_error`.
- `test_query_all_tags_failed_page_raises`: page 1 is OK, page 2 is a 500 after retries; it raises and returns no partial list.
- `test_query_all_tags_falls_back_to_100`: `per_page=1000` fails with HTTP 422, the retry uses 100, and it succeeds.
- `test_search_raises_on_error`: `search_tags_by_name` raises on a 500 instead of returning `[]`.
- `test_find_scene_by_id_none_only_when_missing`: `{"data":{"findScene":null}}` gives None; a 500 raises.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_stashdb_api_errors.py`, which fails.

Change:
- `PLUGIN_VERSION` is read once from the `^version:` line of `tagManager.yml` next to the module, falling back to `"unknown"`.
- `USER_AGENT = f"stash-plugins-tagManager/{PLUGIN_VERSION}"` goes on every request.
- `StashDBAPIError(message, status_code=None, retryable=False, body=None)` gets `@property is_auth_error` (401/403, or a GraphQL error whose message contains "not authorized" or "unauthorized", case-insensitive). HTTP error messages include up to 200 characters of the body.
- `graphql_request` raises on GraphQL errors when `data` is null, and warns and returns data when both are present.
- `query_all_tags(url, api_key, per_page=1000)` raises on any failed page. On a 400/422 or GraphQL error for page 1 with `per_page > 100`, it restarts once at 100.
- `search_tags_by_name`, `find_scene_by_id` and `find_scenes_by_fingerprints` raise instead of returning empty. `find_scene_by_id` returns None only for a null `findScene`.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes.

Commit: `fix(tagManager): typed stash-box errors, a User-Agent, and larger tag pages`

### Task 3: runtime state in Stash's config dir; only complete tag fetches cached (independent of Tasks 1-2)

Files: create `plugins/tagManager/plugin_data.py`; modify `plugins/tagManager/tag_manager.py` (`get_cache_dir`, `get_cache_file_path`, `save_tags_to_cache`, `handle_fetch_all`, `main`); test `plugins/tagManager/tests/test_plugin_data.py`

Test first: tests:
- `test_data_dir_under_stash_config`: `data_dir({"Dir": tmp})` returns `tmp/plugin_data/tagManager` and creates it.
- `test_data_dir_fallback`: with no `Dir`, it returns `<plugin dir>/data`.
- `test_cache_filename_windows_safe`: `get_cache_file_path("https://stashdb.org:443/graphql")` has no `:`, `\` or `/` in its basename.
- `test_empty_fetch_not_cached`: `save_tags_to_cache(url, [])` returns False and writes nothing.
- `test_cache_write_atomic`: after a save, only `<name>.json` exists (no `.tmp`).
- `test_fetch_all_error_returns_error_and_does_not_cache`: patch `tag_manager.query_all_tags` to raise `StashDBAPIError("HTTP 403", status_code=403)`. `handle_fetch_all` returns `{"error": ..., "auth_error": True}` and no cache file exists.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_plugin_data.py`, which fails.

Change:
- `plugin_data.py` has `data_dir(server_connection=None) -> str` and `configure(server_connection)`, which stores the dir in a module global that `current_dir()` returns. Tests can call `configure({"Dir": tmp})`.
- `tag_manager.get_cache_dir()` returns `os.path.join(plugin_data.current_dir(), "tag_cache")`.
- Filenames are `tags_<re.sub(r"[^A-Za-z0-9._-]", "_", readable)>_<md5[:12]>.json`.
- `save_tags_to_cache` refuses empty lists and writes to `.tmp` then `os.replace`.
- `handle_fetch_all` catches `StashDBAPIError` and returns `{"error": str(e), "auth_error": e.is_auth_error}`.
- `main()` calls `plugin_data.configure(server_connection)` first.
- Add `plugins/tagManager/data/` to the root `.gitignore`.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes.

Commit: `fix(tagManager): keep caches in Stash's config dir; never cache a failed tag fetch`

### Task 4: blacklist parser: `/body/flags`, `,` and `;` separators, no stdout (independent)

Files: modify `plugins/tagManager/blacklist.py`; create `plugins/tagManager/tests/blacklist_cases.json`; test `plugins/tagManager/tests/test_blacklist.py`

Test first: create `blacklist_cases.json`, a list of `{"input": str, "matches": {tagName: bool}}`. It must cover:
- `"/Available$/"`: `"Now Available"` true, `"Available Now"` false.
- `"/^\\d+p$/"`: `"1080p"` true.
- `"/test/i"`: `"TEST"` true.
- `"Foo, Bar; Baz"`: all three true.
- `"/a{1,3}b/"`: `"aab"` true. The comma inside a regex does not split it.
- the legacy form with no closing slash, `"/^Avail"`: `"Available"` true.
- newline-separated mixes.

In `test_blacklist.py`, add:
- `test_cases_file`: loops the cases through `Blacklist(...).is_blacklisted`.
- `test_bad_regex_warns_not_stdout`: `Blacklist("/[/")` writes nothing to stdout (capture with `contextlib.redirect_stdout`) and calls `log.LogWarning` (patch `blacklist.log.LogWarning`).

Run: `cd plugins/tagManager && python -m pytest -q tests/test_blacklist.py`, which fails on the trailing-slash, flags and separator cases.

Change: `split_patterns(text) -> list[str]` in `blacklist.py`, with these rules:
- Skip whitespace and the separators `\n`, `,` and `;`.
- A token starting with `/` is a regex literal up to the next unescaped `/`, followed by `[a-z]*` flags. If there is no closing `/` before the end of the line, the regex body is the rest of the line (the legacy form).
- Any other token runs to the next separator and is trimmed.

Regexes always compile with `re.IGNORECASE`. Flags `m` and `s` map to `MULTILINE` and `DOTALL`, `i` is accepted, and other flags log a warning. A bad regex logs `log.LogWarning` (`import log`) and is skipped; remove the `print`.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes.

Commit: `fix(tagManager): blacklist accepts /regex/flags and comma or semicolon separators`

### Task 5: search reads settings and the tag cache on the server

Depends on Tasks 2, 3, 4.

Files: modify `plugins/tagManager/tag_manager.py` (`main`, `handle_search`, `get_settings_from_config`); test `plugins/tagManager/tests/test_tag_manager.py`

Test first: in `test_tag_manager.py`, add a class that runs `tag_manager.main()`. Patch `sys.stdin` with a JSON input, patch `tag_manager.LocalStash` (from Task 1) so `.configuration()` returns a config with one StashDB box and `plugins.tagManager.tagBlacklist = "Blonde"`, and patch `stashdb_api.graphql_request` to return tags "Blonde" and "Blonde Hair". Capture stdout. The stdin input's `server_connection.Dir` points at a temp dir, so Task 3's data dir lands there. Tests:
- `test_search_uses_server_blacklist`: the output matches exclude "Blonde". Also `args.settings.tagBlacklist` = `""` from the client does not bring it back.
- `test_search_ignores_client_stashdb_tags`: a client `stashdb_tags` arg is ignored. With fuzzy on, the tag cache file (written in the tmp data dir from Task 3) is used.
- `test_search_error_is_reported`: `graphql_request` raising a 403 `StashDBAPIError` gives `output.error` and `output.auth_error == True`.

Update the existing `handle_search` tests for the new signature.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_tag_manager.py`, which fails.

Change:
- `main()` builds `settings = get_settings_from_config({**DEFAULT_PLUGIN_SETTINGS, **plugin_config})` from the server config's `plugins.tagManager`, and ignores `args["settings"]`.
- `handle_search(tag_name, stashdb_url, stashdb_api_key, settings)` has no `stashdb_tags` parameter. When fuzzy is on it calls `load_cached_tags(stashdb_url)`; with no cache it skips fuzzy and returns `fuzzy_unavailable: True`.
- `main()` catches `StashDBAPIError` from search and returns `{"error": str(e), "auth_error": e.is_auth_error}`.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes.

Commit: `fix(tagManager): search applies the saved blacklist and reads its own tag cache`

### Task 6 (hard): scene sync per endpoint, ADD-mode writes, abort on auth failure, no stashapi

Depends on Tasks 1, 2, 4.

Files: modify `plugins/tagManager/stashdb_scene_sync.py` (`sync_scene_tags`, `process_scene`, `_process_pass_one`, `_process_pass_two`, remove `_fetch_all_local_tags` and `_fetch_scenes_with_stashdb_ids`); modify `plugins/tagManager/tag_manager.py` (`handle_sync_scene_tags`, the `sync_scene_tags` branch of `main`); test: rewrite `plugins/tagManager/tests/test_stashdb_scene_sync.py`, update `tests/test_scene_sync_blacklist.py`

Test first: add a `FakeStash` in the test file with in-memory tags and scenes, implementing `find_all_tags`, `iter_scenes_with_stash_id(endpoint, limit=None)` (it counts scenes yielded) and `add_scene_tags` (it records calls). Patch `stashdb_scene_sync.find_scenes_by_fingerprints` and `find_scene_by_id`. Tests:
- `test_each_scene_synced_against_its_own_endpoint`: a scene on StashDB and another on ThePornDB each get tags from their own box.
- `test_add_scene_tags_gets_only_new_ids`: the ADD payload holds only the missing tags.
- `test_auth_failure_aborts_with_error`: `find_scenes_by_fingerprints` raising a 403 `StashDBAPIError` stops the run. `stats.error` names the box, and no later batch is requested.
- `test_dry_run_stops_fetching_at_limit`: 500 scenes; the dry run yields at most 200 and writes nothing.
- `test_blacklist_from_settings`: `settings["tag_blacklist"]` filters.
- `test_zero_success_reports_error`: every scene fails, so `stats.error` is set.
- `test_rate_limit_from_box`: `max_requests_per_minute=30` gives a limiter interval of at least 2s (assert `limiter.min_interval`).

Run: `cd plugins/tagManager && python -m pytest -q tests/test_stashdb_scene_sync.py`, which fails.

Change:
- `sync_scene_tags(client, boxes, settings) -> SyncStats`:
  - `boxes` is a list of `{endpoint, api_key, name, max_requests_per_minute}` with an api_key.
  - `SyncStats` gains `error: Optional[str] = None` and `by_endpoint: dict`.
  - For each box: `RateLimiter(requests_per_second=min(2, mrpm / 60) if mrpm else 2)`, then scenes from `client.iter_scenes_with_stash_id(endpoint, limit=remaining dry-run budget)`, then passes one and two as today.
  - A `StashDBAPIError` with `is_auth_error` raises a private `SyncAborted` with a message such as `"<box name> rejected the request (HTTP 403). Check the API key in Settings → Metadata Providers."`. It is caught in `sync_scene_tags` and sets `stats.error`.
  - A non-auth error on a batch counts the batch's scenes as errors and continues.
  - Messages use the box `name`, not "StashDB".
- `process_scene` writes with `client.add_scene_tags(scene_id, sorted(new_tag_ids))`.
- `handle_sync_scene_tags(server_connection)`:
  - builds `LocalStash(server_connection, api_key=config.general.apiKey or None)` and the boxes from `config.general.stashBoxes`;
  - takes settings (`dry_run`, `tag_blacklist`) from `plugins.tagManager`;
  - returns `error` when `stats.error` is set, or when `processed == 0` and `errors > 0`.
- Remove every `stashapi` import.
- Update `tests/test_scene_sync_blacklist.py` to the new `sync_scene_tags(client, boxes, settings)` signature, using the same `FakeStash`.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes. `grep -rn stashapi plugins/tagManager --include=*.py` prints nothing outside `tests/test_integration_sync.py`.

Commit: `fix(tagManager): scene sync per endpoint with ADD-mode writes; abort on auth failures`

### Task 7 (hard): sync history so removed tags stay removed

Depends on Tasks 3, 6.

Files: create `plugins/tagManager/sync_history.py`; modify `plugins/tagManager/stashdb_scene_sync.py` (`process_scene`, `sync_scene_tags`), `plugins/tagManager/tag_manager.py` (a `reset_sync_history` mode), `plugins/tagManager/tagManager.yml` (a task); test `plugins/tagManager/tests/test_sync_history.py`

Test first:
- Unit tests of `SyncHistory`:
  - `test_get_missing_returns_none`
  - `test_record_then_get`
  - `test_remote_id_change_returns_none`
  - `test_persists_across_instances`
  - `test_reset_returns_count`
- Integration tests through `sync_scene_tags` with Task 6's `FakeStash`:
  - `test_first_sync_adds_all`
  - `test_removed_tag_not_readded`: after sync 1, remove a tag from the fake scene; sync 2 doesn't add it back.
  - `test_new_stashdb_tag_added`: StashDB gains a tag after sync 1, and sync 2 adds it.
  - `test_unmatched_tag_added_once_matched`: a StashDB tag with no local match in sync 1, then a local tag created, gets added in sync 2.
  - `test_dry_run_records_nothing`
  - `test_history_file_outside_plugin_dir`: the path is under `<Dir>/plugin_data/tagManager/`.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_sync_history.py`, which fails.

Change:
- `class SyncHistory(path)` over `sqlite3`: table `scene_tags(endpoint TEXT, scene_id TEXT, remote_id TEXT, tag_ids TEXT, synced_at INTEGER, PRIMARY KEY(endpoint, scene_id))`, WAL journal, and methods:
  - `get(endpoint, scene_id, remote_id) -> set | None`, which returns None when there's no row or the remote_id differs;
  - `record(endpoint, scene_id, remote_id, stashdb_tag_ids)`, storing space-separated sorted ids and committing every 200 records;
  - `reset() -> int`;
  - `close()`.
- In `process_scene`, `prior = history.get(...)`. When `prior` is not None, drop StashDB tags whose id is in `prior` before matching.
- After a live `updated` or `no_changes` result, record the StashDB ids that matched a local tag: those already present plus those added. Record unmatched and blacklisted tags nowhere. Never record in dry run.
- The history file is `plugin_data.current_dir()/sync_history.sqlite`.
- Add a manifest task, "Reset Scene Tag Sync History", with `defaultArgs: {mode: reset_sync_history}`; `main()` returns `{"success": True, "cleared": n}`.
- Size check (not a unit test): record 20,000 scenes × 30 tag ids in a temp file and print its size and time in the commit message body.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes, and `python .github/scripts/lint_manifests.py` from the worktree root, which prints ok.

Commit: `feat(tagManager): scene sync remembers StashDB tags so removed tags stay removed`

### Task 8: drop stashapp-tools

Depends on Task 6.

Files: modify `plugins/tagManager/requirements.txt`, `plugins/tagManager/README.md` (install steps and requirements), `README.md` (root, Python prerequisites table); test `plugins/tagManager/tests/test_no_stashapi.py`

Test first: `test_sync_runs_without_stashapi` sets `sys.modules["stashapi"] = None` and `sys.modules["stashapi.stashapp"] = None`, then imports `tag_manager` fresh (`importlib.reload`). It runs `main()` in `sync_scene_tags` mode with `LocalStash` and the stashdb_api calls patched, and asserts success.

Run: `cd plugins/tagManager && python -m pytest -q tests/test_no_stashapi.py`. This passes if Task 6 removed every import; if it fails, find the remaining import.

Change:
- `requirements.txt` keeps only `thefuzz` and `python-Levenshtein`, marked optional (fuzzy matching).
- The READMEs say Tag Manager needs no packages beyond those, and give the minimum as Python 3.9+.

Run: `cd plugins/tagManager && python -m pytest -q`, which passes.

Commit: `refactor(tagManager): drop the stashapp-tools dependency`

## JavaScript UI

All JS tasks edit `plugins/tagManager/tag-manager.js`. Run them one at a time, in order.

### Task 9: test harness that loads the real tag-manager.js (independent of Tasks 1-8)

Files: modify `plugins/tagManager/tag-manager.js` (a test hook at the end of the IIFE); create `plugins/tagManager/tests/harness.js`; test `plugins/tagManager/tests/test_real_file.js`

Test first: `test_real_file.js` has these cases:
- `loads without throwing`: `loadTagManager()` returns without throwing, with the ROUTE_PATH and HIERARCHY_ROUTE_PATH routes registered.
- `exports reachable`: `tm.exports.parseBlacklist` is a function.
- `no undefined calls`: collect `name(` call sites not preceded by `.`, then subtract functions and consts declared in the file, parameters (function and arrow parameter lists), JS keywords, and an explicit list of browser and JS globals (`fetch, setTimeout, clearTimeout, confirm, alert, parseInt, parseFloat, Number, String, Boolean, Array, Object, JSON, Promise, Math, Date, Set, Map, RegExp, Error, encodeURIComponent, decodeURIComponent, CSS, URL, MutationObserver, require`…). It fails listing any leftover. On the current file it must flag `loadStashdbTags`.

Run: `node plugins/tagManager/tests/test_real_file.js`, which fails (no harness yet).

Change:
- `harness.js` exports `loadTagManager({ fetchResponses, base = "/" })`. It builds a `vm` context with stubs for:
  - `window` (with `__TAG_MANAGER_TEST__ = {}`) and `document` (`querySelector`, `body`, `createElement`, `addEventListener`/`removeEventListener` counters);
  - `MutationObserver` (observe/disconnect), `fetch` (answers from `fetchResponses` keyed by GraphQL operation name, and records calls), `setTimeout`, `console`, `CSS.escape`, `URL`;
  - `PluginApi` (`React` with `createElement`/`useState`/`useEffect`/`useRef`, `register.route`, `libraries.ReactRouterDOM`).

  It runs the real file and returns `{ exports, getState, setState, fetchCalls, routes, document }`.
- In `tag-manager.js`, before the IIFE's last line, add `if (window.__TAG_MANAGER_TEST__) { window.__TAG_MANAGER_TEST__.exports = {...}; window.__TAG_MANAGER_TEST__.getState = () => ({...}); window.__TAG_MANAGER_TEST__.setState = (patch) => {...}; }`. The exports cover the functions later tasks test; start with `parseBlacklist`, `isBlacklisted` and `callBackend`, and add to it in each task. The state covers `localTags, settings, stashBoxes, selectedStashBox, stashdbTags, matchResults, categoryMappings, tagBlacklist, isImporting, pendingChanges, isEditMode`.
- Leave the `loadStashdbTags` bug for Task 10. In `test_real_file.js`, list it in `KNOWN_UNDEFINED = ["loadStashdbTags"] // removed by Task 10`. The test fails on any name not in that list, and also fails if a listed name is no longer found, which keeps the list honest.

Run: `node plugins/tagManager/tests/test_real_file.js`, which passes. Every other `tests/test_*.js` still passes.

Commit: `test(tagManager): harness that loads the real tag-manager.js`

### Task 10: Browse endpoint switch, backend errors shown, tags loaded without fuzzy (F7, F8 UI)

Depends on Tasks 3, 9.

Files: modify `tag-manager.js` (`attachEventHandlers` `#tm-stashbox` change handler around line 2592; `loadTagsFromCache` 701; `TagManagerPage` init around 3721); test `tests/test_real_file.js`, `tests/test_backend_errors.js`

Test first:
- `test_backend_errors.js`, `formatBackendError`: `{error:"HTTP 403", auth_error:true}` gives text mentioning the API key and "Metadata Providers"; a plain `{error:"x"}` gives `x`.
- `loadTagsFromCache` sets `stashdbTags` from a `fetch_all` response even when `settings.enableFuzzySearch` is false.
- In `test_real_file.js`, empty `KNOWN_UNDEFINED`. `no undefined calls` must still pass.

Run: `node plugins/tagManager/tests/test_backend_errors.js`, which fails.

Change:
- Replace `loadStashdbTags(container)` with `loadTagsFromCache(container)` and clear `selectedForImport` on an endpoint switch.
- Call `loadTagsFromCache` on init regardless of `enableFuzzySearch`.
- Add `formatBackendError(output)` and use it wherever a `callBackend` result carries `error` (`fetch_all`, `search`, cache refresh). Show it through the page's existing error or status element.
- Export both functions.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): Browse endpoint switch no longer throws; show stash-box errors`

### Task 11: blacklist in the UI end to end (F3)

Depends on Tasks 4, 5, 10.

Files: modify `tag-manager.js` (`parseBlacklist` 591, `isBlacklisted` 618, `renderTagRow` ~2388, the `.tm-accept` handler ~2657, `showMatchesModal` 3533-3653, `searchAllOnPage` ~2702, `searchSingleTag` ~2725, `doSearch` ~3603); test: rewrite `tests/test_blacklist.js` to use the harness and `tests/blacklist_cases.json`; add `tests/test_matches_modal.js`

Test first:
- `test_blacklist.js` runs every case in `blacklist_cases.json` through the real `parseBlacklist`/`isBlacklisted`. It must give the same results as Task 4's Python.
- `test_matches_modal.js`, `visibleMatches(matches)` returns `[{match, index}]` with blacklisted entries dropped and `index` pointing into the original array. With a blacklisted match at 0, the first visible entry has `index === 1`.
- `bestVisibleMatch(matches)` returns the first non-blacklisted match and its original index.

Run: `node plugins/tagManager/tests/test_blacklist.js`, which fails on the trailing-slash, flags and separator cases.

Change:
- `parseBlacklist` uses the same tokenizer rules as Task 4 (separators `\n , ;`, `/body/flags`, the legacy no-closing-slash form).
- Rows show and Accept `bestVisibleMatch`.
- The modal renders `visibleMatches`, with `data-index` holding the original index. `doSearch` results go through the same filter.
- Remove the `stashdb_tags` arg from the three `callBackend('search', ...)` calls (F9); Task 5 made the backend read its own cache.
- Add a blacklist editor to the Tag Manager page: a textarea, one pattern per line, plus a Save button. It saves `tagBlacklist` through the existing config write queue (`savePluginConfigPatch`) and reloads `tagBlacklist`.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): make the tag blacklist usable end to end`

### Task 12: saved category mapping shown; parent controls hidden when leaving parents alone (F4, F19)

Depends on Task 11.

Files: modify `tag-manager.js` (`showDiffDialog` 2740: the initial parent state 2762-2784, the dropdown 3030-3048, the Parent row 3020-3060); test `tests/test_parent_options.js`

Test first:
- `buildParentOptions({existingParents, parentMatches, savedMappingId, categoryName, localTags})` returns `[{value,label,selected}]`, covering four cases:
  - a saved mapping whose tag exists gives an option labeled `<name> (saved mapping)`, selected;
  - a saved mapping whose tag no longer exists is not offered and nothing points at it;
  - with no saved mapping, the order and selection match today's behavior;
  - exactly one option is selected.
- `shouldShowParentControls({leaveParentTagsAlone:true}, true)` is false.

Run: `node plugins/tagManager/tests/test_parent_options.js`, which fails.

Change:
- The dialog builds its dropdown from `buildParentOptions`, and the initial `selectedParentId` is the selected option.
- With `leaveParentTagsAlone`, the Parent row (select, Search… and "Remember this mapping") is not rendered.
- A stale saved mapping is dropped from `categoryMappings` when the dialog opens.
- Export both helpers.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): show the saved category mapping; hide parent controls when leaving parents alone`

### Task 13 (hard): atomic, confirmed merges (F5)

Depends on Task 12.

Files: modify `tag-manager.js` (`performTagMerge` 943-1008, `mergeTags` 3508-3528, the `.tm-error-merge` handler ~3307 and the `.tm-error-merge-api` handler ~3392); test `tests/test_merge.js`

Test first: use the harness with `fetchResponses`. Tests:
- `merge with values when supported`: the introspection for `TagsMergeInput` includes `values`, so exactly one `TagsMerge` mutation is sent. Its `values` carry the aliases, stash_ids, the union of the source and destination parent_ids and child_ids, and the description.
- `v0.30 fallback`: no `values` field, so merge then update; the update fails, and the result error says the source was already merged and what to fix.
- `confirm cancelled`: `confirmTagMerge` returns false, so no mutation is sent.
- The confirmation text includes the scene count, child count and parent count.

Run: `node plugins/tagManager/tests/test_merge.js`, which fails.

Change:
- `supportsMergeValues()` runs `{ __type(name:"TagsMergeInput"){ inputFields { name } } }` once and caches the result.
- `confirmTagMerge(sourceTag, destinationTag)` uses `getTagSceneCount` and `confirm()`.
- `performTagMerge` confirms, builds the values, and uses the single mutation when supported (Stash 0.31+). Otherwise it uses the old two-step path with the clearer error.
- Both merge buttons go through `performTagMerge`.
- Export `supportsMergeValues`, `confirmTagMerge` and `performTagMerge`.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): atomic, confirmed tag merges`

### Task 14 (hard): conflict modal, stuck import flag, validate before Apply (F6, F11, F12)

Depends on Task 13.

Files: modify `tag-manager.js` (`renderConflictResolutionModal` 1649-1874 including `doReverse` 1801, `doStrip` 1774, `doMergeInto` 1750; `handleImportSelected` 1408-1639; `handleUpdateLinkedTags` ~1900; the diff-dialog Apply handler 3153-3458); test `tests/test_import_conflict_resolution.js` (rewrite to use the harness), `tests/test_apply_order.js`

Test first:
- `reverse merge failure removes created tag`: `createTag` succeeds, the merge fails, a `TagDestroy` is sent for the new id, and the row stays actionable.
- `conflicts re-checked after each action`: after row A's merge-into changes tag X, row B's conflict against X is recomputed from the fresh `localTags`.
- `strip reports dropped aliases`: the row's result text lists them.
- `isImporting reset on throw`: `handleImportSelected` with `stashdbTags = null` leaves `isImporting === false`.
- `apply validates before creating parent`: a name conflict means no `TagCreate` for the `__create__` parent and no `configurePlugin` for the mapping.
- `apply button disabled while running`.

Run: `node plugins/tagManager/tests/test_apply_order.js`, which fails.

Change:
- **Reverse merge:** on a merge failure after the create, destroy the new tag (`tagDestroy`) and restore the row.
- **Conflicts:** after every row action, run `detectImportConflicts` again for the remaining rows and re-render them. Rows whose conflicts disappeared become importable.
- **Strip:** list the dropped aliases in the result.
- **Existing stash_id:** before `doMergeInto` replaces an existing stash_id for the endpoint, `confirm()` names it.
- **Endpoint guard:** guard `selectedStashBox` being null.
- **Import flag:** wrap the bodies of `handleImportSelected` and `handleUpdateLinkedTags` in `try/finally` that resets `isImporting` and the button.
- **Apply order:** run `validateBeforeSave` first. Only then create a `__create__` parent, save the mapping and update the tag. Disable Apply until it finishes.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): safer import conflict resolution and Apply ordering`

### Task 15: hierarchy state, alias search and keyboard handling (F13, F17, F19)

Depends on Task 14.

Files: modify `tag-manager.js` (`TagHierarchyPage` ~4903-4945, `enterEditMode`/`exitEditMode`/`savePendingChanges` 3782-3986, `fetchAllTagsWithHierarchy` 417-439, `showTagSearchDialog` ~4188, `handleHierarchyKeyboard` 4636-4695, the tab-switch handler 2444-2452, tree rendering); test `tests/test_hierarchy.js` (rewrite to use the harness)

Test first:
- `mount resets edit state`: stale `pendingChanges` and `isEditMode` are cleared by `resetHierarchyEditState()`, which the mount effect calls.
- `save re-fetches parents`: `savePendingChanges` sends parent_ids computed from a fresh `FindTag` response, not the snapshot.
- `failed change stays pending`: one of two updates fails, so that change remains in `pendingChanges`.
- `aliases fetched`: the `fetchAllTagsWithHierarchy` query text includes `aliases`.
- `wouldCreateCircularRef honors pending`: uses the real function now, replacing the drifted copy.
- `search dialog removes its keydown listener on backdrop close`: the document add and remove counts are equal.
- `ctrl+c ignored in inputs and when the tree is not focused`.

Run: `node plugins/tagManager/tests/test_hierarchy.js`, which fails.

Change:
- `resetHierarchyEditState()` runs on mount and unmount.
- Saves re-fetch current parents per tag (`fetchTagParentIds`) and keep failed changes pending.
- Add `aliases` to the query.
- Collapsed branches render their children only when expanded.
- Every dialog removes its keydown listener on every close path.
- Ctrl/Cmd+C, Ctrl/Cmd+V and Delete/Backspace act only when focus is inside the tree and not in an input, select, textarea or contentEditable. Match the key case-insensitively.
- Remove the Tag Manager tab handler's clearing of hierarchy state.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `fix(tagManager): hierarchy edit state, alias search and keyboard handling`

### Task 16: large libraries: local tag index, import progress and cancel (F10)

Depends on Task 15.

Files: modify `tag-manager.js` (the Import All handler 2548-2576, `handleImportSelected`, `findLocalTagByName`, `detectImportConflicts` 848-858, `resolveCategoryParents` 1112-1160, `searchAllOnPage` ~2690); test `tests/test_local_index.js`

Test first:
- `buildLocalTagIndex(localTags)` returns maps by `endpoint|stash_id`, lowercase name and lowercase alias. Lookups agree with the old linear finds on a 50-tag fixture.
- `import-all candidates fast`: with 10,000 local tags and 3,000 StashDB tags, `importAllCandidates(...)` returns in under 300 ms and excludes blacklisted and already-linked tags.
- `import all ignored while importing`.
- `cancel stops after current item`: with `requestImportCancel()` set, the loop stops and reports how many were done.
- `searchAllOnPage selects tags unlinked for the current endpoint only`.

Run: `node plugins/tagManager/tests/test_local_index.js`, which fails.

Change:
- Implement `buildLocalTagIndex`, rebuilt whenever `localTags` changes, and route the finds through it.
- Import All uses `importAllCandidates` and checks `isImporting`. If the category preview is cancelled, it restores `selectedForImport`.
- `handleImportSelected` shows `Importing i / N` and a Cancel button that sets the flag.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done`, which passes.

Commit: `perf(tagManager): index local tags; progress and cancel for large imports`

### Task 17: base-path-aware navigation, numeric settings, per-endpoint mappings (F16, F18, F20)

Depends on Task 16.

Files: modify `tag-manager.js` (`injectNavButtons` 5009-5077, `setupNavButtonInjection` 5082-5105, absolute `/tags/` links at ~1711, 2424, 3264, 3278, 3371, 3433 and 4572, `loadSettings` 238-248, `loadCategoryMappings` 280-305, `saveCategoryMappings` 310-328, and the mapping reads in `showDiffDialog` and `resolveCategoryParents`); test `tests/test_navigation_settings.js`

Test first:
- `stashPath("/tags/5")` with `<base href="/stash/">` gives `/stash/tags/5`, and with no base gives `/tags/5`.
- `navigateTo(path)` calls `history.pushState` with the based path and dispatches `popstate` (stubbed); it does not assign `location.href`.
- `parseIntSetting("0", 25, 1)` gives 25 (below the minimum), `("40", 25, 1)` gives 40, `("", 25, 1)` gives 25, and `(0, 80, 0)` gives 0.
- `migrateCategoryMappings({"Action":"12"}, "https://stashdb.org/graphql")` gives `{"https://stashdb.org/graphql": {"Action":"12"}}`, and an already-nested map passes through unchanged.
- `getCategoryMapping(endpoint, category)` reads only that endpoint's map.

Run: `node plugins/tagManager/tests/test_navigation_settings.js`, which fails.

Change:
- Implement `stashPath`, `navigateTo`, `parseIntSetting` (radix 10, with min), `migrateCategoryMappings` (a flat map is moved under the StashDB endpoint on first load, then saved) and `getCategoryMapping`/`setCategoryMapping`. Use them at every listed site.
- The nav observer re-injects only when the button is missing, debounced to one run per animation frame.

Run: `for f in plugins/tagManager/tests/test_*.js; do node "$f" || exit 1; done && node --check plugins/tagManager/tag-manager.js`, which passes.

Commit: `fix(tagManager): base-path navigation, numeric settings, per-endpoint category mappings`

## Release

### Task 18: docs, version 0.7.0, changelog

Depends on Tasks 1-17.

Files: modify `plugins/tagManager/tagManager.yml` (`version: 0.7.0`, the tagBlacklist description with the new syntax), `plugins/tagManager/README.md`, `plugins/tagManager/USERGUIDE.md`, `CHANGELOG.md` (root)

Check (docs, no unit test):
- **README:**
  - requirements (Python 3.9+, only the optional `thefuzz`);
  - a full settings table covering every key in `tagManager.yml`, and what `assets/default_settings.json` is;
  - fetch time (about 5s for StashDB now);
  - a v0.7.0 changelog entry.
- **USERGUIDE:**
  - blacklist syntax (`/regex/flags`, separators, the on-page editor);
  - scene sync: which endpoint each scene uses, how removals are remembered, the Reset task, what an HTTP 403 means;
  - merge confirmation and the Stash 0.31 atomic merge.
- **Root CHANGELOG:** a `### 0.7.0` entry under Tag Manager.

Run from the worktree root: `python .github/scripts/lint_manifests.py`, the link check from #171, and `cd plugins/tagManager && python -m pytest -q`. All must pass.

Commit: `docs(tagManager): 0.7.0 docs and changelog`

### Task 19: deploy to stash-test and exercise the UI on the small DB

Depends on Task 18.

Files: none changed.

Check:
- **Deploy:** `rsync -a --exclude tests --exclude __pycache__ --exclude cache --exclude data --exclude '.env' plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/`, then `reloadPlugins` through GraphQL (key: `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it). Remove the old `plugins/tagManager/cache/` on stash-test only after `<config>/plugin_data/tagManager/tag_cache/` exists.
- **Backend checks through `runPluginOperation`:**
  - search on StashDB and ThePornDB; the ThePornDB 403 from #140 is gone;
  - `fetch_all` takes under 10s;
  - a sync dry run returns stats with no error;
  - Reset Sync History returns `cleared`.
- **Browser pass** (Chrome extension; if it isn't connected, ask the user):
  - the #122, #124 and #126 flows;
  - the Browse endpoint switch;
  - blacklist save and filtering;
  - a merge with its confirmation;
  - a hierarchy edit, navigate away and back;
  - the nav buttons.

  Record what was checked for the PR.

Commit: none.

### Task 20: scale window on stash-test (prod snapshot), then restore

Depends on Task 19.

Files: none changed.

Check. Announce to the user before starting and when finished; they relay it to the other sessions.
1. `ssh root@10.0.0.4`, then stop stash-test (tell the user first). Preserve `/mnt/nvme_cache/appdata/stash-test/config/stash-go.sqlite` and its `-wal`/`-shm` into `/mnt/nvme_cache/appdata/stash-test/config/pre-scale-backup-<date>/`, recording their sha256.
2. Copy `/mnt/nvme_cache/appdata/stash/config/stash-go.sqlite.85.20260928_193215` (or a fresh `backupDatabase` from prod) to stash-test's `stash-go.sqlite`, with no `-wal`/`-shm`. Start stash-test.
3. Run a sync dry run with StashDB and ThePornDB configured, recording its time.
4. Run a live sync (it writes only into the test copy), recording the time, request rate and `sync_history.sqlite` size. Remove a tag from 3 scenes and sync again; confirm they are not re-added.
5. Time the Tag Manager page load and a search with the 269 prod tags. Add 10,000 synthetic local tags through a bulk `tagCreate` script and time Import All's candidate step, then delete them.
6. Stop stash-test. Restore the preserved files byte for byte (the sha256 must match) and delete the prod copy and the test-run `plugin_data/tagManager/sync_history.sqlite`. Start stash-test. Confirm `findScenes` count is 16 and `findTags` count is 11, as before.
7. Put the numbers in the PR description. Tell the user stash-test is restored.

Commit: none.
