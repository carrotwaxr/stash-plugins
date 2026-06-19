# mcMetadata — Unified Processing Conditions (design)

> **Status:** 🟢 ready (scoped, design validated 2026-06-19)
> **Repo:** stash-plugins · **Plugin:** mcMetadata (v1.4.0)
> **Theme:** ROADMAP P1 "mcMetadata processing conditions" (Theme B in `docs/CATCHUP-2026-06.md`)
> **Closes / addresses:** GitHub #127 · Discourse #11, #13, #16, #18

## Problem

mcMetadata decides *whether* to touch a scene with logic that is **scattered and
inconsistent between its two entry points**:

- **Hook** (`Scene.Update.Post`, `mcMetadata.py:173-208`): three separate checks —
  `requireStashId` (configurable, default off), `hookTriggerMode` (`always`/`on_organized`),
  and a hardcoded cascade guard that skips already-organized scenes so the renamer's
  mark-organized step doesn't re-fire the hook forever.
- **Bulk task** (`process_all_scenes`, `scene.py:19-64`): hardcodes
  `stash_id NOT_NULL` and **ignores every hook setting** — so it silently skips every
  scene without a StashID. This is exactly GitHub **#127**.

The community's dominant request for this plugin (8 Discourse posts) is finer, *consistent*
control over when a scene is processed:

- **#11** — process only when a scene is marked **Organized**.
- **#18** — restrict by **required tag** or **directory** so a file is only processed once,
  in its final location.
- **#13 / #16** — the existing `hookTriggerMode` shipped but users couldn't find or
  understand it (discoverability gap).
- Proactive ask: a way to **see what would change before it runs**, to pre-empt the
  "it processed files I didn't want" class of report.

## Goals

1. One **unified processing gate** used identically by the hook and the bulk task.
2. Support four independently-optional conditions: **organized**, **required tags**,
   **directory scope**, **StashID**.
3. Fix **#127** as a natural consequence (bulk stops hardcoding the StashID filter).
4. Make the existing `dryRun` answer "*which* scenes are skipped and *why*."
5. Improve discoverability (settings descriptions, README, startup log).

## Non-goals (YAGNI)

- No tag-**hierarchy/descendant** resolution in v1 — exact tags only.
- No **boolean-OR** logic between gates. #18's "required tag *or* directory" means two
  *alternative restrictions*; each gate is independently optional and they AND together.
- No new "preview-only" task — the enriched bulk dry-run covers it.
- No per-condition `dryRun` override — the global flag is enough.
- No ALL-match tag mode — ANY-match only (revisit if requested).

## Design

### The condition model — single source of truth

A pure function evaluated by **both** paths:

```python
def should_process(scene, settings) -> tuple[bool, str]:
    """Return (passes, reason). reason is "" on pass, else a short skip key."""
```

Gates, **combined with AND**; an unset gate is a no-op (never blocks):

