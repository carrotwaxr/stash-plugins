/**
 * Pure model helpers of the REAL studio-manager.js: parents, cycles, save order.
 * Run with: node plugins/studioManager/tests/test_model.js
 */
const assert = require("assert");
const { loadStudioManager } = require("./harness");

const sm = loadStudioManager({});
const x = sm.exports;
// Objects made inside the vm have another Array/Map prototype; normalize for deepStrictEqual.
const plain = (v) => JSON.parse(JSON.stringify(v));

const studio = (id, parent, name) => ({
  id, name: name || "S" + id, parent_studio: parent ? { id: parent } : null,
});
const change = (type, studioId, parentId = null) => ({
  type, studioId, studioName: "S" + studioId, parentId, parentName: parentId ? "S" + parentId : null,
});
const mapOf = (obj) => new Map(Object.entries(obj));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("effectiveParentMap applies set-parent and remove-parent, original untouched", () => {
  const orig = mapOf({ 1: null, 2: "1", 3: "1" });
  const eff = x.effectiveParentMap(orig, [change("set-parent", "1", "3"), change("remove-parent", "2")]);
  assert.ok(eff instanceof Map || typeof eff.get === "function");
  assert.strictEqual(eff.get("1"), "3");
  assert.strictEqual(eff.get("2"), null);
  assert.strictEqual(eff.get("3"), "1");
  assert.strictEqual(orig.get("1"), null);
  assert.strictEqual(orig.get("2"), "1");
});

test("effectiveParentMap with no pending copies the original", () => {
  const eff = x.effectiveParentMap(mapOf({ 1: null, 2: "1" }), []);
  assert.deepStrictEqual(plain([...eff]), [["1", null], ["2", "1"]]);
});

test("wouldCreateCycle: self, descendant, unrelated", () => {
  const m = mapOf({ 1: null, 2: "1", 3: "2", 4: null });
  assert.strictEqual(x.wouldCreateCycle("1", "1", m), true);
  assert.strictEqual(x.wouldCreateCycle("1", "3", m), true);
  assert.strictEqual(x.wouldCreateCycle("1", "2", m), true);
  assert.strictEqual(x.wouldCreateCycle("3", "1", m), false);
  assert.strictEqual(x.wouldCreateCycle("1", "4", m), false);
  assert.strictEqual(x.wouldCreateCycle("4", "3", m), false);
});

test("wouldCreateCycle: walk into an existing cycle is refused and terminates", () => {
  const m = mapOf({ 1: "2", 2: "1", 3: null, 4: "1" });
  assert.strictEqual(x.wouldCreateCycle("3", "4", m), true);
  assert.strictEqual(x.wouldCreateCycle("3", "1", m), true);
  assert.strictEqual(x.wouldCreateCycle("3", "3", m), true);
  assert.strictEqual(x.wouldCreateCycle("3", null, m), false);
});

test("wouldCreateCircularRef wraps the pending-aware check", () => {
  sm.setState({
    hierarchyStudios: [studio("1"), studio("2", "1")],
    pendingChanges: [change("remove-parent", "2")],
  });
  // after removing 2 from 1, putting 1 under 2 is fine
  assert.strictEqual(x.wouldCreateCircularRef("2", "1"), false);
  sm.setState({ pendingChanges: [] });
  assert.strictEqual(x.wouldCreateCircularRef("2", "1"), true);
  assert.strictEqual(x.wouldCreateCircularRef("1", "1"), true);
});

test("findCycleMembers: cycle members and their descendants only", () => {
  const studios = [
    studio("1", "2"), studio("2", "1"),     // 2-cycle
    studio("3", "1"), studio("4", "3"),     // descendants
    studio("5"), studio("6", "5"),          // healthy
    studio("7", "7"),                       // self-parent
  ];
  const ids = [...x.findCycleMembers(studios)].sort();
  assert.deepStrictEqual(plain(ids), ["1", "2", "3", "4", "7"]);
  assert.deepStrictEqual(plain([...x.findCycleMembers([studio("5"), studio("6", "5")])]), []);
});

