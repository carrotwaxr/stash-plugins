# mcMetadata 1.6.0 plan

Goal: ship mcMetadata 1.6.0, closing #147-#153 and #136.
Approach: first build one path layer that every rename and image path goes through (sanitize, join under a base, byte budgets). Then fix the data-loss and correctness bugs: bulk paging, the hook re-running itself, sidecars, NFO XML. Then add Plex and Jellyfin options, harden settings, logging and downloads, and test each file-moving path against a fake Stash in a temp dir.
Run with: /fluffer:plan-run

## Context every task needs

- Worktree: `/home/carrot/code/stash-plugins/.worktrees/mcmetadata-1.6`, branch `fix/mcmetadata-1.6.0`. It is stacked on `docs/readme-refresh` (PR #171). Never touch the main checkout or any other worktree.
- Plugin dir: `plugins/mcMetadata`. Tests: `cd plugins/mcMetadata && python -m pytest -q` (root `pytest.ini`; currently 156 passed, 1 skipped).
  - Tests mock `stashapi` where needed; see `tests/test_unit.py`.
  - `mcMetadata.py` reads stdin at import, so it can't be imported in tests until Task 12 fixes that.
- Tickets hold the full requirements: `gh issue view <n> --json body --jq .body` (#147-#153).
  - Finding ids (F1-F16) are defined in the "mcMetadata v1.5.0" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path.
  - A code map with exact line numbers for every finding is in the "Code map" section at the end of this plan.
- mcMetadata keeps `stashapp-tools` (0.2.59+). It must show a clear startup error when that is missing or too old.
- Stash floor is v0.30.
- Stash facts checked in the Stash source:
  - Hooks run synchronously. The plugin's own `sceneUpdate` blocks while a nested plugin run handles the same scene.
  - `hookContext` has `{id, type, input, inputFields}`. `inputFields` can't tell the plugin's own `{id, organized}` update from a user clicking Organized.
  - `moveFiles` doesn't fire Scene.Update.Post, and it refuses to overwrite and to move outside a library.
  - Scene sort `"id"` is valid, and `SceneFilterType.id` is an `IntCriterionInput` that supports `GREATER_THAN`.
  - With no sort, Stash adds no ORDER BY.
  - stashapi's `Studio.parent_studio` fragment is only `{ id }`.
  - Performer `image_path` always exists, with `default=true` when the performer has no image.
- **Conventions:**
  - Never `print()` to stdout outside the plugin's JSON reply; use the plugin's logger (`utils/logger.py` / `stashapi.log`).
  - Data functions return data or raise.
  - New settings go in `mcMetadata.yml` (types STRING, NUMBER or BOOLEAN), and their defaults go in `plugin_settings.map_settings`.
- **Every file-moving change** gets a test against a fake Stash that performs moves in a temp dir. Tests must never touch a real library.
- **stash-test is shared with other sessions.** Only Task 17 deploys there, and it only moves throwaway files it creates itself.

## Paths

### Task 1: path layer: sanitize components, join under a base, byte budgets

Files: create `plugins/mcMetadata/utils/paths.py`; test `plugins/mcMetadata/tests/test_paths.py`

Test first: table tests for:
- `sanitize_component(value) -> str`:
  - replaces `<>:"/\|?*` and control characters; `:` becomes `-`, others a space
  - collapses runs of whitespace
  - strips leading/trailing spaces and dots
  - turns `.`/`..`/empty into `_`
  - appends `_` to Windows reserved names (`CON`, `nul.txt`, `COM1`, case-insensitive)
  - keeps `&` (no `and` rewrite here; the replacer decides that)
- `fit_bytes(name, max_bytes=255, keep_suffix="") -> str`: trims whole UTF-8 characters from the end of the stem so `len(name.encode()) <= max_bytes`, keeps `keep_suffix` (an extension) intact, and never splits a character. Test with a 200-character CJK string.
- `join_under(base, *parts) -> str`:
  - `os.path.join` after `os.path.normpath`
  - raises `PathEscapeError` if the result isn't inside `base` (use `os.path.commonpath`)
  - raises `ValueError` on an empty base
  - base with and without a trailing slash; a part with `..`; an absolute part
- `is_inside(path, base) -> bool`: boundary-aware, so `/media/lib2/x` is not inside `/media/lib`, and path case is preserved.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_paths.py`, which fails (no module).

Change: implement the four functions and `PathEscapeError(ValueError)` in `utils/paths.py`, standard library only, with a short module docstring.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `feat(mcMetadata): shared path sanitizing and safe joins`

### Task 2 (hard): renamer builds paths through the path layer (F1, F7, F10, truncation, re-expansion, deep studios)

Depends on Task 1.

Files: modify `plugins/mcMetadata/utils/replacer.py` (`get_new_path`, `__replace_invalid_file_chars`, `__trim_filename`, `__replacer_studios`, the replacers loop), `plugins/mcMetadata/scene.py` (`__rename_videos` in-target check ~l.234-236; `__hydrate_scene` studio fetch ~l.144); tests `plugins/mcMetadata/tests/test_replacer_paths.py`, update `tests/test_core.py` TestReplacers where expectations change

Test first:
- `test_base_path_joined_safely`: `renamer_path="/media/lib"` and `"/media/lib/"` both give `/media/lib/<Studio>/...`.
- `test_empty_base_path_rejected`: `get_new_path` returns None and logs an error.
- `test_metadata_cannot_escape_base`: studio `..`, title `../../x`, studio `/abs` all stay under the base.
- `test_windows_reserved_and_trailing_dots`: title `CON` gives `CON_`; studio `Ends.` gives `Ends`.
- `test_tokens_in_metadata_not_reexpanded`: title `Pay $Tags now` with template `$Title - $Tags` keeps the literal `$Tags` in the title.
- `test_truncation_trims_only_overflow`: template `$Studio/$Title - $Performers`.
  - With a budget of exactly the full length, nothing is cut.
  - Over budget by 5, the last truncable variable loses exactly 5 characters.
  - Performers are never cut to empty while the title has room: truncate truncable variables in their existing order and stop when the path fits.
- `test_component_byte_limit`: a 150-character CJK title gives a component of at most 255 bytes, with the extension intact.
- `test_float_budget`: budget `250.0` works.
- `test_in_target_dir_boundary`: `/media/library2/x` is not inside `renamer_path=/media/lib`.
- `test_deep_studio_hierarchy`: `$Studios` with a 3-level chain renders all three names. Hydration walks `parent_studio.id` with `find_studio` per level; mock it.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_replacer_paths.py`, which fails.

Change:
- `get_new_path` renders the template in ONE pass: a single regex of all known `$Keys` (longest first), replaced through a callback, so substituted values are never re-scanned.
- Each rendered value is passed through `sanitize_component`. The existing `&`→`and` rule is kept only where it exists today, for performers, studio and tags; document that in a comment. Components are split on the template's `/`.
- Budget:
  - `renamerFilepathBudget` stays the total path length in characters (as documented), coerced to int.
  - Each component is also capped by `fit_bytes(...)` at 255 bytes.
  - Truncation trims only the overflow from truncable values in order.
- The final path is `join_under(base, *components) + ext`. An empty base means no rename, with an error log.
- The error text names the real setting (`renamerFilepathBudget`).
- `$Studios` on Windows uses `os.sep` without the regex escaping, because it is no longer a `re.sub` replacement.
- `scene.py` uses `is_inside(video_path, renamer_path)`.
- `__hydrate_scene` fetches the studio chain by following `parent_studio.id` with `find_studio(id, "id name parent_studio { id }")`, max 10 levels, to guard against loops.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): safe, correctly budgeted rename paths`

### Task 3: performer image paths stay inside the People folder (F6)

Depends on Task 1.

Files: modify `plugins/mcMetadata/performer.py` (`get_actor_image_path` l.109-142); test `plugins/mcMetadata/tests/test_unit.py` TestPerformerImagePath

Test first:
- `AC/DC` gives a single component (no nested folder).
- `../../etc` and `/abs` stay under `actor_metadata_path`, or return None with a warning.
- A leading space is stripped.
- `media_server="Jellyfin"` (capitalised) works the same as `jellyfin`.
- Existing jellyfin/emby expectations still hold, including the first-letter folder being the raw first character. Don't change existing users' layout.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_unit.py -k PerformerImagePath`, which fails on the new cases.

Change: sanitize the name with `sanitize_component`, lowercase `media_server` for comparison, and build with `join_under(base, [letter,] name, "folder.jpg")`. On `PathEscapeError`, warn and return None.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): performer image paths can't escape the People folder`

