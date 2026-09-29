/**
 * A failed load of the stored category mappings must never let a later save
 * overwrite them. Run with: node plugins/tagManager/tests/test_mapping_safety.js
 */
const { loadTagManager } = require('./harness');

const EP = 'https://stashdb.org/graphql';
const STORED = JSON.stringify({ [EP]: { Action: '12' } });

let passed = 0;
let failed = 0;
const queue = [];
function test(name, fn) { queue.push([name, fn]); }
function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

/** Plugin whose Configuration query fails while `ctl.fail` is true. */
async function plugin(config) {
  const ctl = { fail: false, config: { ...config } };
  const tm = loadTagManager({
    fetchResponses: {
      Configuration: () => ctl.fail
        ? { errors: [{ message: 'boom' }] }
        : { data: { configuration: { plugins: { tagManager: ctl.config } } } },
      ConfigurePlugin: (body) => { ctl.config = body.variables.input; return { data: { configurePlugin: ctl.config } }; },
    },
  });
  await tm.settle();
  tm.baseline = tm.fetchCalls.length; // startup makes its own settings writes
  return { tm, ctl };
}
const writes = (tm) => tm.fetchCalls.slice(tm.baseline).filter((c) => c.op === 'ConfigurePlugin');
const writtenMap = (tm) => JSON.parse(writes(tm).pop().body.variables.input.categoryMappings);

test('load failure then save merges with stored', async () => {
  const { tm, ctl } = await plugin({ categoryMappings: STORED });
  ctl.fail = true;
  await tm.exports.loadCategoryMappings();
  ctl.fail = false;
  tm.exports.setCategoryMapping(EP, 'Hair Color', '50');
  assertEqual(await tm.exports.saveCategoryMappings(), true);
  assertEqual(writtenMap(tm), { [EP]: { Action: '12', 'Hair Color': '50' } });
});

test('load failure and re-read failure means no write', async () => {
  const { tm, ctl } = await plugin({ categoryMappings: STORED });
  ctl.fail = true;
  await tm.exports.loadCategoryMappings();
  tm.exports.setCategoryMapping(EP, 'Hair Color', '50');
  assertEqual(await tm.exports.saveCategoryMappings({ quiet: true }), false);
  assertEqual(writes(tm).length, 0, 'no ConfigurePlugin write');
});

test('normal path unchanged', async () => {
  const { tm } = await plugin({ categoryMappings: STORED });
  await tm.exports.loadCategoryMappings();
  tm.exports.setCategoryMapping(EP, 'Hair Color', '50');
  assertEqual(await tm.exports.saveCategoryMappings(), true);
  assertEqual(writes(tm).length, 1);
  assertEqual(writtenMap(tm), { [EP]: { Action: '12', 'Hair Color': '50' } });
});

test('in-memory entry wins over stored; deletion while unloaded applies', async () => {
  const stored = JSON.stringify({ [EP]: { Action: '12', Old: '3', Keep: '4' } });
  const { tm, ctl } = await plugin({ categoryMappings: stored });
  ctl.fail = true;
  await tm.exports.loadCategoryMappings();
  ctl.fail = false;
  tm.exports.setCategoryMapping(EP, 'Action', '99');
  tm.exports.deleteCategoryMapping(EP, 'Old');
  assertEqual(await tm.exports.saveCategoryMappings(), true);
  assertEqual(writtenMap(tm), { [EP]: { Action: '99', Keep: '4' } });
});

test('corrupt stored JSON warns and is replaced on save', async () => {
  const { tm } = await plugin({ categoryMappings: '{not json' });
  await tm.exports.loadCategoryMappings();
  assertEqual(tm.logs.warn.some((a) => /unreadable/.test(a.join(' '))), true, 'warned');
  tm.exports.setCategoryMapping(EP, 'Hair Color', '50');
  assertEqual(await tm.exports.saveCategoryMappings(), true);
  assertEqual(writtenMap(tm), { [EP]: { 'Hair Color': '50' } });
});

(async () => {
  for (const [name, fn] of queue) {
    try { await fn(); passed++; console.log(`  ok   ${name}`); }
    catch (e) { failed++; console.log(`  FAIL ${name}\n${e.message}`); }
  }
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
