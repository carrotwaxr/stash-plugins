/**
 * Saving: locked, ordered, failures stay pending.
 * Run with: node plugins/studioManager/tests/test_save.js
 */
const assert = require("assert");
const { loadStudioManager, createQueryableElement } = require("./harness");

const plain = (v) => JSON.parse(JSON.stringify(v));
const studio = (id, parent) => ({
  id, name: "S" + id, parent_studio: parent ? { id: parent } : null, child_studios: [],
});

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/**
 * studios: array of [id, parentId]. Server state lives in `server` (id -> parent);
 * StudioUpdate applies it unless `fail(input)` returns a message; `gate` (a promise)
 * holds every StudioUpdate until resolved; `failFetch` makes FindStudios return errors.
 */
function setup(studios, { fail = () => null, gate = null, failFetch = false } = {}) {
  const server = new Map(studios);
  const updates = [];
  const sm = loadStudioManager({
    fetchResponses: {
      StudioUpdate: (body) => {
        const input = body.variables.input;
        updates.push(plain(input));
        const done = () => {
          const msg = fail(input);
          if (msg) return { errors: [{ message: msg }] };
          server.set(input.id, input.parent_id);
          return { data: { studioUpdate: { id: input.id } } };
        };
        return gate ? gate.then(done) : done();
      },
      FindStudios: () => failFetch
        ? { errors: [{ message: "boom" }] }
        : { data: { findStudios: { studios: [...server].map(([id, p]) => studio(id, p)) } } },
    },
  });
  const container = createQueryableElement("div");
  const panel = createQueryableElement("div");
  const toasts = createQueryableElement("div");
  toasts.children = [];
  sm.document.querySelector = (sel) => {
    if (sel === ".studio-hierarchy-container") return container;
    if (sel === ".sh-changes-panel") return panel;
    if (sel === ".sh-toast-container") return toasts;
    return null;
  };
  sm.document.createElement = (t) => createQueryableElement(t);
  const list = studios.map(([id, p]) => studio(id, p));
  sm.setState({
    hierarchyStudios: list, hierarchyTree: sm.exports.buildStudioTree(list),
    hierarchyStats: sm.exports.getTreeStats(list), expandedNodes: new Set(),
    pendingChanges: [], isEditMode: false, originalParentMap: new Map(),
  });
  return { sm, x: sm.exports, server, updates, panel, toasts };
}
const toastTexts = (t) => t.children.map((c) => c.textContent);
const pendingIds = (sm) => plain(sm.getState().pendingChanges.map((c) => c.studioId));

test("editing is locked while a save runs", async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const { sm, x, panel } = setup([["1"], ["2"], ["3"]], { gate });
  x.setParent("1", "2");
  const saving = x.savePendingChanges();
  await sm.settle();
  assert.ok(/Saving…/.test(panel.innerHTML), "panel shows Saving…");
  x.setParent("3", "2");
  x.removeParent("1");
  x.removePendingChange(0);
  x.cancelPendingChanges();
  assert.deepStrictEqual(pendingIds(sm), ["1"]);
  assert.strictEqual(sm.getState().isEditMode, true);
  release();
  await saving;
  assert.deepStrictEqual(pendingIds(sm), []);
  x.setParent("3", "2"); // unlocked again
  assert.deepStrictEqual(pendingIds(sm), ["3"]);
});

test("saves run in orderForSave order (A/B swap)", async () => {
  // A=1 is B's parent... B=2 under A=1; move A under B while B leaves A
  const { sm, x, updates } = setup([["1", null], ["2", "1"]]);
  x.removeParent("2");
  x.setParent("1", "2");
  // queue in the unsafe order (set first), as a saved-later edit could
  sm.setState({ pendingChanges: sm.getState().pendingChanges.slice().reverse() });
  assert.strictEqual(sm.getState().pendingChanges[0].type, "set-parent");
  await x.savePendingChanges();
  assert.deepStrictEqual(updates, [{ id: "2", parent_id: null }, { id: "1", parent_id: "2" }]);
  assert.deepStrictEqual(pendingIds(sm), []);
});

