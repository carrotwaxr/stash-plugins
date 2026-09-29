/**
 * Handing the chosen match to the Tagger row, and the F11 selectors.
 * Run with: node plugins/sceneMatcher/tests/test_handoff.js
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { loadSceneMatcher, createElement } = require("./harness");

// ---- a tiny DOM with real selector semantics (descendant combinator, tag, .class,
// [attr*="substring"]), so a substring selector matches what a browser would match ----
function parseCompound(c) {
  const m = c.match(/^([a-z]+)?((?:\.[\w-]+)*)((?:\[[\w-]+\*="[^"]*"\])*)$/i);
  if (!m) throw new Error("selector not supported by the test DOM: " + c);
  return {
    tag: m[1] ? m[1].toUpperCase() : null,
    classes: (m[2].match(/\.[\w-]+/g) || []).map((x) => x.slice(1)),
    attrs: (m[3].match(/\[[\w-]+\*="[^"]*"\]/g) || []).map((x) => {
      const a = x.match(/^\[([\w-]+)\*="([^"]*)"\]$/);
      return [a[1], a[2]];
    }),
  };
}
function matchesCompound(el, c) {
  if (c.tag && el.tagName !== c.tag) return false;
  if (!c.classes.every((k) => el.classList.contains(k))) return false;
  return c.attrs.every(([k, v]) => { const got = el.getAttribute(k); return got !== null && got.includes(v); });
}
function matchesSelector(el, sel) {
  return sel.split(",").some((alt) => {
    const parts = alt.trim().split(/\s+/).map(parseCompound);
    if (!matchesCompound(el, parts[parts.length - 1])) return false;
    let i = parts.length - 2;
    for (let a = el.parentElement; a && i >= 0; a = a.parentElement) if (matchesCompound(a, parts[i])) i--;
    return i < 0;
  });
}
function descendants(root) {
  const out = [];
  (function walk(n) { for (const c of n.children || []) { out.push(c); walk(c); } })(root);
  return out;
}
function node(tag, { cls = [], href = null } = {}) {
  const el = createElement(tag);
  cls.forEach((c) => el.classList.add(c));
  if (href !== null) {
    el.setAttribute("href", href);
    el.href = new URL(href, "http://localhost/").href;
  }
  el.querySelectorAll = (sel) => descendants(el).filter((d) => matchesSelector(d, sel));
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  el.closest = (sel) => { for (let a = el; a; a = a.parentElement) if (a.classList && matchesSelector(a, sel)) return a; return null; };
  return el;
}

/**
 * A Tagger row as Stash renders it: the scene's own link (relative, `/scenes/<id>`, maybe
 * with a query), the search input and the input-group buttons, then stash-id pills that
 * link to the stash-box (`https://stashdb.org/scenes/<uuid>`).
 */
function makeRow(id, { disabled = false, noButton = false, noInput = false, href = null, pills = [] } = {}) {
  const row = node("div", { cls: ["search-item"] });
  const link = row.appendChild(node("a", { href: href || `/scenes/${id}` }));
  const group = row.appendChild(node("div", { cls: ["input-group"] }));
  const input = node("input", { cls: ["text-input"] });
  input.value = "";
  input.events = [];
  input.dispatchEvent = (e) => { input.events.push(e); return true; };
  if (!noInput) group.appendChild(input);
  const append = group.appendChild(node("div", { cls: ["input-group-append"] }));
  const mine = append.appendChild(node("button", { cls: ["sm-match-button"] }));
  const own = node("button");
  own.disabled = disabled;
  for (const b of [mine, own]) { b.clicks = 0; b.click = () => { b.clicks++; }; }
  if (!noButton) append.appendChild(own);
  for (const p of pills) row.appendChild(node("a", { cls: ["stash-id-pill"], href: p }));
  Object.assign(row, { link, input, own, mine });
  return row;
}

function load(rows) {
  const sm = loadSceneMatcher({ pathname: "/scenes", search: "?disp=3" });
  const seen = [];
  const list = Array.isArray(rows) ? rows : Object.values(rows);
  for (const r of list) sm.document.body.appendChild(r);
  const body = sm.document.body;
  sm.document.querySelectorAll = (sel) => { seen.push(sel); return descendants(body).filter((d) => d.classList && matchesSelector(d, sel)); };
  sm.document.querySelector = (sel) => {
    seen.push(sel);
    if (sel === "base") return { getAttribute: () => "/" };
    return descendants(body).find((d) => d.classList && matchesSelector(d, sel)) || null;
  };
  sm.seen = seen;
  sm.exports = sm.window.__SCENE_MATCHER_TEST__.exports;
  return sm;
}

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("sets the value, dispatches input, clicks the row's own button not ours", () => {
  const r = makeRow(7);
  const sm = load([r]);
  sm.exports.handleSelectMatch("7", "uuid-1");
  assert.strictEqual(r.input.value, "uuid-1");
  const ev = r.input.events.find((e) => e.type === "input");
  assert.ok(ev && ev.bubbles === true);
  assert.strictEqual(r.own.clicks, 1);
  assert.strictEqual(r.mine.clicks, 0);
  assert.ok(!r.input.events.some((e) => e.type === "keypress"));
});

