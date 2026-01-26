/**
 * Unit tests for browse search functions.
 * Run with: node plugins/tagManager/tests/test_browse_search.js
 */

// Copy of filterTagsBySearch for testing
let stashdbTags = [];

function filterTagsBySearch(query) {
  if (!query || !stashdbTags) return [];
  const lowerQuery = query.toLowerCase().trim();
  if (!lowerQuery) return [];

  return stashdbTags.filter(tag => {
    // Check tag name
    if (tag.name.toLowerCase().includes(lowerQuery)) return true;
    // Check aliases
    if (tag.aliases?.some(alias => alias.toLowerCase().includes(lowerQuery))) return true;
    return false;
  });
}

// Test runner
let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`✓ ${name}`);
    passed++;
  } catch (e) {
    console.log(`✗ ${name}`);
    console.log(`  Error: ${e.message}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// Sample test data
const sampleTags = [
  { id: '1', name: 'Blonde', aliases: ['Blond', 'Fair Hair'], category: { name: 'Hair Color' } },
  { id: '2', name: 'Brunette', aliases: ['Brown Hair'], category: { name: 'Hair Color' } },
  { id: '3', name: 'MILF', aliases: ['Mother', 'Mom'], category: { name: 'Age' } },
  { id: '4', name: 'Teen', aliases: ['Young', '18+'], category: { name: 'Age' } },
  { id: '5', name: 'Outdoor', aliases: [], category: { name: 'Location' } },
  { id: '6', name: 'Indoor', aliases: null, category: { name: 'Location' } },
  { id: '7', name: 'POV', aliases: ['Point of View'], category: null },
];

// Tests
console.log('\n=== filterTagsBySearch tests ===\n');

console.log('--- Empty/Invalid Queries ---\n');

test('returns empty array for null query', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch(null), []);
});

test('returns empty array for undefined query', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch(undefined), []);
});

test('returns empty array for empty string', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch(''), []);
});

test('returns empty array for whitespace-only query', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch('   '), []);
  assertEqual(filterTagsBySearch('\t\n'), []);
});

test('returns empty array when stashdbTags is null', () => {
  stashdbTags = null;
  assertEqual(filterTagsBySearch('test'), []);
});

test('returns empty array when stashdbTags is empty', () => {
  stashdbTags = [];
  assertEqual(filterTagsBySearch('test'), []);
});

console.log('\n--- Name Matching ---\n');

test('finds tag by exact name match', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Blonde');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'Blonde');
});

test('finds tag by partial name match', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Blon');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'Blonde');
});

test('name matching is case-insensitive', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch('blonde').length, 1);
  assertEqual(filterTagsBySearch('BLONDE').length, 1);
  assertEqual(filterTagsBySearch('BlOnDe').length, 1);
});

test('finds multiple tags matching query', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('door');
  assertEqual(results.length, 2);
  const names = results.map(t => t.name).sort();
  assertEqual(names, ['Indoor', 'Outdoor']);
});

console.log('\n--- Alias Matching ---\n');

test('finds tag by exact alias match', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Mother');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'MILF');
});

test('finds tag by partial alias match', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Moth');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'MILF');
});

test('alias matching is case-insensitive', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch('mother').length, 1);
  assertEqual(filterTagsBySearch('MOTHER').length, 1);
  assertEqual(filterTagsBySearch('MoThEr').length, 1);
});

test('handles tags with empty aliases array', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Outdoor');
  assertEqual(results.length, 1);
  assertEqual(results[0].aliases, []);
});

test('handles tags with null aliases', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('Indoor');
  assertEqual(results.length, 1);
  assertEqual(results[0].aliases, null);
});

console.log('\n--- Combined Name and Alias Matching ---\n');

test('finds tags matching either name or alias', () => {
  stashdbTags = sampleTags;
  // "Hair" matches Brunette alias "Brown Hair" and Blonde alias "Fair Hair"
  const results = filterTagsBySearch('Hair');
  assertEqual(results.length, 2);
});

test('query trims whitespace', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('  Blonde  ');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'Blonde');
});

console.log('\n--- No Matches ---\n');

test('returns empty array when no tags match', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch('XYZ123'), []);
});

test('returns empty array for query not in any name or alias', () => {
  stashdbTags = sampleTags;
  assertEqual(filterTagsBySearch('Redhead'), []);
});

console.log('\n--- Edge Cases ---\n');

test('handles special characters in query', () => {
  stashdbTags = sampleTags;
  // "18+" is an alias for Teen
  const results = filterTagsBySearch('18+');
  assertEqual(results.length, 1);
  assertEqual(results[0].name, 'Teen');
});

test('handles tags without category', () => {
  stashdbTags = sampleTags;
  const results = filterTagsBySearch('POV');
  assertEqual(results.length, 1);
  assertEqual(results[0].category, null);
});

test('single character query works', () => {
  stashdbTags = sampleTags;
  // 'O' appears in Blonde, Outdoor, Indoor, POV, and aliases
  const results = filterTagsBySearch('O');
  // Should match: Blonde, Outdoor, Indoor, POV, and tags with 'o' in aliases
  assertEqual(results.length > 0, true);
});

// Summary
console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
