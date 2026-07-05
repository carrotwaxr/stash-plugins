# Tag Manager Import Conflict Resolution — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When importing stash-box tags, surface alias/name conflicts in an end-of-import resolution modal with actionable choices, instead of silently logging them to the console.

**Architecture:** Pre-flight conflict detection in `handleImportSelected` collects a `conflicts[]` array (with a `try/catch` fallback so nothing regresses to logs-only). After the batch, a modal renders one row per conflict with five actions (merge-into-existing, strip-alias-and-import, open, skip, reverse-merge). The testable logic is factored into **pure helper functions** following this repo's JS-test convention (browser IIFE, no build step → tests mirror the pure helpers and pass `localTags` as a param). DOM/GraphQL glue is verified manually on stash-test.

**Tech Stack:** Vanilla JS (browser IIFE), Stash GraphQL (`tagCreate`, `tagUpdate`, `tagsMerge`), `node`-run mirror tests, Python backend untouched.

**Design doc:** `docs/plans/2026-06-20-tagmanager-import-conflict-resolution-design.md`

**Reuse (already in `tag-manager.js`):**
- `findConflictingTag(name, excludeTagId)` (~822) — case-insensitive name+alias match.
- `validateBeforeSave(...)` (~915) — existing name+alias conflict-record builder (edit flow); our detection is the DRY import analogue.
- `mergeTags(sourceIds, destinationId)` (~3125), `performTagMerge(...)` (~846) — native `tagsMerge` plumbing.
- Edit-flow conflict card + `.tm-error-*` CSS (~2880) — visual pattern to mirror.

**Test convention (IMPORTANT):** Per `tests/test_alias_validation.js`, each pure helper is **re-defined inline** in the test file with `localTags` passed as an explicit parameter (the source version closes over module-scope `localTags`). Keep the logic byte-identical to source. Run a suite with `node plugins/tagManager/tests/test_<name>.js`.

**Commit style:** Conventional Commits, lowercase, imperative. No AI attribution. Commit after each task.

**Run all node tests:** `cd plugins/tagManager && for f in tests/test_*.js; do node "$f"; done`

---

## Task 1: Pure helper — `detectImportConflicts`

Detect whether an incoming stash-box tag's **name or any alias** collides with an existing local tag (case-insensitive), returning one record per colliding value.

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (add helper near `findConflictingTag`, ~830)
- Test: `plugins/tagManager/tests/test_import_conflict_resolution.js` (create)

**Step 1: Write the failing test**

Create the test file with the mirrored helpers and first cases:

```js
/**
 * Unit tests for #125 import conflict resolution helpers.
 * Run with: node plugins/tagManager/tests/test_import_conflict_resolution.js
 *
 * Per repo convention (browser IIFE, no build step), helpers are mirrored here
 * with localTags passed as a param. Keep byte-identical to tag-manager.js.
 */
let passed = 0, failed = 0;
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(a, b, msg) { assert(JSON.stringify(a) === JSON.stringify(b), `${msg}\n  got: ${JSON.stringify(a)}\n  exp: ${JSON.stringify(b)}`); }

function findConflictingTag(name, excludeTagId, localTags) {
  const lowerName = name.toLowerCase();
  return localTags.find(t =>
    t.id !== excludeTagId && (
      t.name.toLowerCase() === lowerName ||
      t.aliases?.some(a => a.toLowerCase() === lowerName)
    )
  ) || null;
}

function detectImportConflicts(stashdbTag, localTags) {
  const conflicts = [];
  const values = [stashdbTag.name, ...(stashdbTag.aliases || [])];
  for (const value of values) {
    const conflictingTag = findConflictingTag(value, null, localTags);
    if (conflictingTag) {
      conflicts.push({ conflictingValue: value, conflictingTag });
    }
  }
  return conflicts;
}

test('no conflict when name and aliases are unique', () => {
  const local = [{ id: '1', name: 'Existing', aliases: [] }];
  const incoming = { name: 'Fresh', aliases: ['Brand New'] };
  eq(detectImportConflicts(incoming, local), [], 'should be empty');
});

test('detects name collision (case-insensitive)', () => {
  const local = [{ id: '1', name: 'Barbara', aliases: [] }];
  const incoming = { name: 'barbara', aliases: [] };
  const r = detectImportConflicts(incoming, local);
  eq(r.length, 1, 'one conflict');
  eq(r[0].conflictingValue, 'barbara', 'value');
  eq(r[0].conflictingTag.id, '1', 'tag id');
});

test('detects alias collision against existing alias', () => {
  const local = [{ id: '1', name: 'Barbara', aliases: ['Bar'] }];
  const incoming = { name: 'Foo', aliases: ['Bar'] };
  const r = detectImportConflicts(incoming, local);
  eq(r.length, 1, 'one conflict');
  eq(r[0].conflictingValue, 'Bar', 'value');
});

test('detects multiple collisions against different tags', () => {
  const local = [
    { id: '1', name: 'Barbara', aliases: ['Bar'] },
    { id: '2', name: 'Quux', aliases: [] },
  ];
  const incoming = { name: 'Quux', aliases: ['Bar'] };
  const r = detectImportConflicts(incoming, local);
  eq(r.length, 2, 'two conflicts');
  eq(r.map(c => c.conflictingTag.id).sort(), ['1', '2'], 'both tags');
});

// runner
for (const [name, fn] of tests) {
  try { fn(); passed++; console.log(`  ✓ ${name}`); }
  catch (e) { failed++; console.log(`  ✗ ${name}\n    ${e.message}`); }
}
console.log(`\nPassed: ${passed}  Failed: ${failed}`);
process.exit(failed ? 1 : 0);
```