test("re-queries the row at Select time (the row captured at click time may be gone)", () => {
  const stale = makeRow(9);
  const fresh = makeRow(9);
  const sm = load([fresh]);
  sm.window.__SCENE_MATCHER_TEST__.setState({ currentSceneElement: stale });
  sm.exports.handleSelectMatch("9", "u");
  assert.strictEqual(fresh.own.clicks, 1);
  assert.strictEqual(stale.own.clicks, 0);
});

test("Select fills the row whose own scene link is exactly the scene, not /scenes/123 or a stash-box link", () => {
  // Row order matters: a substring match on "/scenes/12" hits row 123 and the
  // stash-box pill "https://stashdb.org/scenes/12ab..." before it reaches row 12.
  const r123 = makeRow(123);
  const r7 = makeRow(7, { pills: ["https://stashdb.org/scenes/12ab34cd-0000-4000-8000-000000000000"] });
  const r12 = makeRow(12, { href: "/scenes/12?sceneIndex=4&qfp=1" });
  const sm = load([r123, r7, r12]);
  sm.exports.handleSelectMatch("12", "uuid-12");
  assert.strictEqual(r12.input.value, "uuid-12");
  assert.strictEqual(r12.own.clicks, 1);
  for (const other of [r123, r7]) {
    assert.strictEqual(other.input.value, "", "filled the wrong row");
    assert.strictEqual(other.own.clicks, 0, "searched the wrong row");
  }
});

test("getSceneIdFromElement reads the row's own local link, exactly", () => {
  const sm = load([]);
  const id = sm.exports.getSceneIdFromElement;
  assert.strictEqual(id(makeRow(123)), "123");
  assert.strictEqual(id(makeRow(12, { href: "/scenes/12?sceneIndex=4" })), "12");
  assert.strictEqual(id(makeRow(12, { href: "/stash/scenes/12/" })), "12");
  // A stash-box link first (absolute, other origin) is not the scene's id
  const pillFirst = node("div", { cls: ["search-item"] });
  pillFirst.appendChild(node("a", { href: "https://stashdb.org/scenes/12ab34cd-0000-4000-8000-000000000000" }));
  pillFirst.appendChild(node("a", { href: "/scenes/7" }));
  assert.strictEqual(id(pillFirst), "7");
  const pillOnly = node("div", { cls: ["search-item"] });
  pillOnly.appendChild(node("a", { href: "https://stashdb.org/scenes/12345678-0000-4000-8000-000000000000" }));
  assert.strictEqual(id(pillOnly), null);
  const notAScene = node("div", { cls: ["search-item"] });
  notAScene.appendChild(node("a", { href: "/scenes/12abc" }));
  assert.strictEqual(id(notAScene), null);
});

test("uses the native value setter when available", () => {
  const r = makeRow(3);
  const sm = load([r]);
  let called = 0;
  sm.window.HTMLInputElement.prototype = Object.defineProperty({}, "value", {
    set(v) { called++; this._v = v; }, get() { return this._v; }, configurable: true,
  });
  sm.exports.handleSelectMatch("3", "abc");
  assert.strictEqual(called, 1);
  assert.strictEqual(r.input._v, "abc");
});

test("disabled button: dispatches a React-friendly Enter keypress instead", () => {
  const r = makeRow(4, { disabled: true });
  const sm = load([r]);
  sm.exports.handleSelectMatch("4", "u");
  assert.strictEqual(r.own.clicks, 0);
  const k = r.input.events.find((e) => e.type === "keypress");
  assert.ok(k, "keypress dispatched");
  assert.strictEqual(k.key, "Enter");
  assert.strictEqual(k.keyCode, 13);
  assert.strictEqual(k.charCode, 13);
  assert.strictEqual(k.which, 13);
  assert.strictEqual(k.bubbles, true);
});

test("no search button: Enter keypress", () => {
  const r = makeRow(5, { noButton: true });
  const sm = load([r]);
  sm.exports.handleSelectMatch("5", "u");
  assert.ok(r.input.events.some((e) => e.type === "keypress"));
  assert.strictEqual(r.mine.clicks, 0);
});

test("missing row or input shows a visible error", () => {
  const sm = load([]);
  sm.exports.handleSelectMatch("1", "u");
  const shown = () => JSON.stringify(sm.document.body.children.map((c) => c.textContent + c.innerHTML));
  assert.ok(/Could not find/.test(shown()), "row error shown: " + shown());
  const r = makeRow(2, { noInput: true });
  const sm2 = load([r]);
  sm2.exports.handleSelectMatch("2", "u");
  assert.ok(/search box|search input/i.test(JSON.stringify(sm2.document.body.children.map((c) => c.textContent + c.innerHTML))));
  assert.strictEqual(r.own.clicks, 0);
});

test("closes the modal after a successful handoff", () => {
  const r = makeRow(8);
  const sm = load([r]);
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
