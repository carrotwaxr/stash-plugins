/**
 * Unit tests for category parent resolution during import.
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

let categoryMappings = {};

// Copy of findLocalParentMatches (same as in tag-manager.js)
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

// --- Function under test ---
function resolveCategoryParents(selectedIds) {
  const result = {};

  for (const stashdbId of selectedIds) {
    const tag = stashdbTags.find(t => t.id === stashdbId);
    if (!tag?.category) continue;

    const catName = tag.category.name;
    if (result[catName]) continue; // Already resolved

    // 1. Check saved mapping
    const savedId = categoryMappings[catName];
    if (savedId) {
      const savedTag = localTags.find(t => t.id === savedId);
      if (savedTag) {
        result[catName] = {
          parentTagId: savedTag.id,
          parentTagName: savedTag.name,
          resolution: 'saved',
          description: tag.category.description || '',
        };
        continue;
      }
    }

    // 2. Exact name match from local tags
    const matches = findLocalParentMatches(catName);
    const exactMatch = matches.find(m => m.matchType === 'exact');
    if (exactMatch) {
      result[catName] = {
        parentTagId: exactMatch.tag.id,
        parentTagName: exactMatch.tag.name,
        resolution: 'exact',
        description: tag.category.description || '',
      };
      continue;
    }

    // 3. Will create new
    result[catName] = {
      parentTagId: null,
      parentTagName: catName,
      resolution: 'create',
      description: tag.category.description || '',
    };
  }

  return result;
}

// --- Tests ---
console.log('\n=== resolveCategoryParents tests ===\n');

test('returns empty for tags with no categories', () => {
  const result = resolveCategoryParents(['s4']);
  assertEqual(result, {});
});

test('resolves existing local tag by exact name', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('flags create for category with no local match', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].parentTagId, null);
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('uses saved mapping when available', () => {
  categoryMappings = { 'Action': '20' }; // Override to Clothing tag
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '20');
  assertEqual(result['Action'].resolution, 'saved');
});

test('falls back to match if saved mapping points to deleted tag', () => {
  categoryMappings = { 'Action': '999' }; // Non-existent
  const result = resolveCategoryParents(['s1']);
  assertEqual(result['Action'].parentTagId, '10');
  assertEqual(result['Action'].resolution, 'exact');
});

test('deduplicates categories across multiple tags', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1', 's5']); // Both are Action
  assertEqual(Object.keys(result).length, 1);
  assertEqual(result['Action'].parentTagId, '10');
});

test('resolves multiple categories independently', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s1', 's2', 's3']);
  assertEqual(Object.keys(result).length, 3);
  assertEqual(result['Action'].resolution, 'exact');
  assertEqual(result['Accessories'].resolution, 'create');
  assertEqual(result['Clothing'].resolution, 'exact');
  assertEqual(result['Clothing'].parentTagId, '20');
});

test('carries category description for create entries', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s2']);
  assertEqual(result['Accessories'].description, 'Wearable accessories');
});

test('skips tags with null category', () => {
  categoryMappings = {};
  const result = resolveCategoryParents(['s4', 's1']);
  assertEqual(Object.keys(result).length, 1); // Only Action
});

// --- Summary ---
console.log(`\n=== Summary ===\n`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) process.exit(1);
