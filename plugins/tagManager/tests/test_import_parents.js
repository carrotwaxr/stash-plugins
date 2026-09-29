/**
 * Unit tests for category parent resolution during import, against the REAL
 * tag-manager.js.
 * Run with: node plugins/tagManager/tests/test_import_parents.js
 */

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`\u2713 ${name}`);
    passed++;
  } catch (e) {
    console.log(`\u2717 ${name}`);
    console.log(`  Error: ${e.message}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// --- Mock data ---
const localTags = [
  { id: '10', name: 'Action', aliases: [], parent_count: 0, description: '' },
  { id: '20', name: 'Clothing', aliases: ['Apparel'], parent_count: 0, description: 'Existing desc' },
  { id: '30', name: 'Some Child', aliases: [], parent_count: 1, description: '' },
];

const stashdbTags = [
  { id: 's1', name: 'Anal', category: { id: 'c1', name: 'Action', group: 'ACTION', description: 'Action category' } },
  { id: 's2', name: 'Blindfold', category: { id: 'c2', name: 'Accessories', group: 'ACTION', description: 'Wearable accessories' } },
  { id: 's3', name: 'Skirt', category: { id: 'c3', name: 'Clothing', group: 'SCENE', description: 'Clothing items' } },
  { id: 's4', name: 'No Category Tag', category: null },
  { id: 's5', name: 'Oral', category: { id: 'c1', name: 'Action', group: 'ACTION', description: 'Action category' } },
];

// The REAL resolveCategoryParents / findLocalParentMatches (F20: mappings are
// per endpoint, read for the selected stash-box).
const { loadTagManager } = require('./harness');
const EP = 'https://stashdb.org/graphql';
const tm = loadTagManager({});
tm.setState({ localTags, stashdbTags, selectedStashBox: { endpoint: EP, name: 'StashDB' }, categoryMappings: {} });

/** Set this endpoint's saved mappings ({ category: localTagId }). */
function setMappings(map) {
  tm.setState({ categoryMappings: Object.keys(map).length ? { [EP]: { ...map } } : {} });
}

function resolveCategoryParents(selectedIds) {
  return tm.exports.resolveCategoryParents(selectedIds);
}

// --- Tests ---
console.log('\n=== resolveCategoryParents tests ===\n');

test('returns empty for tags with no categories', () => {
  const result = resolveCategoryParents(['s4']);
  assertEqual(result, {});
});

test('resolves existing local tag by exact name', () => {
  setMappings({});
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('flags create for category with no local match', () => {
  setMappings({});
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].parentTagId, null);
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('uses saved mapping when available', () => {
  setMappings({ 'Action': '20' }); // Override to Clothing tag
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '20');
  assertEqual(result['Action'].resolution, 'saved');
});

test('falls back to match if saved mapping points to deleted tag', () => {
  setMappings({ 'Action': '999' }); // Non-existent
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('deduplicates categories across multiple tags', () => {
  setMappings({});
  const result = resolveCategoryParents(['s1', 's5']); // Both are Action
  assertEqual(Object.keys(result).length, 1);
  assertEqual(result['Action'].parentTagId, '10');
});

test('resolves multiple categories independently', () => {
  setMappings({});
  const result = resolveCategoryParents(['s1', 's2', 's3']);
  assertEqual(Object.keys(result).length, 3);
  assertEqual(result['Action'].resolution, 'exact');
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Clothing'].resolution, 'exact');
  assertEqual(result['Clothing'].parentTagId, '20');
});

test('carries category description for create entries', () => {
  setMappings({});
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('skips tags with null category', () => {
  setMappings({});
  const result = resolveCategoryParents(['s4', 's1']);
  assertEqual(Object.keys(result).length, 1); // Only Action
});

// --- Edge case tests ---
console.log('\n=== Edge case tests ===\n');

test('handles empty selection', () => {
  const result = resolveCategoryParents([]);
  assertEqual(result, {});
});

test('handles selection of only uncategorized tags', () => {
  const result = resolveCategoryParents(['s4']);
  assertEqual(result, {});
});

test('saved mapping takes priority over exact match', () => {
  setMappings({ 'Action': '20' }); // Mapped to Clothing instead of Action
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '20');
  assertEqual(result['Action'].parentTagName, 'Clothing');
  assertEqual(result['Action'].resolution, 'saved');
});

test('handles mixed categorized and uncategorized tags', () => {
  setMappings({});
  const result = resolveCategoryParents(['s1', 's4', 's2']);
  assertEqual(Object.keys(result).length, 2); // Action + Accessories, not s4
  assertEqual(result['Action'] !== undefined, true);
  assertEqual(result['Accessories'] !== undefined, true);
});

// --- Summary ---
console.log(`\n=== Summary ===\n`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