test("ancestorsOf: parent up to root; stops at a cycle", () => {
  const m = mapOf({ 1: null, 2: "1", 3: "2" });
  assert.deepStrictEqual(plain(x.ancestorsOf("3", m)), ["2", "1"]);
  assert.deepStrictEqual(plain(x.ancestorsOf("1", m)), []);
  const c = mapOf({ 1: "2", 2: "1", 3: "1" });
  assert.deepStrictEqual(plain(x.ancestorsOf("3", c)), ["1", "2"]);
  assert.deepStrictEqual(plain(x.ancestorsOf("1", c)), ["2"]);
});

const walk = (roots, fn) => roots.forEach((n) => { fn(n); walk(n.childNodes, fn); });

test("buildStudioTree: orphan with missing parent is a root; sorted by name", () => {
  const roots = x.buildStudioTree([studio("2", "999", "Zed"), studio("1", null, "Alpha"), studio("3", "1", "Child")]);
  assert.deepStrictEqual(plain(roots.map((r) => r.id)), ["1", "2"]);
  assert.deepStrictEqual(plain(roots[0].childNodes.map((c) => c.id)), ["3"]);
  assert.ok(!roots[0].inCycle);
  assert.strictEqual(roots.totalStudios, 3);
});

test("buildStudioTree: cycles broken at smallest id, flagged, nothing disappears", () => {
  const studios = [
    studio("5", "3"), studio("3", "4"), studio("4", "5"), // 3-cycle, smallest id 3
    studio("6", "4"),                                     // descendant
    studio("8", "9"), studio("9", "8"),                   // second cycle, smallest 8
    studio("1"), studio("2", "1"),
  ];
  const roots = x.buildStudioTree(studios);
  const seen = [];
  walk(roots, (n) => seen.push(n.id));
  assert.deepStrictEqual(plain(seen.slice().sort()), ["1", "2", "3", "4", "5", "6", "8", "9"]);
  assert.strictEqual(new Set(seen).size, seen.length);
  assert.deepStrictEqual(plain(roots.map((r) => r.id).sort()), ["1", "3", "8"]);
  const flagged = [];
  walk(roots, (n) => { if (n.inCycle) flagged.push(n.id); });
  assert.deepStrictEqual(plain(flagged.sort()), ["3", "4", "5", "6", "8", "9"]);
  assert.strictEqual(roots.find((r) => r.id === "1").inCycle, false);
  assert.strictEqual(roots.totalStudios, 8);
});

test("buildStudioTree: self-parent is a flagged root", () => {
  const roots = x.buildStudioTree([studio("7", "7"), studio("1")]);
  assert.deepStrictEqual(plain(roots.map((r) => r.id).sort()), ["1", "7"]);
  assert.strictEqual(roots.find((r) => r.id === "7").inCycle, true);
  assert.strictEqual(roots.totalStudios, 2);
});

test("orderForSave: removes first, then set-parent shallowest first", () => {
  const orig = mapOf({ A: null, B: "A", C: null, D: null });
  const pending = [
    change("set-parent", "D", "C"),
    change("set-parent", "A", "B"),   // A under B, while B leaves A
    change("remove-parent", "B"),
    change("set-parent", "C", "A"),
  ];
  const eff = x.effectiveParentMap(orig, pending);
  const ordered = x.orderForSave(pending, eff);
  assert.deepStrictEqual(plain(ordered.map((c) => c.type + ":" + c.studioId)),
    ["remove-parent:B", "set-parent:A", "set-parent:C", "set-parent:D"]);
  // input untouched
  assert.strictEqual(pending[0].studioId, "D");
  // applying in order never creates a cycle for the server
  const cur = new Map(orig);
  for (const c of ordered) {
    const np = c.type === "remove-parent" ? null : c.parentId;
    assert.strictEqual(np !== null && x.wouldCreateCycle(c.studioId, np, cur), false, "cycle at " + c.studioId);
    cur.set(c.studioId, np);
  }
});

test("orderForSave: same depth keeps insertion order", () => {
  const pending = [change("set-parent", "2", "1"), change("set-parent", "3", "1")];
  const eff = x.effectiveParentMap(mapOf({ 1: null, 2: null, 3: null }), pending);
  assert.deepStrictEqual(plain(x.orderForSave(pending, eff).map((c) => c.studioId)), ["2", "3"]);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message)); }
  }
  if (failed) process.exitCode = 1;
})();
