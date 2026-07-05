# Tag Manager — in-UI alias-conflict resolution on import (#125)

**Repo:** stash-plugins · **Plugin:** tagManager · **Status:** 🟢 ready (design validated 2026-06-20)
**Fast-follow to:** Tag Manager state/persistence overhaul (#133, v0.5.0)
**Target version:** tagManager **0.6.0**

## ELI5

When you import tags from a stash-box, sometimes an incoming tag's name or one of
its aliases is already used by a local tag. Today the create silently fails — the
detail only appears in the browser console, and the import summary just says
"N errors." This adds an end-of-import **resolution modal** that lists each
conflict and lets you fix it in place: merge into the existing tag, strip the
clashing alias and import anyway, open the existing tag to hand-fix it, skip, or
(power-user, confirm-gated) absorb the existing tag into the incoming one.

## Problem

`handleImportSelected` (`tag-manager.js`) loops over `selectedForImport`. For a tag
with no exact local name match it calls `tagCreate`. If the incoming **name or any
alias** collides with an existing local tag's name/alias, Stash rejects the create.
The current `catch` just `console.error`s and increments `errors` (line ~1475) —
**logs-only, no actionable UI.** Issue #125 asks for merge / navigate / keep-separate
options instead.

## Building blocks that already exist (reuse, don't rebuild)

- `findConflictingTag(name, excludeTagId)` (~line 822) — case-insensitive match of a
  value against local tags' **name and aliases**. Exactly the detection primitive.
- `mergeTags(sourceIds, destinationId)` (~line 3125) — native `tagsMerge` mutation.
- `performTagMerge({...})` (~line 846) — merge helper (stash-id-on-destination pattern).
- Edit-flow conflict card UI (~line 2880) with `.tm-error-*` classes, "Remove from
  aliases", and "Edit/navigate to conflicting tag" — patterns to mirror for the modal.

The work is mostly **bringing the existing edit-flow conflict UX to the import path.**

## Design

### 1. Detection & flow

- **Pre-flight, not error-parsing.** In the import loop, before `tagCreate` for a
  name-mismatched incoming tag, run `findConflictingTag` on the incoming **name and
  each alias**. On a hit, skip the create and push a record
  `{ stashdbTag, parentId, conflictingValue, conflictingTag }` to `conflicts[]`.
  Deterministic, identifies the exact existing tag, leaves no partial state.
- **Belt-and-suspenders.** Keep the `try/catch` around `tagCreate`; a create-rejection
  for a conflict the in-memory check missed (out-of-band tag, case/whitespace
  normalization) routes into the **same** `conflicts[]` queue — never back to logs-only.
- **Batch completes first.** Non-conflicting tags still create/link/parent and tally
  normally. After the loop, if `conflicts.length > 0`, open the resolution modal.
- **Summary line** gains a distinct `N conflicts` count (separate from hard `errors`).
- Resolving a row mutates live (updates `localTags`) and removes the row; finishing
  re-renders the grid once. `localTags` stays authoritative via the #124 refresh work.

### 2. Per-conflict actions

"Incoming" = StashDB tag being imported; "Existing" = the colliding local tag.

1. **Merge into existing** *(default, safe)* — `updateTag` on the existing tag: add/
   replace its `stash_id` for `selectedStashBox.endpoint`, add the incoming
   **name + non-conflicting aliases** as aliases, set resolved `parentId` if missing.
   No deletion. Mirrors the name-match link path (lines ~1399–1436). → `linked++`.
2. **Strip conflicting alias(es) & import** *(keep separate)* — create the incoming tag
   with **all** colliding values removed from aliases (a tag may collide on several,
   against different existing tags). Name/description/non-conflicting aliases/stash_id/
   parent import normally. Row reports which aliases were dropped. → `created++`.
3. **Open conflicting tag** *(escape hatch)* — `window.open('/tags/<existingId>')`;
   row stays marked "deferred"; user re-runs import later. No state change.
4. **Skip** — drop this import; tally as skipped (not a hard error).
5. **Merge existing INTO incoming** *(destructive, confirm-gated)* — create the incoming
   tag, then `mergeTags([existingId], newId)` to absorb the existing tag (reassigns its
   scenes, deletes it). Confirm dialog shows the existing tag's **scene count**. Reuses
   the `performTagMerge` stash-id-on-destination pattern.

Footer **"Skip all remaining"** clears the rest in one click. No per-choice "remember"
or bulk auto-merge in v1.

### 3. Files touched (all in `plugins/tagManager`)

- `tag-manager.js` — collect `conflicts[]` in `handleImportSelected`;
  `renderConflictResolutionModal(conflicts, container)`; five action handlers reusing
  `findConflictingTag` / `updateTag` / `createTag` / `mergeTags`; extend summary line.
- `tag-manager.css` — conflict-row styling (reuse `.tm-error-*`).
- `tagManager.yml` — version → **0.6.0**.
- `README.md` / `USERGUIDE.md` — document the resolution modal.

### 4. Tests (TDD — extend the node suite)

New `tests/test_import_conflict_resolution.js`, mocking `localTags` / GraphQL:

- pre-flight detects name- and alias-collisions (case-insensitive); clean batch unaffected
- catch-fallback routes a create-rejection into the queue (no logs-only path)
- merge-into-existing → stash_id + name-as-alias + parent applied; conflicting aliases not added
- strip-alias → created without the colliding alias(es); dropped list reported
- reverse-merge → `mergeTags` called with correct source/dest; confirm-gated
- skip / skip-all → no mutations
- multi-alias / multi-target conflict handled

Backend Python untouched → no pytest changes.

## Out of scope (YAGNI)

- Persistence of unresolved conflicts across reload (resolve in-session).
- "Remember this choice" defaults / bulk auto-merge heuristics.
- Conflict resolution for the category/parent import path (#126 leaves parents alone;
  parents rarely alias-collide).

## Verification caveat

Plugin **UI/DOM flows can't be headlessly verified** — stash-test web login isn't in
`~/code/.env` (only `STASH_TEST_API_KEY`). Node tests + backend deploy-load go green
automatically, but the **modal DOM interaction needs a manual browser pass before
merge** — same constraint as #133.
