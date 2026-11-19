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

// ---------------- unmonitored scenes: Monitor / Monitor + Search ----------------

const unmon = (id, extra = {}) => ({
  ...scene(id), in_whisparr: true,
  whisparr_status: { status: "unmonitored", monitored: false, whisparr_id: 7 }, ...extra,
});
const monitorCalls = (ms) => ms.fetchCalls.filter((c) => c.body && c.body.variables && c.body.variables.args
  && c.body.variables.args.operation === "monitor_in_whisparr").map((c) => c.body.variables.args);
const CARD_CFG = { stashdbUrl: "https://stashdb.org", whisparrConfigured: true, endpoint: "https://stashdb.org/graphql" };

function cardFor(sc, cfg = {}, responses = {}) {
  const ms = loadMissingScenes({ fetchResponses: responses });
  const card = ms.exports.core.createSceneCard(sc, { ...CARD_CFG, ...cfg });
  return { ms, card };
}
const whisparrBtn = (card) => {
  const find = (el) => {
    if (el.className && el.className.includes("ms-btn-whisparr")) return el;
    for (const c of el.children || []) { const b = find(c); if (b) return b; }
    return null;
  };
  return find(card);
};

test("card: unmonitored scene gets an enabled Monitor + Search button when search on add is on", () => {
  const { card } = cardFor(unmon("a"), { whisparrSearchOnAdd: true });
  const b = whisparrBtn(card);
  assert.strictEqual(b.textContent, "Monitor + Search");
  assert.strictEqual(b.disabled, false);
  assert.ok(typeof b.onclick === "function");
});

test("card: unmonitored scene gets a plain Monitor button when search on add is off or unset", () => {
  for (const cfg of [{ whisparrSearchOnAdd: false }, {}]) {
    const b = whisparrBtn(cardFor(unmon("a"), cfg).card);
    assert.strictEqual(b.textContent, "Monitor");
    assert.strictEqual(b.disabled, false);
  }
});

test("card: waiting (monitored) stays a disabled Waiting button; so do the other statuses", () => {
  for (const [status, label] of [["waiting", "Waiting"], ["queued", "Queued"], ["downloaded", "Downloaded"]]) {
    const { card } = cardFor({ ...scene("a"), in_whisparr: true, whisparr_status: { status, monitored: true } },
      { whisparrSearchOnAdd: true });
    const b = whisparrBtn(card);
    assert.strictEqual(b.textContent, label);
    assert.strictEqual(b.disabled, true);
    assert.ok(!b.onclick, "no click handler");
  }
});

test("card: an old cache entry without monitored that says waiting is still Waiting", () => {
  const { card } = cardFor({ ...scene("a"), in_whisparr: true, whisparr_status: { status: "waiting" } });
  assert.strictEqual(whisparrBtn(card).textContent, "Waiting");
  assert.strictEqual(whisparrBtn(card).disabled, true);
});

test("card click: monitor with a search shows Searching, disables, and keeps the scene from flipping back", async () => {
  const sc = unmon("a");
  const seenResults = [];
  const { ms, card } = cardFor(sc, { whisparrSearchOnAdd: true, onWhisparrMonitor: (s, ok, d) => seenResults.push([ok, d]) },
    { RunPluginOperation: () => wrap({ success: true, message: "m", search_triggered: true }) });
  const b = whisparrBtn(card);
  b.onclick(ev);
  assert.strictEqual(b.textContent, "Monitoring...");
  assert.strictEqual(b.disabled, true);
  await ms.settle();
  assert.deepStrictEqual(monitorCalls(ms), [{
    operation: "monitor_in_whisparr", stash_id: "a", title: "Scene a", endpoint: "https://stashdb.org/graphql" }]);
  assert.strictEqual(b.textContent, "Searching");
  assert.strictEqual(b.disabled, true);
  assert.strictEqual(sc.whisparr_status.status, "waiting");
  assert.strictEqual(sc.whisparr_status.monitored, true);
  assert.strictEqual(sc.in_whisparr, true);
  assert.strictEqual(seenResults.length, 1);
  assert.strictEqual(seenResults[0][0], true);
  // a re-render builds a disabled, non-clickable button
  const again = whisparrBtn(ms.exports.core.createSceneCard(sc, { ...CARD_CFG, whisparrSearchOnAdd: true }));
  assert.strictEqual(again.textContent, "Waiting");
  assert.strictEqual(again.disabled, true);
});

test("card click: monitor without a search shows Monitored", async () => {
  const { ms, card } = cardFor(unmon("a"), { whisparrSearchOnAdd: false },
    { RunPluginOperation: () => wrap({ success: true, message: "m", search_triggered: false }) });
  const b = whisparrBtn(card);
  b.onclick(ev);
  await ms.settle();
  assert.strictEqual(b.textContent, "Monitored");
  assert.strictEqual(b.disabled, true);
  assert.strictEqual(monitorCalls(ms).length, 1);
});

test("card click: a search that could not start still says Monitored and reports search_error", async () => {
  const got = [];
  const { ms, card } = cardFor(unmon("a"), { whisparrSearchOnAdd: true, onWhisparrMonitor: (s, ok, d) => got.push(d) },
    { RunPluginOperation: () => wrap({ success: true, message: "m", search_triggered: false, search_error: "indexers down" }) });
  const b = whisparrBtn(card);
  b.onclick(ev);
  await ms.settle();
  assert.strictEqual(b.textContent, "Monitored");
  assert.strictEqual(got[0].search_error, "indexers down");
});

