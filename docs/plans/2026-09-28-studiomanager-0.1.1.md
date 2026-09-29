# studioManager 0.1.1 plan

Goal: ship studioManager 0.1.1, closing #166.
Approach:
- First a harness that loads the real JS, and pure helpers for the parent map, cycles and save order.
- Then the edit model: the view is derived from the original parents plus the pending changes, so removing one change never refetches. Saves are locked, ordered and keep their failures.
- Then lifecycle fixes: delegated events, one navigation listener, mount tokens, base-path-aware navigation, and a warning before leaving with unsaved changes.
Run with: /fluffer:plan-run

## Context every task needs

- **Worktree:** `/home/carrot/code/stash-plugins/.worktrees/studiomanager-0.1.1`, branch `fix/studiomanager-0.1.1`, based on `main` (2b306a8). Never touch the main checkout or other worktrees. **Never use `git stash`**, because the stack is shared with other sessions; compare with `git show HEAD:<path>` instead.
- **Plugin:** `plugins/studioManager`, UI only. It has `studio-manager.js` (1145 lines, one IIFE), `studio-manager.css` and `studioManager.yml` (v0.1.0, no settings, no backend). There are no tests yet.
- **Tests:** plain `node <file>` scripts under `plugins/studioManager/tests/`, loading the real file into a vm context. Copy and adapt `plugins/tagManager/tests/harness.js`, and add a `window.__STUDIO_MANAGER_TEST__` export block at the end of `studio-manager.js`, like `tag-manager.js` has. Write each test first and see it fail. Run all JS tests with `for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done` from the worktree root.
- **Ticket:** `gh issue view 166 --json body --jq .body`. Finding ids S1-S10 are in the "studioManager" section of `/home/carrot/code/stash-plugins/docs/AUDIT-2026-09.md`. That file is untracked in the main checkout, so read it by absolute path. JS line numbers in the audit still match; use the code map at the end of this plan.
- **Reference code in tagManager 0.7.0, which is on main:**
  - `plugins/tagManager/tag-manager.js`: `applyPendingChangesToTree` (~5271) rebuilds the tree from the original parents plus pending changes;
  - `savePendingChanges` (~5214-5268) keeps failed changes pending;
  - `stashPath`/`navigateTo` (~81-109) do base-path-aware in-app navigation.
- **Upstream facts:**
  - Stash's `studioUpdate` rejects a parent that would make a studio its own ancestor. Its check (`pkg/studio/validate.go` `validateParent`) has no visited set, so a request whose new parent's chain runs into an existing cycle recurses forever and can crash the server. The client must refuse those requests.
  - `child_ids` on `studioUpdate` is not in v0.31.1 and can't be used; the floor is v0.30.
- **Scope:** keyboard-only drag and drop (S7) and a rendering overhaul (S4) are out of scope.
- **CI:** `.github/workflows/test.yml` JS job. If main's loop is still `plugins/tagManager/tests/test_*.js` when Task 1 runs, generalize it to `plugins/*/tests/test_*.js`. Other release branches make the same change; whichever lands second rebases.

## Tasks

### Task 1: harness and pure helpers for parents, cycles and save order

Files: create `plugins/studioManager/tests/harness.js`, `plugins/studioManager/tests/test_real_file.js`, `plugins/studioManager/tests/test_model.js`; modify `plugins/studioManager/studio-manager.js` (new pure helpers, the test export block, and making the load-time side effects stub-friendly), `.github/workflows/test.yml`

Test first:
- `test_real_file.js`:
  - the file loads in the vm with stubbed `PluginApi` (`register.route`, `Event.addEventListener`, `React`), `document`, `MutationObserver`, timers and `location`;
  - the exports are reachable;
  - the static "no undefined calls" check passes. Copy tagManager's `test_real_file.js` approach.
