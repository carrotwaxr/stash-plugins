/**
 * Handing the chosen match to the Tagger row, and the F11 selectors.
 * Run with: node plugins/sceneMatcher/tests/test_handoff.js
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { loadSceneMatcher, createElement } = require("./harness");

function makeRow(id, { disabled = false, noButton = false, noInput = false } = {}) {
  const row = createElement("div");
  const link = createElement("a");
  link.attributes.href = `/scenes/${id}`;
  const input = createElement("input");
  input.value = "";
  input.events = [];
  input.dispatchEvent = (e) => { input.events.push(e); return true; };
  const own = { classList: { contains: () => false }, disabled, clicks: 0, click() { this.clicks++; } };
  const mine = { classList: { contains: (c) => c === "sm-match-button" }, disabled: false, clicks: 0, click() { this.clicks++; } };
  row.input = input; row.own = own; row.mine = mine;
  row.queries = [];
  row.querySelector = (sel) => {
    row.queries.push(sel);
    if (sel === "input.text-input") return noInput ? null : input;
    return null;
  };
  row.querySelectorAll = (sel) => (sel === ".input-group-append button" ? (noButton ? [mine] : [mine, own]) : []);
  const anchor = { closest: (s) => (s === ".search-item" ? row : null) };
  row.anchor = anchor;
  return { row, anchor };
}

function load(rows) {
  const sm = loadSceneMatcher({ pathname: "/scenes", search: "?disp=3" });
  const seen = [];
  sm.document.querySelector = (sel) => {
    seen.push(sel);
    if (sel === "base") return { getAttribute: () => "/" };
    const m = sel.match(/a\[href\*="\/scenes\/(\d+)"\]/);
    return m && rows[m[1]] ? rows[m[1]].anchor : null;
  };
  sm.seen = seen;
  sm.removed = 0;
  sm.exports = sm.window.__SCENE_MATCHER_TEST__.exports;
  return sm;
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("sets the value, dispatches input, clicks the row's own button not ours", () => {
  const r = makeRow(7);
  const sm = load({ 7: r });
  sm.exports.handleSelectMatch("7", "uuid-1");
  assert.strictEqual(r.row.input.value, "uuid-1");
  const ev = r.row.input.events.find((e) => e.type === "input");
  assert.ok(ev && ev.bubbles === true);
  assert.strictEqual(r.row.own.clicks, 1);
  assert.strictEqual(r.row.mine.clicks, 0);
  assert.ok(!r.row.input.events.some((e) => e.type === "keypress"));
});

test("re-queries the row by scene link (the captured element may be gone)", () => {
  const r = makeRow(9);
  const sm = load({ 9: r });
  sm.window.__SCENE_MATCHER_TEST__.setState({ currentSceneElement: null });
  sm.exports.handleSelectMatch("9", "u");
  assert.ok(sm.seen.some((s) => s.includes('/scenes/9')));
  assert.strictEqual(r.row.own.clicks, 1);
});

test("uses the native value setter when available", () => {
  const r = makeRow(3);
  const sm = load({ 3: r });
  let called = 0;
  sm.window.HTMLInputElement.prototype = Object.defineProperty({}, "value", {
    set(v) { called++; this._v = v; }, get() { return this._v; }, configurable: true,
  });
  sm.exports.handleSelectMatch("3", "abc");
  assert.strictEqual(called, 1);
  assert.strictEqual(r.row.input._v, "abc");
});

test("disabled button: dispatches a React-friendly Enter keypress instead", () => {
  const r = makeRow(4, { disabled: true });
  const sm = load({ 4: r });
  sm.exports.handleSelectMatch("4", "u");
  assert.strictEqual(r.row.own.clicks, 0);
  const k = r.row.input.events.find((e) => e.type === "keypress");
  assert.ok(k, "keypress dispatched");
  assert.strictEqual(k.key, "Enter");
  assert.strictEqual(k.keyCode, 13);
  assert.strictEqual(k.charCode, 13);
  assert.strictEqual(k.which, 13);
  assert.strictEqual(k.bubbles, true);
});

test("no search button: Enter keypress", () => {
  const r = makeRow(5, { noButton: true });
  const sm = load({ 5: r });
  sm.exports.handleSelectMatch("5", "u");
  assert.ok(r.row.input.events.some((e) => e.type === "keypress"));
  assert.strictEqual(r.row.mine.clicks, 0);
});

test("missing row or input shows a visible error", () => {
  const sm = load({});
  sm.exports.handleSelectMatch("1", "u");
  const shown = () => JSON.stringify(sm.document.body.children.map((c) => c.textContent + c.innerHTML));
  assert.ok(/Could not find/.test(shown()), "row error shown: " + shown());
  const r = makeRow(2, { noInput: true });
  const sm2 = load({ 2: r });
  sm2.exports.handleSelectMatch("2", "u");
  assert.ok(/search box|search input/i.test(JSON.stringify(sm2.document.body.children.map((c) => c.textContent + c.innerHTML))));
  assert.strictEqual(r.row.own.clicks, 0);
});

test("closes the modal after a successful handoff", () => {
  const r = makeRow(8);
  const sm = load({ 8: r });
  let removed = false;
  sm.window.__SCENE_MATCHER_TEST__.setState({ modalRoot: { remove() { removed = true; } } });
  sm.exports.handleSelectMatch("8", "u");
  assert.ok(removed);
});

test("isTaggerPage: /scenes route plus select#scraper or .search-item, no .tagger-container", () => {
  const sm = loadSceneMatcher({ pathname: "/scenes/12", search: "" });
  const q = [];
  let present = {};
  sm.document.querySelector = (s) => { q.push(s); return present[s] || null; };
  sm.document.querySelectorAll = (s) => { q.push(s); return present[s] || []; };
  const { isTaggerPage } = sm.window.__SCENE_MATCHER_TEST__.exports;
  assert.strictEqual(isTaggerPage(), false);
  present = { "select#scraper": {} };
  assert.strictEqual(isTaggerPage(), true);
  present = { ".search-item": {} };
  assert.strictEqual(isTaggerPage(), true);
  sm.window.location.pathname = "/performers";
  assert.strictEqual(isTaggerPage(), false);
  assert.ok(!q.some((s) => s.includes("tagger-container")));
});

test("source has none of the dead selectors or a hard-coded endpoint gate", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "scene-matcher.js"), "utf8");
  for (const bad of [".tagger-scene", "StashIDPill", 'input[type="text"]', "input[type='text']", "stashdb.org/scenes'", ".tagger-container"]) {
    assert.ok(!src.includes(bad), `still contains ${bad}`);
  }
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + (e && e.stack || e)); }
  }
  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log(`${tests.length} passed`);
})();
