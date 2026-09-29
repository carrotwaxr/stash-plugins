/**
 * Result cards: partial dates, sort parity with Python's result_sort_key, safe new windows.
 * Run with: node plugins/sceneMatcher/tests/test_cards.js
 */
const assert = require("assert");
const { loadSceneMatcher } = require("./harness");

const tests = [];
const test = (n, f) => tests.push([n, f]);

function load() {
  const sm = loadSceneMatcher({ pathname: "/scenes", search: "?disp=3" });
  sm.x = sm.window.__SCENE_MATCHER_TEST__.exports;
  return sm;
}

const scene = (over) => Object.assign({
  stash_id: "s1", title: "T", score: 5, duration_score: 0.5, in_local_stash: false,
  release_date: "2024-01-01", performers: [],
}, over);

test("formatDate handles year, year-month and full dates", () => {
  const { x } = load();
  assert.strictEqual(x.formatDate("2024"), "2024");
  const may = x.formatDate("2024-05");
  assert.ok(/May/.test(may) && /2024/.test(may) && !/\b\d{1,2},/.test(may), may);
  const full = x.formatDate("2024-05-03");
  assert.ok(/May/.test(full) && /3/.test(full) && /2024/.test(full), full);
});

test("formatDate never says Invalid Date", () => {
  const { x } = load();
  for (const bad of ["20x4", "2024-13", "2024-02-30", "garbage", "2024-05-", "0000-00-00"]) {
    const out = x.formatDate(bad);
    assert.ok(!/Invalid/.test(out), bad + " -> " + out);
    assert.ok(out === "" || out === bad, bad + " -> " + out);
  }
  assert.strictEqual(x.formatDate(null), "");
  assert.strictEqual(x.formatDate(""), "");
});

test("formatDate does not drift with the time zone", () => {
  const { x } = load();
  assert.ok(/1/.test(x.formatDate("2024-01-01")) && /2024/.test(x.formatDate("2024-01-01")));
  assert.ok(/31/.test(x.formatDate("2023-12-31")) && /2023/.test(x.formatDate("2023-12-31")));
});

const order = (x, dates) => Array.from(x.mergeResults([], dates.map((d, i) => scene({
  stash_id: "id" + i, release_date: d,
}))), (r) => r.release_date);

// Same fixtures and expected order as TestResultSorting in test_unit.py
test("mergeResults: partial dates sort as end of period", () => {
  const { x } = load();
  assert.deepStrictEqual(
    order(x, ["", "2023-12-31", "2024-05-03", "2024", "2024-05", "2024-12-31"]),
    ["2024-12-31", "2024", "2024-05", "2024-05-03", "2023-12-31", ""]);
});

test("mergeResults: partial date never below an older period", () => {
  const { x } = load();
  assert.deepStrictEqual(order(x, ["2023-12-31", "2024"]), ["2024", "2023-12-31"]);
  assert.deepStrictEqual(order(x, ["", "2024-05", "2023-12-31"]), ["2024-05", "2023-12-31", ""]);
});

test("mergeResults: malformed date sorts as no date, input order kept", () => {
  const { x } = load();
  assert.deepStrictEqual(order(x, ["20x4", "2023-12-31", "2024-02-30"]),
    ["2023-12-31", "20x4", "2024-02-30"]);
  assert.deepStrictEqual(order(x, [null, "2024-01-01", ""]), ["2024-01-01", null, ""]);
});

test("mergeResults: in-Stash last, score, then duration score, then date", () => {
  const { x } = load();
  const ids = (rs) => Array.from(rs, (r) => r.stash_id);
  const rs = [
    scene({ stash_id: "stashed", in_local_stash: true, score: 9 }),
    scene({ stash_id: "low", score: 2 }),
    scene({ stash_id: "farDur", score: 5, duration_score: 0.3, release_date: "2025-01-01" }),
    scene({ stash_id: "nearDur", score: 5, duration_score: 1.0, release_date: "2020-01-01" }),
    scene({ stash_id: "zeroDur", score: 5, duration_score: 0, release_date: "2026-01-01" }),
  ];
  assert.deepStrictEqual(ids(x.mergeResults(rs.slice(0, 2), rs.slice(2))),
    ["nearDur", "farDur", "zeroDur", "low", "stashed"]);
});

test("mergeResults skips duplicates already present", () => {
  const { x } = load();
  const out = x.mergeResults([scene({ stash_id: "a", title: "first" })], [scene({ stash_id: "a", title: "second" })]);
  assert.strictEqual(out.length, 1);
  assert.strictEqual(out[0].title, "first");
});

test("card click opens the stash-box page with noopener,noreferrer, from stashdbUrl", () => {
  const sm = load();
  sm.window.__SCENE_MATCHER_TEST__.setState({ stashdbUrl: "https://fansdb.example" });
  const calls = [];
  sm.window.open = (...a) => { calls.push(a); };
  const card = sm.x.createSceneCard(scene({ stash_id: "abc" }));
  card.onclick();
  assert.deepStrictEqual(calls, [["https://fansdb.example/scenes/abc", "_blank", "noopener,noreferrer"]]);
});

test("match description includes Date when matches_date", () => {
  const { x } = load();
  const card = x.createSceneCard(scene({ matches_date: true, matches_title: true, matches_studio: true, matching_performers: 2 }));
  const badges = card.children[0].children.find((c) => c.className === "sm-badges");
  const text = badges.children.map((c) => c.textContent).join("|");
  assert.ok(/Title/.test(text) && /Studio/.test(text) && /Date/.test(text) && /2 Performers/.test(text), text);
  const plain = x.createSceneCard(scene({ matches_studio: true }));
  const t2 = plain.children[0].children.find((c) => c.className === "sm-badges").children.map((c) => c.textContent).join("|");
  assert.ok(!/Date/.test(t2), t2);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n  " + (e && e.stack || e)); }
  }
  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log(`${tests.length} passed`);
})();