## Bulk runs and the hook

### Task 4: bulk paging never skips scenes (F3)

Files: modify `plugins/mcMetadata/scene.py` (`process_all_scenes` l.16-91); test `plugins/mcMetadata/tests/test_bulk.py`

Test first: `test_skip_condition_does_not_skip_scenes_that_leave_the_filter`. A FakeStash holds 250 unorganized scenes and applies `organized` and `id > N` / `sort=id` filters. Patched `process_scene` marks each scene organized, which makes it leave the `organized=false` set mid-run. Every one of the 250 scenes is processed exactly once, and the summary counts 250. Also keep the existing count and histogram tests passing.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_bulk.py`, which fails (about 100 processed).

Change:
- Page by cursor: `filter={"per_page": 100, "sort": "id", "direction": "ASC"}` plus `id: {value: last_id, modifier: GREATER_THAN}` added to the scene filter from `build_scene_filter`.
- Loop until a page returns fewer than 100 scenes.
- The total count comes from the initial count query, is used for progress only, and progress is clamped to 1.0.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): bulk runs no longer skip scenes`

### Task 5 (hard): the hook ignores the plugin's own updates; sidecars move before marking organized (F2)

Depends on Task 4.

Files: create `plugins/mcMetadata/utils/self_updates.py`; modify `plugins/mcMetadata/scene.py` (`__rename_videos` l.291-315: order of the organized update and the sidecar relocation), `plugins/mcMetadata/mcMetadata.py` (hook branch l.151-173); test `plugins/mcMetadata/tests/test_self_updates.py`

