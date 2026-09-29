/**
 * Pending changes: the edited view is derived from server state + pending changes.
 * Run with: node plugins/studioManager/tests/test_pending.js
 */
const assert = require("assert");
const { loadStudioManager, createQueryableElement } = require("./harness");

const plain = (v) => JSON.parse(JSON.stringify(v));
const studio = (id, parent, kids) => ({
  id, name: "S" + id, parent_studio: parent ? { id: parent, name: "S" + parent } : null,
  child_studios: (kids || []).map((k) => ({ id: k, name: "S" + k })),
});

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// 1,2,3,4 roots; 5 under 4; 6 under 5
function setup() {
  const sm = loadStudioManager({});
  const container = createQueryableElement("div");
  const panel = createQueryableElement("div");
  panel.buttons = [];
  sm.document.querySelector = (sel) => {
    if (sel === ".studio-hierarchy-container") return container;
    if (sel === ".sh-changes-panel") return panel;
    return null;
  };
  sm.document.createElement = (t) => createQueryableElement(t);
  const studios = [studio("1"), studio("2"), studio("3"), studio("4", null, ["5"]), studio("5", "4", ["6"]), studio("6", "5")];
  sm.setState({
    hierarchyStudios: studios, hierarchyTree: sm.exports.buildStudioTree(studios),
    hierarchyStats: sm.exports.getTreeStats(studios), expandedNodes: new Set(),
    pendingChanges: [], isEditMode: false, originalParentMap: new Map(),
  });
  return { sm, container, panel, x: sm.exports };
}
const rootIds = (sm) => plain(sm.getState().hierarchyTree.map((n) => n.id));
const kidsOf = (sm, id) => {
  const find = (nodes) => { for (const n of nodes) { if (n.id === id) return n; const r = find(n.childNodes); if (r) return r; } };
  return plain(find(sm.getState().hierarchyTree).childNodes.map((n) => n.id));
};

test("derivedStudios: server state untouched, child_studios recomputed", () => {
  const { sm, x } = setup();
  x.setParent("1", "2");
  const st = sm.getState();
  assert.strictEqual(st.hierarchyStudios.find((s) => s.id === "1").parent_studio, null);
  const d = x.derivedStudios();
  assert.strictEqual(d.find((s) => s.id === "1").parent_studio.id, "2");
  assert.deepStrictEqual(plain(d.find((s) => s.id === "2").child_studios.map((c) => c.id)), ["1"]);
  assert.strictEqual(st.hierarchyStudios.find((s) => s.id === "2").child_studios.length, 0);
});

test("removing the middle change keeps the others, no fetch", () => {
  const { sm, x } = setup();
  x.setParent("1", "2"); // A
  x.setParent("3", "2"); // B
  x.setParent("6", "1"); // C
  x.removePendingChange(1);
  assert.deepStrictEqual(plain(sm.getState().pendingChanges.map((c) => c.studioId)), ["1", "6"]);
  assert.deepStrictEqual(kidsOf(sm, "2"), ["1"]);
  assert.deepStrictEqual(kidsOf(sm, "1"), ["6"]);
  assert.ok(rootIds(sm).includes("3"), "B reverted");
  assert.strictEqual(sm.fetchCalls.length, 0);
});

test("removing the last change leaves edit mode, original tree, no fetch", () => {
  const { sm, x } = setup();
  x.setParent("1", "2");
  x.removePendingChange(0);
  assert.strictEqual(sm.getState().isEditMode, false);
  assert.deepStrictEqual(rootIds(sm), ["1", "2", "3", "4"]);
  assert.strictEqual(sm.fetchCalls.length, 0);
});

test("Cancel restores the original tree locally", () => {
  const { sm, x, panel } = setup();
  x.setParent("1", "2");
  x.setParent("3", "2");
  x.renderChangesPanel();
  const cancel = panel.found.get("#sh-cancel-changes");
  assert.ok(cancel && cancel.listeners.click, "cancel handler attached");
  cancel.listeners.click[0]();
  assert.deepStrictEqual(sm.getState().pendingChanges.length, 0);
  assert.deepStrictEqual(rootIds(sm), ["1", "2", "3", "4"]);
  assert.strictEqual(sm.getState().hierarchyStats.rootStudios, 4);
  assert.strictEqual(sm.fetchCalls.length, 0);
});

