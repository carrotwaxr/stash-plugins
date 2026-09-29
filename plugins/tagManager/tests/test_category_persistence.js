/**
 * Unit tests for category mapping persistence functions.
 * The serialization/parsing/saved-mapping cases run against the REAL
 * tag-manager.js (harness) and its per-endpoint shape (F20):
 *   { endpoint: { categoryName: localTagId } }
 * Run with: node plugins/tagManager/tests/test_category_persistence.js
 */
const { loadTagManager } = require('./harness');

const EP = 'https://stashdb.org/graphql';
const TPDB = 'https://theporndb.net/graphql';

// Test runner (tests run in order; async ones are awaited)
let passed = 0;
let failed = 0;
const queue = [];

function test(name, fn) {
  queue.push([name, fn]);
}

function heading(title) {
  queue.push([title, null]);
}

function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

/** Real plugin instance whose stored plugin config is `config` (mutated by saves). */
async function pluginWithConfig(config) {
  const store = { config: { ...config } };
  const tm = loadTagManager({
    fetchResponses: {
      Configuration: () => ({ data: { configuration: { plugins: { tagManager: store.config } } } }),
      ConfigurePlugin: (body) => { store.config = body.variables.input; return { data: { configurePlugin: store.config } }; },
    },
  });
  await tm.settle();
  return { tm, store };
}

/** Load `stored` (the raw plugin setting) through the real loadCategoryMappings. */
async function loadStored(stored) {
  const config = stored === undefined ? {} : { categoryMappings: stored };
  const { tm } = await pluginWithConfig(config);
  await tm.exports.loadCategoryMappings();
  await tm.settle();
  return tm.getState().categoryMappings;
}

/** Save `mappings` per endpoint with the real code, then load them in a fresh instance. */
async function roundTrip(mappings) {
  const a = await pluginWithConfig({});
  for (const [endpoint, map] of Object.entries(mappings)) {
    for (const [category, id] of Object.entries(map)) a.tm.exports.setCategoryMapping(endpoint, category, id);
  }
  const ok = await a.tm.exports.saveCategoryMappings();
  if (!ok) throw new Error('save failed');
  const b = await pluginWithConfig(a.store.config);
  await b.tm.exports.loadCategoryMappings();
  return { stored: a.store.config.categoryMappings, loaded: b.tm.getState().categoryMappings };
}

// ============================================================================
// Category Mapping Serialization Tests
// ============================================================================

heading('Category Mapping Serialization tests');

test('serializes empty mappings correctly', async () => {
  const { tm, store } = await pluginWithConfig({});
  await tm.exports.saveCategoryMappings();
  assertEqual(store.config.categoryMappings, '{}');
});

test('serializes single mapping correctly', async () => {
  const { stored, loaded } = await roundTrip({ [EP]: { 'Action': '123' } });
  assertEqual(JSON.parse(stored), { [EP]: { 'Action': '123' } });
  assertEqual(loaded, { [EP]: { 'Action': '123' } });
});

test('serializes multiple mappings and endpoints correctly', async () => {
  const mappings = {
    [EP]: { 'Action': '123', 'Comedy': '456', 'Drama': '789' },
    [TPDB]: { 'Action': '321' },
  };
  const { loaded } = await roundTrip(mappings);
  assertEqual(loaded, mappings);
});

test('handles special characters in category names', async () => {
  const mappings = {
    [EP]: {
      'Sci-Fi': '100',
      'Action/Adventure': '101',
      'Category "Quoted"': '102',
      'Category: With Colon': '103',
    },
  };
  const { loaded } = await roundTrip(mappings);
  assertEqual(loaded, mappings);
});

test('handles unicode in category names', async () => {
  const mappings = { [EP]: { 'Acción': '200', '日本語': '201', 'Émotionnel': '202' } };
  const { loaded } = await roundTrip(mappings);
  assertEqual(loaded, mappings);
});

// ============================================================================
// Category Mapping Parsing (the real loadCategoryMappings)
// ============================================================================

heading('Category Mapping Parsing tests');

test('parses a per-endpoint JSON string', async () => {
  const stored = JSON.stringify({ [EP]: { 'Action': '123' }, [TPDB]: { 'Comedy': '456' } });
  assertEqual(await loadStored(stored), { [EP]: { 'Action': '123' }, [TPDB]: { 'Comedy': '456' } });
});

test('migrates a legacy flat JSON string under StashDB', async () => {
  const result = await loadStored('{"Action":"123","Comedy":"456"}');
  assertEqual(result, { [EP]: { 'Action': '123', 'Comedy': '456' } });
});

test('returns empty object for a missing setting', async () => {
  assertEqual(await loadStored(undefined), {});
});

test('returns empty object for null input', async () => {
  assertEqual(await loadStored(null), {});
});

test('returns empty object for empty string', async () => {
  assertEqual(await loadStored(''), {});
});

test('handles corrupt JSON gracefully', async () => {
  assertEqual(await loadStored('{invalid json}'), {});
});

test('handles truncated JSON gracefully', async () => {
  assertEqual(await loadStored('{"Action":"123"'), {});
});

test('handles non-object JSON gracefully', async () => {
  // An array is not a mapping: nothing is loaded
  assertEqual(await loadStored('["Action", "Comedy"]'), {});
});

// ============================================================================
// findLocalParentMatches tests (supplemental)
// ============================================================================

heading('Category-to-Parent Matching tests');

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

test('uses the saved mapping for the same endpoint only', async () => {
  const { tm } = await pluginWithConfig({});
  tm.setState({ categoryMappings: { [EP]: { 'Action': '999' } } });
  assertEqual(tm.exports.getCategoryMapping(EP, 'Action'), '999');
  assertEqual(tm.exports.getCategoryMapping(TPDB, 'Action'), undefined);
});

// ============================================================================
// Summary
// ============================================================================

(async () => {
  for (const [name, fn] of queue) {
    if (!fn) { console.log(`\n=== ${name} ===\n`); continue; }
    try {
      await fn();
      console.log(`✓ ${name}`);
      passed++;
    } catch (e) {
      console.log(`✗ ${name}`);
      console.log(`  Error: ${e.message}`);
      failed++;
    }
  }

  console.log('\n=== Summary ===\n');
  console.log(`Passed: ${passed}`);
  console.log(`Failed: ${failed}`);

  if (failed > 0) {
    process.exit(1);
  }
})();
