/**
 * Events, navigation and lifecycle.
 * Run with: node plugins/studioManager/tests/test_lifecycle.js
 */
const assert = require("assert");
const { loadStudioManager, createQueryableElement } = require("./harness");

const plain = (v) => JSON.parse(JSON.stringify(v));
const studio = (id, parent) => ({
  id, name: "S" + id, parent_studio: parent ? { id: parent, name: "S" + parent } : null, child_studios: [],
});

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/**
 * studios: [[id, parent]]. opts: gate (promise holding FindStudios, or function(callIndex)
 * returning one or null), gateUpdate (promise holding StudioUpdate, or function(input)
 * returning one or null), fail(input) (an error message fails that update), failFetch
 * (or function(callIndex)), base, pathname, confirm. The server snapshot is taken when FindStudios is called.
 * `env.attached` is the container in the document: mount() attaches one, its cleanup
 * detaches it (React removes the element on unmount).
 */
function setup(studios, opts = {}) {
  const server = new Map(studios);
  let findCalls = 0;
  const sm = loadStudioManager({
    base: opts.base, pathname: opts.pathname, confirm: opts.confirm,
    fetchResponses: {
      FindStudios: () => {
        const call = findCalls++;
        const fails = typeof opts.failFetch === "function" ? opts.failFetch(call) : opts.failFetch;
        const r = fails
          ? { errors: [{ message: "boom" }] }
          : { data: { findStudios: { studios: [...server].map(([id, p]) => studio(id, p)) } } };
        const g = typeof opts.gate === "function" ? opts.gate(call) : opts.gate;
        return g ? g.then(() => r) : r;
      },
      StudioUpdate: (body) => {
        const input = body.variables.input;
        const done = () => {
          const msg = opts.fail && opts.fail(input);
          if (msg) return { errors: [{ message: msg }] };
          server.set(input.id, input.parent_id);
          return { data: { studioUpdate: {} } };
        };
        const g = typeof opts.gateUpdate === "function" ? opts.gateUpdate(input) : opts.gateUpdate;
        return g ? g.then(done) : done();
      },
    },
  });
  const container = createQueryableElement("div");
  const panel = createQueryableElement("div");
  const env = { sm, x: sm.exports, container, server, panel, attached: container };
  sm.document.querySelector = (sel) => {
    if (sel === ".studio-hierarchy-container") return env.attached;
    if (sel === ".sh-changes-panel") return panel;
    if (sel === "base") return { getAttribute: () => opts.base || "/" };
    return null;
  };
  sm.document.createElement = (t) => createQueryableElement(t);
  const list = studios.map(([id, p]) => studio(id, p));
  sm.setState({
    hierarchyStudios: list, hierarchyTree: sm.exports.buildStudioTree(list),
    hierarchyStats: sm.exports.getTreeStats(list), expandedNodes: new Set(),
    pendingChanges: [], isEditMode: false, originalParentMap: new Map(),
  });
  return env;
}

/**
 * Run the component as React would: render, hand it the container, run the effect.
 * Returns the unmount: the container leaves the document, then the effect cleanup runs.
 */
function mount(env, container = env.container) {
  const { sm } = env;
  const before = sm.effects.length;
  const el = sm.routes[0].component();
  el.props.ref.current = container;
  env.attached = container;
  const cleanup = sm.effects[before]();
  return () => {
    if (env.attached === container) env.attached = null;
    cleanup();
  };
}

const pendingIds = (sm) => plain(sm.getState().pendingChanges.map((c) => c.studioId));
const rootIds = (sm) => plain(sm.getState().hierarchyTree.map((n) => n.id));
/** Texts of every toast shown (each toast makes its own container: none is found). */
const toastTexts = (sm) => sm.document.body.children
  .filter((c) => c.className === "sh-toast-container")
  .flatMap((c) => c.children.map((t) => t.textContent));
/** Promises released by hand: gates.open(key) resolves gates.get(key). */
function gateSet() {
  const promises = new Map();
  const resolvers = new Map();
  const make = (k) => { if (!promises.has(k)) promises.set(k, new Promise((r) => resolvers.set(k, r))); };
  return {
    get: (k) => { make(k); return promises.get(k); },
    open: (k) => { make(k); resolvers.get(k)(); },
  };
}

