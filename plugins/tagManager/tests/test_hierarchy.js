/**
 * Tag Hierarchy page, against the REAL tag-manager.js (via the vm harness):
 * tree building, circular-reference checks, edit state reset, re-fetched saves,
 * alias search, lazy tree rendering, dialog listener cleanup and keyboard scoping.
 * Run with: node plugins/tagManager/tests/test_hierarchy.js
 */
const { loadTagManager, createQueryableElement } = require("./harness");

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const show = (v) => JSON.stringify(v);

const T = (id, name, parents = [], extra = {}) => ({
  id, name, parents: parents.map((p) => ({ id: p })), children: [], ...extra,
});

(async () => {
  // ---------------- wouldCreateCircularRef (real function) ----------------
  console.log("\n=== wouldCreateCircularRef ===");
  {
    const tm = loadTagManager();
    const circ = (tags, pending, a, b) => {
      tm.setState({ hierarchyTags: tags, pendingChanges: pending || [] });
      return tm.exports.wouldCreateCircularRef(a, b);
    };
    check("direct cycle", circ([T("1", "P"), T("2", "C", ["1"])], [], "2", "1") === true);
    check("indirect cycle", circ([T("1", "G"), T("2", "P", ["1"]), T("3", "C", ["2"])], [], "3", "1") === true);
    check("unrelated tags are fine", circ([T("1", "A"), T("2", "B")], [], "1", "2") === false);
    check("sibling as parent is fine", circ([T("1", "P"), T("2", "A", ["1"]), T("3", "B", ["1"])], [], "2", "3") === false);
    check("unknown tag is fine", circ([T("1", "Root")], [], "1", "999") === false);
    const diamond = [T("A", "A"), T("B", "B", ["A"]), T("C", "C", ["A"]), T("D", "D", ["B", "C"])];
    check("diamond: D above A is a cycle", circ(diamond, [], "D", "A") === true);
    check("diamond: A above D is fine", circ(diamond, [], "A", "D") === false);
    // pending changes count
    const flat = [T("1", "A"), T("2", "B")];
    check("wouldCreateCircularRef honors pending add",
      circ(flat, [{ type: "add-parent", tagId: "2", parentId: "1" }], "2", "1") === true);
    const linked = [T("1", "A"), T("2", "B", ["1"])];
    check("wouldCreateCircularRef honors pending remove",
      circ(linked, [{ type: "remove-parent", tagId: "2", parentId: "1" }], "2", "1") === false);
  }

  // ---------------- buildTagTree / getTreeStats (real functions) ----------------
  console.log("\n=== buildTagTree / getTreeStats ===");
  {
    const tm = loadTagManager();
    const { buildTagTree, getTreeStats } = tm.exports;
    let tree = buildTagTree([T("1", "Root2"), T("2", "Root1")]);
    check("roots sorted", eq(tree.map((n) => n.name), ["Root1", "Root2"]));
    tree = buildTagTree([T("1", "P"), T("2", "Zed", ["1"]), T("3", "Apple", ["1"])]);
    check("children sorted, parentContextId set",
      eq(tree[0].childNodes.map((n) => n.name), ["Apple", "Zed"]) &&
      tree[0].parentContextId === null && tree[0].childNodes[0].parentContextId === "1");
    tree = buildTagTree([T("1", "PA"), T("2", "PB"), T("3", "M", ["1", "2"])]);
    check("multi-parent tag appears under each parent",
      tree.every((r) => r.childNodes.length === 1 && r.childNodes[0].name === "M"));
    check("empty input", buildTagTree([]).length === 0);
    const stats = getTreeStats([
      T("1", "Root", [], { child_count: 2, parent_count: 0 }),
      T("2", "C1", ["1"], { child_count: 0, parent_count: 1 }),
      T("3", "C2", ["1"], { child_count: 1, parent_count: 1 }),
      T("4", "G", ["3"], { child_count: 0, parent_count: 1 }),
    ]);
    check("stats", eq(stats, { totalTags: 4, rootTags: 1, tagsWithChildren: 2, tagsWithParents: 3 }), show(stats));
  }

  // ---------------- mount resets edit state ----------------
  console.log("\n=== mount resets edit state ===");
  {
    const tm = loadTagManager();
    check("resetHierarchyEditState is exported", typeof tm.exports.resetHierarchyEditState === "function");
    const stale = () => tm.setState({
      isEditMode: true, selectedTagId: "9", copiedTagId: "8",
      pendingChanges: [{ type: "add-parent", tagId: "1", parentId: "2" }],
      originalParentMap: new Map([["1", ["5"]]]),
    });
    stale();
    tm.exports.resetHierarchyEditState();
    let st = tm.getState();
    check("reset clears edit mode, pending, snapshot, selection",
      st.isEditMode === false && st.pendingChanges.length === 0 && st.originalParentMap.size === 0 &&
      st.selectedTagId === null && st.copiedTagId === null, show([st.isEditMode, st.pendingChanges, st.selectedTagId]));

    const page = tm.routes[1];
    check("hierarchy route registered", !!page);
    tm.effects.length = 0;
    page.component();
    check("component registers a mount effect", tm.effects.length === 1);
    stale();
    const cleanup = tm.effects[0]();
    st = tm.getState();
    check("mount effect clears stale pending changes and edit mode",
      st.isEditMode === false && st.pendingChanges.length === 0, show(st.pendingChanges));
    stale();
    if (typeof cleanup === "function") cleanup();
    st = tm.getState();
    check("unmount cleanup clears edit state too",
      typeof cleanup === "function" && st.isEditMode === false && st.pendingChanges.length === 0);
  }

  // ---------------- savePendingChanges ----------------
  console.log("\n=== savePendingChanges ===");
  {
    const mutations = [];
    const tm = loadTagManager({
      fetchResponses: {
        FindTag: (body) => ({
          data: { findTag: { parents: body.variables.id === "1" ? [{ id: "7" }, { id: "8" }] : [{ id: "50" }] } },
        }),
        TagUpdate: (body) => {
          mutations.push(body.variables.input);
          if (body.variables.input.id === "2") return { errors: [{ message: "boom" }] };
          return { data: { tagUpdate: { id: body.variables.input.id, name: "x", parents: [] } } };
        },
      },
    });
    tm.setState({
      hierarchyTags: [T("1", "One", ["3"]), T("2", "Two", ["4"]), T("3", "Three"), T("4", "Four"), T("9", "Nine")],
      isEditMode: true,
      originalParentMap: new Map([["1", ["3"]], ["2", ["4"]]]),
      pendingChanges: [
        { type: "add-parent", tagId: "1", tagName: "One", parentId: "9", parentName: "Nine" },
        { type: "remove-parent", tagId: "1", tagName: "One", parentId: "8", parentName: "Eight" },
        { type: "add-parent", tagId: "2", tagName: "Two", parentId: "9", parentName: "Nine" },
      ],
    });
    const ok = await tm.exports.savePendingChanges();
    const m1 = mutations.find((m) => m.id === "1");
    check("save re-fetches parents (uses fresh FindTag, not the snapshot)",
      m1 && eq([...m1.parent_ids].sort(), ["7", "9"]), show(mutations));
    check("FindTag was queried per tag", tm.fetchCalls.filter((c) => c.op === "FindTag").length === 2);
    const st = tm.getState();
    check("failed change stays pending, successful ones are removed",
      st.pendingChanges.length === 1 && st.pendingChanges[0].tagId === "2", show(st.pendingChanges));
    check("save reports failure", ok === false);
    const one = st.pendingChanges.length && tm.getState().hierarchyTags.find((t) => t.id === "1");
    check("saved tag's local parents updated", one && eq(one.parents.map((p) => p.id).sort(), ["7", "9"]), show(one));

    // exitEditMode(true) with a failure stays in edit mode
    tm.setState({ isEditMode: true });
    await tm.exports.exitEditMode(true);
    check("edit mode kept when a save fails", tm.getState().isEditMode === true && tm.getState().pendingChanges.length === 1);
  }

  // ---------------- alias search ----------------
  console.log("\n=== aliases fetched ===");
  {
    const tm = loadTagManager({ fetchResponses: { AllTagsWithHierarchy: { data: { allTags: [] } } } });
    await tm.exports.fetchAllTagsWithHierarchy();
    const call = tm.fetchCalls.find((c) => c.op === "AllTagsWithHierarchy");
    check("AllTagsWithHierarchy query requests aliases", call && /\baliases\b/.test(call.body.query));
  }

  // ---------------- search dialog listener cleanup ----------------
  console.log("\n=== search dialog listeners ===");
  {
    const tm = loadTagManager();
    const mk = (t) => {
      const el = createQueryableElement(t);
      el.focus = () => {};
      const q = el.querySelector;
      el.querySelector = (sel) => { const c = q(sel); if (c && !c.focus) c.focus = () => {}; return c; };
      return el;
    };
    tm.document.createElement = mk;
    const counts = tm.document.listenerCounts;
    const open = () => {
      const made = [];
      const orig = tm.document.createElement;
      tm.document.createElement = (t) => { const el = orig(t); made.push(el); return el; };
      tm.exports.showTagSearchDialog("parent", T("1", "One"));
      tm.document.createElement = orig;
      return made;
    };
    let a0 = counts.add, r0 = counts.remove;
    let made = open();
    made[0].listeners.click[0]();   // backdrop click
    check("keydown listener removed on backdrop close", counts.add - a0 === counts.remove - r0 && counts.add - a0 >= 1,
      `add=${counts.add - a0} remove=${counts.remove - r0}`);

    a0 = counts.add; r0 = counts.remove;
    made = open();
    tm.exports.closeTagSearchDialog();
    check("keydown listener removed on programmatic close (result click path)",
      counts.add - a0 === counts.remove - r0, `add=${counts.add - a0} remove=${counts.remove - r0}`);

    // Escape handler still works and removes exactly once
    a0 = counts.add; r0 = counts.remove;
    open();
    tm.exports.closeTagSearchDialog();
    tm.exports.closeTagSearchDialog();
    check("closing twice does not double-remove", counts.remove - r0 === counts.add - a0);
  }

  // ---------------- lazy tree rendering ----------------
  console.log("\n=== tree rendering ===");
  {
    const tm = loadTagManager();
    const { buildTagTree, renderTreeNode } = tm.exports;
    const tree = buildTagTree([T("1", "Root", [], { child_count: 1 }), T("2", "Kid", ["1"], { child_count: 1 }), T("3", "Grandkid", ["2"])]);
    tm.setState({ expandedNodes: new Set() });
    let html = renderTreeNode(tree[0], true);
    check("collapsed branch renders no children", html.includes('data-tag-id="1"') && !html.includes('data-tag-id="2"'), html.slice(0, 300));
    check("collapsed branch keeps an empty children container", /th-children\s*"[^>]*data-parent-id="1"/.test(html));
    tm.setState({ expandedNodes: new Set(["1"]) });
    html = renderTreeNode(tree[0], true);
    check("expanded node renders its children, but not a collapsed grandchild level",
      html.includes('data-tag-id="2"') && !html.includes('data-tag-id="3"'));
    check("expanded container has th-expanded", /th-children th-expanded/.test(html));
    tm.setState({ expandedNodes: new Set(["1", "2"]) });
    check("nested expanded renders all", renderTreeNode(tree[0], true).includes('data-tag-id="3"'));
  }

  // ---------------- keyboard ----------------
  console.log("\n=== keyboard ===");
  {
    const tm = loadTagManager();
    const insideTree = { tagName: "DIV", closest: (s) => (s === ".tag-hierarchy-container" ? container : null) };
    const container = { classList: { contains: () => true }, querySelectorAll: () => [], contains: (n) => n === insideTree };
    const outside = { tagName: "DIV", closest: () => null };
    const input = { tagName: "INPUT", closest: (s) => (s === ".tag-hierarchy-container" ? container : null) };
    const select = { tagName: "SELECT", closest: (s) => (s === ".tag-hierarchy-container" ? container : null) };
    const editable = { tagName: "DIV", isContentEditable: true, closest: (s) => (s === ".tag-hierarchy-container" ? container : null) };
    const ev = (o, target) => ({ key: "c", ctrlKey: true, target, preventDefault() { this.prevented = true; }, ...o });
    const { shouldHandleHierarchyKey } = tm.exports;

    check("helper: focus inside tree", shouldHandleHierarchyKey(ev({}, insideTree), insideTree) === true);
    check("helper: focus outside tree", shouldHandleHierarchyKey(ev({}, outside), outside) === false);
    check("helper: input in tree is not handled", shouldHandleHierarchyKey(ev({}, input), input) === false);
    check("helper: select in tree is not handled", shouldHandleHierarchyKey(ev({}, select), select) === false);
    check("helper: contentEditable is not handled", shouldHandleHierarchyKey(ev({}, editable), editable) === false);
    check("helper: nothing focused but target in tree", shouldHandleHierarchyKey(ev({}, insideTree), null) === true);
    check("helper: nothing focused, target outside", shouldHandleHierarchyKey(ev({}, outside), null) === false);

    tm.document.querySelector = (sel) => (sel === ".tag-hierarchy-container" ? container : null);
    const run = (event, active) => {
      tm.document.activeElement = active;
      tm.setState({ selectedTagId: "5", copiedTagId: null });
      tm.exports.handleHierarchyKeyboard(event);
      return tm.getState().copiedTagId;
    };
    let e = ev({}, insideTree);
    check("ctrl+c copies when the tree is focused", run(e, insideTree) === "5" && e.prevented === true);
    e = ev({ key: "C" }, insideTree);
    check("uppercase C also copies", run(e, insideTree) === "5");
    e = ev({ ctrlKey: false, metaKey: true }, insideTree);
    check("cmd+c copies", run(e, insideTree) === "5");
    e = ev({}, input);
    check("ctrl+c ignored in inputs (native copy kept)", run(e, input) === null && !e.prevented);
    e = ev({}, outside);
    check("ctrl+c ignored when the tree is not focused", run(e, outside) === null && !e.prevented);

    // Ctrl+V: needs copied + selected; ignored in input
    const runPaste = (event, active) => {
      tm.document.activeElement = active;
      tm.setState({ selectedTagId: "5", copiedTagId: "6", hierarchyTags: [T("5", "Five"), T("6", "Six")], pendingChanges: [], isEditMode: false });
      tm.exports.handleHierarchyKeyboard(event);
      return tm.getState().pendingChanges.length;
    };
    e = ev({ key: "v" }, input);
    check("ctrl+v ignored in inputs", runPaste(e, input) === 0 && !e.prevented);
    e = ev({ key: "V" }, insideTree);
    check("ctrl+v pastes in the tree (case-insensitive)", runPaste(e, insideTree) === 1 && e.prevented === true);

    // Delete
    e = ev({ key: "Delete", ctrlKey: false }, select);
    tm.document.activeElement = select;
    tm.setState({ selectedTagId: "5" });
    tm.exports.handleHierarchyKeyboard(e);
    check("Delete ignored in a select", !e.prevented);
    e = ev({ key: "Backspace", ctrlKey: false }, outside);
    tm.document.activeElement = outside;
    tm.exports.handleHierarchyKeyboard(e);
    check("Backspace ignored when tree not focused", !e.prevented);
  }

  console.log(failed ? `\n${failed} FAILED` : "\nAll hierarchy tests passed");
  process.exit(failed ? 1 : 0);
})();
