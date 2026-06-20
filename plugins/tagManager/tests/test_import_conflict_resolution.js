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

// ---- Runner (keep at bottom) ----
for (const [name, fn] of tests) {
  try { fn(); passed++; console.log(`  ✓ ${name}`); }
  catch (e) { failed++; console.log(`  ✗ ${name}\n    ${e.message}`); }
}
console.log(`\nPassed: ${passed}  Failed: ${failed}`);
process.exit(failed ? 1 : 0);
