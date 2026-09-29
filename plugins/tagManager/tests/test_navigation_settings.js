/**
 * F16, F18, F20 against the REAL tag-manager.js:
 *   - stashPath / navigateTo honor <base href>; the nav buttons and the in-page
 *     tag links navigate inside the SPA (pushState + popstate), never location.href.
 *   - the nav observer re-injects only when the button is missing, at most once
 *     per frame; no retry-timer cascade.
 *   - parseIntSetting: radix 10, a minimum, 0 kept when the minimum allows it;
 *     loadSettings uses it and survives an empty DEFAULTS.
 *   - category mappings are per stash-box endpoint; a legacy flat map moves under
 *     StashDB on first load and is saved once in the new shape.
 * Run with: node plugins/tagManager/tests/test_navigation_settings.js
 */
const { loadTagManager, createQueryableElement } = require("./harness");

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
async function section(name, fn) {
  console.log(name);
  try { await fn(); }
  catch (e) { failed++; console.log(`FAIL  ${name} threw: ${(e && e.stack) || e}`); }
}
const j = (x) => JSON.stringify(x);
const clone = (x) => JSON.parse(JSON.stringify(x));

const EP = "https://stashdb.org/graphql";
const TPDB = "https://theporndb.net/graphql";

/** A config store behind Configuration / ConfigurePlugin, like test_apply_order. */
function configStore(initial = {}, { failWrites = () => false } = {}) {
  const store = { config: { ...initial } };
  store.responses = {
    Configuration: () => ({
      data: { configuration: { plugins: { tagManager: store.config }, general: { stashBoxes: [{ endpoint: EP, name: "StashDB" }] } } },
    }),
    ConfigurePlugin: (body) => {
      if (failWrites()) return { errors: [{ message: "write refused" }] };
      store.config = body.variables.input;
      return { data: { configurePlugin: store.config } };
    },
  };
  return store;
}
const writes = (tm, mark) => tm.fetchCalls.slice(mark).filter((c) => c.op === "ConfigurePlugin");

/** Toolbar stub for injectNavButtons: one insertion point whose parent records inserts. */
function toolbarStub() {
  const parent = {
    inserted: [],
    insertBefore(el, ref) { el.parentNode = parent; parent.inserted.push(el); return el; },
  };
  const zoom = { parentNode: parent, nextSibling: null };
  const toolbar = {
    querySelector: (sel) => (sel === ".zoom-slider-container" ? zoom : null),
    querySelectorAll: () => [],
  };
  return { parent, toolbar };
}

/** Route document.querySelector for the nav tests; counts toolbar lookups (= injection attempts). */
function instrumentNavDom(tm, { toolbar = null, hasButton = () => false } = {}) {
  const orig = tm.document.querySelector;
  const stats = { toolbarLookups: 0 };
  tm.document.querySelector = (sel) => {
    if (sel === ".filtered-list-toolbar") { stats.toolbarLookups++; return toolbar; }
    if (sel === "#tm-nav-button") return hasButton() ? { id: "tm-nav-button" } : null;
    return orig(sel);
  };
  return stats;
}