**Step 2: Run test to verify it passes (test is self-contained spec)**

Run: `node plugins/tagManager/tests/test_import_conflict_resolution.js`
Expected: `Passed: 4  Failed: 0` (the mirrored helper IS the spec).

**Step 3: Add the real helper to `tag-manager.js`**

Immediately after `findConflictingTag` (~line 830), add the byte-identical (minus `localTags` param — source closes over module scope) version:

```js
  /**
   * #125: Detect name/alias collisions for an incoming stash-box tag against
   * local tags. Returns one record per colliding value (name or alias).
   * @param {object} stashdbTag - incoming tag { name, aliases }
   * @returns {Array<{conflictingValue: string, conflictingTag: object}>}
   */
  function detectImportConflicts(stashdbTag) {
    const conflicts = [];
    const values = [stashdbTag.name, ...(stashdbTag.aliases || [])];
    for (const value of values) {
      const conflictingTag = findConflictingTag(value, null);
      if (conflictingTag) {
        conflicts.push({ conflictingValue: value, conflictingTag });
      }
    }
    return conflicts;
  }
```

**Step 4: Re-run the suite** — `node plugins/tagManager/tests/test_import_conflict_resolution.js` → `Failed: 0`.

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tests/test_import_conflict_resolution.js docs/plans/2026-06-20-tagmanager-import-conflict-resolution-design.md docs/plans/2026-06-20-tagmanager-import-conflict-resolution.md
git commit -m "test: add import-conflict detection helper (#125)"
```

---

## Task 2: Pure helper — `sanitizeAliasesForImport` (strip-alias action)

Return the incoming tag's aliases with **all** colliding values removed, plus the list of dropped values (for the UI note).

**Files:** Modify `tag-manager.js`; extend `tests/test_import_conflict_resolution.js`.

**Step 1: Add failing test cases** (append before the runner; also add the mirrored helper above the tests):

```js
function sanitizeAliasesForImport(stashdbTag, conflicts) {
  const dropped = new Set(conflicts.map(c => c.conflictingValue.toLowerCase()));
  const kept = [];
  const removed = [];
  for (const alias of (stashdbTag.aliases || [])) {
    if (dropped.has(alias.toLowerCase())) removed.push(alias);
    else kept.push(alias);
  }
  return { aliases: kept, removed };
}

test('strip-alias removes only colliding aliases', () => {
  const incoming = { name: 'Foo', aliases: ['Bar', 'Keep'] };
  const conflicts = [{ conflictingValue: 'Bar', conflictingTag: { id: '1' } }];
  eq(sanitizeAliasesForImport(incoming, conflicts), { aliases: ['Keep'], removed: ['Bar'] }, 'keep non-colliding');
});

test('strip-alias removes all of several colliding aliases', () => {
  const incoming = { name: 'Foo', aliases: ['Bar', 'Baz', 'Keep'] };
  const conflicts = [
    { conflictingValue: 'Bar', conflictingTag: { id: '1' } },
    { conflictingValue: 'Baz', conflictingTag: { id: '2' } },
  ];
  eq(sanitizeAliasesForImport(incoming, conflicts), { aliases: ['Keep'], removed: ['Bar', 'Baz'] }, 'drop both');
});

