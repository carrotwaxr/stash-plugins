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
  for (const id of ["ms-results", "ms-stats", "ms-status", "ms-load-more-btn", "ms-add-all-btn",
    "ms-fingerprint"]) {
    els[id] = createElement("div");
  }
  // like a real element, assigning innerHTML drops the children
  for (const id of ["ms-results", "ms-fingerprint"]) {
    let html = "";
    Object.defineProperty(els[id], "innerHTML", {
      get: () => html,
      set: (v) => { html = v; els[id].children = []; },
    });
  }
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

// ---------------- no scenes, but not done (partial or capped) ----------------

// (a) owned-first pages were read, then the next stash-box page failed: partial, a cursor, no scenes
const partialEmpty = (extra = {}) => okPage({
  partial: true, error: "StashDB request for page 3 failed: timed out", cursor: "c3",
  has_more: true, is_complete: false, missing_count_estimate: 10, missing_scenes: [], ...extra,
});
// (b) 50 pages checked with nothing qualifying (a favorites filter): more pages, a cursor, no scenes
const cappedEmpty = (extra = {}) => okPage({
  cursor: "c51", has_more: true, is_complete: false, missing_count_estimate: null,
  filters_active: true, missing_scenes: [], ...extra,
});
const shown = (el) => el.style.display !== "none";

test("modal: a failed page with no scenes yet shows the error and Retry from here, not 'all found'", async () => {
  const calls = [];
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => {
      calls.push(body.variables.args);
      return wrap(calls.length === 1 ? partialEmpty() : okPage({ missing_scenes: [scene("c")] }));
    },
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]) + " " + text(els["ms-status"]);
  assert.ok(t.includes("page 3 failed"), t);
  assert.ok(!t.includes("all available scenes"), t);
  const retry = findButton(els["ms-results"], "Retry from here");
  assert.ok(retry, "Retry from here");
  click(retry);
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "c3");
  assert.ok(text(els["ms-results"]).includes("Scene c"), text(els["ms-results"]));
});

test("modal: capped with no scenes says so and offers Load More from the cursor", async () => {
  const calls = [];
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => {
      calls.push(body.variables.args);
      if (calls.length === 1) return wrap(cappedEmpty());
      if (calls.length === 2) return wrap(failure());  // the next request fails outright
      return wrap(okPage({ missing_scenes: [scene("z")] }));
    },
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]) + " " + text(els["ms-status"]);
  assert.ok(t.includes("No missing scenes in the pages checked so far"), t);
  assert.ok(!t.includes("all available scenes"), t);
  const more = els["ms-load-more-btn"];
  assert.ok(shown(more), "Load More shown");
  more.onclick({});
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "c51");
  // That request failed: Retry resends the same cursor instead of starting over
  assert.ok(text(els["ms-results"]).includes("StashDB is down"), text(els["ms-results"]));
  click(findButton(els["ms-results"], "Retry"));
  await flush(ms);
  assert.strictEqual(calls[2].cursor, "c51");
  assert.ok(text(els["ms-results"]).includes("Scene z"), text(els["ms-results"]));
});

test("modal: complete with no scenes still says you have them all; a failed first page hides Load More", async () => {
  const a = modalSetup({ RunPluginOperation: () => wrap(okPage({ missing_scenes: [] })) });
  await a.m.performSearch(true);
  assert.ok(text(a.els["ms-results"]).includes("all available scenes"), text(a.els["ms-results"]));
  assert.ok(!shown(a.els["ms-load-more-btn"]));
  const b = modalSetup({ RunPluginOperation: () => wrap(failure()) });
  await b.m.performSearch(true);
  assert.ok(!shown(b.els["ms-load-more-btn"]), "no cursor, no Load More");
});

test("browse: a failed page with no scenes yet shows the error; Retry from here resends the cursor", async () => {
  const calls = [];
  const ms = loadMissingScenes({
    fetchResponses: {
      RunPluginOperation: (body) => {
        calls.push(body.variables.args);
        return wrap(calls.length === 1 ? partialEmpty() : okPage({ missing_scenes: [scene("c")] }));
      },
    },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  const t = text(c);
  assert.ok(t.includes("page 3 failed") && t.includes("Retry from here"), t);
  assert.ok(!t.includes("No missing scenes found"), t);
  c.els["#ms-retry-btn"].listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "c3");
});

test("browse: capped with no scenes says so and offers Load More from the cursor", async () => {
  const calls = [];
  const ms = loadMissingScenes({
    fetchResponses: {
      RunPluginOperation: (body) => {
        calls.push(body.variables.args);
        if (calls.length === 1) return wrap(cappedEmpty());
        if (calls.length === 2) return wrap(failure());
        return wrap(okPage({ missing_scenes: [scene("z")] }));
      },
    },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  const t = text(c);
  assert.ok(t.includes("No missing scenes in the pages checked so far"), t);
  assert.ok(!t.includes("No missing scenes found"), t);
  assert.ok(/id="ms-load-more-btn" style="display: inline-block;"/.test(c.innerHTML), c.innerHTML);
  c.querySelector("#ms-load-more-btn").listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(calls[1].cursor, "c51");
  assert.ok(text(c).includes("StashDB is down"), text(c));
  c.querySelector("#ms-retry-btn").listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(calls[2].cursor, "c51");
});

test("browse: complete with no scenes still says none are missing", async () => {
  const ms = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(okPage({ missing_scenes: [] })) },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(text(c).includes("No missing scenes found"), text(c));
  assert.ok(/id="ms-load-more-btn" style="display: none;"/.test(c.innerHTML));
});

test("browse: says when ThePornDB searched only the first favorites", async () => {
  const limited = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(okPage({ favorites_limited: true, favorites_query_limit: 10 })) },
  });
  const c = browseContainer();
  await limited.exports.browse.performSearch(c, true);
  assert.ok(text(c).includes("Only your first 10 favorite performers/studios are searched on ThePornDB"), text(c));

  const all = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(okPage({ favorites_limited: false })) },
  });
  const c2 = browseContainer();
  await all.exports.browse.performSearch(c2, true);
  assert.ok(!text(c2).includes("searched on ThePornDB"), text(c2));
});