Test first:
- `test_marker_roundtrip`:
  - `mark(scene_id)` then `consume(scene_id)` gives True.
  - A second `consume` gives False.
  - A marker older than 120s gives False and is removed.
- `test_hook_skips_self_update`:
  - With a marker for scene 5 and `inputFields == ["id","organized"]`, the hook decision function `should_skip_hook(hook_context, data_dir)` returns True.
  - With no marker it returns False, even with the same inputFields, which is the user's Organized click.
  - With a marker but `inputFields` that include `title`, it returns False, since the user edited more than organized.
- `test_sidecars_moved_before_organized_update`: a fake stash records call order; the sidecar relocation happens before `update_scene(organized=True)`.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_self_updates.py`, which fails.

Change:
- `utils/self_updates.py`: `mark(scene_id, data_dir)`, `consume(scene_id, data_dir)` and `should_skip_hook(hook_context, data_dir)`.
  - Markers are small files under `<Stash config Dir>/plugin_data/mcMetadata/self_updates/`. Take `Dir` from `server_connection`, and fall back to a temp dir if it can't be created.
- `__rename_videos` relocates NFO, poster and sidecars (Task 6 adds sidecars) first. Then it calls `mark(scene_id)` right before `update_scene({"id", "organized": True})`, and it `consume`s the marker if the update raises.
- In the hook branch, `should_skip_hook` runs before `find_scene`; skip with a debug log.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): the hook ignores mcMetadata's own organized updates`

