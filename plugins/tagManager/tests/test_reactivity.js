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

console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