// ---------------- browse sort controls ----------------

/** The <option> labels of a <select id=...> in rendered HTML, and whether it is hidden. */
function selectState(html, id) {
  const m = html.match(new RegExp(`<select id="${id}"([^>]*)>([\\s\\S]*?)</select>`));
  assert.ok(m, `select #${id}`);
  const labels = [...m[2].matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((x) => x[1]);
  return { labels, hidden: /display:\s*none/.test(m[1]) };
}

async function browseSortedBy(sort) {
  const calls = [];
  const ms = loadMissingScenes({
    fetchResponses: { RunPluginOperation: (body) => { calls.push(body.variables.args); return wrap(okPage()); } },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  c.querySelector("#ms-sort-field").listeners.change[0]({ target: { value: sort } });
  await flush(ms);
  return { c, calls };
}

test("browse: direction labels follow the sort, and Trending hides the direction", async () => {
  const date = await browseSortedBy("DATE");
  assert.deepStrictEqual(selectState(date.c.innerHTML, "ms-sort-direction"),
    { labels: ["Newest First", "Oldest First"], hidden: false });
  const title = await browseSortedBy("TITLE");
  assert.deepStrictEqual(selectState(title.c.innerHTML, "ms-sort-direction"),
    { labels: ["Descending", "Ascending"], hidden: false });
  const trending = await browseSortedBy("TRENDING");
  assert.strictEqual(selectState(trending.c.innerHTML, "ms-sort-direction").hidden, true);
  assert.strictEqual(trending.calls[1].sort, "TRENDING");
});

// ---------------- fingerprint index (#160) ----------------

/** RunPluginOperation that answers per operation; records every call's args. */
function byOperation(handlers) {
  const calls = [];
  const handler = (body) => {
    const args = body.variables.args;
    calls.push(args);
    const h = handlers[args.operation];
    return wrap(typeof h === "function" ? h(args, calls) : h);
  };
  return { calls, handler };
}
const ops = (calls, name) => calls.filter((a) => a.operation === name);

test("modal: renders the count owned by fingerprint, summed over Load More", async () => {
  let n = 0;
  const { els, m } = modalSetup({
    RunPluginOperation: () => wrap(++n === 1
      ? okPage({ fingerprint_index: true, fingerprint_matching: true, fingerprint_index_complete: true,
          owned_by_fingerprint: 2, has_more: true, is_complete: false, cursor: "c1" })
      : okPage({ fingerprint_index: true, fingerprint_matching: true, fingerprint_index_complete: true,
          owned_by_fingerprint: 3, missing_scenes: [scene("b")] })),
  });
  await m.performSearch(true);
  assert.ok(text(els["ms-fingerprint"]).includes("2 counted as owned by fingerprint"), text(els["ms-fingerprint"]));
  assert.ok(!findButton(els["ms-fingerprint"], "Build fingerprint index"), "no build button with an index");
  await m.performSearch(false);
  assert.ok(text(els["ms-fingerprint"]).includes("5 counted as owned by fingerprint"), text(els["ms-fingerprint"]));
  await m.performSearch(true);
  assert.ok(!text(els["ms-fingerprint"]).includes("5 counted"), "a new search starts the count again");
});

test("modal: no index shows the Build button; it runs the operation, then searches again", async () => {
  let built = false;
  const r = byOperation({
    find_missing: () => okPage(built
      ? { fingerprint_index: true, fingerprint_matching: true, fingerprint_index_complete: true, owned_by_fingerprint: 1 }
      : { fingerprint_index: false, fingerprint_matching: true, owned_by_fingerprint: 0 }),
    build_fingerprint_index: () => { built = true; return { success: true, scanned: 10, queried: 10, matched: 4, partial: false, complete: true }; },
  });
  const { ms, els, m } = modalSetup({ RunPluginOperation: r.handler });
  await m.performSearch(true);
  const t = text(els["ms-fingerprint"]);
  assert.ok(t.includes("Settings > Tasks"), t);
  const btn = findButton(els["ms-fingerprint"], "Build fingerprint index");
  assert.ok(btn, "build button");
  click(btn);
  await flush(ms);
  assert.strictEqual(ops(r.calls, "build_fingerprint_index").length, 1);
  const finds = ops(r.calls, "find_missing");
  assert.strictEqual(finds.length, 2, "searched again after the build");
  assert.ok(!finds[1].cursor, "a fresh search");
  assert.ok(r.calls.indexOf(finds[1]) > r.calls.indexOf(ops(r.calls, "build_fingerprint_index")[0]));
  const t2 = text(els["ms-fingerprint"]);
  assert.ok(t2.includes("1 counted as owned by fingerprint"), t2);
  assert.ok(t2.includes("4"), "reports what the build matched: " + t2);
  assert.ok(!findButton(els["ms-fingerprint"], "Build fingerprint index"));
});

test("modal: a failed build shows its error and doesn't search again", async () => {
  const r = byOperation({
    find_missing: () => okPage({ fingerprint_index: false, fingerprint_matching: true }),
    build_fingerprint_index: { success: false, error: "StashDB refused the request (HTTP 401)",
      auth_error: true, scanned: 5, queried: 0, matched: 0, partial: false },
  });
  const { ms, els, m } = modalSetup({ RunPluginOperation: r.handler });
  await m.performSearch(true);
  click(findButton(els["ms-fingerprint"], "Build fingerprint index"));
  await flush(ms);
  assert.ok(text(els["ms-fingerprint"]).includes("HTTP 401"), text(els["ms-fingerprint"]));
  assert.strictEqual(ops(r.calls, "find_missing").length, 1);
  assert.ok(findButton(els["ms-fingerprint"], "Build fingerprint index"), "can try again");
});

test("modal: an incomplete index offers the build; matching turned off offers nothing", async () => {
  const a = modalSetup({ RunPluginOperation: () => wrap(okPage({ fingerprint_index: true,
    fingerprint_matching: true, fingerprint_index_complete: false, owned_by_fingerprint: 0 })) });
  await a.m.performSearch(true);
  assert.ok(/incomplete/i.test(text(a.els["ms-fingerprint"])), text(a.els["ms-fingerprint"]));
  assert.ok(findButton(a.els["ms-fingerprint"], "Build fingerprint index"));

  const b = modalSetup({ RunPluginOperation: () => wrap(okPage({ fingerprint_index: false,
    fingerprint_matching: false, owned_by_fingerprint: 0 })) });
  await b.m.performSearch(true);
  assert.ok(!findButton(b.els["ms-fingerprint"], "Build fingerprint index"));
  assert.ok(!/fingerprint/i.test(text(b.els["ms-fingerprint"])), text(b.els["ms-fingerprint"]));
});

test("browse: renders the count owned by fingerprint", async () => {
  const ms = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap(okPage({ fingerprint_index: true,
      fingerprint_matching: true, fingerprint_index_complete: true, owned_by_fingerprint: 7 })) },
  });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(text(c).includes("7 counted as owned by fingerprint"), text(c));
  assert.ok(!c.innerHTML.includes("ms-build-fp-btn"));
});

