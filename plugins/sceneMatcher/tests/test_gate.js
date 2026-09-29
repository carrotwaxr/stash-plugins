/**
 * The Match button follows the Tagger's selected endpoint.
 * Run with: node plugins/sceneMatcher/tests/test_gate.js
 */
const assert = require("assert");
const { loadSceneMatcher, createElement } = require("./harness");

const STASHDB = "https://stashdb.org/graphql";
const TPDB = "https://theporndb.net/graphql";

const CONFIG = {
  data: {
    configuration: {
      general: { stashBoxes: [{ endpoint: STASHDB, name: "StashDB" }, { endpoint: TPDB, name: "ThePornDB" }] },
      ui: { taggerConfig: { selectedEndpoint: TPDB } },
      plugins: { sceneMatcher: { stashBoxEndpoint: STASHDB } },
    },
  },
};

/** A .search-item stub: scene link, own pills, an input-group, and a button slot. */
function makeRow(id, pills = []) {
  const row = createElement("div");
  const link = createElement("a");
  link.attributes.href = `/scenes/${id}`;
  link.href = `http://localhost/scenes/${id}`;
  const group = createElement("div");
  const pillEls = () => pills.map((p) => ({ getAttribute: () => p }));
  row.pills = pills;
  row.buttons = [];
  group.appendChild = (b) => { b.remove = () => { row.buttons = row.buttons.filter((x) => x !== b); }; row.buttons.push(b); return b; };
  row.querySelector = (sel) => {
    if (sel.includes("/scenes/")) return link;
    if (sel === ".sm-match-button") return row.buttons[0] || null;
    if (sel.includes("input-group")) return group;
    return null;
  };
  row.querySelectorAll = (sel) => (sel.includes("stash-id-pill") ? pillEls() : sel.includes("/scenes/") ? [link] : []);
  group.querySelector = () => null;
  return row;
}

function setup({ rows, select, findScenes, config = CONFIG }) {
  const sm = loadSceneMatcher({
    fetchResponses: {
      SceneMatcherConfig: config,
      RunPluginOperation: { data: { runPluginOperation: JSON.stringify({ results: [] }) } },
      SceneMatcherGate: findScenes || ((b) => ({
        data: { findScenes: { scenes: b.variables.ids.map((id) => ({ id, stash_ids: [] })) } },
      })),
    },
  });
  sm.select = select;
  sm.rows = rows;
  sm.document.querySelector = (sel) => (sel === "base" ? { getAttribute: () => "/" } : sel === "select#scraper" ? sm.select || null : null);
  sm.document.querySelectorAll = (sel) => (sel.includes("search-item") ? sm.rows : []);
  return sm;
}
const sel = (value) => {
  const s = createElement("select");
  s.value = value;
  return s;
};
const gateCalls = (sm) => sm.fetchCalls.filter((c) => c.op === "SceneMatcherGate");
const configCalls = (sm) => sm.fetchCalls.filter((c) => c.op === "SceneMatcherConfig");

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("effectiveEndpoint reads the select each call", async () => {
  const sm = setup({ rows: [], select: sel(`stashbox:${STASHDB}`) });
  const { effectiveEndpoint } = sm.exports;
  assert.strictEqual(await effectiveEndpoint(), STASHDB);
  sm.select.value = `stashbox:${TPDB}`;
  assert.strictEqual(await effectiveEndpoint(), TPDB);
  sm.select.value = "scraper:xyz";
  assert.strictEqual(await effectiveEndpoint(), null);
});

test("effectiveEndpoint falls back to ui config, then plugin setting, then null; fetched once", async () => {
  const sm = setup({ rows: [], select: null });
  assert.strictEqual(await sm.exports.effectiveEndpoint(), TPDB);
  assert.strictEqual(await sm.exports.effectiveEndpoint(), TPDB);
  assert.strictEqual(configCalls(sm).length, 1);

  const noUi = JSON.parse(JSON.stringify(CONFIG));
  noUi.data.configuration.ui = {};
  const sm2 = setup({ rows: [], select: null, config: noUi });
  assert.strictEqual(await sm2.exports.effectiveEndpoint(), STASHDB);

  noUi.data.configuration.plugins = {};
  const sm3 = setup({ rows: [], select: null, config: noUi });
  assert.strictEqual(await sm3.exports.effectiveEndpoint(), null);
});

