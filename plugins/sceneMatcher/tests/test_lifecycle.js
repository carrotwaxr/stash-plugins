/**
 * One observer, request tokens, client timeout, structured error rendering.
 * Run with: node plugins/sceneMatcher/tests/test_lifecycle.js
 */
const assert = require("assert");
const { loadSceneMatcher, createElement } = require("./harness");

const STASHDB = "https://stashdb.org/graphql";
const CONFIG = {
  data: { configuration: {
    general: { stashBoxes: [{ endpoint: STASHDB, name: "StashDB" }] },
    ui: {}, plugins: {},
  } },
};

function makeRow(id) {
  const row = createElement("div");
  const link = createElement("a");
  link.attributes.href = `/scenes/${id}`;
  const group = createElement("div");
  row.buttons = [];
  group.appendChild = (b) => { b.remove = () => { row.buttons = row.buttons.filter((x) => x !== b); }; row.buttons.push(b); return b; };
  row.querySelector = (sel) => {
    if (sel.includes("/scenes/")) return link;
    if (sel === ".sm-match-button") return row.buttons[0] || null;
    if (sel.includes("input-group")) return group;
    return null;
  };
  // A fresh pill signature on every read, so each sync makes a (uncached) gate query.
  let n = 0;
  row.querySelectorAll = (sel) => (sel.includes("stash-id-pill") ? [{ getAttribute: () => `e${++n}` }] : sel.includes("/scenes/") ? [link] : []);
  group.querySelector = () => null;
  return row;
}

/** Make document.createElement produce elements the modal code can use; getElementById finds them. */
function wireDom(sm, rows = []) {
  const made = [];
  const base = sm.document.createElement;
  sm.document.createElement = (tag) => {
    const el = base(tag);
    let html = "";
    Object.defineProperty(el, "innerHTML", {
      get: () => html,
      set: (v) => { html = v; el.children = []; },
    });
    let tc = "";
    Object.defineProperty(el, "textContent", {
      get: () => tc,
      set: (v) => { tc = String(v); html = tc.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); el.children = []; },
    });
    el.querySelector = () => ({});
    made.push(el);
    return el;
  };
  sm.document.getElementById = (id) => made.filter((e) => e.id === id && !e.removed).pop() || null;
  sm.document.querySelector = (sel) => (sel === "base" ? { getAttribute: () => "/" } : null);
  sm.document.querySelectorAll = (sel) => (sel.includes("search-item") ? rows : []);
  sm.text = (el = sm.document.getElementById("sm-results")) => (el ? el.innerHTML + " " + el.children.map((c) => sm.text(c)).join(" ") : "");
  sm.find = (pred, el = sm.document.getElementById("sm-results")) => {
    if (!el) return null;
    if (pred(el)) return el;
    for (const c of el.children) { const r = sm.find(pred, c); if (r) return r; }
    return null;
  };
  sm.made = made;
  return sm;
}

