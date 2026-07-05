/**
 * Unit tests for the #124 refresh reconciliation helper.
 * MIRROR of reconcileSelections from tag-manager.js (browser IIFE, no build step).
 *
 * Run with: node plugins/tagManager/tests/test_reactivity.js
 */

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); console.log(`✓ ${name}`); passed++; }
  catch (e) { console.log(`✗ ${name}\n  Error: ${e.message}`); failed++; }
}
function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// ---- MIRROR of tag-manager.js: reconcileSelections ----
function reconcileSelections(prevSelectedIds, freshTags) {
  const ids = new Set(freshTags.map((t) => t.id));
  const next = new Set();
  for (const id of prevSelectedIds) {
    if (ids.has(id)) next.add(id);
  }
  return next;
}

// ---- MIRROR of tag-manager.js: reconcileImportSelection (#125) ----
// The browse import selection holds StashDB tag ids (uuids) — a different id space
// than local Stash tags (integers). refreshLocalTags() must reconcile it against
// the loaded StashDB set, NOT localTags: reconciling against localTags drops every
// selection (a uuid never equals a local integer id) and silently empties an
// in-progress import. A null cache (not loaded yet) leaves the selection untouched.
function reconcileImportSelection(selectedForImport, stashdbTags) {
  if (!stashdbTags) return new Set(selectedForImport);
  return reconcileSelections(selectedForImport, stashdbTags);
}

console.log('\n=== reconcileSelections tests ===\n');

test('drops ids that no longer exist (merged away)', () => {
  const result = reconcileSelections(new Set(['1', '2', '3']), [{ id: '1' }, { id: '3' }]);
  assertEqual([...result].sort(), ['1', '3']);
});

test('keeps all when every selected tag still exists', () => {
  const result = reconcileSelections(new Set(['1', '2']), [{ id: '1' }, { id: '2' }, { id: '9' }]);
  assertEqual([...result].sort(), ['1', '2']);
});

test('empty selection stays empty', () => {
  const result = reconcileSelections(new Set(), [{ id: '1' }]);
  assertEqual([...result], []);
});

test('all dropped when none survive', () => {
  const result = reconcileSelections(new Set(['7', '8']), [{ id: '1' }]);
  assertEqual([...result], []);
});

console.log('\n=== reconcileImportSelection (#125 regression) tests ===\n');

test('preserves a StashDB import selection across a local-tag refresh', () => {
  // selection = StashDB uuids; only the StashDB cache knows them (localTags never will)
  const selection = new Set(['6cd8-uuid', 'f391-uuid', '49d1-uuid']);
  const stashdbTags = [{ id: '6cd8-uuid' }, { id: 'f391-uuid' }, { id: '49d1-uuid' }, { id: 'x-uuid' }];
  const result = reconcileImportSelection(selection, stashdbTags);
  assertEqual([...result].sort(), ['49d1-uuid', '6cd8-uuid', 'f391-uuid']);
});

test('leaves the selection untouched when the StashDB cache is not loaded (null)', () => {
  const result = reconcileImportSelection(new Set(['6cd8-uuid']), null);
  assertEqual([...result], ['6cd8-uuid']);
});

test('drops only a selection whose StashDB tag vanished from the cache', () => {
  const result = reconcileImportSelection(new Set(['6cd8-uuid', 'gone-uuid']), [{ id: '6cd8-uuid' }]);
  assertEqual([...result], ['6cd8-uuid']);
});

console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
