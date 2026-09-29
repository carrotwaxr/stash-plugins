/**
 * Scene card site links and sort control options.
 * Run with: node plugins/missingScenes/tests/test_cards.js
 */
const assert = require("assert");
const { loadMissingScenes } = require("./harness");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function links(el, out = []) {
  if (el.tagName === "A" && el.className.includes("ms-site-link")) out.push(el);
  for (const c of el.children || []) links(c, out);
  return out;
}
const build = (scene) => {
  const ms = loadMissingScenes();
  const card = ms.exports.core.createSceneCard(
    { stash_id: "s1", title: "T", performers: [], tags: [], ...scene },
    { stashdbUrl: "https://stashdb.org", endpoint: "https://stashdb.org/graphql" });
  return { ms, card };
};

test("one link per distinct site, labelled, new tab, noopener", () => {
  const { card } = build({ urls: [
    { url: "https://a.example/1", site: "Alpha" },
    { url: "https://a.example/2", site: "Alpha" },
    { url: "https://b.example/1", site: "Beta" },
  ] });
  const ls = links(card);
  assert.deepStrictEqual(ls.map((l) => l.textContent), ["Alpha", "Beta"]);
  assert.strictEqual(ls[0].href, "https://a.example/1");
  assert.strictEqual(ls[0].target, "_blank");
  assert.strictEqual(ls[0].rel, "noopener noreferrer");
});

test("clicking a site link does not trigger the card click", () => {
  const { card } = build({ urls: [{ url: "https://a.example/1", site: "Alpha" }] });
  let stopped = false;
  links(card)[0].onclick({ stopPropagation() { stopped = true; } });
  assert.ok(stopped);
});

test("only http and https urls become links", () => {
  const { card } = build({ urls: [
    { url: "javascript:alert(1)", site: "Evil" },
    { url: "data:text/html,x", site: "Data" },
    { url: "https://ok.example/", site: "Ok" },
  ] });
  assert.deepStrictEqual(links(card).map((l) => l.textContent), ["Ok"]);
});

test("site names are set as text, not markup", () => {
  const { card } = build({ urls: [{ url: "https://a.example/", site: "<img src=x onerror=1>" }] });
  const l = links(card)[0];
  assert.strictEqual(l.textContent, "<img src=x onerror=1>");
  assert.ok(!l.innerHTML.includes("<img"));
});

test("a card without urls has no site links", () => {
  assert.strictEqual(links(build({}).card).length, 0);
});

test("sort options include Trending; direction labels follow the sort", () => {
  const { ms } = build({});
  const m = ms.exports.modal;
  assert.ok(m.SORT_OPTIONS.some((o) => o.value === "TRENDING" && /Trending/.test(o.label)));
  const labels = (s) => [...m.directionFor(s).options.map((o) => o.label)];
  assert.deepStrictEqual(labels("DATE"), ["Newest First", "Oldest First"]);
  assert.deepStrictEqual(labels("CREATED_AT"), ["Newest First", "Oldest First"]);
  assert.deepStrictEqual(labels("TITLE"), ["Descending", "Ascending"]);
  assert.strictEqual(m.directionFor("TITLE").hidden, false);
  assert.strictEqual(m.directionFor("TRENDING").hidden, true);
  assert.strictEqual(m.directionFor("DATE").hidden, false);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n  " + e.message); }
  }
  if (failed) process.exit(1);
})();