### Task 6: sidecar files move with the video, never overwriting (F4, F9)

Depends on Task 5.

Files: modify `plugins/mcMetadata/scene.py` (sidecar relocation), `plugins/mcMetadata/utils/files.py` (`rename_file` l.198-213 and a new `find_sidecars`), `plugins/mcMetadata/mcMetadata.yml` + `plugin_settings.py` (a new BOOLEAN `renamerMoveSidecars`, default True); test `plugins/mcMetadata/tests/test_sidecars.py`

Test first (real temp dir, fake stash doing the video move with `os.rename`):
- `test_find_sidecars_boundaries`: for video `Scene 1.mp4`, it finds `Scene 1.srt`, `Scene 1.en.srt`, `Scene 1.funscript`, `Scene 1-fanart.jpg`, `Scene 1.nfo`, `Scene 1-poster.jpg`. It does NOT take `Scene 10.srt`, `Scene 1x.srt` or the video itself.
- `test_sidecars_follow_every_moved_file`: in a multi-file scene, each moved file takes its own sidecars.
- `test_existing_destination_not_overwritten`: a sidecar whose destination exists is skipped with a warning, and both files are unchanged. This also covers the NFO/poster overwrite (F9).
- `test_setting_off_moves_only_nfo_and_poster`.
- `test_dry_run_lists_sidecars`: dry run logs each sidecar it would move and touches nothing.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_sidecars.py`, which fails.

Change:
- `find_sidecars(video_path) -> list[str]` returns files in the same folder whose name starts with `<video stem>` followed by `.` or `-`, excluding the video itself.
- `rename_file` refuses to overwrite: it returns False and warns if the destination exists, uses `os.makedirs(exist_ok=True)`, then `shutil.move`.
- Relocation runs for every file the renamer moved, not only the primary, keeping the part of the name after the stem.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `feat(mcMetadata): move sidecar files with the video`

## NFO output

### Task 7: always-valid NFO XML; thumb, actor and tag excludable (F5, F15)

Files: modify `plugins/mcMetadata/utils/nfo.py` (`build_nfo_xml` l.44-141, `escape_xml`), `plugins/mcMetadata/plugin_settings.py` (`nfo_exclude_fields` l.55-59), `plugins/mcMetadata/scene.py` (`__write_nfo` l.353-385: the Created/Updated log), `plugins/mcMetadata/mcMetadata.yml` (nfoExcludeFields description); test `plugins/mcMetadata/tests/test_nfo_valid.py`

Test first: every generated NFO is parsed with `xml.etree.ElementTree.fromstring`. Tests:
- `test_plot_with_cdata_terminator`: `a]]>b` round-trips as the plot text.
- `test_control_characters_stripped`: `\x0b` and `\x00` in the title and plot.
- `test_empty_fields_omitted`: no rating, no date and no studio give no empty `<rating/>`, `<premiered/>`, `<year/>` or `<studio/>`.
- `test_exclude_thumb_actor_tag`: `thumb`/`poster`, `actor` and `tag` in `nfoExcludeFields` remove those elements.
- `test_unknown_exclude_key_warns`.
- `test_null_exclude_fields`: None parses as empty.
- `test_write_nfo_logs_created_vs_updated`.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_nfo_valid.py`, which fails.