test('strip-alias note: name-only collision strips no alias', () => {
  const incoming = { name: 'Foo', aliases: ['Keep'] };
  const conflicts = [{ conflictingValue: 'Foo', conflictingTag: { id: '1' } }];
  // name collides, not an alias — nothing to strip; caller handles name case
  eq(sanitizeAliasesForImport(incoming, conflicts), { aliases: ['Keep'], removed: [] }, 'no alias dropped');
});
```

**Step 2:** Run suite → those 3 pass (spec). Total `Passed: 7`.

**Step 3:** Add byte-identical `sanitizeAliasesForImport(stashdbTag, conflicts)` to `tag-manager.js` after `detectImportConflicts`.

> **Note for implementer:** If the conflict is on the **name** (not an alias), strip-alias can't help — the modal must disable/hide "strip alias & import" for name-only conflicts and only offer merge/open/skip/reverse-merge. Wire this in Task 6.

**Step 4:** Re-run → `Failed: 0`.

**Step 5: Commit** — `git commit -am "test: add import alias-sanitization helper (#125)"`

---

## Task 3: Pure helper — `buildMergeIntoExistingInput` (merge-into-existing action)

Build the `tagUpdate` input that links the incoming stash-box entity to the existing conflicting tag: replace the stash_id for this endpoint, add the incoming name + non-conflicting aliases, add parent if missing.

**Files:** Modify `tag-manager.js`; extend test file.

**Step 1: Add test (with mirrored helper):**

```js
function buildMergeIntoExistingInput(existingTag, stashdbTag, conflicts, endpoint, stashdbId, parentId) {
  const conflictVals = new Set(conflicts.map(c => c.conflictingValue.toLowerCase()));
  const existingLower = new Set([
    existingTag.name.toLowerCase(),
    ...(existingTag.aliases || []).map(a => a.toLowerCase()),
  ]);
  // candidate aliases to add: incoming name + incoming aliases, minus conflicts and dupes
  const candidates = [stashdbTag.name, ...(stashdbTag.aliases || [])];
  const addAliases = [];
  for (const v of candidates) {
    const low = v.toLowerCase();
    if (conflictVals.has(low) || existingLower.has(low) || addAliases.some(a => a.toLowerCase() === low)) continue;
    addAliases.push(v);
  }
  const filteredStashIds = (existingTag.stash_ids || []).filter(s => s.endpoint !== endpoint);
  const input = {
    id: existingTag.id,
    aliases: [...(existingTag.aliases || []), ...addAliases],
    stash_ids: [...filteredStashIds, { endpoint, stash_id: stashdbId }],
  };
  const existingParents = (existingTag.parents || []).map(p => p.id);
  if (parentId && !existingParents.includes(parentId)) {
    input.parent_ids = [...existingParents, parentId];
  }
  return input;
}

test('merge-into-existing links stash_id and adds non-conflicting aliases', () => {
  const existing = { id: '1', name: 'Barbara', aliases: ['Bar'], stash_ids: [], parents: [] };
  const incoming = { name: 'Foo', aliases: ['Bar', 'Fooey'] };
  const conflicts = [{ conflictingValue: 'Bar', conflictingTag: existing }];
  const input = buildMergeIntoExistingInput(existing, incoming, conflicts, 'https://sb', 'sbid1', null);
  eq(input.aliases, ['Bar', 'Foo', 'Fooey'], 'adds name + non-conflicting alias, drops Bar');
  eq(input.stash_ids, [{ endpoint: 'https://sb', stash_id: 'sbid1' }], 'links stash id');
  assert(!('parent_ids' in input), 'no parent when none given');
});

test('merge-into-existing replaces stash_id for same endpoint and sets missing parent', () => {
  const existing = { id: '1', name: 'Barbara', aliases: [], stash_ids: [{ endpoint: 'https://sb', stash_id: 'OLD' }], parents: [] };
  const incoming = { name: 'Barbara', aliases: [] }; // name collision
  const conflicts = [{ conflictingValue: 'Barbara', conflictingTag: existing }];
  const input = buildMergeIntoExistingInput(existing, incoming, conflicts, 'https://sb', 'NEW', 'p9');
  eq(input.stash_ids, [{ endpoint: 'https://sb', stash_id: 'NEW' }], 'replaced, not duplicated');
  eq(input.parent_ids, ['p9'], 'adds missing parent');
  eq(input.aliases, [], 'name collision adds no alias');
});
```

**Step 2:** Run → pass (spec). **Step 3:** Mirror into `tag-manager.js`. **Step 4:** Re-run → `Failed: 0`.

**Step 5: Commit** — `git commit -am "test: add merge-into-existing input builder (#125)"`

---

## Task 4: Pure helper — `summarizeImportResult`

Build the import summary string, adding distinct **conflicts** and **skipped** counts alongside the existing created/linked/parented/errors tallies.

**Files:** Modify `tag-manager.js` (replace the inline summary assembly at ~1493–1500 with a call to this helper); extend test file.

**Step 1: Add test (mirror current phrasing + new counts):**

```js
function summarizeImportResult(c) {
  const parts = [];
  if (c.created > 0) parts.push(`Created ${c.created} tag${c.created !== 1 ? 's' : ''}`);
  if (c.linked > 0) parts.push(`linked ${c.linked} existing`);
  if (c.parented > 0) parts.push(`set parents for ${c.parented} (${c.categories} ${c.categories === 1 ? 'category' : 'categories'})`);
  if (c.conflicts > 0) parts.push(`${c.conflicts} conflict${c.conflicts !== 1 ? 's' : ''} resolved`);
  if (c.skipped > 0) parts.push(`${c.skipped} skipped`);
  if (c.errors > 0) parts.push(`${c.errors} error${c.errors !== 1 ? 's' : ''}`);
  return parts.length ? parts.join(', ') : 'Nothing to import';
}