const listenerTotal = (el) => Object.values(el.listeners).reduce((n, a) => n + a.length, 0);
const tgt = (map, extra = {}) => ({ closest: (sel) => map[sel] || null, dataset: {}, classList: { contains: () => false }, ...extra });
const nodeEl = (id) => { const n = createQueryableElement("div"); n.dataset.studioId = id; return n; };

test("one delegated listener set per mount; rendering does not add more", async () => {
  const env = setup([["1"], ["2", "1"]]);
  const cleanup = mount(env);
  await env.sm.settle();
  const after = listenerTotal(env.container);
  for (const t of ["click", "contextmenu", "dragstart", "dragover", "drop"]) {
    assert.strictEqual((env.container.listeners[t] || []).length, 1, t + " listener count");
  }
  for (let i = 0; i < 5; i++) env.x.renderHierarchyPage(env.container);
  assert.strictEqual(listenerTotal(env.container), after);
  cleanup();
  assert.strictEqual(listenerTotal(env.container), 0, "unmount removes them");
});

test("context menu closes with Escape or another right-click and drops its document listener", async () => {
  const env = setup([["1"], ["2", "1"]]);
  const { sm, x } = env;
  const doc = sm.document;
  const count = () => (doc.listeners.click || []).length + (doc.listeners.contextmenu || []).length;
  x.showContextMenu(10, 10, "1");
  sm.flushTimers();
  assert.ok(count() >= 1, "listener registered");
  x.handleHierarchyKeyboard({ key: "Escape", target: tgt({}, { tagName: "BODY" }), preventDefault() {} });
  assert.strictEqual(count(), 0, "Escape removed it");

  x.showContextMenu(10, 10, "1");
  sm.flushTimers();
  doc.listeners.contextmenu[0]({});
  assert.strictEqual(count(), 0, "right-click removed it");

  x.showContextMenu(10, 10, "1");
  x.showContextMenu(20, 20, "2");
  sm.flushTimers();
  const n = count();
  x.handleHierarchyKeyboard({ key: "Escape", target: tgt({}, { tagName: "BODY" }), preventDefault() {} });
  assert.strictEqual(count(), 0, "reopening does not leak; n was " + n);

  x.showContextMenu(10, 10, "1");
  x.handleHierarchyKeyboard({ key: "Escape", target: tgt({}, { tagName: "BODY" }), preventDefault() {} });
  sm.flushTimers();
  assert.strictEqual(count(), 0, "closed before the deferred registration: nothing added");
});

function navEnv(opts) {
  const env = setup([["1"]], opts);
  const inserted = [];
  const created = [];
  const toolbar = createQueryableElement("div");
  const anchor = { parentNode: { insertBefore: (el) => inserted.push(el) }, nextSibling: null };
  toolbar.querySelector = (sel) => (sel === ".zoom-slider-container" ? anchor : null);
  const baseQuery = env.sm.document.querySelector;
  env.sm.document.querySelector = (sel) => {
    if (sel === ".filtered-list-toolbar") return toolbar;
    if (sel === "#sh-nav-button") return inserted[0] || null;
    if (sel === "base") return { getAttribute: () => opts.base || "/" };
    return baseQuery(sel);
  };
  env.sm.document.createElement = (t) => { const e = createQueryableElement(t); created.push(e); return e; };
  env.inserted = inserted;
  return env;
}
const fireLocation = (sm) => (sm.eventListeners["stash:location"] || []).forEach((f) => f({ detail: {} }));

test("nav button: stash:location event, no MutationObserver, trailing slash, in-app", () => {
  const env = navEnv({ pathname: "/stash/studios/", base: "/stash/" });
  const { sm } = env;
  assert.strictEqual(sm.mutationObservers.length, 0, "no MutationObserver");
  assert.ok((sm.eventListeners["stash:location"] || []).length >= 1, "listens to stash:location");
  fireLocation(sm);
  assert.strictEqual(env.inserted.length, 1, "button inserted on /studios/");
  env.inserted[0].listeners.click[0]({});
  assert.deepStrictEqual(plain(sm.historyCalls.map((c) => c.url)), ["/stash/plugins/studio-hierarchy"]);
  assert.strictEqual(sm.locationAssignments.length, 0, "no full reload");
  assert.strictEqual(sm.dispatchedEvents.length, 1, "popstate dispatched");
});