test("gateScenes: one batched findScenes query, returns unlinked ids, normalized compare", async () => {
  const sm = setup({
    rows: [],
    select: null,
    findScenes: () => ({
      data: { findScenes: { scenes: [
        { id: "1", stash_ids: [{ endpoint: "https://StashDB.org/graphql/" }] },
        { id: "2", stash_ids: [{ endpoint: TPDB }] },
        { id: "3", stash_ids: [] },
      ] } },
    }),
  });
  const out = await sm.exports.gateScenes(["1", "2", "3"], STASHDB);
  assert.deepStrictEqual([...out].sort(), ["2", "3"]);
  const calls = gateCalls(sm);
  assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0].body.variables.ids, ["1", "2", "3"]);
  assert.ok(/findScenes\(ids: \$ids/.test(calls[0].body.query));
  assert.ok(/per_page: -1/.test(calls[0].body.query));
  assert.ok(/stash_ids \{ endpoint \}/.test(calls[0].body.query));
});

test("gateScenes with null endpoint resolves the first configured box", async () => {
  const sm = setup({
    rows: [],
    select: null,
    findScenes: () => ({ data: { findScenes: { scenes: [
      { id: "1", stash_ids: [{ endpoint: STASHDB }] },
      { id: "2", stash_ids: [{ endpoint: TPDB }] },
    ] } } }),
  });
  assert.deepStrictEqual(await sm.exports.gateScenes(["1", "2"], null), ["2"]);
});

test("gateScenes caches per (endpoint, id) until the pill signature changes", async () => {
  const sm = setup({ rows: [], select: null });
  await sm.exports.gateScenes(["1", "2"], STASHDB, { 1: "a", 2: "" });
  await sm.exports.gateScenes(["1", "2"], STASHDB, { 1: "a", 2: "" });
  assert.strictEqual(gateCalls(sm).length, 1);
  await sm.exports.gateScenes(["1", "2"], STASHDB, { 1: "a", 2: "StashDB" });
  assert.strictEqual(gateCalls(sm).length, 2);
  assert.deepStrictEqual(gateCalls(sm)[1].body.variables.ids, ["2"]);
  await sm.exports.gateScenes(["1"], TPDB, { 1: "a" });
  assert.strictEqual(gateCalls(sm).length, 3);
});

test("syncMatchButtons: button only on rows not linked to the selected endpoint; one query", async () => {
  const rows = [makeRow(1), makeRow(2), makeRow(3)];
  const sm = setup({
    rows,
    select: sel(`stashbox:${STASHDB}`),
    findScenes: () => ({ data: { findScenes: { scenes: [
      { id: "1", stash_ids: [{ endpoint: STASHDB }] },
      { id: "2", stash_ids: [{ endpoint: TPDB }] }, // ThePornDB only
      { id: "3", stash_ids: [] },
    ] } } }),
  });
  await sm.exports.syncMatchButtons();
  assert.deepStrictEqual(rows.map((r) => r.buttons.length), [0, 1, 1]);
  assert.strictEqual(gateCalls(sm).length, 1);
  assert.ok(/Match on StashDB/.test(rows[1].buttons[0].innerHTML));
  assert.ok(/StashDB/.test(rows[1].buttons[0].title));
  // idempotent
  await sm.exports.syncMatchButtons();
  assert.deepStrictEqual(rows.map((r) => r.buttons.length), [0, 1, 1]);
  assert.strictEqual(gateCalls(sm).length, 1);
});