test("a parent chain that runs into an existing cycle is refused before any request", async () => {
  // server data already has a cycle 8 <-> 9; 1 is free
  const { sm, x, updates } = setup([["1", null], ["8", "9"], ["9", "8"]]);
  sm.setState({ pendingChanges: [], isEditMode: true, originalParentMap: new Map([["1", null], ["8", "9"], ["9", "8"]]) });
  sm.setState({ pendingChanges: [{ type: "set-parent", studioId: "1", studioName: "S1", parentId: "8", parentName: "S8" }] });
  await x.savePendingChanges();
  assert.strictEqual(updates.length, 0);
  const c = sm.getState().pendingChanges;
  assert.strictEqual(c.length, 1);
  assert.strictEqual(c[0].error, "Its parent chain runs into an existing cycle; fix that first");
});

test("a change that would make a studio its own ancestor says so", async () => {
  // 1 is under 2; a pending 2 -> 1 (e.g. re-applied on data changed elsewhere)
  const { sm, x, updates, toasts } = setup([["1", "2"], ["2", null]]);
  sm.setState({
    isEditMode: true, originalParentMap: new Map([["1", "2"], ["2", null]]),
    pendingChanges: [{ type: "set-parent", studioId: "2", studioName: "S2", parentId: "1", parentName: "S1" }],
  });
  await x.savePendingChanges();
  assert.strictEqual(updates.length, 0);
  assert.strictEqual(sm.getState().pendingChanges[0].error, 'Would make "S2" its own ancestor');
  assert.ok(toastTexts(toasts).some((t) => t.includes('"S2": Would make "S2" its own ancestor')), toastTexts(toasts).join("|"));
});

test("partial failure: others saved, failed stays pending with its error", async () => {
  const { sm, x, server, updates, panel, toasts } = setup(
    [["1"], ["2"], ["3"], ["4"]],
    { fail: (i) => (i.id === "2" ? "nope" : null) },
  );
  x.setParent("1", "4");
  x.setParent("2", "4");
  x.setParent("3", "4");
  await x.savePendingChanges();
  assert.strictEqual(updates.length, 3);
  assert.strictEqual(server.get("1"), "4");
  assert.strictEqual(server.get("3"), "4");
  assert.deepStrictEqual(pendingIds(sm), ["2"]);
  assert.ok(/nope/.test(sm.getState().pendingChanges[0].error));
  const st = sm.getState();
  assert.strictEqual(st.isEditMode, true);
  assert.strictEqual(st.originalParentMap.get("1"), "4", "re-snapshotted from refetch");
  assert.strictEqual(st.originalParentMap.get("2"), null);
  assert.strictEqual(st.hierarchyStudios.find((s) => s.id === "1").parent_studio.id, "4");
  const eff = plain([...x.effectiveParentMap(st.originalParentMap, st.pendingChanges)]);
  assert.deepStrictEqual(eff.find(([id]) => id === "2"), ["2", "4"], "failed change re-applied");
  assert.ok(/nope/.test(panel.innerHTML), "error on its row");
  const texts = toastTexts(toasts);
  assert.ok(texts.some((t) => t.includes("2 saved, 1 failed") && t.includes("\n")), texts.join("|"));
});

test("full success clears the queue and reports the count", async () => {
  const { sm, x, toasts } = setup([["1"], ["2"], ["3"], ["4"]]);
  x.setParent("1", "4");
  x.setParent("2", "4");
  x.setParent("3", "4");
  await x.savePendingChanges();
  assert.deepStrictEqual(pendingIds(sm), []);
  assert.strictEqual(sm.getState().isEditMode, false);
  assert.strictEqual(sm.getState().originalParentMap.size, 0);
  assert.ok(toastTexts(toasts).includes("3 saved"), toastTexts(toasts).join("|"));
});