test("nav button: plain /studios and other pages", () => {
  const a = navEnv({ pathname: "/studios" });
  fireLocation(a.sm);
  assert.strictEqual(a.inserted.length, 1);
  const b = navEnv({ pathname: "/scenes" });
  fireLocation(b.sm);
  assert.strictEqual(b.inserted.length, 0);
});

test("studio link and context menu Open/Edit navigate in-app, base aware", async () => {
  const env = setup([["1"], ["2", "1"]], { base: "/stash/" });
  const { sm, x, container } = env;
  const cleanup = mount(env);
  await sm.settle();
  assert.ok(container.innerHTML.includes('href="/stash/studios/1"'), "href has base");
  assert.ok(container.innerHTML.includes('data-sh-route="/studios/1"'));
  const link = createQueryableElement("a");
  link.getAttribute = (k) => (k === "data-sh-route" ? "/studios/1" : null);
  let prevented = 0;
  container.listeners.click[0]({ button: 0, target: tgt({ "a[data-sh-route]": link }), preventDefault() { prevented++; } });
  assert.strictEqual(prevented, 1);
  assert.deepStrictEqual(plain(sm.historyCalls.map((c) => c.url)), ["/stash/studios/1"]);
  container.listeners.click[0]({ button: 0, ctrlKey: true, target: tgt({ "a[data-sh-route]": link }), preventDefault() { prevented++; } });
  container.listeners.click[0]({ button: 1, target: tgt({ "a[data-sh-route]": link }), preventDefault() { prevented++; } });
  assert.strictEqual(prevented, 1, "modified/middle clicks left to the browser");
  assert.strictEqual(sm.historyCalls.length, 1);

  const menus = [];
  sm.document.createElement = (t) => { const e = createQueryableElement(t); menus.push(e); return e; };
  for (const [action, url] of [["view", "/stash/studios/2"], ["edit", "/stash/studios/2/edit"]]) {
    x.showContextMenu(1, 1, "2");
    const menu = menus[menus.length - 1];
    menu.listeners.click[0]({ target: { dataset: { action }, classList: { contains: () => false } } });
    assert.strictEqual(sm.historyCalls[sm.historyCalls.length - 1].url, url);
  }
  assert.strictEqual(sm.locationAssignments.length, 0);
  cleanup();
});

test("plugin navigation with pending changes asks to confirm", () => {
  const answers = [false, true];
  const env = setup([["1"], ["2"], ["3"]], { confirm: () => answers.shift() });
  const { sm, x } = env;
  x.navigateTo("/studios/5");
  assert.strictEqual(sm.confirmCalls.length, 0, "nothing pending: no confirm");
  assert.strictEqual(sm.historyCalls.length, 1);
  x.setParent("1", "2");
  x.setParent("3", "2");
  x.navigateTo("/studios/5");
  assert.deepStrictEqual(sm.confirmCalls, ["You have 2 unsaved changes. Leave anyway?"]);
  assert.strictEqual(sm.historyCalls.length, 1, "declined: stays");
  x.navigateTo("/studios/5");
  assert.strictEqual(sm.historyCalls.length, 2, "accepted: leaves");
  x.navigateTo("/plugins/studio-hierarchy");
  assert.strictEqual(sm.confirmCalls.length, 2, "going to the page that restores them does not ask");
});

test("beforeunload is set while changes are pending and cleared otherwise", () => {
  const env = setup([["1"], ["2"]]);
  const { sm, x } = env;
  const n = () => (sm.window.listeners.beforeunload || []).length;
  assert.strictEqual(n(), 0);
  x.setParent("1", "2");
  assert.strictEqual(n(), 1);
  x.setParent("1", "2");
  assert.strictEqual(n(), 1, "not added twice");
  const evt = { preventDefault() { this.p = true; }, returnValue: undefined };
  sm.window.listeners.beforeunload[0](evt);
  assert.ok(evt.p);
  x.cancelPendingChanges();
  assert.strictEqual(n(), 0);
});