test("card click: a scene that already has a file becomes Downloaded", async () => {
  const sc = unmon("a");
  const { ms, card } = cardFor(sc, { whisparrSearchOnAdd: true },
    { RunPluginOperation: () => wrap({ success: true, message: "m", already_has_file: true, search_triggered: false }) });
  const b = whisparrBtn(card);
  b.onclick(ev);
  await ms.settle();
  assert.strictEqual(b.textContent, "Downloaded");
  assert.strictEqual(b.disabled, true);
  assert.strictEqual(sc.whisparr_status.status, "downloaded");
});

test("card click: failure shows Failed with the error as the title, re-enables, and keeps the status", async () => {
  const sc = unmon("a");
  const errs = [];
  const { ms, card } = cardFor(sc, { whisparrSearchOnAdd: true, onWhisparrMonitor: (s, ok, d) => errs.push([ok, d]) },
    { RunPluginOperation: () => wrap({ success: false, error: "Scene 'x' is not in Whisparr any more" }) });
  const b = whisparrBtn(card);
  b.onclick(ev);
  await ms.settle();
  assert.strictEqual(b.textContent, "Failed");
  assert.strictEqual(b.title, "Scene 'x' is not in Whisparr any more");
  assert.strictEqual(b.disabled, false);
  assert.ok(b.classList.contains("ms-btn-error"));
  assert.strictEqual(sc.whisparr_status.status, "unmonitored");
  assert.strictEqual(errs[0][0], false);
  ms.flushTimers();
  assert.strictEqual(b.textContent, "Monitor + Search");
  assert.ok(!b.classList.contains("ms-btn-error"));
  assert.strictEqual(b.disabled, false);
});

test("card: no Monitor button on a non-StashDB view", () => {
  const { card } = cardFor(unmon("a"), { endpoint: "https://pmvstash.org/graphql", stashdbUrl: "https://pmvstash.org" });
  assert.ok(!whisparrBtn(card));
});

test("modal: unmonitored scene shows Monitor + Search per the response's setting; click reports in the status line", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => body.variables.args.operation === "monitor_in_whisparr"
      ? wrap({ success: true, message: "m", search_triggered: true })
      : wrap(okPage({ whisparr_search_on_add: true, missing_scenes: [unmon("a")] })),
  });
  await m.performSearch(true);
  const btn = findButton(els["ms-results"], "Monitor + Search");
  assert.ok(btn, text(els["ms-results"]));
  assert.ok(!findButton(els["ms-results"], "Add to Whisparr"));
  click(btn);
  await ms.settle();
  assert.strictEqual(monitorCalls(ms)[0].endpoint, "https://stashdb.org");
  assert.strictEqual(btn.textContent, "Searching");
  const s = els["ms-status"].textContent;
  assert.ok(s.includes("Scene a") && /monitor/i.test(s), s);
  assert.ok(!els["ms-status"].classList.contains("ms-status-error"));
});

test("modal: Monitor label without the search setting; failure shows the error text, not HTML", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: (body) => body.variables.args.operation === "monitor_in_whisparr"
      ? wrap({ success: false, error: "Whisparr said <b>no</b>" })
      : wrap(okPage({ whisparr_search_on_add: false, missing_scenes: [unmon("a")] })),
  });
  await m.performSearch(true);
  const btn = findButton(els["ms-results"], "Monitor");
  assert.strictEqual(btn.textContent, "Monitor");
  click(btn);
  await ms.settle();
  assert.strictEqual(btn.textContent, "Failed");
  const status = els["ms-status"];
  assert.ok(status.textContent.includes("Whisparr said <b>no</b>"), status.textContent);
  assert.ok(!status.innerHTML.includes("<b>"));
  assert.ok(status.classList.contains("ms-status-error"));
});

test("modal: Add All ignores unmonitored scenes (they are already in Whisparr)", async () => {
  const { ms, els, m } = modalSetup({
    RunPluginOperation: () => wrap(okPage({ missing_scenes: [unmon("a"), scene("b")] })),
  });
  await m.performSearch(true);
  assert.ok(els["ms-add-all-btn"].textContent.includes("(1)"), els["ms-add-all-btn"].textContent);
  await drive(ms, els["ms-add-all-btn"].onclick());
  assert.deepStrictEqual(addCalls(ms).map((a) => a.stash_id), ["b"]);
  assert.strictEqual(monitorCalls(ms).length, 0);
});

test("browse: unmonitored scene shows Monitor + Search; click goes to the status line", async () => {
  const { ms, c } = await browseSetup((body) => body.variables.args.operation === "monitor_in_whisparr"
    ? wrap({ success: true, message: "m", search_triggered: false, search_error: "no indexers" })
    : wrap(okPage({ whisparr_search_on_add: true, missing_scenes: [unmon("a")] })));
  const btn = findButton(browseResults(c), "Monitor + Search");
  assert.ok(btn, text(c));
  click(btn);
  await ms.settle();
  assert.strictEqual(monitorCalls(ms)[0].endpoint, "https://stashdb.org");
  assert.strictEqual(btn.textContent, "Monitored");
  const s = c.els["#ms-browse-status"].textContent;
  assert.ok(s.includes("no indexers"), s);
  assert.ok(c.els["#ms-browse-status"].className.includes("ms-status-error"));
});

test("browse: Monitor label when the response says search on add is off", async () => {
  const { c } = await browseSetup(() => wrap(okPage({ whisparr_search_on_add: false, missing_scenes: [unmon("a")] })));
  assert.ok(findButton(browseResults(c), "Monitor"));
  assert.ok(!findButton(browseResults(c), "Monitor + Search"));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