- `test_model.js` covers these pure functions:
  - `effectiveParentMap(originalParentMap, pending)` returns a `Map` from studio id to parent id or null.
  - `wouldCreateCycle(studioId, newParentId, parentMap)` is true when `newParentId` is the studio itself or one of its descendants. It is also true when the walk up from `newParentId` meets an existing cycle; track visited ids, and never loop.
  - `findCycleMembers(studios)` returns the ids of studios on a parent cycle, plus their descendants.
  - `ancestorsOf(id, parentMap)` returns the ids from the parent up to the root, and stops at a cycle.
  - `buildStudioTree(studios)` covers:
    - an orphan whose parent is missing becomes a root;
    - studios caught in a cycle, and their descendants, come back as pseudo-roots, each cycle broken at its smallest id and flagged `inCycle: true`, so none disappear;
    - `totalStudios` equals the number of reachable nodes.
  - `orderForSave(pending, parentMap)` returns all `remove-parent` changes first, then `set-parent` changes ordered by the studio's depth in the final tree, shallowest first. Test a case where insertion order would make the server see a temporary cycle: moving A under B while B moves out from under A.

Run: `node plugins/studioManager/tests/test_real_file.js; node plugins/studioManager/tests/test_model.js`, which fail.

Change:
- Add the helpers, plus the export block.
- `wouldCreateCircularRef` becomes a thin wrapper over `wouldCreateCycle(…, effectiveParentMap(…))`.
- `buildStudioTree` uses the cycle handling.
- Update the CI loop.

Run: all JS tests, which pass.

Commit: `test(studioManager): harness plus pure helpers for parents, cycles and save order`

### Task 2: the edited view is derived; removing a change never refetches (S1)

Depends on Task 1.

Files: modify `plugins/studioManager/studio-manager.js` (`addPendingChange` 430-477, `removePendingChange` 482-495, `setParent` 622-652, `removeParent` 657-681, `renderChangesPanel` 500-560, `getTreeStats` 188-218, `enterEditMode` 414-425, and Cancel at 547-557); test `plugins/studioManager/tests/test_pending.js`