test("remount re-applies pending changes to fresh data, banner, drops no-ops", async () => {
  const env = setup([["1"], ["2"], ["3"], ["4"], ["5", "4"]]);
  const { sm, container } = env;
  sm.setState({
    pendingChanges: [
      { type: "set-parent", studioId: "1", studioName: "S1", parentId: "2", parentName: "S2" },
      { type: "remove-parent", studioId: "4", studioName: "S4", parentId: null, parentName: null }, // already root
      { type: "set-parent", studioId: "3", studioName: "S3", parentId: "2", parentName: "S2" },
      { type: "set-parent", studioId: "5", studioName: "S5", parentId: "4", parentName: "S4" }, // already there
    ],
    isEditMode: true, originalParentMap: new Map(),
  });
  const cleanup = mount(env);
  await sm.settle();
  const st = sm.getState();
  assert.deepStrictEqual(plain(st.pendingChanges.map((c) => c.studioId)), ["1", "3"]);
  assert.strictEqual(st.isEditMode, true);
  assert.ok(container.innerHTML.includes("2 unsaved changes restored"), container.innerHTML.slice(0, 300));
  assert.strictEqual(plain(st.hierarchyTree.map((n) => n.id)).includes("1"), false, "1 shown under 2");
  cleanup();

  // nothing pending: no banner, not in edit mode
  const env2 = setup([["1"], ["2"]]);
  mount(env2);
  await env2.sm.settle();
  assert.ok(!env2.container.innerHTML.includes("restored"));
  assert.strictEqual(env2.sm.getState().isEditMode, false);
});

test("restoring keeps changes that are valid together, whatever order they were made in", async () => {
  // 1 is under 2; 3 and 4 are free
  const env = setup([["1", "2"], ["2"], ["3"], ["4"]]);
  const { sm, x, container } = env;
  const leave = mount(env);
  await sm.settle();
  x.setParent("1", "3");
  x.setParent("2", "1"); // fine once 1 has left 2
  x.setParent("1", "4"); // re-editing 1 moves its change to the end
  assert.deepStrictEqual(pendingIds(sm), ["2", "1"]);
  leave(); // Stash's navbar
  mount(env);
  await sm.settle();
  assert.deepStrictEqual(pendingIds(sm), ["2", "1"], "both kept, in the order shown before");
  assert.ok(container.innerHTML.includes("2 unsaved changes restored"), container.innerHTML.slice(0, 300));
  assert.ok(!/sh-cycle/.test(container.innerHTML));
});

test("restoring drops a change that would now form a cycle and keeps the rest", async () => {
  // meanwhile 1 went under 2 on the server, so a pending 2 -> 1 no longer fits
  const env = setup([["1", "2"], ["2"], ["3"], ["4"]]);
  const { sm, container } = env;
  sm.setState({
    pendingChanges: [
      { type: "set-parent", studioId: "2", studioName: "S2", parentId: "1", parentName: "S1" },
      { type: "set-parent", studioId: "3", studioName: "S3", parentId: "4", parentName: "S4" },
    ],
    isEditMode: true, originalParentMap: new Map(),
  });
  mount(env);
  await sm.settle();
  assert.deepStrictEqual(pendingIds(sm), ["3"]);
  assert.ok(container.innerHTML.includes("1 unsaved change restored"));
  assert.ok(!/sh-cycle/.test(container.innerHTML));
});

test("unmount during the initial fetch: no render, toast or throw afterwards", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const env = setup([["1"]], { gate });
  const { sm, container } = env;
  const cleanup = mount(env);
  const html = container.innerHTML;
  cleanup();
  release();
  await sm.settle();
  assert.strictEqual(container.innerHTML, html, "no render");
  assert.strictEqual(sm.logs.error.length, 0);
  assert.strictEqual(sm.document.body.children.length, 0, "no toast container");

  const env2 = setup([["1"]], { gate: Promise.resolve(), failFetch: true });
  const c2 = mount(env2);
  const html2 = env2.container.innerHTML;
  c2();
  await env2.sm.settle();
  assert.strictEqual(env2.container.innerHTML, html2, "no error page after unmount");
});

test("unmount during a save: no toast or panel, state still consistent", async () => {
  let release;
  const gateUpdate = new Promise((r) => { release = r; });
  const env = setup([["1"], ["2"], ["3"]], { gateUpdate });
  const { sm, x } = env;
  const cleanup = mount(env);
  await sm.settle();
  x.setParent("1", "2");
  const bodyKids = sm.document.body.children.length;
  const saving = x.savePendingChanges();
  await sm.settle();
  cleanup();
  release();
  await saving;
  assert.strictEqual(sm.document.body.children.length, bodyKids, "no toast after unmount");
  assert.strictEqual(sm.getState().pendingChanges.length, 0, "saved change is no longer pending");
  x.setParent("3", "2");
  assert.strictEqual(sm.getState().pendingChanges.length, 1, "lock released");
});

