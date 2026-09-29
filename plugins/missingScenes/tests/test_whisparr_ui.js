/**
 * Whisparr UI: StashDB only, clear errors, status-unavailable banner.
 * Run with: node plugins/missingScenes/tests/test_whisparr_ui.js
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
  whisparr_configured: true, missing_scenes: [scene("a")], ...extra,
});

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
const ev = { stopPropagation() {}, preventDefault() {} };
const click = (b) => b.onclick ? b.onclick(ev) : b.listeners.click[0](ev);

function modalSetup(fetchResponses) {
  const ms = loadMissingScenes({ fetchResponses });
  const els = {};
  for (const id of ["ms-results", "ms-stats", "ms-status", "ms-load-more-btn", "ms-add-all-btn"]) {
    els[id] = createElement("div");
  }
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

/** Run pending timers and microtasks until the promise settles. */
async function drive(ms, promise) {
  let done = false;
  promise.then(() => { done = true; }, () => { done = true; });
  for (let i = 0; i < 200 && !done; i++) { await ms.settle(); ms.flushTimers(); }
  await promise;
}

const addCalls = (ms) => ms.fetchCalls.filter((c) => c.body && c.body.variables && c.body.variables.args
  && c.body.variables.args.operation === "add_to_whisparr").map((c) => c.body.variables.args);

// ---------------- helpers ----------------

test("isStashdbEndpoint mirrors the backend: host stashdb.org, case-insensitive", () => {
  const ms = loadMissingScenes({});
  const f = ms.exports.core.isStashdbEndpoint;
  assert.ok(f("https://stashdb.org/graphql"));
  assert.ok(f("https://StashDB.org"));
  assert.ok(f("HTTPS://STASHDB.ORG/graphql"));
  assert.ok(!f("https://pmvstash.org/graphql"));
  assert.ok(!f("https://stashdb.org.evil.example/graphql"));
  assert.ok(!f("https://theporndb.net/graphql?x=stashdb.org"));
  assert.ok(!f(""));
  assert.ok(!f(null));
  assert.ok(!f("not a url"));
});

test("addToWhisparr sends the endpoint", async () => {
  const ms = loadMissingScenes({
    fetchResponses: { RunPluginOperation: () => wrap({ success: true, message: "ok", search_triggered: true }) },
  });
  await ms.exports.core.addToWhisparr("sid", "T", "https://stashdb.org/graphql");
  const args = addCalls(ms)[0];
  assert.strictEqual(args.endpoint, "https://stashdb.org/graphql");
  assert.strictEqual(args.stash_id, "sid");
});

// ---------------- modal ----------------

test("modal: Add sends the view's endpoint; success message; search_triggered false", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => body.variables.args.operation === "add_to_whisparr"
      ? wrap({ success: true, message: "added", search_triggered: false, search_error: "indexer down" })
      : wrap(okPage()),
  });
  await m.performSearch(true);
  const btn = findButton(els["ms-results"], "Add to Whisparr");
  assert.ok(btn, "Add button on StashDB");
  click(btn);
  await ms.settle();
  assert.strictEqual(addCalls(ms)[0].endpoint, "https://stashdb.org");
  const s = text(els["ms-status"]);
  assert.ok(/without (starting )?a search/.test(s), s);
  assert.ok(s.includes("indexer down"), s);
});

test("modal: success with search shows a plain added message", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => body.variables.args.operation === "add_to_whisparr"
      ? wrap({ success: true, message: "added", search_triggered: true })
      : wrap(okPage()),
  });
  await m.performSearch(true);
  click(findButton(els["ms-results"], "Add to Whisparr"));
  await ms.settle();
  const s = text(els["ms-status"]);
  assert.ok(s.includes("Added") && !/without/.test(s), s);
});

test("modal: failed add shows Whisparr's message (status line, not HTML)", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => body.variables.args.operation === "add_to_whisparr"
      ? wrap({ success: false, error: "Root folder <b>missing</b>" })
      : wrap(okPage()),
  });
  await m.performSearch(true);
  const btn = findButton(els["ms-results"], "Add to Whisparr");
  click(btn);
  await ms.settle();
  const status = els["ms-status"];
  assert.ok(status.textContent.includes("Root folder <b>missing</b>"), status.textContent);
  assert.ok(!status.innerHTML.includes("<b>"), "no HTML injected");
  assert.ok(status.classList.contains("ms-status-error"), "error class");
  assert.strictEqual(btn.textContent, "Failed");
});

test("modal: non-StashDB endpoint shows the hint and no Add or Add All button", async () => {
  const { els, m } = modalSetup({
    RunPluginOperation: () => wrap(okPage({ stashdb_url: "https://pmvstash.org", stashdb_name: "PMV" })),
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]);
  assert.ok(t.includes("Whisparr needs StashDB"), t);
  assert.ok(!findButton(els["ms-results"], "Add to Whisparr"), "no Add button");
  assert.strictEqual(els["ms-add-all-btn"].style.display, "none");
});