Change:
- `xml_safe(text)` removes characters outside XML 1.0's allowed set.
- Plot CDATA splits `]]>` into `]]]]><![CDATA[>`.
- Empty values are omitted.
- The exclusion keys are extended and validated against a known set, warning on unknown keys.
- `__write_nfo` records whether the file existed before writing.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): always-valid NFO XML; make thumb, actor and tag excludable`

### Task 8: Plex NFO mode for Plex Media Server 1.43.1+ (#151, #136)

Depends on Task 7.

Files: modify `plugins/mcMetadata/utils/nfo.py`, `plugins/mcMetadata/performer.py`, `plugins/mcMetadata/scene.py` (poster naming for plex), `plugins/mcMetadata/README.md` (Plex section l.179-183), `plugins/mcMetadata/mcMetadata.yml` (mediaServer description); test `plugins/mcMetadata/tests/test_plex_mode.py`

First, research (record findings in the commit body): read Plex's current docs for the local NFO provider added in PMS 1.43.1 and for local media assets (movie poster/fanart file names, and what an actor `<thumb>` may contain). Use WebFetch on support.plex.tv / forums.plex.tv. If a detail can't be confirmed, choose the Kodi-compatible form and say so.

Test first: `media_server="plex"` NFO snapshot tests:
- `<actor>` blocks with `<name>`, `<role>` and `<order>` are present.
- An actor `<thumb>` holds the performer image URL form decided by the research. If the research says local paths aren't supported, the thumb is the Stash performer image URL, with no API key in it.
- Poster and fanart file names follow the documented Plex local-asset names for movies.
- jellyfin/emby output is unchanged (existing snapshots).

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_plex_mode.py`, which fails.

Change: implement plex-mode differences behind `media_server == "plex"`.

README Plex section:
- PMS 1.43.1+ reads Kodi-style NFOs with its built-in provider.
- How to set the library up.
- The legacy XBMCnfoMoviesImporter agent stays only as a note.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `feat(mcMetadata): Plex NFO mode for Plex Media Server 1.43.1+`

### Task 9: Jellyfin interop options (#152)

Depends on Task 7.

Files: modify `plugins/mcMetadata/utils/nfo.py`, `plugins/mcMetadata/scene.py`, `plugins/mcMetadata/performer.py`, `plugins/mcMetadata/mcMetadata.yml`, `plugins/mcMetadata/plugin_settings.py`, `plugins/mcMetadata/mcMetadata.py` (a Performer.Update.Post hook branch); test `plugins/mcMetadata/tests/test_jellyfin_options.py`

New settings, with defaults that keep today's output:
- `nfoFilename` (STRING): `{basename}.nfo` or `movie.nfo`.
- `posterFilename` (STRING): `{basename}-poster.jpg`, `poster.jpg` or `folder.jpg`.
- `backdropFilename` (STRING, empty means none): e.g. `{basename}-fanart.jpg` or `backdrop.jpg`. The source is the scene screenshot.
- `nfoRatingField` (STRING): `both` (today), `rating` or `userrating`.

Always emit:
- `<director>` when the scene has one.
- `<uniqueid type="<box>">` for each stash_id, where the type comes from the endpoint host: `stashdb`, `theporndb`, `fansdb`, else the host. Keep `<uniqueid type="stash" default="true">` holding the local id.
- `<theporndbid>` when a ThePornDB stash_id exists.

Test first:
- template rendering for each filename setting;
- `folder_level_names_require_one_video_per_folder`: `movie.nfo`, `poster.jpg` and `folder.jpg` are skipped with a warning when the folder holds more than one video;
- the rating field choice;
- `<director>`;
- a stash_id uniqueid per endpoint;
- `<theporndbid>`;
- the Performer.Update.Post hook re-exports that performer's image when `enableActorImages` is on, and does nothing otherwise.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_jellyfin_options.py`, which fails.

Change: implement the settings and the hook, adding the hook to `mcMetadata.yml` (`triggeredBy: [Performer.Update.Post]`) and dispatching it in `main()`. Relocation and sidecar handling use the configured names.

Run: `cd plugins/mcMetadata && python -m pytest -q` and `python .github/scripts/lint_manifests.py` from the worktree root. Both pass.

Commit: `feat(mcMetadata): Jellyfin interop options`

## Robustness

### Task 10: settings that can't crash the plugin (F11) and a clear stashapp-tools error

