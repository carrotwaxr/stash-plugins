/**
 * Unit tests for the #122 persistence helpers.
 *
 * The frontend is a browser IIFE with no build step, so (per this repo's existing
 * JS-test convention) these tests MIRROR the pure helpers from tag-manager.js and
 * validate the algorithm. Keep them byte-identical to the source.
 *
 * Run with: node plugins/tagManager/tests/test_persistence_overhaul.js
 */

let passed = 0;
let failed = 0;
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// ---- MIRROR of tag-manager.js: createConfigWriteQueue ----
function createConfigWriteQueue() {
  let chain = Promise.resolve();
  return function enqueue(task) {
    const result = chain.then(task, task); // run after prior settles (ok or error)
    chain = result.then(() => {}, () => {}); // advance chain, never poisoned
    return result;
  };
}

// ---- MIRROR of tag-manager.js: valuesPersisted ----
function valuesPersisted(written, readback) {
  return Object.keys(written).every(
    (k) => JSON.stringify(readback?.[k]) === JSON.stringify(written[k])
  );
}

// ---- test helpers: a fake async config store with read/merge/write ----
function makeStore(initial) {
  let store = { ...initial };
  return {
    read: () => new Promise((res) => setTimeout(() => res({ ...store }), 5)),
    write: (next) => new Promise((res) => setTimeout(() => { store = { ...next }; res(); }, 5)),
    snapshot: () => ({ ...store }),
  };
}
function readMergeWrite(store, partial) {
  return async () => {
    const current = await store.read();
    const next = { ...current, ...partial };
    await store.write(next);
    return next;
  };
}

console.log('\n=== createConfigWriteQueue tests ===\n');

test('serialized read-merge-writes do not clobber each other', async () => {
  const store = makeStore({});
  const enqueue = createConfigWriteQueue();
  await Promise.all([
    enqueue(readMergeWrite(store, { a: 1 })),
    enqueue(readMergeWrite(store, { b: 2 })),
    enqueue(readMergeWrite(store, { c: 3 })),
  ]);
  assertEqual(store.snapshot(), { a: 1, b: 2, c: 3 }, 'all keys must survive');
});

test('each enqueued task resolves to its own result', async () => {
  const enqueue = createConfigWriteQueue();
  const [r1, r2] = await Promise.all([
    enqueue(async () => 'first'),
    enqueue(async () => 'second'),
  ]);
  assertEqual(r1, 'first');
  assertEqual(r2, 'second');
});

test('a failing task does not break the chain', async () => {
  const enqueue = createConfigWriteQueue();
  let rejected = false;
  await enqueue(async () => { throw new Error('boom'); }).catch(() => { rejected = true; });
  const after = await enqueue(async () => 'ok');
  assert(rejected, 'first task should reject');
  assertEqual(after, 'ok', 'chain should keep running after a failure');
});

console.log('\n=== valuesPersisted tests ===\n');

test('true when readback contains the written values', () => {
  assert(valuesPersisted({ categoryMappings: '{"A":"1"}' }, { categoryMappings: '{"A":"1"}', other: 'x' }));
});

test('false when a written value is missing on readback', () => {
  assert(!valuesPersisted({ categoryMappings: '{"A":"1"}' }, {}));
});

test('false when a written value differs on readback', () => {
  assert(!valuesPersisted({ categoryMappings: '{"A":"1"}' }, { categoryMappings: '{"A":"2"}' }));
});

(async () => {
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`✓ ${name}`); passed++; }
    catch (e) { console.log(`✗ ${name}\n  Error: ${e.message}`); failed++; }
  }
  console.log(`\nPassed: ${passed}\nFailed: ${failed}`);
  if (failed > 0) process.exit(1);
})();