const out = (o) => ({ data: { runPluginOperation: JSON.stringify(o) } });
const scene = (id, extra = {}) => ({ stash_id: id, title: "T" + id, score: 90, in_local_stash: false, ...extra });
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function load(extra = {}) {
  const sm = loadSceneMatcher({ pathname: "/scenes", search: "?disp=3", ...extra });
  return wireDom(sm, extra.rows || []);
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

// ---------------- listeners ----------------
test("after init and 5 navigations: one observer observing, no popstate listener", async () => {
  const sm = load();
  for (let i = 0; i < 5; i++) {
    sm.window.location.search = `?disp=3&page=${i}`;
    sm.pluginEvents.filter((e) => e.type === "stash:location").forEach((e) => e.fn({ detail: {} }));
    sm.mutationObservers.forEach((o) => o.cb([], o));
    sm.flushTimers();
    await sm.settle();
  }
  assert.strictEqual(sm.mutationObservers.filter((o) => o.observing).length, 1);
  assert.strictEqual(sm.listeners.window.filter((l) => l.type === "popstate").length, 0);
  assert.ok(sm.pluginEvents.some((e) => e.type === "stash:location"));
});

test("without PluginApi the single observer detects navigation", async () => {
  const rows = [makeRow(1)];
  const sm = load({ noPluginApi: true, rows, fetchResponses: { SceneMatcherConfig: CONFIG } });
  sm.flushTimers();
  await sm.settle();
  sm.fetchCalls.length = 0;
  // navigate off the tagger, then back: the observer alone must notice
  sm.window.location.pathname = "/performers"; sm.window.location.search = "";
  sm.mutationObservers.forEach((o) => o.cb([], o));
  sm.flushTimers();
  await sm.settle();
  assert.strictEqual(sm.fetchCalls.length, 0, "no queries off the Tagger");
  sm.window.location.pathname = "/scenes"; sm.window.location.search = "?disp=3";
  sm.mutationObservers.forEach((o) => o.cb([], o));
  sm.flushTimers();
  await sm.settle();
  assert.ok(sm.fetchCalls.some((c) => c.op === "SceneMatcherGate"), "synced after returning");
  assert.strictEqual(sm.mutationObservers.filter((o) => o.observing).length, 1);
  assert.strictEqual(sm.mutationObservers.length, 1);
});

test("syncMatchButtons is debounced 150ms: 20 mutations, one sync", async () => {
  const rows = [makeRow(1)];
  const sm = load({ rows, fetchResponses: { SceneMatcherConfig: CONFIG } });
  sm.flushTimers();
  await sm.settle();
  sm.fetchCalls.length = 0;
  const obs = sm.mutationObservers.find((o) => o.observing);
  for (let i = 0; i < 20; i++) obs.cb([], obs);
  const pending = sm.pendingTimers();
  assert.strictEqual(pending.length, 1);
  assert.strictEqual(pending[0].ms, 150);
  sm.flushTimers();
  await sm.settle();
  const gates = sm.fetchCalls.filter((c) => c.op === "SceneMatcherGate");
  assert.strictEqual(gates.length, 1);
});

test("leaving the Tagger stops syncing", async () => {
  const rows = [makeRow(1)];
  const sm = load({ rows, fetchResponses: { SceneMatcherConfig: CONFIG } });
  sm.flushTimers();
  await sm.settle();
  sm.fetchCalls.length = 0;
  sm.window.location.pathname = "/performers"; sm.window.location.search = "";
  const obs = sm.mutationObservers.find((o) => o.observing);
  for (let i = 0; i < 5; i++) obs.cb([], obs);
  sm.flushTimers();
  await sm.settle();
  assert.strictEqual(sm.fetchCalls.length, 0);
});

// ---------------- request tokens ----------------
function searchSetup(idA = "A", rows = []) {
  const gates = { fastA: deferred(), fastB: deferred(), slowA: deferred(), slowB: deferred() };
  const sm = load({
    rows,
    fetchResponses: {
      SceneMatcherConfig: CONFIG,
      RunPluginOperation: (b) => {
        const a = b.variables.args;
        if (a.operation === "find_matches_fast") return (a.scene_id === idA ? gates.fastA : gates.fastB).promise;
        return (a.scene_id === idA ? gates.slowA : gates.slowB).promise;
      },
    },
  });
  sm.gates = gates;
  return sm;
}

test("clicking Match on B before A's phase 1 returns renders only B", async () => {
  const sm = searchSetup();
  const rowA = createElement("div"), rowB = createElement("div");
  const a = sm.exports.handleMatchClick("A", rowA, STASHDB);
  await sm.settle();
  const b = sm.exports.handleMatchClick("B", rowB, STASHDB);
  await sm.settle();
  sm.gates.fastB.resolve(out({ results: [scene("b1")], has_more: false }));
  await b;
  sm.gates.fastA.resolve(out({ results: [scene("a1")], has_more: true }));
  await a;
  await sm.settle();
  const t = sm.text();
  assert.ok(t.includes("Tb1"), t);
  assert.ok(!t.includes("Ta1"), t);
  assert.ok(!sm.getState().canSearchDeep, "A's has_more dropped");
  assert.strictEqual(sm.getState().currentSceneElement, rowB);
  assert.strictEqual(sm.getState().currentSceneId, "B");
});

test("A's late phase 2 is dropped after B starts", async () => {
  const sm = searchSetup();
  const rowA = createElement("div"), rowB = createElement("div");
  const a = sm.exports.handleMatchClick("A", rowA, STASHDB);
  sm.gates.fastA.resolve(out({ results: [scene("a1")], has_more: true }));
  await a;
  const deep = sm.exports.handleDeepSearchClick();
  await sm.settle();
  const b = sm.exports.handleMatchClick("B", rowB, STASHDB);
  await sm.settle();
  sm.gates.fastB.resolve(out({ results: [scene("b1")], has_more: false }));
  await b;
  sm.gates.slowA.resolve(out({ results: [scene("a2")] }));
  await deep;
  await sm.settle();
  const t = sm.text();
  assert.ok(t.includes("Tb1") && !t.includes("Ta2") && !t.includes("Ta1"), t);
  assert.deepStrictEqual(Array.from(sm.getState().matchResults, (r) => r.stash_id), ["b1"]);
  assert.strictEqual(sm.getState().isLoadingDeep, false);
});

test("Select after A-then-B uses B's row", async () => {
  // Scenes 1 (A) and 2 (B); each row answers its own scene link.
  const rowA = makeRow(1), rowB = makeRow(2);
  const typedInto = [];
  for (const row of [rowA, rowB]) {
    const input = createElement("input");
    input.dispatchEvent = () => true;
    input.row = row;
    const linkOnly = row.querySelector;
    row.querySelector = (sel) => (sel === "input.text-input" ? input : linkOnly(sel));
  }
  const sm = searchSetup("1", [rowA, rowB]);
  Object.defineProperty(sm.window.HTMLInputElement.prototype, "value", {
    set(v) { typedInto.push([this.row, v]); }, configurable: true,
  });
  const a = sm.exports.handleMatchClick("1", rowA, STASHDB);
  await sm.settle();
  const b = sm.exports.handleMatchClick("2", rowB, STASHDB);
  sm.gates.fastB.resolve(out({ results: [scene("b1")] }));
  await b;
  sm.gates.fastA.resolve(out({ results: [scene("a1")] }));
  await a;
  const st = sm.getState();
  sm.exports.handleSelectMatch(st.currentSceneId, st.matchResults[0].stash_id);
  assert.deepStrictEqual(typedInto, [[rowB, "b1"]]);
});

test("Match click while a previous search still loads (modal closed) starts the new search", async () => {
  const sm = searchSetup();
  const a = sm.exports.handleMatchClick("A", createElement("div"), STASHDB);
  await sm.settle();
  sm.exports.removeModal();
  assert.strictEqual(sm.getState().isLoading, true);
  const b = sm.exports.handleMatchClick("B", createElement("div"), STASHDB);
  await sm.settle();
  const fast = sm.fetchCalls.filter((c) => c.op === "RunPluginOperation");
  assert.deepStrictEqual(Array.from(fast, (c) => c.body.variables.args.scene_id), ["A", "B"]);
  sm.gates.fastB.resolve(out({ results: [scene("b1")] }));
  sm.gates.fastA.resolve(out({ results: [] }));
  await Promise.all([a, b]);
  assert.strictEqual(sm.getState().isLoading, false);
  assert.deepStrictEqual(Array.from(sm.getState().matchResults, (r) => r.stash_id), ["b1"]);
});

// ---------------- timeout ----------------
test("graphqlRequest aborts after 120s and the error offers Retry", async () => {
  const sm = load({ fetchResponses: { RunPluginOperation: { __hang: true } } });
  const p = sm.exports.handleMatchClick("A", createElement("div"), STASHDB);
  await sm.settle();
  const timer = sm.pendingTimers().find((t) => t.ms === 120000);
  assert.ok(timer, "a 120s timer is queued");
  const call = sm.fetchCalls.find((c) => c.op === "RunPluginOperation");
  assert.ok(call.opts.signal, "fetch gets an AbortSignal");
  sm.flushTimers();
  await p;
  const t = sm.text();
  assert.ok(/timed out/i.test(t), t);
  assert.ok(sm.find((e) => e.tagName === "BUTTON" && /Retry/.test(e.innerHTML + e.textContent)), "Retry button");
});

test("graphqlRequest clears its timer on success", async () => {
  const sm = load({ fetchResponses: { SceneMatcherConfig: CONFIG } });
  await sm.exports.graphqlRequest("query SceneMatcherConfig { x }");
  assert.strictEqual(sm.pendingTimers().filter((t) => t.ms === 120000).length, 0);
});

// ---------------- rendering ----------------
async function runPhase1(output) {
  const sm = load({ fetchResponses: { RunPluginOperation: () => out(output) } });
  await sm.exports.handleMatchClick("A", createElement("div"), STASHDB);
  await sm.settle();
  return sm;
}

test("runPluginOperation returns output with a phase key instead of throwing", async () => {
  const sm = load({ fetchResponses: { RunPluginOperation: () => out({ phase: 1, error: "boom", auth_error: true }) } });
  const r = await sm.exports.runPluginOperation({ operation: "find_matches_fast" });
  assert.strictEqual(r.error, "boom");
  const sm2 = load({ fetchResponses: { RunPluginOperation: () => out({ error: "plain" }) } });
  await assert.rejects(() => sm2.exports.runPluginOperation({}), /plain/);
});

test("warnings render as a notice above the results", async () => {
  const sm = await runPhase1({ phase: 1, results: [scene("w1")], warnings: ["Skipped <b>x</b>"] });
  const t = sm.text();
  assert.ok(t.includes("Skipped &lt;b&gt;x&lt;/b&gt;"), t);
  assert.ok(!t.includes("<b>x</b>"));
  assert.ok(t.indexOf("Skipped") < t.indexOf("Tw1"), "notice above results");
});

test("partial renders results plus 'Some pages failed'", async () => {
  const sm = await runPhase1({ phase: 1, results: [scene("p1")], partial: true });
  const t = sm.text();
  assert.ok(t.includes("Some pages failed") && t.includes("Tp1"), t);
});

test("auth_error renders the API-key hint with the message", async () => {
  const sm = await runPhase1({ phase: 1, error: "Unauthorized <img>", auth_error: true, results: [] });
  const t = sm.text();
  assert.ok(/API key/i.test(t), t);
  assert.ok(t.includes("Unauthorized &lt;img&gt;"), t);
  assert.ok(!t.includes("<img>"));
});

test("error renders message with Retry, not 'No matching scenes found'", async () => {
  const sm = await runPhase1({ phase: 1, error: "StashDB is down" });
  const t = sm.text();
  assert.ok(t.includes("StashDB is down"), t);
  assert.ok(!/No matching scenes/.test(t), t);
  const retry = sm.find((e) => e.tagName === "BUTTON" && /Retry/.test(e.innerHTML + e.textContent));
  assert.ok(retry);
  const before = sm.fetchCalls.filter((c) => c.op === "RunPluginOperation").length;
  retry.onclick();
  await sm.settle();
  assert.strictEqual(sm.fetchCalls.filter((c) => c.op === "RunPluginOperation").length, before + 1);
});

test("truncated shows 'Showing the top N of M'", async () => {
  const sm = await runPhase1({ phase: 1, results: [scene("t1"), scene("t2")], truncated: true, total_candidates: 250 });
  const t = sm.text();
  assert.ok(/Showing the top 2 of 250/.test(t), t);
});

test("phase 2 error keeps phase 1 results and lets the user retry", async () => {
  let n = 0;
  const sm = load({ fetchResponses: { RunPluginOperation: (b) => {
    n++;
    return b.variables.args.operation === "find_matches_fast"
      ? out({ phase: 1, results: [scene("k1")], has_more: true })
      : out({ phase: 2, error: "deep failed", auth_error: false });
  } } });
  await sm.exports.handleMatchClick("A", createElement("div"), STASHDB);
  await sm.exports.handleDeepSearchClick();
  await sm.settle();
  const t = sm.text();
  assert.ok(t.includes("Tk1") && t.includes("deep failed"), t);
  assert.strictEqual(sm.getState().canSearchDeep, true);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok - ${name}`); } catch (e) { failed++; console.log(`FAIL - ${name}\n  ${e.stack}`); }
  }
  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log(`${tests.length} passed`);
})();
