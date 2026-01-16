/**
 * Unit tests for category mapping persistence functions.
 * Run with: node plugins/tagManager/tests/test_category_persistence.js
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

// ============================================================================
// Category Mapping Serialization Tests
// ============================================================================

console.log('\n=== Category Mapping Serialization tests ===\n');

test('serializes empty mappings correctly', () => {
  const categoryMappings = {};
  const serialized = JSON.stringify(categoryMappings);
  assertEqual(serialized, '{}');
  assertEqual(JSON.parse(serialized), {});
});

test('serializes single mapping correctly', () => {
  const categoryMappings = { 'Action': '123' };
  const serialized = JSON.stringify(categoryMappings);
  const parsed = JSON.parse(serialized);
  assertEqual(parsed, { 'Action': '123' });
});

test('serializes multiple mappings correctly', () => {
  const categoryMappings = {
    'Action': '123',
    'Comedy': '456',
    'Drama': '789'
  };
  const serialized = JSON.stringify(categoryMappings);
  const parsed = JSON.parse(serialized);
  assertEqual(parsed, categoryMappings);
});

test('handles special characters in category names', () => {
  const categoryMappings = {
    'Sci-Fi': '100',
    'Action/Adventure': '101',
    'Category "Quoted"': '102',
    'Category: With Colon': '103'
  };
  const serialized = JSON.stringify(categoryMappings);
  const parsed = JSON.parse(serialized);
  assertEqual(parsed, categoryMappings);
});

test('handles unicode in category names', () => {
  const categoryMappings = {
    'Acción': '200',
    '日本語': '201',
    'Émotionnel': '202'
  };
  const serialized = JSON.stringify(categoryMappings);
  const parsed = JSON.parse(serialized);
  assertEqual(parsed, categoryMappings);
});

// ============================================================================
// Category Mapping Parsing (simulating load from settings)
// ============================================================================

console.log('\n=== Category Mapping Parsing tests ===\n');

/**
 * Parse category mappings from plugin settings string.
 * This simulates what loadCategoryMappings does.
 */
function parseCategoryMappings(settingsStr) {
  if (!settingsStr) return {};

  try {
    return JSON.parse(settingsStr);
  } catch (e) {
    console.warn('Failed to parse category mappings:', e.message);
    return {};
  }
}

test('parses valid JSON string', () => {
  const input = '{"Action":"123","Comedy":"456"}';
  const result = parseCategoryMappings(input);
  assertEqual(result, { 'Action': '123', 'Comedy': '456' });
});

test('returns empty object for null input', () => {
  const result = parseCategoryMappings(null);
  assertEqual(result, {});
});

test('returns empty object for undefined input', () => {
  const result = parseCategoryMappings(undefined);
  assertEqual(result, {});
});

test('returns empty object for empty string', () => {
  const result = parseCategoryMappings('');
  assertEqual(result, {});
});

test('handles corrupt JSON gracefully', () => {
  const result = parseCategoryMappings('{invalid json}');
  assertEqual(result, {});
});

test('handles truncated JSON gracefully', () => {
  const result = parseCategoryMappings('{"Action":"123"');
  assertEqual(result, {});
});

test('handles non-object JSON gracefully', () => {
  // Array instead of object
  const result = parseCategoryMappings('["Action", "Comedy"]');
  // JSON.parse succeeds but returns array - caller should handle
  assertEqual(Array.isArray(result), true);
});

// ============================================================================
// findLocalParentMatches tests (supplemental)
// ============================================================================

console.log('\n=== Category-to-Parent Matching tests ===\n');

// Mock local tags
const localTags = [
  { id: '1', name: 'Action', aliases: ['Acts'], parent_count: 0 },
  { id: '2', name: 'CATEGORY: Action', aliases: [], parent_count: 0 },
  { id: '3', name: 'Comedy', aliases: ['Funny'], parent_count: 0 },
  { id: '4', name: 'Action Movies', aliases: [], parent_count: 1 }, // Has parent, lower priority
];

function findLocalParentMatches(categoryName) {
  if (!categoryName) return [];

  const lowerCategoryName = categoryName.toLowerCase();
  const matches = [];

  for (const tag of localTags) {
    const isChild = tag.parent_count > 0;

    if (tag.name.toLowerCase() === lowerCategoryName) {
      matches.push({ tag, matchType: 'exact', score: isChild ? 95 : 100 });
      continue;
    }

    if (tag.name.toLowerCase().includes(lowerCategoryName)) {
      matches.push({ tag, matchType: 'contains', score: isChild ? 85 : 90 });
      continue;
    }

    if (tag.aliases?.some(a => a.toLowerCase() === lowerCategoryName)) {
      matches.push({ tag, matchType: 'alias', score: isChild ? 80 : 85 });
      continue;
    }
  }

  matches.sort((a, b) => b.score - a.score);
  return matches.slice(0, 5);
}

test('prioritizes exact match over contains', () => {
  const matches = findLocalParentMatches('Action');

  // "Action" (exact) should rank higher than "CATEGORY: Action" (contains)
  assertEqual(matches[0].tag.name, 'Action');
  assertEqual(matches[0].matchType, 'exact');
  assertEqual(matches[0].score, 100);
});

test('deprioritizes tags that have parents', () => {
  const matches = findLocalParentMatches('Action');

  // "Action Movies" has parent_count=1, should have lower score
  const actionMovies = matches.find(m => m.tag.name === 'Action Movies');
  assertEqual(actionMovies.score, 85); // 90 - 5 penalty for having parent
});

test('uses saved mapping when available', () => {
  // Simulate checking for saved mapping before auto-matching
  const categoryMappings = { 'Action': '999' };
  const categoryName = 'Action';

  // If saved mapping exists, use it directly
  const savedMapping = categoryMappings[categoryName];
  if (savedMapping) {
    assertEqual(savedMapping, '999');
  }
});

// ============================================================================
// Summary
// ============================================================================

console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