(async () => {
  // ---------------------------------------------------------------------------
  await section("stashPath honors <base href>", async () => {
    let tm = loadTagManager({ base: "/stash/" });
    const { stashPath } = tm.exports;
    check("based: /tags/5 -> /stash/tags/5", stashPath("/tags/5") === "/stash/tags/5", stashPath("/tags/5"));
    check("based: tags/5 (no leading slash) -> /stash/tags/5", stashPath("tags/5") === "/stash/tags/5", stashPath("tags/5"));
    check("based: /tags -> /stash/tags", stashPath("/tags") === "/stash/tags");

    tm = loadTagManager({ base: "/" });
    check("base /: /tags/5", tm.exports.stashPath("/tags/5") === "/tags/5", tm.exports.stashPath("/tags/5"));
    check("base /: tags/5", tm.exports.stashPath("tags/5") === "/tags/5", tm.exports.stashPath("tags/5"));

    tm = loadTagManager({ base: "/stash" });
    check("base without trailing slash", tm.exports.stashPath("/tags/5") === "/stash/tags/5", tm.exports.stashPath("/tags/5"));

    tm = loadTagManager({ base: "http://localhost/stash/" });
    check("absolute base href: its path", tm.exports.stashPath("/tags/5") === "/stash/tags/5", tm.exports.stashPath("/tags/5"));

    tm = loadTagManager({});
    tm.document.querySelector = () => null; // no <base> element at all
    check("no <base>: /tags/5", tm.exports.stashPath("/tags/5") === "/tags/5", tm.exports.stashPath("/tags/5"));
  });

  // ---------------------------------------------------------------------------
  await section("navigateTo: pushState + popstate, no location.href", async () => {
    const tm = loadTagManager({ base: "/stash/" });
    tm.exports.navigateTo("/plugins/tag-manager");
    check("one pushState with the based path",
      tm.historyCalls.length === 1 && tm.historyCalls[0].url === "/stash/plugins/tag-manager", j(tm.historyCalls));
    check("popstate dispatched", tm.dispatchedEvents.length === 1 && tm.dispatchedEvents[0].type === "popstate",
      j(tm.dispatchedEvents));
    // history v4 ignores a popstate whose state is undefined
    check("popstate state is not undefined", tm.dispatchedEvents[0] && tm.dispatchedEvents[0].state !== undefined);
    check("location.href not assigned", tm.locationAssignments.length === 0, j(tm.locationAssignments));
    check("location moved", tm.window.location.pathname === "/stash/plugins/tag-manager", tm.window.location.pathname);
  });

  // ---------------------------------------------------------------------------
  await section("nav buttons navigate in the SPA, base-aware tags page check", async () => {
    const tm = loadTagManager({ base: "/stash/", pathname: "/stash/tags" });
    const { parent, toolbar } = toolbarStub();
    instrumentNavDom(tm, { toolbar, hasButton: () => parent.inserted.some((e) => e.id === "tm-nav-button") });
    tm.exports.injectNavButtons();
    const tmBtn = parent.inserted.find((e) => e.id === "tm-nav-button");
    const thBtn = parent.inserted.find((e) => e.id === "th-nav-button");
    check("both buttons injected on /stash/tags", !!tmBtn && !!thBtn, j(parent.inserted.map((e) => e.id)));
    tmBtn.listeners.click[0]();
    thBtn.listeners.click[0]();
    check("buttons pushState the based routes",
      j(tm.historyCalls.map((c) => c.url)) === j(["/stash/plugins/tag-manager", "/stash/plugins/tag-hierarchy"]),
      j(tm.historyCalls));
    check("buttons dispatch popstate", tm.dispatchedEvents.filter((e) => e.type === "popstate").length === 2);
    check("buttons never assign location.href", tm.locationAssignments.length === 0, j(tm.locationAssignments));

    tm.exports.injectNavButtons();
    check("not injected twice", parent.inserted.length === 2, `${parent.inserted.length}`);

    const other = loadTagManager({ base: "/stash/", pathname: "/tags" });
    const o = toolbarStub();
    instrumentNavDom(other, { toolbar: o.toolbar });
    other.exports.injectNavButtons();
    check("/tags outside the base is not the tags page", o.parent.inserted.length === 0);

    const slash = loadTagManager({ base: "/stash/", pathname: "/stash/tags/" });
    const s = toolbarStub();
    instrumentNavDom(slash, { toolbar: s.toolbar });
    slash.exports.injectNavButtons();
    check("/stash/tags/ (trailing slash) is the tags page", s.parent.inserted.length === 2);

    const detail = loadTagManager({ base: "/stash/", pathname: "/stash/tags/5" });
    const d = toolbarStub();
    instrumentNavDom(detail, { toolbar: d.toolbar });
    detail.exports.injectNavButtons();
    check("a tag detail page is not the tags list", d.parent.inserted.length === 0);
  });

  // ---------------------------------------------------------------------------
  await section("nav observer: one run per frame, only when the button is missing", async () => {
    const tm = loadTagManager({ base: "/", pathname: "/tags" });
    check("no retry timers queued at load", tm.pendingTimers().length === 0, j(tm.pendingTimers()));
    check("one observer, observing", tm.mutationObservers.length === 1 && tm.mutationObservers[0].observing,
      `${tm.mutationObservers.length}`);
    const obs = tm.mutationObservers[0];

    let present = false;
    const stats = instrumentNavDom(tm, { hasButton: () => present });
    for (let i = 0; i < 50; i++) obs.cb([], obs);
    check("50 mutations schedule one frame", tm.pendingTimers().length === 1, j(tm.pendingTimers()));
    tm.flushTimers();
    check("one injection attempt", stats.toolbarLookups === 1, `${stats.toolbarLookups}`);

    obs.cb([], obs);
    tm.flushTimers();
    check("next frame tries again while the button is missing", stats.toolbarLookups === 2, `${stats.toolbarLookups}`);

    present = true;
    for (let i = 0; i < 10; i++) obs.cb([], obs);
    tm.flushTimers();
    check("button present: no injection attempt", stats.toolbarLookups === 2, `${stats.toolbarLookups}`);

    present = false;
    tm.window.history.pushState({}, "", "/scenes");
    obs.cb([], obs);
    tm.flushTimers();
    check("off the tags page: no injection attempt", stats.toolbarLookups === 2, `${stats.toolbarLookups}`);

    tm.window.history.pushState({}, "", "/tags");
    obs.cb([], obs);
    tm.flushTimers();
    check("back on the tags page: injects again", stats.toolbarLookups === 3, `${stats.toolbarLookups}`);
    check("observer never disconnected", obs.observing === true);
  });

  // ---------------------------------------------------------------------------
  await section("tag links: based href; in-page links navigate in the SPA", async () => {
    const tm = loadTagManager({ base: "/stash/" });
    const row = tm.exports.renderTagRow({ id: "5", name: "Five", aliases: [] });
    check("tag row href is based", row.includes('href="/stash/tags/5"'), row);
    check("tag row carries the route for SPA clicks", row.includes('data-tm-route="/tags/5"'), row);

    const node = tm.exports.renderTreeNode({ id: "7", name: "Seven", childNodes: [], parents: [] }, true);
    check("hierarchy href is based", node.includes('href="/stash/tags/7"'), node);
    check("hierarchy link carries the route", node.includes('data-tm-route="/tags/7"'), node);

    const conflictHtml = tm.exports.conflictRowHtml({
      i: 0, done: false, result: "", error: "",
      stashdbTag: { id: "SB1", name: "Incoming", aliases: ["Foo"] },
      conflicts: [{ conflictingTag: { id: "2", name: "Two" }, conflictingValue: "Foo" }],
    });
    check("conflict Open link is based, still a new tab",
      /href="\/stash\/tags\/2" target="_blank"/.test(conflictHtml), conflictHtml);
    check("no /tags/ href left unbased", !/href="\/tags\//.test(row + node + conflictHtml));

    const { handleInternalLinkClick } = tm.exports;
    const link = (attrs) => ({ getAttribute: (k) => (k in attrs ? attrs[k] : null) });
    const inPage = link({ href: "/stash/tags/5", "data-tm-route": "/tags/5" });
    const ev = (over = {}, target = inPage) => ({
      button: 0, defaultPrevented: false, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
      target: { closest: (sel) => (/^a\b/.test(sel) ? target : null) },
      prevented: false, preventDefault() { this.prevented = true; },
      ...over,
    });

    let e = ev();
    handleInternalLinkClick(e);
    check("plain left click: prevented and pushState", e.prevented && tm.historyCalls.length === 1
      && tm.historyCalls[0].url === "/stash/tags/5", j(tm.historyCalls));
    check("plain left click: popstate", tm.dispatchedEvents.length === 1 && tm.dispatchedEvents[0].type === "popstate");
    for (const mod of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) {
      e = ev({ [mod]: true });
      handleInternalLinkClick(e);
      check(`${mod} click left to the browser`, !e.prevented && tm.historyCalls.length === 1);
    }
    e = ev({ button: 1 });
    handleInternalLinkClick(e);
    check("middle click left to the browser", !e.prevented && tm.historyCalls.length === 1);
    e = ev({ defaultPrevented: true });
    handleInternalLinkClick(e);
    check("already handled: ignored", tm.historyCalls.length === 1);
    e = ev({}, link({ href: "/stash/tags/5", "data-tm-route": "/tags/5", target: "_blank" }));
    handleInternalLinkClick(e);
    check("target=_blank link left to the browser", !e.prevented && tm.historyCalls.length === 1);
    e = ev({}, null);
    handleInternalLinkClick(e);
    check("click outside a link: ignored", !e.prevented && tm.historyCalls.length === 1);
    check("links never assign location.href", tm.locationAssignments.length === 0);
  });

  // ---------------------------------------------------------------------------
  await section("parseIntSetting", async () => {
    const { parseIntSetting } = loadTagManager({}).exports;
    const cases = [
      [["0", 25, 1], 25, "below the minimum"],
      [["40", 25, 1], 40, "valid"],
      [["", 25, 1], 25, "empty"],
      [[0, 80, 0], 0, "0 allowed when min is 0"],
      [["08", 25, 1], 8, "radix 10"],
      [[undefined, 25, 1], 25, "undefined"],
      [[null, 25, 1], 25, "null"],
      [["-3", 25, 1], 25, "negative"],
      [["abc", 25, 1], 25, "not a number"],
      [[60, 80, 0, 100], 60, "number under max"],
      [["150", 80, 0, 100], 100, "capped at max"],
    ];
    for (const [args, want, label] of cases) {
      const got = parseIntSetting(...args);
      check(`${label}: (${args.map((a) => j(a)).join(", ")}) -> ${want}`, got === want, `got ${j(got)}`);
    }
  });

  // ---------------------------------------------------------------------------
  await section("loadSettings: numeric settings", async () => {
    let store = configStore({ fuzzyThreshold: 0, pageSize: "0" });
    let tm = loadTagManager({ fetchResponses: store.responses });
    await tm.settle();
    await tm.exports.loadSettings();
    let s = tm.getState().settings;
    check("fuzzyThreshold 0 kept", s.fuzzyThreshold === 0, j(s.fuzzyThreshold));
    check("pageSize 0 falls back to 25", s.pageSize === 25, j(s.pageSize));

    store = configStore({ fuzzyThreshold: "150", pageSize: "-5" });
    tm = loadTagManager({ fetchResponses: store.responses });
    await tm.settle();
    await tm.exports.loadSettings();
    s = tm.getState().settings;
    check("fuzzyThreshold capped at 100", s.fuzzyThreshold === 100, j(s.fuzzyThreshold));
    check("negative pageSize falls back", s.pageSize === 25, j(s.pageSize));

    store = configStore({ fuzzyThreshold: "70", pageSize: "50" });
    tm = loadTagManager({ fetchResponses: store.responses });
    await tm.settle();
    await tm.exports.loadSettings();
    s = tm.getState().settings;
    check("valid strings parsed", s.fuzzyThreshold === 70 && s.pageSize === 50, j(s));

    // default_settings.json unusable -> DEFAULTS is {}
    store = configStore({});
    tm = loadTagManager({ fetchResponses: { ...store.responses, "default_settings.json": {} } });
    await tm.settle();
    await tm.exports.loadSettings();
    s = tm.getState().settings;
    check("empty DEFAULTS: hard-coded fuzzyThreshold 80", s.fuzzyThreshold === 80, j(s.fuzzyThreshold));
    check("empty DEFAULTS: hard-coded pageSize 25", s.pageSize === 25, j(s.pageSize));
  });

  // ---------------------------------------------------------------------------
  await section("migrateCategoryMappings", async () => {
    const tm = loadTagManager({});
    const { migrateCategoryMappings } = tm.exports;

    const flat = { Action: "12" };
    const flatCopy = clone(flat);
    check("flat map moves under the endpoint",
      j(migrateCategoryMappings(flat, EP)) === j({ [EP]: { Action: "12" } }), j(migrateCategoryMappings(flat, EP)));
    check("input not mutated", j(flat) === j(flatCopy));

    const nested = { [EP]: { Action: "12", "Hair Color": "3" }, [TPDB]: { Action: "40" }, "https://x/graphql": {} };
    check("nested map passes through unchanged", j(migrateCategoryMappings(clone(nested), EP)) === j(nested),
      j(migrateCategoryMappings(clone(nested), EP)));
    check("empty map stays empty", j(migrateCategoryMappings({}, EP)) === "{}");
    check("numeric ids become strings",
      j(migrateCategoryMappings({ Action: 12 }, EP)) === j({ [EP]: { Action: "12" } }), j(migrateCategoryMappings({ Action: 12 }, EP)));

    let before = tm.logs.warn.length;
    const mixed = migrateCategoryMappings({
      Action: "12", [TPDB]: { Hair: "3", Junk: false }, Bad: null, Flag: true, List: ["1"],
    }, EP);
    check("mixed: object entries kept, flat entries moved, garbage dropped",
      j(mixed) === j({ [TPDB]: { Hair: "3" }, [EP]: { Action: "12" } }), j(mixed));
    check("mixed: warned", tm.logs.warn.length > before, `${tm.logs.warn.length - before} warnings`);

    before = tm.logs.warn.length;
    const clash = migrateCategoryMappings({ Action: "12", Body: "5", [EP]: { Action: "99" } }, EP);
    check("flat entry never overrides a nested one for the same endpoint",
      j(clash) === j({ [EP]: { Action: "99", Body: "5" } }), j(clash));
    check("clash: warned", tm.logs.warn.length > before);

    for (const bad of [null, undefined, "x", 7, ["Action"]]) {
      check(`non-object ${j(bad)} -> {}`, j(migrateCategoryMappings(bad, EP)) === "{}");
    }
  });

  // ---------------------------------------------------------------------------
  await section("loadCategoryMappings: migrates a flat map once", async () => {
    const store = configStore({ categoryMappings: j({ Action: "12", "Hair Color": "3" }) });
    const tm = loadTagManager({ fetchResponses: store.responses });
    await tm.settle();
    const mark = tm.fetchCalls.length;
    await tm.exports.loadCategoryMappings();
    await tm.settle();
    const want = { [EP]: { Action: "12", "Hair Color": "3" } };
    check("in memory: nested under StashDB", j(tm.getState().categoryMappings) === j(want), j(tm.getState().categoryMappings));
    const w = writes(tm, mark);
    check("saved once", w.length === 1, `${w.length} writes`);
    check("saved in the new shape", w[0] && w[0].body.variables.input.categoryMappings === j(want),
      w[0] && w[0].body.variables.input.categoryMappings);
    check("persisted", store.config.categoryMappings === j(want), store.config.categoryMappings);

    const mark2 = tm.fetchCalls.length;
    await tm.exports.loadCategoryMappings();
    await tm.settle();
    check("second load: nothing to migrate, no write", writes(tm, mark2).length === 0);
    check("second load: same map", j(tm.getState().categoryMappings) === j(want));
  });

  await section("loadCategoryMappings: nested or empty maps are not rewritten", async () => {
    for (const stored of [j({ [EP]: { Action: "12" }, [TPDB]: { Action: "40" } }), "{}"]) {
      const store = configStore({ categoryMappings: stored });
      const tm = loadTagManager({ fetchResponses: store.responses });
      await tm.settle();
      const mark = tm.fetchCalls.length;
      await tm.exports.loadCategoryMappings();
      await tm.settle();
      check(`${stored}: loaded as is`, j(tm.getState().categoryMappings) === stored, j(tm.getState().categoryMappings));
      check(`${stored}: no write`, writes(tm, mark).length === 0);
    }
  });

  await section("loadCategoryMappings: a failed migration save keeps the migrated map and retries", async () => {
    let refuse = true;
    const store = configStore({ categoryMappings: j({ Action: "12" }) }, { failWrites: () => refuse });
    const tm = loadTagManager({ fetchResponses: store.responses });
    await tm.settle();
    await tm.exports.loadCategoryMappings(); // must not throw
    await tm.settle();
    check("in memory: migrated", j(tm.getState().categoryMappings) === j({ [EP]: { Action: "12" } }),
      j(tm.getState().categoryMappings));
    check("storage still has the flat map (nothing lost)", store.config.categoryMappings === j({ Action: "12" }),
      store.config.categoryMappings);

    refuse = false;
    tm.exports.setCategoryMapping(TPDB, "Hair Color", "3");
    const ok = await tm.exports.saveCategoryMappings();
    check("next save succeeds", ok === true);
    check("next save writes the migrated map plus the new mapping",
      store.config.categoryMappings === j({ [EP]: { Action: "12" }, [TPDB]: { "Hair Color": "3" } }),
      store.config.categoryMappings);
  });

  // ---------------------------------------------------------------------------
  await section("get/set/deleteCategoryMapping are per endpoint", async () => {
    const tm = loadTagManager({});
    const { getCategoryMapping, setCategoryMapping, deleteCategoryMapping } = tm.exports;
    tm.setState({ categoryMappings: { [EP]: { Action: "12" }, [TPDB]: { Action: "30" } } });
    check("reads the StashDB map", getCategoryMapping(EP, "Action") === "12");
    check("reads the TPDB map", getCategoryMapping(TPDB, "Action") === "30");
    check("unknown endpoint: undefined", getCategoryMapping("https://other/graphql", "Action") === undefined);
    check("unknown category: undefined", getCategoryMapping(EP, "Hair") === undefined);
    check("prototype keys are not mappings", getCategoryMapping(EP, "constructor") === undefined
      && getCategoryMapping("constructor", "Action") === undefined);
    check("no endpoint: undefined", getCategoryMapping(undefined, "Action") === undefined);

    setCategoryMapping("https://fansdb.cc/graphql", "Hair", "8");
    check("set creates the endpoint map", j(tm.getState().categoryMappings["https://fansdb.cc/graphql"]) === j({ Hair: "8" }));
    setCategoryMapping(EP, "Hair", "9");
    check("set leaves other entries alone",
      j(tm.getState().categoryMappings[EP]) === j({ Action: "12", Hair: "9" }) && getCategoryMapping(TPDB, "Action") === "30");
    deleteCategoryMapping(EP, "Action");
    check("delete removes only that endpoint's entry",
      getCategoryMapping(EP, "Action") === undefined && getCategoryMapping(TPDB, "Action") === "30");
    deleteCategoryMapping("https://fansdb.cc/graphql", "Hair");
    check("an emptied endpoint map is dropped", !("https://fansdb.cc/graphql" in tm.getState().categoryMappings),
      j(tm.getState().categoryMappings));
  });

  // ---------------------------------------------------------------------------
  await section("resolveCategoryParents reads the import endpoint's mapping", async () => {
    const tm = loadTagManager({});
    tm.setState({
      localTags: [
        { id: "10", name: "Action", aliases: [], stash_ids: [], parents: [] },
        { id: "20", name: "Clothing", aliases: [], stash_ids: [], parents: [] },
      ],
      stashdbTags: [{ id: "s1", name: "Anal", category: { id: "c1", name: "Action", description: "" } }],
      categoryMappings: { [EP]: { Action: "20" } },
      selectedStashBox: { endpoint: EP, name: "StashDB" },
    });
    let r = tm.exports.resolveCategoryParents(new Set(["s1"]));
    check("selected StashDB: its saved mapping", r.Action && r.Action.parentTagId === "20" && r.Action.resolution === "saved", j(r));
    tm.setState({ selectedStashBox: { endpoint: TPDB, name: "TPDB" } });
    r = tm.exports.resolveCategoryParents(new Set(["s1"]));
    check("selected TPDB: StashDB's mapping not used", r.Action && r.Action.parentTagId === "10" && r.Action.resolution === "exact", j(r));
    r = tm.exports.resolveCategoryParents(new Set(["s1"]), EP);
    check("explicit endpoint wins", r.Action && r.Action.parentTagId === "20", j(r));
  });

  // ---------------------------------------------------------------------------
  const CATEGORY = { id: "c1", name: "Hair Color" };
  const LOCAL = [
    { id: "1", name: "Blonde", description: "", aliases: [], stash_ids: [], parents: [] },
    { id: "2", name: "Blond Hair", description: "", aliases: [], stash_ids: [], parents: [] },
  ];
  const SB_TAG = { id: "SB2", name: "Blonde", description: "box desc", aliases: [], category: CATEGORY };
  async function diffSetup(mappings, endpoint = EP) {
    const store = configStore({});
    const tm = loadTagManager({
      fetchResponses: {
        ...store.responses,
        FindTag: { data: { findTag: { parents: [] } } },
        TagUpdate: (body) => ({ data: { tagUpdate: {
          id: body.variables.input.id, name: "Blonde", stash_ids: body.variables.input.stash_ids,
        } } }),
      },
    });
    await tm.settle();
    tm.setState({
      localTags: clone(LOCAL),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: false },
      selectedStashBox: { endpoint, name: "Box" },
      stashBoxes: [{ endpoint, name: "Box" }],
      categoryMappings: clone(mappings),
      matchResults: { "1": [{ tag: SB_TAG, match_type: "exact", score: 100 }] },
    });
    tm.document.createElement = (t) => createQueryableElement(t);
    return { tm, store };
  }
  const openDiff = (tm) => {
    tm.exports.showDiffDialog("1", createQueryableElement("div"));
    return tm.document.body.children[tm.document.body.children.length - 1];
  };

  await section("showDiffDialog: saved mapping and stale cleanup per endpoint", async () => {
    let { tm } = await diffSetup({ [TPDB]: { "Hair Color": "2" } }, EP);
    let modal = openDiff(tm);
    check("another endpoint's mapping is not offered", !modal.innerHTML.includes("(saved mapping)"), modal.innerHTML);

    ({ tm } = await diffSetup({ [EP]: { "Hair Color": "2" } }, EP));
    modal = openDiff(tm);
    check("this endpoint's mapping is offered", modal.innerHTML.includes("Blond Hair (saved mapping)"));

    let store;
    ({ tm, store } = await diffSetup({ [EP]: { "Hair Color": "999", Body: "1" }, [TPDB]: { "Hair Color": "999" } }, EP));
    openDiff(tm);
    await tm.settle();
    const m = tm.getState().categoryMappings;
    check("stale mapping dropped for this endpoint only",
      j(m) === j({ [EP]: { Body: "1" }, [TPDB]: { "Hair Color": "999" } }), j(m));
    check("cleanup saved", store.config.categoryMappings === j(m), store.config.categoryMappings);
  });

  await section("applyDiff remembers the mapping under its endpoint", async () => {
    const { tm, store } = await diffSetup({ [EP]: { "Hair Color": "1" } }, TPDB);
    const tag = tm.getState().localTags.find((t) => t.id === "1");
    const r = await tm.exports.applyDiff({
      tag, stashdbTag: SB_TAG, endpoint: TPDB, nameChoice: "local", descChoice: "local",
      aliases: [], parentId: "2", rememberMapping: true,
    });
    check("applied", r && r.ok, j(r));
    check("saved under TPDB, StashDB's untouched",
      j(tm.getState().categoryMappings) === j({ [EP]: { "Hair Color": "1" }, [TPDB]: { "Hair Color": "2" } }),
      j(tm.getState().categoryMappings));
    check("persisted", store.config.categoryMappings === j(tm.getState().categoryMappings), store.config.categoryMappings);
  });

  console.log(failed ? `\n${failed} FAILED` : "\nall passed");
  if (failed) process.exitCode = 1;
})();
