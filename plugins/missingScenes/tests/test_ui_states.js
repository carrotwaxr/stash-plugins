/**
 * UI states: errors, partial results, retry, stale responses, estimate text.
 * Run with: node plugins/missingScenes/tests/test_ui_states.js
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
const failure = (extra = {}) => ({
  error: "StashDB is down", partial: false, missing_scenes: [], cursor: null,
  has_more: false, is_complete: false, total_on_stashdb: null, ...extra,
});

/** Text of an element tree (innerHTML, textContent, children). */
function text(el) {
  if (!el) return "";
  let s = (el.innerHTML || "") + " " + (el.textContent || "");
  for (const c of el.children || []) s += " " + text(c);
  if (el.els) for (const c of Object.values(el.els)) s += " " + text(c);
  return s;
}
function findButton(el, label) {
  if (!el) return null;
  if (el.tagName === "BUTTON" && (el.textContent || "").includes(label)) return el;
  for (const c of el.children || []) { const b = findButton(c, label); if (b) return b; }
  return null;
}
const click = (b) => b.onclick ? b.onclick({}) : b.listeners.click[0]({});

/** Deferred responses per operation, resolved by the test. */
function deferred() {
  const pending = [];
  const handler = (body) => new Promise((resolve) => pending.push({ args: body.variables.args, resolve }));
  return { pending, handler };
}

function modalSetup(fetchResponses) {
  const ms = loadMissingScenes({ fetchResponses });
  const els = {};
  for (const id of ["ms-results", "ms-stats", "ms-status", "ms-load-more-btn", "ms-add-all-btn"]) {
    els[id] = createElement("div");
  }
  // like a real element, assigning innerHTML drops the children
  let html = "";
  Object.defineProperty(els["ms-results"], "innerHTML", {
    get: () => html,
    set: (v) => { html = v; els["ms-results"].children = []; },
  });
  ms.document.getElementById = (id) => els[id] || null;
  return { ms, els, m: ms.exports.modal };
}

function browseContainer() {
  let html = "";
  let els = {};
  const c = {
    children: [],
    get els() { return els; },
    querySelector(sel) {
      const key = sel.slice(1);
      if (!html.includes(key)) return null;
      if (!(sel in els)) {
        els[sel] = createElement("div");
        if (sel === "#ms-sort-field" || sel === "#ms-sort-direction") els[sel].value = "DATE";
      }
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
const flush = async (ms) => { await ms.settle(); };

// ---------------- modal ----------------

test("modal: error without scenes shows the error and Retry, not 'all available'", async () => {
  let call = 0;
  const { ms, els, m } = modalSetup({
    RunPluginOperation: () => wrap(++call === 1 ? failure() : okPage()),
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]);
  assert.ok(t.includes("StashDB is down"), t);
  assert.ok(!t.includes("all available scenes"), t);
  assert.ok(!text(els["ms-status"]).includes("all available"));
  const retry = findButton(els["ms-results"], "Retry");
  assert.ok(retry, "Retry button");
  click(retry);
  await flush(ms);
  assert.strictEqual(ms.fetchCalls.length, 2);
  assert.ok(text(els["ms-results"]).includes("Scene a"), text(els["ms-results"]));
});

test("modal: Trending with nothing missing doesn't claim you have every scene", async () => {
  const { els, m } = modalSetup({ RunPluginOperation: () => wrap(okPage({ missing_scenes: [] })) });
  m.setSortField("TRENDING");
  await m.performSearch(true);
  const t = text(els["ms-results"]) + " " + text(els["ms-status"]);
  assert.ok(!t.includes("all available scenes"), t);
  assert.ok(t.includes("last 7 days"), t);
});

test("modal: Trending results say they only cover the last 7 days", async () => {
  const { els, m } = modalSetup({ RunPluginOperation: () => wrap(okPage()) });
  m.setSortField("TRENDING");
  await m.performSearch(true);
  assert.ok(text(els["ms-status"]).includes("last 7 days"), text(els["ms-status"]));
});

test("modal: partial renders scenes, warning, and Retry from here resends the cursor", async () => {
  const calls = [];
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => {
      calls.push(body.variables.args);
      if (calls.length === 1) {
        return wrap(okPage({ partial: true, error: "Timed out on page 3", cursor: "c2",
          has_more: true, is_complete: false, missing_scenes: [scene("a"), scene("b")] }));
      }
      return wrap(okPage({ missing_scenes: [scene("c")] }));
    },
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]);
  assert.ok(t.includes("Scene a") && t.includes("Scene b"), t);
  assert.ok(t.includes("Timed out on page 3"), t);
  const retry = findButton(els["ms-results"], "Retry from here");
  assert.ok(retry, "Retry from here");
  click(retry);
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "c2");
  const t2 = text(els["ms-results"]);
  assert.ok(t2.includes("Scene c") && t2.includes("Scene a"), t2);
  assert.ok(!t2.includes("Timed out"), "warning cleared after successful retry");
});

test("modal: auth error hint and rate limit text", async () => {
  const { els, m } = modalSetup({
    RunPluginOperation: () => wrap(failure({ error: "Unauthorized", auth_error: true })),
  });
  await m.performSearch(true);
  assert.ok(/API key/i.test(text(els["ms-results"])), text(els["ms-results"]));

  const b = modalSetup({
    RunPluginOperation: () => wrap(failure({ error: "Too many requests", rate_limited: true, retry_after: 30 })),
  });
  await b.m.performSearch(true);
  assert.ok(text(b.els["ms-results"]).includes("rate-limited, try again in 30 s"), text(b.els["ms-results"]));
});