Files: modify `plugins/mcMetadata/plugin_settings.py` (`map_settings` l.30-64, `_resolve_organized_condition` l.16-27), `plugins/mcMetadata/mcMetadata.py` (settings load l.59-91: move it inside `main()`'s try; the stashapp-tools import and version check); delete `plugins/mcMetadata/utils/settings.py` and `tests/test_core.py::TestSettings`; test `plugins/mcMetadata/tests/test_settings.py`

Test first:
- `map_settings` with every key set to None, and with wrong types (`"true"` string for booleans, `"300"` string for the budget, `True` for `organizedCondition`), returns sane typed values and logs warnings;
- an `organizedCondition` typo like `requre` fails closed: nothing is processed, with a warning naming the valid values;
- `nfoExcludeFields: None` doesn't crash;
- the template uniqueness rule from the dead validator (the template must include `$Title`, or `$StashID`, or `$ReleaseDate` plus something else; check the dead code for the exact rule) is enforced at runtime with an error that stops renames only;
- the budget is clamped to 40-800 as the old validator did.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_settings.py`, which fails.

Change:
- Coerce types in one helper per type.
- Fail closed on an unknown `organizedCondition`: add a `"invalid"` state that `should_process` rejects.
- Load settings inside `main()`'s try, so a bad setting gives a logged error and a JSON reply, not a traceback.
- If `import stashapi` fails, or the version is below 0.2.59, log a clear error: what to install (`pip install stashapp-tools`), and a pointer to the root README's Python prerequisites. Then exit cleanly.
- Delete the dead module and its tests.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): settings robustness and a clear stashapp-tools error`

### Task 11: quiet hook when disabled, append-mode log, glob and tag matching (F12, F13)

Files: modify `plugins/mcMetadata/mcMetadata.py` (the order of `init_file_logger` versus the `enable_hook` check), `plugins/mcMetadata/utils/logger.py` (`init_file_logger` l.19-47), `plugins/mcMetadata/conditions.py` (`_matches_any` l.21-24, the tags check l.116-120); test `plugins/mcMetadata/tests/test_conditions.py`, `plugins/mcMetadata/tests/test_logger.py`

Test first:
- `test_disabled_hook_logs_nothing_and_leaves_log_file`: a hook run with `enableHook=false` writes no INFO lines and doesn't create or truncate the log file. Use a helper that decides the flow before any logging, since main isn't importable yet.
- `test_log_file_appends`.
- Glob tests:
  - `[curated]` matches literally;
  - a bare directory `/media/trash` or `/media/trash/` excludes everything under it;
  - Windows `D:\media\x.mp4` matches `D:/media/*`;
  - `*` still spans directories, as documented.
- Required tags match case-insensitively.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_conditions.py tests/test_logger.py`, which fails.

Change:
- Check `enable_hook` before `init_file_logger` and before the dry-run banner.
- Open the log in append mode, with a run separator line.
- Normalize separators to `/`, escape `[`, and treat a pattern without wildcards as a directory prefix.
- Compare tags with `casefold`.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): quiet disabled hook, append-mode log, predictable path and tag matching`

### Task 12 (hard): testable entry point, None guards, dry-run parity (F8, F14)

Depends on Tasks 2, 5, 6, 10, 11.

Files: modify `plugins/mcMetadata/mcMetadata.py` (no stdin read at import; a `main(stdin_json)`-style entry and `if __name__ == "__main__"`), `plugins/mcMetadata/scene.py` (`process_scene`, `__hydrate_scene`, `__rename_videos`, `__write_nfo`), `plugins/mcMetadata/utils/files.py`; test `plugins/mcMetadata/tests/test_process_scene.py`

Test first. Use a `FakeStash` that keeps scenes and files in memory and performs `moveFiles` as a real move in a temp dir. It refuses destinations outside its library roots and existing files, as Stash does. Tests:
- `test_scene_with_no_files_is_skipped` instead of TypeError.
- `test_deleted_performer_ignored`: `find_performer` returns None.
- Dry-run parity: for the same scene, a dry run's logged target paths for the video, NFO, poster and sidecars equal the paths a live run produces in the temp dir. The NFO and poster are logged at the NEW path.
- `test_dry_run_reports_outside_library`: a target outside the fake library roots is reported as "would be refused by Stash". Library roots come from `configuration.general.stashes[].path`.
- `test_dry_run_null_is_dry`: `dryRun: None` means dry everywhere, with one truthiness helper.
- `test_hook_main_smoke`: run the entry point with a hook input and the hook disabled; nothing is processed and a JSON reply is printed.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_process_scene.py`, which fails.

Change:
- Make the entry point importable.
- Add the None guards.
- Compute the target paths once and share them between dry and live runs.
- Fetch library roots once per run.
- Use one `is_dry_run(settings)` helper everywhere.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): dry runs match live runs; guard missing files and performers`

### Task 13: image downloads authenticate with the session cookie; skip default performer images

Files: modify `plugins/mcMetadata/utils/files.py` (`download_image` l.76-195), `plugins/mcMetadata/scene.py` (poster URL l.124-127), `plugins/mcMetadata/performer.py` (l.72-106); test `plugins/mcMetadata/tests/test_downloads.py`

Test first:
- `download_image` sends `Cookie: session=<value>` when a session cookie is configured, and adds no `apikey` query when it isn't needed;
- a `text/html` response logs an error naming the cause: a login redirect, so the plugin isn't authenticated, and what to check;
- a performer whose `image_path` contains `default=true` is skipped (no download);
- existing validation (magic bytes, size) still applies.

Use a local `http.server` in a thread or patch `urllib.request.urlopen`.

Run: `cd plugins/mcMetadata && python -m pytest -q tests/test_downloads.py`, which fails.

Change:
- Take the cookie from `server_connection.SessionCookie`, store it in settings or a module global at startup, and send it with each download.
- Keep `apikey` only as a fallback when the API key is set and no cookie exists.
- Improve the `text/html` error message.

Run: `cd plugins/mcMetadata && python -m pytest -q`, which passes.

Commit: `fix(mcMetadata): authenticate image downloads and skip default performer images`

## Release

### Task 14: docs, version 1.6.0, changelog

Depends on Tasks 1-13.

Files: modify `plugins/mcMetadata/mcMetadata.yml` (`version: 1.6.0`, descriptions for new or changed settings), `plugins/mcMetadata/README.md`, `CHANGELOG.md` (root, the mcMetadata section)

Check (docs):
- **README:**
  - rename rules: the base path must be inside a Stash library, how components are sanitized, the byte limit, collisions, what dry run can and can't check;
  - sidecars;
  - excludable NFO fields;
  - how the conditions interact with "mark organized";
  - a Plex section (PMS 1.43.1+);
  - a Jellyfin recipe with the new filename options;
  - the settings table covers every yml key;
  - the Python requirement (`stashapp-tools`, see the root README);
  - a v1.6.0 changelog entry.
- **Root CHANGELOG:** a `### 1.6.0` entry replacing "No release this cycle yet".
- No em-dash characters.

Run from the worktree root: `python .github/scripts/lint_manifests.py`, `cd plugins/mcMetadata && python -m pytest -q`, and a relative-link check over the READMEs (inline script, no network). All pass.

Commit: `docs(mcMetadata): 1.6.0 docs and changelog`

### Task 15: deploy to stash-test and exercise on throwaway files

Depends on Task 14.

Files: none changed.

Check:
1. Deploy with `rsync -a --exclude tests --exclude __pycache__ --exclude '.env' --exclude samples --exclude '.coverage' plugins/mcMetadata/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/mcMetadata/`, then run `reloadPlugins`. The key is `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it.
2. Snapshot stash-test first: tag, scene and studio IDs, and the mcMetadata plugin config.
3. Create a throwaway library folder under stash-test's `/data` (host path `/mnt/user/syslib/bunh/stash-test/vid/zz-mcmeta-test/`) with 2 tiny copies of an existing test video (plus a `.srt` and a `.funscript` sidecar for one). Scan only that folder and give the scenes a throwaway studio and title.
4. Run:
   - a bulk dry run with the renamer on and `renamerPath` pointing inside that folder;
   - then a live run on only those scenes (conditions: include path = the throwaway folder).

   Confirm the video and sidecars moved together, the NFO parses, and the poster downloaded with the session cookie.
5. Clean up: delete the throwaway scenes (with files) and the folder, and restore the plugin config and snapshot IDs. Confirm the counts match the snapshot.

Commit: none.

### Task 16: live Plex check (#151), with the user

Depends on Task 8.

Files: none changed.

Check: the user sets up (or approves setting up) the `_nfo-test` Plex library from the audit decisions: a dummy video plus an NFO generated in plex mode in `/mnt/nvme_cache/appdata/plex/_nfo-test`, with Plex's NFO provider on. Confirm Plex shows the title, plot, studio, tags and actors (and actor thumbs if supported), then remove the library and folder. Ask the user before doing anything in Plex.

Commit: none.

## Code map

This was mapped on 2026-09-28 against HEAD `2a521b6`; line numbers are approximate.

- `mcMetadata.py`:
  - reads stdin at import (l.59), builds `StashInterface` (l.62) and loads settings at module level (l.91);
  - `get_plugin_mode` (l.94-109) reads `hookContext.type`;
  - the hook branch is l.151-173: `enable_hook` check, `find_scene`, `should_process`, `process_scene`;
  - `init_file_logger` runs at l.118-119, before the hook check;
  - `general.apiKey` is read at l.133-134.
- `scene.py`:
  - `process_all_scenes` l.16-91: a count query, then unsorted `page`/`per_page` pages;
  - `process_scene` l.93-127, with the NFO target at l.110-114 and the poster at l.124-127;
  - `__hydrate_scene` l.129-148: performers via `find_performer`, the studio via `find_studio(id, "id name parent_studio { ...Studio }")`;
  - `__rename_videos` l.151-317: multi-file mode l.181-195, in-target check l.234-236, dry run l.261-266, move l.272-289, `update_scene` organized l.291-297, NFO and poster relocation l.299-315;
  - `__move_file_graphql` l.320-350, the moveFiles mutation;
  - `__write_nfo` l.353-385, BOM, with a buggy exists-after-write log.
- `utils/replacer.py`: `get_new_path` l.224-286 (`basepath + ...` at l.284), `__replace_invalid_file_chars` l.298-302, `__trim_filename` l.289-295, `__replacer_studios` l.117-137, the replacers dict order l.155-174.
- `utils/nfo.py`: `escape_xml` l.5-18, `_get_actor_thumb_path` l.21-41, `build_nfo_xml` l.44-141 (plot CDATA l.95, `field_lines`, uniqueid l.137).
- `utils/files.py`: `download_image` l.76-195 (apikey URL, content-type error l.116), `_is_valid_image` l.29-73, `rename_file` l.198-213 (shutil.move, `is False` guards).
- `performer.py`: `process_all_performers` l.9-54, `process_performer` l.57-106, `get_actor_image_path` l.109-142.
- `conditions.py`: `build_scene_filter` l.27-44, `_matches_any` l.21-24, `should_process` l.100-135.
- `plugin_settings.py`: `_split_csv` l.11-13, `_resolve_organized_condition` l.16-27, `map_settings` l.30-64.
- `utils/logger.py`: `init_file_logger` l.19-47 (mode `'w'`).
- `utils/settings.py`: dead; only `tests/test_core.py` TestSettings imports it.
