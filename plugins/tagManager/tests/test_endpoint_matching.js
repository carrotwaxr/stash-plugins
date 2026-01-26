/**
 * Unit tests for endpoint-aware tag matching functions.
 * Run with: node plugins/tagManager/tests/test_endpoint_matching.js
 */

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

// Copy of function under test (will be updated after implementation)
function hasStashIdForEndpoint(tag, endpoint) {
  if (!tag || !endpoint) return false;
  return tag.stash_ids?.some(sid => sid.endpoint === endpoint) ?? false;
}

// Sample test data
const sampleTags = [
  {
    id: '1',
    name: 'Blonde',
    stash_ids: [
      { endpoint: 'https://stashdb.org/graphql', stash_id: 'abc123' }
    ]
  },
  {
    id: '2',
    name: 'Brunette',
    stash_ids: [
      { endpoint: 'https://stashdb.org/graphql', stash_id: 'def456' },
      { endpoint: 'https://pmvstash.org/graphql', stash_id: 'ghi789' }
    ]
  },
  {
    id: '3',
    name: 'MILF',
    stash_ids: []
  },
  {
    id: '4',
    name: 'Teen',
    stash_ids: null
  },
  {
    id: '5',
    name: 'Outdoor'
    // no stash_ids property at all
  },
];

// Tests
console.log('\n=== hasStashIdForEndpoint tests ===\n');

console.log('--- Basic Matching ---\n');

test('returns true when tag has stash_id for endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], 'https://stashdb.org/graphql'), true);
});

test('returns false when tag lacks stash_id for endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], 'https://pmvstash.org/graphql'), false);
});

test('returns true for tag with multiple stash_ids (matching endpoint)', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[1], 'https://stashdb.org/graphql'), true);
  assertEqual(hasStashIdForEndpoint(sampleTags[1], 'https://pmvstash.org/graphql'), true);
});

console.log('\n--- Edge Cases ---\n');

test('returns false for empty stash_ids array', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[2], 'https://stashdb.org/graphql'), false);
});

test('returns false for null stash_ids', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[3], 'https://stashdb.org/graphql'), false);
});

test('returns false for missing stash_ids property', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[4], 'https://stashdb.org/graphql'), false);
});

test('returns false for null tag', () => {
  assertEqual(hasStashIdForEndpoint(null, 'https://stashdb.org/graphql'), false);
});

test('returns false for null endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], null), false);
});

test('returns false for undefined endpoint', () => {
  assertEqual(hasStashIdForEndpoint(sampleTags[0], undefined), false);
});

// Test getFilteredTags behavior
console.log('\n=== getFilteredTags endpoint-aware tests ===\n');

// Simulated state
let localTags = [];
let selectedStashBox = null;
let currentFilter = 'unmatched';

function getFilteredTags() {
  const endpoint = selectedStashBox?.endpoint;

  const hasEndpointMatch = (tag) => hasStashIdForEndpoint(tag, endpoint);

  const unmatchedTags = localTags.filter(t => !hasEndpointMatch(t));
  const matchedTags = localTags.filter(t => hasEndpointMatch(t));

  switch (currentFilter) {
    case 'matched':
      return { filtered: matchedTags, unmatched: unmatchedTags, matched: matchedTags };
    case 'all':
      return { filtered: localTags, unmatched: unmatchedTags, matched: matchedTags };
    default: // 'unmatched'
      return { filtered: unmatchedTags, unmatched: unmatchedTags, matched: matchedTags };
  }
}

test('unmatched filter shows tags without stash_id for selected endpoint', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // Tags 3, 4, 5 have no StashDB stash_id
  assertEqual(result.filtered.length, 3);
  assertEqual(result.unmatched.length, 3);
  assertEqual(result.matched.length, 2);
});

test('unmatched filter is endpoint-specific', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://pmvstash.org/graphql' };
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // Only tag 2 (Brunette) has PMVstash stash_id
  // So unmatched should be tags 1, 3, 4, 5
  assertEqual(result.filtered.length, 4);
  assertEqual(result.matched.length, 1);
});

test('matched filter shows tags with stash_id for selected endpoint', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'matched';

  const result = getFilteredTags();
  // Tags 1 and 2 have StashDB stash_id
  assertEqual(result.filtered.length, 2);
});

test('all filter shows all tags', () => {
  localTags = sampleTags;
  selectedStashBox = { endpoint: 'https://stashdb.org/graphql' };
  currentFilter = 'all';

  const result = getFilteredTags();
  assertEqual(result.filtered.length, 5);
});

test('handles null selectedStashBox gracefully', () => {
  localTags = sampleTags;
  selectedStashBox = null;
  currentFilter = 'unmatched';

  const result = getFilteredTags();
  // With no endpoint, all tags should be "unmatched"
  assertEqual(result.filtered.length, 5);
  assertEqual(result.matched.length, 0);
});

// Summary
console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