Test first:
- **Deriving:** `hierarchyStudios` stays the server state. The rendered tree and the stats come from `effectiveParentMap(originalParentMap, pendingChanges)`, and `child_studios` counts are recomputed from it.
- **Removing:** make changes A, B and C, then remove B. The tree shows A and C applied and B reverted, with no GraphQL request made (assert that `fetch` wasn't called).
- **Cancel:** Cancel restores the original tree locally, with no refetch.
- **No-op changes:** a change that returns a studio to its original parent removes its pending entry. Keep one of the two duplicate checks at 433-449 and 457-466.
- **Moves:** after a set-parent, every ancestor of the moved studio is expanded, so the moved node is visible.
- **Stats:** the stats bar and the context menu's `hasChildren` reflect pending changes.

Run: `node plugins/studioManager/tests/test_pending.js`, which fails.

Change: implement the above, following tagManager's `applyPendingChangesToTree` pattern.

Run: all JS tests, which pass.

Commit: `fix(studioManager): removing one pending change keeps the others`

### Task 3: locked, ordered saves that keep their failures (S2, S3)

Depends on Task 2.

Files: modify `plugins/studioManager/studio-manager.js` (`savePendingChanges` 565-602, `reloadHierarchy` 607-617, `updateStudioParent` 112-134, `showToast` 259-276, the changes panel), `plugins/studioManager/studio-manager.css` (`.sh-toast` `white-space: pre-line`, failed-change rows, the error list); test `plugins/studioManager/tests/test_save.js`

Test first:
- **Locking:** while a save runs, editing is locked. Drag and drop, the context menu, keyboard removal, the panel's × buttons and Cancel all do nothing, and the panel shows "Saving…".
- **Order:** saves run in `orderForSave` order. Assert the sequence of `studioUpdate` calls for the A/B swap case.
- **Cycles:** a change whose new parent's chain runs into an existing cycle is refused before any request. It stays pending with the error "Parent chain contains a cycle; fix that first".
- **Partial failure:** with 3 changes where the 2nd fails:
  - the other two are saved;
  - the failed one stays pending with its error shown on its row;
  - the data is refetched, `originalParentMap` is re-snapshotted from it, and the failed change is re-applied on top;
  - the toast says "2 saved, 1 failed", and its message keeps line breaks.
- **Success:** a full success clears the queue and says "3 saved". The count is taken before clearing.

Run: `node plugins/studioManager/tests/test_save.js`, which fails.

Change: implement the above, following tagManager's `savePendingChanges`.

Run: all JS tests, which pass.

Commit: `fix(studioManager): ordered saves that keep failed changes pending`

### Task 4: events, navigation and lifecycle (S5, S6, S8, S9, S10)

Depends on Task 3.

Files: modify `plugins/studioManager/studio-manager.js` (`attachHierarchyEventHandlers` 783-950, the context menu 281-357, `handleHierarchyKeyboard` 955-975, `StudioHierarchyPage` 980-1028, `injectNavButton` 1056-1112, the observer and timeouts 1117-1138, `setPageTitle` 27-33, the navigation sites 335, 338, 733 and 1106), `plugins/studioManager/studio-manager.css` (delete the dead rules: `.sh-highlighted`, `@keyframes sh-toast-out`, `.sh-no-changes`, `.sh-search-*`, `.sh-child-count-badge`, after a grep of the JS shows no use); test `plugins/studioManager/tests/test_lifecycle.js`

Test first:
- **Events:**
  - The page container has one delegated click, contextmenu and drag listener set per mount. Rendering 5 times doesn't add listeners; count them with the harness's `addEventListener` spy.
  - Closing the context menu with Escape or another right-click removes its document listener.
- **Navigation:**
  - The nav button and page detection use `PluginApi.Event.addEventListener("stash:location", ...)`, with no body-wide `MutationObserver`.
  - `/studios/` with a trailing slash also gets the button.
  - The studio link and the context menu's "Open" and "Edit" use tagManager's `stashPath`/`navigateTo` (base-path aware, in-app).
  - The nav button routes in-app.
- **Unsaved changes:**
  - With pending changes, a plugin navigation asks `confirm("You have N unsaved changes. Leave anyway?")`.
  - `beforeunload` is set while changes are pending, and cleared when there are none.
  - On remount with pending changes still in module state, they are re-applied to freshly fetched data, and a banner says "N unsaved changes restored". Changes that became no-ops are dropped.
- **Unmount:**
  - An unmount during the initial fetch (or a reload or save) doesn't render, toast or throw afterwards. Use a mount token.
  - The title timeouts are cleared on unmount.
- **Keyboard:**
  - Only Delete removes a parent; Backspace does nothing.
  - Nothing fires while focus is in `INPUT`, `TEXTAREA`, `SELECT` or a contenteditable.
  - `selectedStudioId` is cleared when that studio is no longer rendered as selected.

Run: `node plugins/studioManager/tests/test_lifecycle.js`, which fails.

Change: implement the above.

Run: all JS tests, which pass.

Commit: `fix(studioManager): delegated events, in-app navigation, no lost changes on leave`

### Task 5: docs, version 0.1.1

Depends on Tasks 1-4.

Files: modify `plugins/studioManager/studioManager.yml` (`version: 0.1.1`), `plugins/studioManager/README.md`, `CHANGELOG.md` (root, the Studio Manager section)

Check:
- **README:**
  - The Stash floor is v0.30.
  - How editing and saving work: pending changes, the save order, failed changes staying pending, and cycles refused.
  - Navigation, and the unsaved-changes warning.
  - Keyboard: Delete only.
  - Known limits: mouse-only drag and drop, large libraries render the whole tree, and studios in an existing cycle are shown flagged.
  - A v0.1.1 changelog entry.
- **Root CHANGELOG:** a `### 0.1.1` entry replacing "No release this cycle yet".
- No em-dash characters.

Run: `python .github/scripts/lint_manifests.py`, all JS tests, and a relative-link check over the READMEs (inline script, no network). All pass.

Commit: `docs(studioManager): 0.1.1 docs and changelog`

### Task 6: exercise on stash-test

Depends on Task 5.

Files: none changed.

Check:
- Snapshot stash-test's studio parents first: `allStudios { id parent_studio { id } }`, saved to a JSON file in `/tmp`.
- Deploy with rsync, excluding `tests`, then `reloadPlugins`. The key is `STASH_TEST_API_KEY` in `/home/carrot/code/stash-plugins/.env`; never print it.
- Browser pass on `/plugins/studio-hierarchy` (the user logs in if needed):
  - make 3 changes and remove the middle one; the others stay visible;
  - save; the order holds and the tree is right after the refetch;
  - navigate away with a pending change; the confirm appears;
  - after 5 visits, one set of listeners remains, checked with a console counter from the test hook.
- Restore every studio's parent from the snapshot, then diff against it. It must match exactly.

Commit: none.

## Code map

This was mapped on 2026-09-28 against main 2b306a8; line numbers are approximate.

- **`studio-manager.js` (1145 lines)**
  - **Constants and state:**
    - `PLUGIN_ID` and `HIERARCHY_ROUTE_PATH` (`/plugins/studio-hierarchy`) are at 4-5.
    - State is at 8-22: `hierarchyStudios` (changed in place today), `hierarchyTree`, `hierarchyStats`, `expandedNodes`, `showImages`, `selectedStudioId`, `pendingChanges`, `isEditMode`, `originalParentMap`, `draggedStudioId` and `contextMenuStudioId`.
  - **Helpers and data:**
    - `setPageTitle` 27-33 uses timeouts at 50, 200 and 500ms.
    - `escapeHtml` 38-46; the GraphQL helpers 51-77 keep only the first error.
    - `fetchAllStudiosWithHierarchy` 82-107 uses `per_page: -1`; `updateStudioParent` 112-134.
  - **Tree:**
    - `buildStudioTree` 140-183 drops studios in a cycle.
    - `getTreeStats` 188-218 uses the server's `child_studios`.
    - `wouldCreateCircularRef` 223-254 reads globals and returns false when it meets a cycle (248).
  - **UI pieces:**
    - `showToast` 259-276 sets `textContent`, and its fade isn't animated.
    - The context menu 281-357 navigates at 335 and 338, and adds a `{once:true}` listener at 354-356.
    - Expand and collapse 362-409.
  - **Pending changes:**
    - `enterEditMode` 414-425.
    - `addPendingChange` 430-477 has a duplicate no-op check (433-449 and 457-466).
    - `removePendingChange` 482-495 calls `reloadHierarchy`, which refetches (494).
    - `renderChangesPanel` 500-560: the panel sits on `document.body`, its handlers are rebound on every render, and Cancel refetches (547-557).
    - `savePendingChanges` 565-602 is serial over the live array (576) and clears everything at 592.
    - `reloadHierarchy` 607-617.
    - `setParent` 622-652 and `removeParent` 657-681 change `parent_studio` in place, and don't update `child_studios` or the stats.
  - **Rendering:**
    - `renderTreeNode` 686-740 has an absolute `href="/studios/${id}"` at 733.
    - `renderHierarchyPage` 745-778 rebuilds the full `innerHTML`.
    - `attachHierarchyEventHandlers` 783-950 adds 7 listeners per node (839-915), and the container click listener at 942-949 piles up on every render.
    - `handleHierarchyKeyboard` 955-975 accepts Backspace at 959 and only guards `INPUT`/`TEXTAREA`.
  - **Mount and route:**
    - `StudioHierarchyPage` 980-1028: its `useEffect` spans 984-1022, `init` 987-1011 (after its `await`, `containerRef.current` may be null), and cleanup 1015-1021.
    - `registerRoute` 1033-1036.
  - **Nav button:** `injectNavButton` 1056-1112 checks `endsWith('/studios')` at 1058 and navigates at 1106.
  - **Global side effects:** a body `MutationObserver` 1117-1138 that is never disconnected, and the load-time run at 1141-1142.
- **`studio-manager.css`:**
  - `.sh-toast` (358-366) needs `pre-line`; the changes panel is `position: sticky` (404-413).
  - Dead rules: `.sh-highlighted` 296-299, `@keyframes sh-toast-out` 389-398, `.sh-no-changes` 457-461, `.sh-search-*` 469-562 and `.sh-child-count-badge` 564-576.
- **README:** it claims Stash v0.28+ (the floor is v0.30), says "Delete key" (Backspace also works today), and has nothing on saves, navigation or limits.
