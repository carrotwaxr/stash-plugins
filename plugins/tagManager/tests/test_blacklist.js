/**
 * Blacklist parser tests against the REAL tag-manager.js, driven by the same
 * cases the Python parser (blacklist.py) runs: tests/blacklist_cases.json.
 * Run with: node plugins/tagManager/tests/test_blacklist.js
 */
const { loadTagManager } = require("./harness");
const cases = require("./blacklist_cases.json");

const tm = loadTagManager();
const { parseBlacklist, isBlacklisted } = tm.exports;

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}

for (const c of cases) {
  tm.setState({ tagBlacklist: parseBlacklist(c.input) });
  for (const [name, expected] of Object.entries(c.matches)) {
    const got = isBlacklisted(name);
    check(`${JSON.stringify(c.input)} vs ${JSON.stringify(name)} -> ${expected}`,
      got === expected, `got ${got}`);
  }
}

// Shape kept for existing callers
const p = parseBlacklist("Foo\n/^x/i");
check("shape: literal", p[0].type === "literal" && p[0].pattern === "foo");
check("shape: regex", p[1].type === "regex" && p[1].pattern === "^x" && p[1].regex.test("X"));
check("empty input", parseBlacklist("").length === 0 && parseBlacklist(null).length === 0);
check("empty regex body skipped", parseBlacklist("//").length === 0);
check("invalid regex skipped", parseBlacklist("/(/").length === 0);

(async () => {
  // saveBlacklist: writes through configurePlugin, then reloads tagBlacklist
  let store = {};
  const tm2 = loadTagManager({
    fetchResponses: {
      Configuration: () => ({ data: { configuration: { plugins: { tagManager: { ...store } } } } }),
      ConfigurePlugin: (body) => { store = { ...body.variables.input }; return { data: { configurePlugin: store } }; },
    },
  });
  const ok = await tm2.exports.saveBlacklist("Foo; /^x/i");
  check("saveBlacklist ok", ok === true);
  check("saveBlacklist persisted raw text", store.tagBlacklist === "Foo; /^x/i", JSON.stringify(store));
  const st = tm2.getState();
  check("saveBlacklist reloaded parsed list", st.tagBlacklist.length === 2);
  finish();
})();

function finish() {
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log("all blacklist tests passed");
}