test("leaving mid-save: no reload, toast or render for the page that is gone", async () => {
  let release;
  const gateUpdate = new Promise((r) => { release = r; });
  const env = setup([["1"], ["2"]], { gateUpdate, failFetch: (call) => call > 0 });
  const { sm, x, container } = env;
  const leave = mount(env);
  await sm.settle();
  x.setParent("1", "2");
  const saving = x.savePendingChanges();
  await sm.settle();
  leave();
  const html = container.innerHTML;
  const kids = sm.document.body.children.length;
  const finds = () => sm.fetchCalls.filter((c) => c.op === "FindStudios").length;
  const before = finds();
  release();
  await saving;
  await sm.settle();
  assert.strictEqual(sm.document.body.children.length, kids, "no toast container on the other page");
  assert.strictEqual(container.innerHTML, html, "no render");
  assert.strictEqual(finds(), before, "no reload for a page that is gone");
  assert.deepStrictEqual(pendingIds(sm), []);
  assert.strictEqual(sm.getState().hierarchyStudios.find((s) => s.id === "1").parent_studio.id, "2",
    "the saved parent is kept for the next visit");
});

test("returning to the page while a save runs keeps its failures, panel and leave guard", async () => {
  const gates = gateSet();
  const env = setup([["1"], ["2"], ["3"], ["4"]], {
    gateUpdate: (input) => gates.get(input.id),
    fail: (input) => (input.id === "3" ? "nope" : null),
  });
  const { sm, x, panel } = env;
  const leave = mount(env);
  await sm.settle();
  x.setParent("1", "4");
  x.setParent("2", "4");
  x.setParent("3", "4");
  const saving = x.savePendingChanges();
  await sm.settle();
  gates.open("1");
  await sm.settle(); // 1 saved, 2 in flight
  leave(); // Back
  const second = createQueryableElement("div");
  const leave2 = mount(env, second); // return while the save runs
  await sm.settle();
  assert.ok(/Saving…/.test(panel.innerHTML), "the running save is shown");
  assert.deepStrictEqual(pendingIds(sm), ["1", "2", "3"]);
  gates.open("2");
  gates.open("3");
  await saving;
  await sm.settle();
  assert.deepStrictEqual(pendingIds(sm), ["3"], "the failed change is kept");
  assert.ok(/nope/.test(sm.getState().pendingChanges[0].error));
  assert.ok(/1 pending change\b/.test(panel.innerHTML), panel.innerHTML);
  assert.ok(!/Saving…|disabled/.test(panel.innerHTML), "panel unlocked");
  assert.strictEqual((sm.window.listeners.beforeunload || []).length, 1, "leave guard on");
  assert.ok(toastTexts(sm).some((t) => t.startsWith("2 saved, 1 failed")), toastTexts(sm).join("|"));
  assert.deepStrictEqual(rootIds(sm), ["4"], "1, 2 saved and 3 pending, all under 4");
  assert.ok(second.innerHTML.includes('data-studio-id="3"'), "the new page is rendered");
  leave2();
});

test("a page fetch that started during a save does not undo what the save loaded", async () => {
  const gates = gateSet();
  const env = setup([["1"], ["2"], ["3"]], {
    gateUpdate: (input) => gates.get("u" + input.id),
    gate: (call) => (call === 1 ? gates.get("page") : null), // the fetch of the second mount
  });
  const { sm, x } = env;
  const leave = mount(env);
  await sm.settle();
  x.setParent("1", "3");
  x.setParent("2", "3");
  const saving = x.savePendingChanges();
  await sm.settle();
  gates.open("u1");
  await sm.settle();
  leave();
  const second = createQueryableElement("div");
  mount(env, second); // its fetch sees only 1 saved, and is slow
  await sm.settle();
  gates.open("u2");
  await saving; // the save's own reload sees both
  await sm.settle();
  gates.open("page");
  await sm.settle();
  assert.deepStrictEqual(pendingIds(sm), []);
  assert.deepStrictEqual(rootIds(sm), ["3"], "1 and 2 stay under 3");
  assert.ok(!second.innerHTML.includes("restored"));
});