test("returning a studio to its original parent drops the pending entry", () => {
  const { sm, x } = setup();
  x.setParent("5", "1");
  assert.strictEqual(sm.getState().pendingChanges.length, 1);
  x.setParent("5", "4");
  assert.strictEqual(sm.getState().pendingChanges.length, 0);
  x.removeParent("5");
  assert.strictEqual(sm.getState().pendingChanges.length, 1);
  x.setParent("5", "4");
  assert.strictEqual(sm.getState().pendingChanges.length, 0);
  x.removeParent("1"); // already root: no entry
  assert.strictEqual(sm.getState().pendingChanges.length, 0);
});

test("set-parent expands every ancestor of the moved studio", () => {
  const { sm, x } = setup();
  x.setParent("2", "6"); // 6 is under 5 under 4
  const ex = sm.getState().expandedNodes;
  for (const id of ["6", "5", "4"]) assert.ok(ex.has(id), "expanded " + id);
  assert.ok(kidsOf(sm, "6").includes("2"));
});

test("stats reflect pending changes", () => {
  const { sm, x } = setup();
  assert.strictEqual(sm.getState().hierarchyStats.rootStudios, 4);
  x.setParent("1", "2");
  let s = sm.getState().hierarchyStats;
  assert.strictEqual(s.rootStudios, 3);
  assert.strictEqual(s.studiosWithChildren, 3);
  assert.strictEqual(s.totalStudios, 6);
  x.removeParent("5");
  s = sm.getState().hierarchyStats;
  assert.strictEqual(s.rootStudios, 4);
  assert.strictEqual(s.maxDepth, 2 - 1); // 6 -> 5 is the only chain left
});

test("context menu hasChildren uses the effective tree", () => {
  const { sm, x, container } = setup();
  const menuHtml = () => {
    let el;
    sm.document.body.appendChild = (c) => { el = c; return c; };
    x.showContextMenu(10, 10, "4");
    return el.innerHTML;
  };
  assert.ok(/expand|Expand/.test(menuHtml()), "4 has children initially");
  x.removeParent("5");
  assert.ok(!/expand|Expand/.test(menuHtml()), "4 has no children after 5 moves out");
  x.setParent("1", "2");
  sm.document.body.appendChild = (c) => c;
  let el;
  sm.document.body.appendChild = (c) => { el = c; return c; };
  x.showContextMenu(10, 10, "2");
  assert.ok(/expand|Expand/.test(el.innerHTML), "2 has children after 1 moves in");
});

const toastTexts = (sm) => sm.document.body.children
  .filter((c) => c.className === "sh-toast-container")
  .flatMap((c) => c.children.map((t) => t.textContent));

test("removing a change the others need to stay acyclic is refused", () => {
  const { sm, x, container } = setup(); // 5 is under 4
  x.removeParent("5");
  x.setParent("4", "5"); // fine while 5 has no parent
  assert.deepStrictEqual(plain(sm.getState().pendingChanges.map((c) => c.studioId)), ["5", "4"]);
  const html = container.innerHTML;
  x.removePendingChange(0); // would put 5 back under 4, with 4 under 5
  assert.deepStrictEqual(plain(sm.getState().pendingChanges.map((c) => c.studioId)), ["5", "4"], "both kept");
  assert.ok(toastTexts(sm).includes('Remove the change for "S4" first: it needs this one to avoid a cycle'),
    toastTexts(sm).join("|"));
  assert.ok(!/sh-cycle/.test(container.innerHTML), "no cycle shown");
  assert.strictEqual(container.innerHTML, html, "view unchanged");
  x.removePendingChange(1); // the dependent one goes first
  x.removePendingChange(0);
  assert.strictEqual(sm.getState().pendingChanges.length, 0);
});

test("removing a change that fixed a cycle already in the data is allowed", () => {
  const { sm, x } = setup();
  const loop = [studio("1", "2"), studio("2", "1")];
  sm.setState({ hierarchyStudios: loop, originalParentMap: new Map() });
  x.removeParent("1");
  assert.strictEqual(sm.getState().pendingChanges.length, 1);
  x.removePendingChange(0); // back to the server's own loop: no pending change depends on it
  assert.strictEqual(sm.getState().pendingChanges.length, 0);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