test("a failed refetch keeps the failures pending and shows an error", async () => {
  const { sm, x, toasts } = setup(
    [["1"], ["2"], ["3"]],
    { fail: (i) => (i.id === "2" ? "nope" : null), failFetch: true },
  );
  x.setParent("1", "3");
  x.setParent("2", "3");
  await x.savePendingChanges();
  assert.deepStrictEqual(pendingIds(sm), ["2"]);
  assert.strictEqual(sm.getState().isEditMode, true);
  assert.ok(toastTexts(toasts).some((t) => /reload/i.test(t)), toastTexts(toasts).join("|"));
  x.setParent("1", "2"); // not locked after a failed refetch
});

const rootIds = (sm) => plain(sm.getState().hierarchyTree.map((n) => n.id));

test("a failed refetch after a full save keeps the saved tree and says so", async () => {
  const { sm, x, toasts } = setup([["1"], ["2"], ["3"]], { failFetch: true });
  x.setParent("1", "3");
  x.setParent("2", "3");
  await x.savePendingChanges();
  assert.deepStrictEqual(pendingIds(sm), []);
  const texts = toastTexts(toasts);
  assert.ok(texts.includes("2 saved"), texts.join("|"));
  assert.ok(texts.includes("Saved, but the hierarchy couldn't be reloaded"), texts.join("|"));
  assert.ok(!texts.some((t) => /pending/.test(t)), "nothing is pending: " + texts.join("|"));
  assert.deepStrictEqual(rootIds(sm), ["3"], "1 and 2 stay under 3");
  x.removeParent("1");
  assert.deepStrictEqual(pendingIds(sm), ["1"], "1 is under 3, so it can be made a root");
  assert.ok(!toastTexts(toasts).includes("Studio is already a root"));
});

test("after a partial failure and a failed refetch, x or Cancel keeps the saved changes", async () => {
  for (const how of ["remove", "cancel"]) {
    const { sm, x, toasts } = setup(
      [["1"], ["2"], ["3"]],
      { fail: (i) => (i.id === "2" ? "nope" : null), failFetch: true },
    );
    x.setParent("1", "3");
    x.setParent("2", "3");
    await x.savePendingChanges();
    assert.deepStrictEqual(pendingIds(sm), ["2"]);
    assert.deepStrictEqual(rootIds(sm), ["3"], how + ": before");
    if (how === "remove") x.removePendingChange(0); else x.cancelPendingChanges();
    assert.deepStrictEqual(pendingIds(sm), []);
    assert.deepStrictEqual(rootIds(sm), ["2", "3"], how + ": 1 stays under 3, 2 goes back to the top");
    assert.ok(toastTexts(toasts).includes("Couldn't reload the hierarchy; the failed changes are still pending"),
      toastTexts(toasts).join("|"));
  }
});

test("drag validity is checked against the displayed tree", async () => {
  const { sm, x, toasts } = setup(
    [["1"], ["2"], ["3"]],
    { fail: (i) => (i.id === "2" ? "nope" : null), failFetch: true },
  );
  x.setParent("1", "3");
  x.setParent("2", "3");
  await x.savePendingChanges();
  // 1 is shown (and saved) under 3, so 3 can't go under 1
  assert.strictEqual(x.wouldCreateCircularRef("1", "3"), true);
  x.setParent("3", "1");
  assert.deepStrictEqual(pendingIds(sm), ["2"], "refused");
  assert.ok(toastTexts(toasts).includes("Cannot create circular reference"));

  // The baseline shown (the edit snapshot) wins over the studio list
  const b = setup([["1"], ["2"]]);
  b.sm.setState({ isEditMode: true, originalParentMap: new Map([["1", "2"], ["2", null]]) });
  assert.strictEqual(b.x.wouldCreateCircularRef("1", "2"), true, "2 would go under its own child 1");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