test("leaving while a save runs says the changes are being saved", async () => {
  let release;
  const gateUpdate = new Promise((r) => { release = r; });
  const env = setup([["1"], ["2"], ["3"]], { gateUpdate, confirm: () => false });
  const { sm, x } = env;
  x.setParent("1", "2");
  x.setParent("3", "2");
  const saving = x.savePendingChanges();
  await sm.settle();
  x.navigateTo("/studios/2");
  assert.deepStrictEqual(sm.confirmCalls,
    ["2 changes are still being saved. Leave anyway? Saving carries on, and any that fail are kept for when you return."]);
  release();
  await saving;
});

test("title timeouts are cleared on unmount", async () => {
  const env = setup([["1"]]);
  const { sm } = env;
  sm.flushTimers(); // drain the nav-button retries queued at load
  const cleanup = mount(env);
  assert.strictEqual(sm.pendingTimers().length, 3);
  cleanup();
  assert.strictEqual(sm.pendingTimers().length, 0);
});

test("keyboard: only Delete removes a parent; not while typing", () => {
  const env = setup([["1"], ["2", "1"]]);
  const { sm, x } = env;
  sm.setState({ hierarchyStudios: [studio("1"), studio("2", "1")], selectedStudioId: "2" });
  const key = (k, target) => x.handleHierarchyKeyboard({ key: k, target, preventDefault() {} });
  key("Backspace", { tagName: "DIV" });
  assert.strictEqual(sm.getState().pendingChanges.length, 0, "Backspace does nothing");
  for (const t of [{ tagName: "INPUT" }, { tagName: "TEXTAREA" }, { tagName: "SELECT" }, { tagName: "DIV", isContentEditable: true }]) {
    key("Delete", t);
    assert.strictEqual(sm.getState().pendingChanges.length, 0, t.tagName + " ignored");
    key("Escape", t);
    assert.strictEqual(sm.getState().selectedStudioId, "2", t.tagName + " Escape ignored");
  }
  key("Delete", { tagName: "DIV" });
  assert.deepStrictEqual(plain(sm.getState().pendingChanges.map((c) => c.studioId)), ["2"]);
});

test("selectedStudioId is cleared when nothing is rendered selected", () => {
  const env = setup([["1"], ["2", "1"]]);
  const { sm, x, container } = env;
  const list = [studio("1"), studio("2", "1")];
  sm.setState({ hierarchyStudios: list, hierarchyTree: x.buildStudioTree(list), selectedStudioId: "2" });
  container.querySelector = (sel) => (sel === ".sh-node.sh-selected" ? nodeEl("2") : null);
  x.renderHierarchyPage(container);
  assert.strictEqual(sm.getState().selectedStudioId, "2");
  assert.ok(container.innerHTML.includes("sh-selected"), "selected class re-applied on render");
  sm.setState({ selectedStudioId: "99" });
  container.querySelector = () => null;
  x.renderHierarchyPage(container);
  assert.strictEqual(sm.getState().selectedStudioId, null);
});

test("studios on a parent cycle are marked, with a warning; their descendants are not", () => {
  // 1 and 2 are each other's parent; 3 is under 1; 4 is unrelated
  const env = setup([["1", "2"], ["2", "1"], ["3", "1"], ["4"]]);
  const { container, x } = env;
  container.querySelector = () => null;
  x.renderHierarchyPage(container);
  const html = container.innerHTML;
  const badged = (id) => new RegExp(`class="sh-node[^"]*\\bsh-in-cycle\\b[^"]*" data-studio-id="${id}"`).test(html);
  assert.ok(badged("1") && badged("2"), "cycle members are badged");
  assert.ok(!badged("3") && !badged("4"), "a descendant or an unrelated studio is not");
  assert.ok(html.includes('class="sh-cycle-warning"'), "a warning is shown");
  assert.ok(/2 studios are in a parent cycle/.test(html), html.slice(0, 400));
});

test("no cycle warning without a cycle", () => {
  const env = setup([["1"], ["2", "1"]]);
  env.container.querySelector = () => null;
  env.x.renderHierarchyPage(env.container);
  assert.ok(!env.container.innerHTML.includes("sh-cycle"));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n     " + (e && e.stack || e)); }
  }
  if (failed) { console.log(failed + " failed"); process.exit(1); }
  console.log("all " + tests.length + " passed");
})();
