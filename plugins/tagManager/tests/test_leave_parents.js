/**
 * Unit tests for the #126 leaveParentTagsAlone gate.
 * MIRROR of shouldResolveParents from tag-manager.js.
 *
 * Run with: node plugins/tagManager/tests/test_leave_parents.js
 */

let passed = 0;
let failed = 0;
function test(name, fn) {
  try { fn(); console.log(`✓ ${name}`); passed++; }
  catch (e) { console.log(`✗ ${name}\n  Error: ${e.message}`); failed++; }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

// ---- MIRROR of tag-manager.js: shouldResolveParents ----
function shouldResolveParents(settings) {
  return !settings.leaveParentTagsAlone;
}

console.log('\n=== shouldResolveParents tests ===\n');

test('resolves parents when setting is off', () => {
  assert(shouldResolveParents({ leaveParentTagsAlone: false }) === true);
});

test('resolves parents when setting is undefined (default)', () => {
  assert(shouldResolveParents({}) === true);
});

test('skips parents when setting is on', () => {
  assert(shouldResolveParents({ leaveParentTagsAlone: true }) === false);
});

console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
