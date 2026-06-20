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

// ---- Mirrored helpers (keep byte-identical to tag-manager.js; localTags as param) ----

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

// ---- Tests ----

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

// ---- Runner (keep at bottom) ----
for (const [name, fn] of tests) {
  try { fn(); passed++; console.log(`  ✓ ${name}`); }
  catch (e) { failed++; console.log(`  ✗ ${name}\n    ${e.message}`); }
}
console.log(`\nPassed: ${passed}  Failed: ${failed}`);
process.exit(failed ? 1 : 0);