test("modal: StashDB shows Add All", async () => {
  const { els, m } = modalSetup({ RunPluginOperation: () => wrap(okPage()) });
  await m.performSearch(true);
  assert.notStrictEqual(els["ms-add-all-btn"].style.display, "none");
  assert.ok(!text(els["ms-results"]).includes("Whisparr needs StashDB"));
});

test("modal: whisparr_error shows a banner and keeps Add usable", async () => {
  const { els, m } = modalSetup({
    RunPluginOperation: () => wrap(okPage({ whisparr_error: "Connection refused <x>" })),
  });
  await m.performSearch(true);
  const t = text(els["ms-results"]);
  assert.ok(t.includes("Whisparr status unavailable: Connection refused &lt;x&gt;. Run Test Whisparr Connection."), t);
  assert.ok(!t.includes("<x>"), "escaped");
  assert.ok(findButton(els["ms-results"], "Add to Whisparr"), "Add still there");

  const ok = modalSetup({ RunPluginOperation: () => wrap(okPage()) });
  await ok.m.performSearch(true);
  assert.ok(!text(ok.els["ms-results"]).includes("Whisparr status unavailable"));
});

test("modal: Add All reports per-scene failures with messages", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => {
      const a = body.variables.args;
      if (a.operation !== "add_to_whisparr") {
        return wrap(okPage({ missing_scenes: [scene("a"), scene("b"), scene("c")] }));
      }
      return a.stash_id === "b" ? wrap({ success: false, error: "Already exists with a different path" })
        : wrap({ success: true, message: "ok", search_triggered: true });
    },
  });
  await m.performSearch(true);
  await drive(ms, els["ms-add-all-btn"].onclick());
  const s = els["ms-status"].textContent;
  assert.ok(s.includes("Added 2") && s.includes("1 failed"), s);
  assert.ok(s.includes("Scene b") && s.includes("Already exists with a different path"), s);
  assert.strictEqual(addCalls(ms)[0].endpoint, "https://stashdb.org");
});

test("modal: Add All refuses on a non-StashDB endpoint", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: () => wrap(okPage({ stashdb_url: "https://pmvstash.org" })),
  });
  await m.performSearch(true);
  await drive(ms, m.handleAddAll([scene("a")]));
  assert.strictEqual(addCalls(ms).length, 0);
  assert.ok(els["ms-status"].textContent.includes("Whisparr needs StashDB"), els["ms-status"].textContent);
});

// ---------------- browse ----------------

async function browseSetup(handler) {
  const ms = loadMissingScenes({ fetchResponses: { RunPluginOperation: handler } });
  const c = browseContainer();
  await ms.exports.browse.performSearch(c, true);
  return { ms, c };
}
const browseResults = (c) => c.els[".ms-browse-results"];

test("browse: cards get Add; endpoint sent; failure message in the status line", async () => {
  const { ms, c } = await browseSetup((body) => body.variables.args.operation === "add_to_whisparr"
    ? wrap({ success: false, error: "Whisparr says no <i>" })
    : wrap(okPage()));
  const btn = findButton(browseResults(c), "Add to Whisparr");
  assert.ok(btn, "Add button on browse");
  click(btn);
  await ms.settle();
  assert.strictEqual(addCalls(ms)[0].endpoint, "https://stashdb.org");
  const status = c.els["#ms-browse-status"];
  assert.ok(status && status.textContent.includes("Whisparr says no <i>"), text(c));
  assert.ok(!status.innerHTML.includes("<i>"));
});

test("browse: success without a search says so", async () => {
  const { ms, c } = await browseSetup((body) => body.variables.args.operation === "add_to_whisparr"
    ? wrap({ success: true, message: "ok", search_triggered: false, search_error: "no indexers" })
    : wrap(okPage()));
  click(findButton(browseResults(c), "Add to Whisparr"));
  await ms.settle();
  const s = c.els["#ms-browse-status"].textContent;
  assert.ok(/without (starting )?a search/.test(s) && s.includes("no indexers"), s);
});

test("browse: non-StashDB endpoint shows hint, no Add", async () => {
  const { c } = await browseSetup(() => wrap(okPage({ stashdb_url: "https://pmvstash.org" })));
  assert.ok(text(browseResults(c)).includes("Whisparr needs StashDB"));
  assert.ok(!findButton(browseResults(c), "Add to Whisparr"));
});

test("browse: whisparr_error banner", async () => {
  const { c } = await browseSetup(() => wrap(okPage({ whisparr_error: "401 Unauthorized" })));
  assert.ok(c.innerHTML.includes("Whisparr status unavailable: 401 Unauthorized. Run Test Whisparr Connection."), c.innerHTML);
  assert.ok(findButton(browseResults(c), "Add to Whisparr"));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
