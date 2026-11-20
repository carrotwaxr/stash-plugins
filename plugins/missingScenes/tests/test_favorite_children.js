/**
 * "Include sub-studios" / "Include sub-tags" checkboxes and the response's warnings (#179).
 * Run with: node plugins/missingScenes/tests/test_favorite_children.js
 */
const assert = require("assert");
const { loadMissingScenes, createElement } = require("./harness");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const wrap = (output) => ({ data: { runPluginOperation: output } });
const scene = (id) => ({ stash_id: id, title: "Scene " + id, performers: [], tags: [] });
const okPage = (extra = {}) => ({
  entity_name: "Ann", entity_type: "performer", stashdb_name: "StashDB",
  stashdb_url: "https://stashdb.org", total_on_stashdb: 100, total_local: 90,
  missing_count_estimate: 10, cursor: null, has_more: false, is_complete: true,
  missing_scenes: [scene("a")], ...extra,
});
const NASTY = "<img src=x onerror=alert(1)> & co";
const ESCAPED = "&lt;img src=x onerror=alert(1)&gt; &amp; co";

/** An element whose innerHTML assignment drops its children, like a real one. */
function realishElement() {
  const el = createElement("div");
  let html = "";
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (v) => { html = v; el.children = []; },
  });
  return el;
}

/** The first element with this id in a tree. */
function byId(el, id) {
  if (!el) return null;
  if (el.id === id) return el;
  for (const c of el.children || []) { const f = byId(c, id); if (f) return f; }
  return null;
}

function recordingResponses(page = () => okPage()) {
  const calls = [];
  return {
    calls,
    fetchResponses: { RunPluginOperation: (body) => { calls.push(body.variables.args); return wrap(page()); } },
  };
}

function modalSetup(page) {
  const rec = recordingResponses(page);
  const ms = loadMissingScenes({ fetchResponses: rec.fetchResponses });
  const els = {};
  for (const id of ["ms-results", "ms-stats", "ms-status", "ms-load-more-btn", "ms-add-all-btn",
    "ms-fingerprint", "ms-filter-controls", "ms-notices"]) {
    els[id] = realishElement();
  }
  ms.document.getElementById = (id) => els[id] || null;
  return { ms, els, m: ms.exports.modal, calls: rec.calls };
}

function browseContainer() {
  let html = "";
  let els = {};
  const c = {
    children: [],
    querySelector(sel) {
      const key = sel.slice(1);
      if (!html.includes(`id="${key}"`)) return null;
      if (!(sel in els)) els[sel] = createElement("div");
      return els[sel];
    },
    appendChild(x) { c.children.push(x); return x; },
  };
  Object.defineProperty(c, "innerHTML", {
    get: () => html,
    set: (v) => { html = v; els = {}; c.children = []; },
  });
  return c;
}

/** The attributes of <input id=...> in rendered HTML. */
function inputTag(html, id) {
  const m = html.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`));
  assert.ok(m, `input #${id}`);
  return m[0];
}

const toggle = async (ms, input, checked) => {
  input.checked = checked;
  input.onchange();
  await ms.settle();
};
const change = async (ms, c, id, checked) => {
  c.querySelector(`#${id}`).listeners.change[0]({ target: { checked } });
  await ms.settle();
};

// ---------------- entity page (modal) ----------------

test("modal: sub-studio and sub-tag checkboxes are disabled until their filter is on", () => {
  const { els, m } = modalSetup();
  m.updateFilterControls();
  const root = els["ms-filter-controls"];
  for (const id of ["ms-filter-studios-children", "ms-filter-tags-children"]) {
    const input = byId(root, id);
    assert.ok(input, id);
    assert.strictEqual(input.disabled, true, id);
    assert.strictEqual(input.checked, false, id);
  }
  const t = JSON.stringify(root.children.map((l) => l.children.map((x) => x.textContent)));
  assert.ok(t.includes("Include sub-studios") && t.includes("Include sub-tags"), t);
});