| Gate | Setting | Semantics | Skip-reason key |
|------|---------|-----------|-----------------|
| **Organized** | `organizedCondition`: `require`/`skip`/`ignore` | `require`→must be Organized (#11); `skip`→must *not* be; `ignore`→default | `not_organized` / `is_organized` |
| **Required tags** | `requiredTags`: comma-sep tag names | Pass if scene has **any** listed tag (ANY-match). Empty → no-op | `missing_required_tag` |
| **Directory scope** | `includePaths` / `excludePaths`: comma-sep globs | Pass if ≥1 file matches an include glob **and** no file matches an exclude. Empty include → all included; exclude wins | `outside_include_paths` / `excluded_path` |
| **StashID** | `requireStashId`: bool (existing) | `true`→must have a StashID; honored by **bulk too** (fixes #127) | `no_stash_id` |

Matching details:
- **Tags**: exact tag-name match, ANY-of. No descendants in v1.
- **Paths**: `fnmatch`-style globs, **case-insensitive**, matched against each file's
  full path. Include = whitelist; exclude beats include. Multi-file scenes pass if any
  file qualifies (consistent with `renamerMultiFileMode`'s per-file handling downstream).

### Settings

New / changed keys in `mcMetadata.yml` + `get_settings()` (`mcMetadata.py:67-110`):

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `organizedCondition` | STRING | `ignore` | Replaces `hookTriggerMode` |
| `requiredTags` | STRING | `""` | Comma-separated tag names |
| `includePaths` | STRING | `""` | Comma-separated globs; empty = all |
| `excludePaths` | STRING | `""` | Comma-separated globs |
| `requireStashId` | BOOLEAN | `false` | Existing; now applied in bulk |
| `dryRun` | BOOLEAN | `true` | Existing; enriched output (below) |

**Deprecated:** `hookTriggerMode` (STRING) — still read when `organizedCondition` is unset,
mapped `on_organized → require`, `always → ignore`. New key wins if both are set. Kept in
the YAML one release for back-compat, with a description noting it's superseded.

### Hook wiring

Replace the three scattered checks at `mcMetadata.py:185-205` with one call:

```python
scene = stash.find_scene(scene_id)
ok, reason = should_process(scene, SETTINGS)
if not ok:
    log.debug(f"Scene {scene_id} skipped: {reason}")
    return
process_scene(scene, stash, SETTINGS, api_key)
```

The old cascade guard is **subsumed**: a user wanting organized-only sets
`organizedCondition=require` (re-fires are prevented in practice by
`renamerIgnoreFilesInPath` + `nfoSkipExisting`); `skip` naturally avoids reprocessing
organized scenes. No special-case code remains.

### Bulk wiring + #127 fix

`process_all_scenes` (`scene.py:19-64`) stops hardcoding `QUERY_WHERE_STASH_ID_NOT_NULL`:

```python
f = build_scene_filter(settings)          # server-side PREFILTER (optimization only)
for scene in paginate(stash.find_scenes(f=f, ...)):
    ok, reason = should_process(scene, settings)   # authoritative
    if ok:
        process_scene(scene, ...)
    else:
        skip_counts[reason] += 1
```

- **`should_process` is authoritative.** `build_scene_filter` only pushes the trivially
  server-equivalent gates — **organized** (`organized: true/false`) and **StashID**
  (`stash_id_endpoint NOT_NULL`) — to avoid fetching the whole library. It **must always
  return a superset** of what `should_process` accepts. Tags and directory globs stay
  client-side (tags could be pushed later once name→ID resolution exists; globs never can).
- With `requireStashId` defaulting to **false**, null-StashID scenes are now processed →
  **#127 resolved**.

### Dry-run preview & skip reasons

`should_process` already returns a reason; bulk tallies them and logs a histogram + a
small sample at the end of every run (dry or live, at INFO):

```
[DRY RUN] Bulk scan: 1,240 scenes
  → would process: 312
  → skipped: 928
      not_organized .......... 700
      missing_required_tag ... 180
      outside_include_paths .. 48
  Sample skipped (first 10): [41] not_organized · [88] missing_required_tag 'curated' · …
```

### Discoverability (#13/#16)

- `mcMetadata.yml` descriptions for the new keys carry concrete example values.
- README gains a **"Processing conditions"** section: the 4-gate table + a worked example
  ("only NFO my Organized, StashDB-linked scenes under `/media/curated`").
- A one-line **startup INFO log** echoes the active gates so users can confirm config:
  `Active conditions: organized=require, tags=[curated], include=[/media/curated/*]`.

## Backwards compatibility

- All new gates default to no-op → existing installs behave identically.
- `hookTriggerMode` users are auto-migrated by the shim; no reconfiguration needed.
- Additive + back-compat → **minor version bump (1.4.0 → 1.5.0)**.

## Testing strategy

Repo uses `pytest` (`plugins/mcMetadata/tests/`). New/updated tests:

1. **`should_process` unit table** — per gate and in combination: organized 3-state;
   tag ANY-match (present/absent/multiple); include/exclude globs (case, multi-file,
   exclude-beats-include); `requireStashId`; AND-combination; every gate empty = pass.
2. **Migration** — `hookTriggerMode=on_organized → require`, `always → ignore`, and that
   `organizedCondition` overrides a stale `hookTriggerMode`.
3. **Superset invariant** — over a fixture set of scenes, assert
   `{passes build_scene_filter} ⊇ {passes should_process}` so the prefilter can never
   silently drop a scene the gate would accept.
4. **#127 regression** — a null-StashID scene is processed by bulk when
   `requireStashId=false`, and skipped (reason `no_stash_id`) when `true`.
5. **Dry-run histogram** — skip reasons are tallied correctly and totals reconcile
   (`processed + skipped == scanned`).
6. Existing mcMetadata tests stay green.

## Rollout

1. Branch/worktree under `.worktrees/` (per `CLAUDE.md`); TDD per the test plan above.
2. Deploy to **stash-test** (`10.0.0.4:6971`) via rsync, reload, run the manual matrix:
   hook `organized=require`; bulk with `includePaths`; dry-run histogram sanity.
3. README "Processing conditions" section + YAML example values.
4. Version bump → **1.5.0**; merge to `main`.
5. **Republish the source index** (`build_site.sh`) so users actually receive it; deploy
   to prod Stash (`6969`) after test passes.
6. Close **#127** with a note; reply on Discourse **#11/#13/#18** pointing at the new
   settings + the worked README example.

## Open questions / future work

- Push `requiredTags` into the server prefilter (needs tag name→ID resolution + ANY = `INCLUDES`).
- Tag **descendant** matching (depth) if users ask for hierarchical tags.
- Optional ALL-match tag mode.
- A standalone "what would be processed" report task, only if the dry-run histogram proves
  insufficient.