test("changing the select re-gates, relabels, and scraper: removes every button", async () => {
  const rows = [makeRow(1), makeRow(2)];
  const sm = setup({
    rows,
    select: sel(`stashbox:${STASHDB}`),
    findScenes: () => ({ data: { findScenes: { scenes: [
      { id: "1", stash_ids: [{ endpoint: STASHDB }] },
      { id: "2", stash_ids: [{ endpoint: TPDB }] },
    ] } } }),
  });
  await sm.exports.syncMatchButtons();
  assert.deepStrictEqual(rows.map((r) => r.buttons.length), [0, 1]);
  assert.ok(sm.select.listeners.change && sm.select.listeners.change.length === 1, "change listener bound once");

  sm.select.value = `stashbox:${TPDB}`;
  sm.select.listeners.change[0]({});
  await sm.settle();
  assert.deepStrictEqual(rows.map((r) => r.buttons.length), [1, 0]);
  assert.ok(/Match on ThePornDB/.test(rows[0].buttons[0].innerHTML));

  sm.select.value = "scraper:abc";
  await sm.exports.syncMatchButtons();
  assert.deepStrictEqual(rows.map((r) => r.buttons.length), [0, 0]);
  assert.strictEqual(sm.select.listeners.change.length, 1);
});

test("a changed pill set re-gates that row", async () => {
  const row = makeRow(5, []);
  const sm = setup({ rows: [row], select: sel(`stashbox:${STASHDB}`) });
  await sm.exports.syncMatchButtons();
  assert.strictEqual(row.buttons.length, 1);
  assert.strictEqual(gateCalls(sm).length, 1);
  await sm.exports.syncMatchButtons();
  assert.strictEqual(gateCalls(sm).length, 1, "unchanged pills use the cache");
  row.pills.push("StashDB");
  await sm.exports.syncMatchButtons();
  assert.strictEqual(gateCalls(sm).length, 2, "changed pills re-query");
});

test("row scene id comes from the link only", async () => {
  const sm = setup({ rows: [], select: null });
  const el = createElement("div");
  el.dataset.sceneId = "99";
  assert.strictEqual(sm.exports.getSceneIdFromElement(el), null);
  const row = makeRow(42);
  assert.strictEqual(sm.exports.getSceneIdFromElement(row), "42");
});

test("the gate asks about the row's own scene, not a stash-box link that looks like one", async () => {
  const row = makeRow(7);
  const pill = createElement("a");
  pill.attributes.href = "https://stashdb.org/scenes/12ab34cd-0000-4000-8000-000000000000";
  const own = row.querySelectorAll('a[href*="/scenes/"]')[0];
  // The pill comes first in document order, so querySelector returns it, as a browser would
  const rowQuery = row.querySelector;
  row.querySelectorAll = (sel) => (sel.includes("/scenes/") ? [pill, own] : []);
  row.querySelector = (sel) => (sel.includes("/scenes/") ? pill : rowQuery(sel));
  const sm = setup({ rows: [row], select: sel(`stashbox:${STASHDB}`) });
  await sm.exports.syncMatchButtons();
  assert.deepStrictEqual(gateCalls(sm)[0].body.variables.ids, ["7"]);
  assert.strictEqual(row.buttons.length, 1);
});

test("Match click sends the endpoint to both ops", async () => {
  const sm = setup({ rows: [], select: sel(`stashbox:${TPDB}`) });
  sm.fetchCalls.length = 0;
  await sm.exports.findMatchesFast("7", TPDB);
  await sm.exports.findMatchesThorough("7", [], TPDB);
  const ops = sm.fetchCalls.filter((c) => c.op === "RunPluginOperation").map((c) => c.body.variables.args);
  assert.strictEqual(ops[0].endpoint, TPDB);
  assert.strictEqual(ops[1].endpoint, TPDB);
  await sm.exports.findMatchesFast("7");
  const last = sm.fetchCalls.filter((c) => c.op === "RunPluginOperation").pop().body.variables.args;
  assert.ok(!("endpoint" in last));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok - ${name}`); } catch (e) { failed++; console.log(`FAIL - ${name}\n  ${e.stack}`); }
  }
  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log(`${tests.length} passed`);
})();