test('summary includes conflicts and skipped distinctly from errors', () => {
  const s = summarizeImportResult({ created: 12, linked: 4, parented: 0, categories: 0, conflicts: 3, skipped: 1, errors: 0 });
  eq(s, 'Created 12 tags, linked 4 existing, 3 conflicts resolved, 1 skipped', 'phrasing');
});

test('summary singular/plural and empty', () => {
  eq(summarizeImportResult({ created: 1, linked: 0, parented: 0, categories: 0, conflicts: 1, skipped: 0, errors: 1 }),
     'Created 1 tag, 1 conflict resolved, 1 error', 'singulars');
  eq(summarizeImportResult({ created: 0, linked: 0, parented: 0, categories: 0, conflicts: 0, skipped: 0, errors: 0 }),
     'Nothing to import', 'empty');
});
```

**Step 2:** Run → pass. **Step 3:** Mirror into `tag-manager.js` and replace the inline assembly (lines ~1493–1500) with `summarizeImportResult({...})`. **Step 4:** Re-run all node suites → `Failed: 0` everywhere.

**Step 5: Commit** — `git commit -am "feat: report import conflicts/skips in summary (#125)"`

---

## Task 5: Wire detection into `handleImportSelected`

Collect conflicts during the batch (pre-flight + catch fallback) without breaking existing tallies, then open the modal.

**Files:** Modify `tag-manager.js` `handleImportSelected` (~1311–1505).

**Step 1: Pre-flight branch.** In the `else` branch (no exact name match, ~1437, before building `input`), insert:

```js
        } else {
          const conflicts = detectImportConflicts(stashdbTag);
          if (conflicts.length > 0) {
            importConflicts.push({ stashdbTag, parentId, conflicts });
            continue; // resolve at end of batch
          }
          const input = { /* ...existing... */ };
```

Declare `const importConflicts = [];` near the other counters (~1347).

**Step 2: Catch fallback.** Replace the `catch` (~1474) so a create-rejection still routes to the queue:

```js
      } catch (e) {
        const conflicts = detectImportConflicts(stashdbTag);
        if (conflicts.length > 0) {
          importConflicts.push({ stashdbTag, parentId, conflicts });
        } else {
          console.error(`[tagManager] Failed to import/link "${stashdbTag.name}":`, e);
          errors++;
        }
      }