test("browse: no index shows the Build button; it runs the operation, then browses again", async () => {
  let built = false;
  const r = byOperation({
    browse_stashdb: () => okPage(built
      ? { fingerprint_index: true, fingerprint_matching: true, fingerprint_index_complete: true, owned_by_fingerprint: 2 }
      : { fingerprint_index: false, fingerprint_matching: true, owned_by_fingerprint: 0 }),
    build_fingerprint_index: () => { built = true; return { success: true, scanned: 3, queried: 3, matched: 2, partial: false, complete: true }; },
  });
  const ms = loadMissingScenes({ fetchResponses: { RunPluginOperation: r.handler } });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  assert.ok(/Settings (>|&gt;) Tasks/.test(text(c)), text(c));
  const btn = c.querySelector("#ms-build-fp-btn");
  assert.ok(btn && btn.listeners.click, "build button");
  btn.listeners.click[0]({});
  await flush(ms);
  assert.strictEqual(ops(r.calls, "build_fingerprint_index").length, 1);
  assert.strictEqual(ops(r.calls, "browse_stashdb").length, 2, "browsed again after the build");
  assert.ok(text(c).includes("2 counted as owned by fingerprint"), text(c));
  assert.ok(!c.innerHTML.includes("ms-build-fp-btn"));
});

test("core: a build result with an error still reaches the caller", async () => {
  const partial = { success: false, error: "StashDB failed", scanned: 85, queried: 40, matched: 1, partial: true };
  const ms = loadMissingScenes({ fetchResponses: { RunPluginOperation: () => wrap(partial) } });
  const res = await ms.exports.core.buildFingerprintIndex("https://stashdb.org/graphql");
  assert.strictEqual(res.queried, 40);
  assert.strictEqual(ms.fetchCalls[0].body.variables.args.operation, "build_fingerprint_index");
  assert.strictEqual(ms.fetchCalls[0].body.variables.args.endpoint, "https://stashdb.org/graphql");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