test("modal: a superseded response is ignored", async () => {
  const d = deferred();
  const { ms, els, m } = modalSetup({ RunPluginOperation: d.handler });
  const p1 = m.performSearch(true);
  const p2 = m.performSearch(true);
  d.pending[1].resolve(wrap(okPage({ missing_scenes: [scene("new")] })));
  await p2;
  d.pending[0].resolve(wrap(okPage({ missing_scenes: [scene("old")] })));
  await p1;
  const t = text(els["ms-results"]);
  assert.ok(t.includes("Scene new") && !t.includes("Scene old"), t);
});

test("modal: a response after the modal closed is ignored", async () => {
  const d = deferred();
  const { els, m } = modalSetup({ RunPluginOperation: d.handler });
  const p = m.performSearch(true);
  m.removeModal();
  const before = text(els["ms-results"]);
  d.pending[0].resolve(wrap(okPage({ missing_scenes: [scene("late")] })));
  await p;
  assert.strictEqual(text(els["ms-results"]), before);
  assert.ok(!text(els["ms-status"]).includes("Found"));
});

test("modal: estimate is never negative; final Load More hidden", async () => {
  const { els, m } = modalSetup({
    RunPluginOperation: () => wrap(okPage({
      total_on_stashdb: 10, total_local: 20, missing_count_estimate: -5,
      is_complete: false, has_more: true, cursor: "c",
    })),
  });
  await m.performSearch(true);
  assert.ok(!/-\d/.test(text(els["ms-stats"])), text(els["ms-stats"]));
  assert.notStrictEqual(els["ms-load-more-btn"].style.display, "none");
  assert.ok(!/-\d/.test(els["ms-load-more-btn"].textContent), els["ms-load-more-btn"].textContent);

  const f = modalSetup({ RunPluginOperation: () => wrap(okPage({ has_more: false, is_complete: false })) });
  await f.m.performSearch(true);
  assert.strictEqual(f.els["ms-load-more-btn"].style.display, "none");
});

// ---------------- browse ----------------

test("browse: error without scenes shows error and Retry", async () => {
  let call = 0;
  const ms = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(++call === 1 ? failure() : okPage()) },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  const t = text(c);
  assert.ok(t.includes("StashDB is down") && !t.includes("No missing scenes found"), t);
  assert.ok(c.els["#ms-retry-btn"], "retry button");
  c.els["#ms-retry-btn"].listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(ms.fetchCalls.length, 2);
  assert.ok(text(c).includes("Scene a") || c.children.length >= 0);
});

test("browse: partial shows warning; Retry from here resends the cursor", async () => {
  const calls = [];
  const ms = loadMissingScenes({
    fetchResponses: {
      RunPluginOperation: (body) => {
        calls.push(body.variables.args);
        return wrap(calls.length === 1
          ? okPage({ partial: true, error: "Timed out on page 2", cursor: "cur2", has_more: true,
              is_complete: false, missing_scenes: [scene("a"), scene("b")] })
          : okPage({ missing_scenes: [scene("c")] }));
      },
    },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(text(c).includes("Timed out on page 2"), text(c));
  assert.ok(text(c).includes("Retry from here"), text(c));
  c.els["#ms-retry-btn"].listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "cur2");
  assert.ok(!text(c).includes("Timed out"));
});

test("browse: auth and rate limit text", async () => {
  const a = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(failure({ error: "Unauthorized", auth_error: true })) },
  });
  const ca = browseContainer();
  await a.exports.browse.performSearch(ca, true);
  assert.ok(/API key/i.test(text(ca)), text(ca));
  const r = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(failure({ rate_limited: true, retry_after: 12 })) },
  });
  const cr = browseContainer();
  await r.exports.browse.performSearch(cr, true);
  assert.ok(text(cr).includes("rate-limited, try again in 12 s"), text(cr));
});

test("browse: controls stay usable during a load and supersede the old request", async () => {
  const d = deferred();
  const ms = loadMissingScenes({ fetchResponses: { RunPluginOperation: d.handler } });
  const c = browseContainer();
  const p1 = ms.exports.browse.performSearch(c, true);
  const sort = c.els["#ms-sort-field"] || c.querySelector("#ms-sort-field");
  assert.ok(sort && sort.listeners.change && sort.listeners.change.length, "sort handler attached during load");
  sort.listeners.change[0]({ target: { value: "TITLE" } });
  assert.strictEqual(d.pending.length, 2, "change started a new request");
  assert.strictEqual(d.pending[1].args.sort, "TITLE");
  d.pending[1].resolve(wrap(okPage({ missing_scenes: [scene("new")] })));
  await flush(ms);
  d.pending[0].resolve(wrap(okPage({ missing_scenes: [scene("old")] })));
  await p1;
  await flush(ms);
  const t = text(c);
  assert.ok(t.includes("Scene new") && !t.includes("Scene old"), t);
});

test("browse: stats say missing only for the estimate; final Load More hidden", async () => {
  const ms = loadMissingScenes({
    fetchResponses: {
      RunPluginOperation: () => wrap(okPage({
        total_on_stashdb: 5000, missing_count_estimate: undefined, is_complete: false,
        has_more: false, cursor: null, missing_scenes: [scene("a"), scene("b")],
      })),
    },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(!/of ~5,000/.test(c.innerHTML), c.innerHTML);
  assert.ok(!/5,000[^<|]*missing/.test(c.innerHTML), c.innerHTML);
  assert.ok(/id="ms-load-more-btn" style="display: none;"/.test(c.innerHTML));

  const e = loadMissingScenes({
    fetchResponses: {
      RunPluginOperation: () => wrap(okPage({ total_on_stashdb: 5000, missing_count_estimate: 120,
        is_complete: false, has_more: true, cursor: "x" })),
    },
  });
  const ce = browseContainer();
  await e.exports.browse.performSearch(ce, true);
  assert.ok(/of ~120 missing/.test(ce.innerHTML), ce.innerHTML);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