test("modal: include-children args go out only while the parent filter is on", async () => {
  const { ms, els, m, calls } = modalSetup();
  m.updateFilterControls();
  const root = els["ms-filter-controls"];

  await toggle(ms, byId(root, "ms-filter-studios"), true);
  let last = calls[calls.length - 1];
  assert.strictEqual(last.filter_favorite_studios, true);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
  assert.strictEqual(byId(root, "ms-filter-studios-children").disabled, false, "enabled with its filter");

  await toggle(ms, byId(root, "ms-filter-studios-children"), true);
  last = calls[calls.length - 1];
  assert.strictEqual(last.filter_favorite_studios, true);
  assert.strictEqual(last.favorite_studios_include_children, true);
  assert.strictEqual(last.favorite_tags_include_children, undefined);

  // Turning the filter off keeps the choice for later, but sends neither
  await toggle(ms, byId(root, "ms-filter-studios"), false);
  last = calls[calls.length - 1];
  assert.strictEqual(last.filter_favorite_studios, undefined);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
  const child = byId(root, "ms-filter-studios-children");
  assert.strictEqual(child.disabled, true);
  assert.strictEqual(child.checked, true, "the choice is kept");

  await toggle(ms, byId(root, "ms-filter-tags"), true);
  await toggle(ms, byId(root, "ms-filter-tags-children"), true);
  last = calls[calls.length - 1];
  assert.strictEqual(last.favorite_tags_include_children, true);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
});

test("modal: the response's warnings are shown, escaped", async () => {
  const { els, m } = modalSetup(() => okPage({ warnings: [NASTY, "second"] }));
  await m.performSearch(true);
  const html = els["ms-notices"].innerHTML;
  assert.ok(html.includes(ESCAPED), html);
  assert.ok(!html.includes("<img"), html);
  assert.ok(html.includes("second"), html);
  assert.notStrictEqual(els["ms-notices"].style.display, "none");
});

test("modal: no warnings, no notice", async () => {
  const { els, m } = modalSetup(() => okPage());
  await m.performSearch(true);
  assert.strictEqual(els["ms-notices"].innerHTML, "");
  assert.strictEqual(els["ms-notices"].style.display, "none");
});

// ---------------- browse page ----------------

test("browse: sub-studio and sub-tag checkboxes are disabled until their filter is on", async () => {
  const { fetchResponses } = recordingResponses();
  const ms = loadMissingScenes({ fetchResponses });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(/disabled/.test(inputTag(c.innerHTML, "ms-filter-studios-children")));
  assert.ok(/disabled/.test(inputTag(c.innerHTML, "ms-filter-tags-children")));
  assert.ok(c.innerHTML.includes("Include sub-studios") && c.innerHTML.includes("Include sub-tags"));
});

test("browse: include-children args go out only while the parent filter is on", async () => {
  const { calls, fetchResponses } = recordingResponses();
  const ms = loadMissingScenes({ fetchResponses });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.strictEqual(calls[0].favorite_studios_include_children, undefined);

  await change(ms, c, "ms-filter-studios", true);
  let last = calls[calls.length - 1];
  assert.strictEqual(last.filter_favorite_studios, true);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
  assert.ok(!/disabled/.test(inputTag(c.innerHTML, "ms-filter-studios-children")));

  await change(ms, c, "ms-filter-studios-children", true);
  last = calls[calls.length - 1];
  assert.strictEqual(last.favorite_studios_include_children, true);
  assert.ok(/checked/.test(inputTag(c.innerHTML, "ms-filter-studios-children")));

  await change(ms, c, "ms-filter-studios", false);
  last = calls[calls.length - 1];
  assert.strictEqual(last.filter_favorite_studios, false);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
  const tag = inputTag(c.innerHTML, "ms-filter-studios-children");
  assert.ok(/disabled/.test(tag) && /checked/.test(tag), tag);

  await change(ms, c, "ms-filter-tags", true);
  await change(ms, c, "ms-filter-tags-children", true);
  last = calls[calls.length - 1];
  assert.strictEqual(last.favorite_tags_include_children, true);
  assert.strictEqual(last.favorite_studios_include_children, undefined);
});

test("browse: the response's warnings are shown, escaped", async () => {
  const { fetchResponses } = recordingResponses(() => okPage({ warnings: [NASTY] }));
  const ms = loadMissingScenes({ fetchResponses });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(c.innerHTML.includes(ESCAPED), c.innerHTML);
  assert.ok(!c.innerHTML.includes("<img"), c.innerHTML);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