```

**Step 3: After the loop**, before clearing `selectedForImport`, add counters `let conflictsResolved = 0, skipped = 0;` and, if `importConflicts.length`, `await renderConflictResolutionModal(importConflicts, container)` (Task 6) which returns `{ resolved, skipped, created, linked }` to fold into the tallies. Replace the summary block with `summarizeImportResult({...})`.

> No new unit test here (DOM/loop orchestration). Covered by manual stash-test pass (Task 11) + the helper tests already written. Verify the existing node suites still pass: `for f in tests/test_*.js; do node "$f"; done` → all `Failed: 0`.

**Step 4: Commit** — `git commit -am "feat: collect import alias conflicts for resolution (#125)"`

---

## Task 6: `renderConflictResolutionModal` (DOM — manual verify)

Render the batch-end modal: one row per `importConflicts` entry, action buttons per row, "Skip all remaining" footer. Returns a Promise resolving to `{ resolved, skipped, created, linked }`.

**Files:** Modify `tag-manager.js` (new function near the import code); reuse `.tm-error-*` + modal classes already in the file.

**Implementation notes (no unit test — DOM):**
- Row shows: incoming `stashdbTag.name`, the `conflictingValue`(s), and `→ "<conflictingTag.name>"`.
- Buttons per row: **Merge into "<name>"** (default), **Strip alias & import** (hide/disable if every conflict is a name collision — see Task 2 note), **Open "<name>"** (`window.open('/tags/<id>')`), **Skip**, **Merge "<name>" into this** (reverse — Task 8).
- Resolving a row removes it from the DOM and resolves its outcome; when all rows are gone (or "Skip all remaining"), resolve the modal Promise with the aggregate counts.
- Mirror the existing edit-flow card markup (~2880) for visual consistency. Use the existing modal open/close helpers (search `showCategoryPreviewModal` ~1326 for the pattern).

**Manual verification deferred to Task 11.** Commit when it renders without console errors locally (static check): `git commit -am "feat: add import conflict resolution modal (#125)"`

---

## Task 7: Wire safe action handlers (DOM + GraphQL — manual verify)

Implement merge-into-existing, strip-alias, open, skip, skip-all.

**Files:** Modify `tag-manager.js` (handlers inside/alongside the modal).

- **Merge into existing:** `await updateTag(buildMergeIntoExistingInput(conflictingTag, stashdbTag, conflicts, selectedStashBox.endpoint, stashdbTag.id, parentId))`; update the matching `localTags` entry in place; `linked++`.
- **Strip alias & import:** `const { aliases, removed } = sanitizeAliasesForImport(stashdbTag, conflicts);` create via the existing `tagCreate` path with these aliases; push to `localTags`; `created++`; show the `removed` list in a small note.
- **Open:** `window.open('/tags/' + conflictingTag.id)`; mark row "deferred" (counts as skipped on finish).
- **Skip / Skip all remaining:** remove row(s); `skipped++` each.

**Manual verification deferred to Task 11.** Commit: `git commit -am "feat: wire safe import-conflict actions (#125)"`

---

## Task 8: Reverse-merge action (destructive, confirm-gated — manual verify)

Absorb the existing tag into the incoming one.

**Files:** Modify `tag-manager.js`.

- Button **Merge "<existing>" into this**. On click, `confirm()` showing the existing tag's scene count (query it if not cached: `findTag` with `scene_count`).
- On confirm: create the incoming tag (clean), then `await mergeTags([conflictingTag.id], newTag.id)`; apply the stash_id to the new tag (mirror `performTagMerge`'s stash-id-on-destination block ~857). Update `localTags` (add new, remove absorbed). `created++`.
- Reuse `mergeTags` / the `performTagMerge` stash-id pattern; do not duplicate the mutation.

**Manual verification deferred to Task 11.** Commit: `git commit -am "feat: add confirm-gated reverse merge to import conflicts (#125)"`

---

## Task 9: CSS

**Files:** Modify `plugins/tagManager/tag-manager.css`.

Add conflict-row + modal styling, reusing `.tm-error-*` where possible (search the existing block). Keep it visually consistent with the category preview modal.

**Commit:** `git commit -am "style: import conflict resolution modal (#125)"`

---

## Task 10: Version bump + docs

**Files:**
- `plugins/tagManager/tagManager.yml` — `version: 0.6.0`
- `plugins/tagManager/README.md` and `USERGUIDE.md` — document the resolution modal + each action.

**Commit:** `git commit -am "docs: document import conflict resolution; bump tagManager 0.6.0 (#125)"`

---

## Task 11: Deploy to stash-test + manual browser pass (handoff)

The browser/DOM flows can't be headlessly verified (stash-test web login isn't in `~/code/.env`). Steps:

1. Run all node suites: `cd plugins/tagManager && for f in tests/test_*.js; do node "$f"; done` → all `Failed: 0`.
2. Run pytest (regression guard, backend untouched): `python3 -m pytest tests/ -q` → 87 passed.
3. Deploy: `rsync -av --delete plugins/tagManager/ root@10.0.0.4:/mnt/nvme_cache/appdata/stash-test/config/plugins/tagManager/` then reload plugins in the stash-test UI.
4. Confirm the plugin loads with no console/traceback errors.
5. **User manual pass** (browser, `carrotwaxr` login on `http://10.0.0.4:6971`): construct an import that collides on (a) a name and (b) an alias; verify each action — merge-into-existing links the stash_id, strip-alias imports without the alias, open navigates, skip/skip-all clear, reverse-merge prompts + absorbs. Confirm the summary line shows the conflict/skip counts.

**After the manual pass passes:** use superpowers:finishing-a-development-branch to open the squash PR (target `main`, v0.6.0), then publish/verify the index serves `tagManager 0.6.0-<sha>`, clean up branch+worktree, and update `ROADMAP.md` + memory.
