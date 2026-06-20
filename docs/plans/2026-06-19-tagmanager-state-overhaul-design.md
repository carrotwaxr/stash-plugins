# Tag Manager — State & Persistence Overhaul (design)

> **Status:** 🟢 ready (scoped, design validated 2026-06-19)
> **Repo:** stash-plugins · **Plugin:** tagManager (v0.4.1)
> **Theme:** ROADMAP P1 "Tag Manager state/persistence overhaul" (Theme D in `docs/CATCHUP-2026-06.md`)
> **This pass (v0.5.0):** #122, #124, #126 + 3 folded hardening follow-ups
> **Fast-follow (v0.5.x):** #125 (in-UI alias-conflict resolution) — deliberately out of scope here

## Problem

The Tag Manager's open issues share one spine: **the frontend trusts its in-memory
state instead of the server**, in two places.

- **Persistence (#122)** — category→parent mappings revert to "create new." `resolveCategoryParents`
  (`tag-manager.js:915-963`) correctly keys on `categoryMappings[catName]`, so the lookup is fine;
  the write path is not. Saves go through `savePluginConfigPatch` (`tag-manager.js:120-138`), a
  **non-atomic read-whole-config-then-overwrite**, and `initializeDefaultSettings()`
  (`tag-manager.js:147-176`) runs **fire-and-forget** at startup doing its own backfill patch — so
  one save clobbers another, and a failed write reverts silently.
- **Reactivity (#124)** — after a merge/conflict-fix the UI re-renders from a stale in-memory
  `localTags` array and never re-fetches (`performTagMerge` re-renders at `tag-manager.js:812` without
  a refetch). Fixing a conflicting tag in another Stash tab and returning shows nothing until a full
  page reload.
- **Leave parents alone (#126)** — import always creates/assigns category parents (unless the user
  clicks a per-import "Import without Parents" button); there's no persistent setting for users who
  maintain their own nested hierarchy.

Plus three hardening follow-ups carried from the #128 review (do not drop).

## Goals (this pass → v0.5.0)

1. **#122** — category→parent mappings persist reliably and survive sessions.
2. **#124** — tag data reflects server changes (ours + made elsewhere) without a full reload.
3. **#126** — a persistent "leave parent tags alone" setting (full bypass of category parents).
4. Harden: await/`.catch` `initializeDefaultSettings()`; restore `syncDryRun` try/except;
   `safe_int` for `fuzzyThreshold`.

## Non-goals (YAGNI)

- **#125** in-UI alias-conflict resolution — separate fast-follow (the meatiest UI work).
- No incremental/delta tag fetching — debounced full `findTags` refetch is enough for now.
- No change to scene sync's parent behavior (it already never creates/modifies parents).
- "Leave parents alone" does **not** remove existing parent relationships — it only stops the
  plugin creating/assigning new ones.

## Design

### 1. Persistence reliability (#122)

1. **Await the bootstrap.** Make `initializeDefaultSettings()` awaited and `.catch`-guarded before
   any save-triggering UI is wired up. Closes the backfill-vs-user-save race and the brief window
   where `DEFAULTS` is `{}`. *(= folded hardening follow-up; it is part of #122's root cause.)*
2. **Write-through-and-verify.** `saveCategoryMappings()` is `await`ed at both call sites (import
   flow `tag-manager.js:1378-1386`; merge dialog `~2753`). After writing, read the config back and
   confirm `categoryMappings` round-tripped; on mismatch/failure show a toast instead of failing
   silently.
3. **Serialize config writes.** `savePluginConfigPatch` keeps its read-merge-write shape (Stash's
   `configurePlugin` overwrites the whole map) but funnels all writes through a single in-flight
   promise chain, so concurrent savers (mappings, blacklist, settings, backfill) merge onto the
   latest persisted config rather than a stale snapshot.

Net: the selected parent persists; next session `resolveCategoryParents` resolves `'saved'`, not `'create'`.

### 2. Reactivity / refresh (#124)

- **`refreshLocalTags()`** — re-runs `fetchLocalTags()` (`findTags(per_page:-1)`, `tag-manager.js:286-301`)
  and re-renders. Replace the by-hand `localTags` edits after **merge / import / link / alias-fix /
  parent change** with a call to it (single source of truth: re-fetch, don't hand-patch).
- **On regaining focus** — `visibilitychange`/`focus` listeners call `refreshLocalTags()` (the literal
  #124 case: fix a conflict in another tab, come back).
- **Guarded** — a `busy` flag skips/defers refresh while a modal/import is mid-flight, running the
  queued refresh when it clears; reconcile selections by tag id (drop ids that vanished, e.g. merged
  away; keep the rest); debounce focus events.
- **Listener lifecycle** — register on page mount, remove on unmount (also subsumes a known
  event-listener-cleanup concern).

### 3. "Leave parent tags alone" setting (#126)

New BOOLEAN `leaveParentTagsAlone` (default **OFF**) in `tagManager.yml` + `default_settings.json`,
read in `loadSettings`. When **ON**: import skips the category-preview modal and imports tags flat —
no parent creation, no category-derived `parent_ids`, no parent-description backfill. Existing parent
relationships are never removed. Scene sync unchanged.

### 4. Python hardening

- `handle_sync_scene_tags`: restore `try/except` around reading `syncDryRun`, defaulting to
  **dry-run = true** on missing/malformed config.
- Replace `int(config.get("fuzzyThreshold", default))` with `safe_int(value, default)` tolerating
  `""`/`None`/non-numeric.

## Testing strategy

Extract new logic into pure, testable units (the frontend is one large IIFE; DOM wiring stays manual).

- **Python (pytest)** — `safe_int` table (`""`, `None`, `"abc"`, `"75"` → 75/default);
  `syncDryRun` resolution defaults to true on malformed config. Follow existing `tagManager/tests`
  + mcMetadata patterns.
- **JS (node `test_*.js`, the repo's existing harness)** for extracted pure helpers:
  - config-write **serialization queue** — concurrent patches merge with no lost keys;
  - **save-verify** — `verifyPersisted(written, readback)` flags mismatch;
  - refresh **selection reconciliation** — drop vanished ids, keep the rest;
  - `leaveParentTagsAlone` import-decision gating.
- **Manual matrix on stash-test** — #122 (set mapping → reload → persists as `'saved'`); #124 (fix a
  conflict in another tab → return refreshes; merge reflects without reload); #126 (setting on →
  import skips modal, no parents created).

## Rollout

1. Branch/worktree under `.worktrees/`; TDD per the test plan.
2. Version bump **0.4.1 → 0.5.0**; README (new setting + persistence/refresh notes) + changelog.
3. Deploy to **stash-test** (`10.0.0.4:6971`) via rsync, reload, run the manual matrix.
4. Merge to `main` → Pages Action republishes the index.

## Fast-follow — #125 (separate effort)

In-UI alias-conflict resolution for bulk import: surface per-tag conflicts (currently caught and only
`console.error`'d + counted, `tag-manager.js:1371-1374`) with actions — merge into / merge here /
navigate to tag / keep separate. Scoped after v0.5.0 ships.

## Open questions / future work

- If `leaveParentTagsAlone` proves too blunt, consider the softer "link to existing parents but
  create none" variant (rejected here for simplicity).
- Focus-refetch on very large tag libraries — revisit delta fetching only if `findTags(per_page:-1)`
  becomes a felt cost.
