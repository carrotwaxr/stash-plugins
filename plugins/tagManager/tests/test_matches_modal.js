/**
 * visibleMatches / bestVisibleMatch index mapping (real tag-manager.js).
 * Run with: node plugins/tagManager/tests/test_matches_modal.js
 */
const { loadTagManager } = require("./harness");
const tm = loadTagManager();
const { parseBlacklist, visibleMatches, bestVisibleMatch } = tm.exports;

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
const m = (name) => ({ tag: { name }, score: 1 });
const matches = [m("4K Available"), m("Blonde"), m("Full HD"), m("Redhead")];
tm.setState({ tagBlacklist: parseBlacklist("4K Available\n/^Full/") });

const vis = visibleMatches(matches);
check("blacklisted dropped", vis.length === 2);
check("first visible index is original (1)", vis[0].index === 1 && vis[0].match === matches[1]);
check("second visible index is 3", vis[1].index === 3 && vis[1].match === matches[3]);
const best = bestVisibleMatch(matches);
check("best is first non-blacklisted with original index", best && best.index === 1 && best.match === matches[1]);

tm.setState({ tagBlacklist: parseBlacklist("/./") });
check("all hidden -> best null", bestVisibleMatch(matches) === null);
check("undefined matches", visibleMatches(undefined).length === 0 && bestVisibleMatch(undefined) === null);
tm.setState({ tagBlacklist: [] });
check("no blacklist keeps all", visibleMatches(matches).length === 4 && bestVisibleMatch(matches).index === 0);

if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log("all matches modal tests passed");
