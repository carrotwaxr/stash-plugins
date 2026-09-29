/**
 * Blacklist parser tests against the REAL tag-manager.js, driven by the same
 * cases the Python parser (blacklist.py) runs: tests/blacklist_cases.json.
 * Run with: node plugins/tagManager/tests/test_blacklist.js
 */
const { loadTagManager, createElement, createQueryableElement } = require("./harness");
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

  // Editor draft: unsaved text survives a re-render (the focus/visibility refresh
  // calls renderPage) and is cleared by a successful Save.
  {
    let saved = { tagBlacklist: "Saved" };
    let failWrite = false;
    const tm3 = loadTagManager({
      fetchResponses: {
        Configuration: () => ({ data: { configuration: { plugins: { tagManager: { ...saved } } } } }),
        ConfigurePlugin: (body) => {
          if (failWrite) return { errors: [{ message: "refused" }] };
          saved = { ...body.variables.input };
          return { data: { configurePlugin: saved } };
        },
      },
    });
    await tm3.settle();
    const box = { endpoint: "https://stashdb.org/graphql", name: "StashDB" };
    tm3.setState({
      settings: { ...tm3.getState().settings, pageSize: 25 },
      stashBoxes: [box], selectedStashBox: box, localTags: [],
      blacklistPanelOpen: true, tagBlacklistRaw: "Saved",
    });
    const textarea = createElement("textarea");
    const saveBtn = createElement("button");
    const container = createQueryableElement("div", {
      query: (sel) => (sel === "#tm-blacklist-text" ? textarea : sel === "#tm-blacklist-save" ? saveBtn : undefined),
    });
    const editorText = () => {
      const m = container.innerHTML.match(/<textarea id="tm-blacklist-text"[^>]*>([^]*?)<\/textarea>/);
      return m ? m[1] : null;
    };
    const type = (value) => {
      textarea.value = value;
      (textarea.listeners.input || []).forEach((fn) => fn({ target: textarea }));
    };

    tm3.exports.renderPage(container);
    check("editor shows the saved text", editorText() === "Saved", editorText());
    type("Saved\nDraft");
    check("typing records a draft", tm3.getState().blacklistDraft === "Saved\nDraft", JSON.stringify(tm3.getState().blacklistDraft));
    tm3.exports.renderPage(container);
    check("a re-render keeps the unsaved text", editorText() === "Saved\nDraft", editorText());
    check("panel stays open across the re-render", tm3.getState().blacklistPanelOpen === true);

    // Focus and caret are kept when the editor had focus during the re-render
    const focused = [];
    const ranges = [];
    textarea.focus = () => focused.push(true);
    textarea.setSelectionRange = (a, b) => ranges.push([a, b]);
    textarea.id = "tm-blacklist-text";
    textarea.selectionStart = 3;
    textarea.selectionEnd = 5;
    tm3.document.activeElement = textarea;
    tm3.exports.renderPage(container);
    check("focused editor is re-focused with its caret after a re-render",
      focused.length === 1 && JSON.stringify(ranges) === "[[3,5]]", JSON.stringify({ focused, ranges }));
    tm3.document.activeElement = null;

    // A failed save keeps the draft
    failWrite = true;
    await saveBtn.listeners.click[0]({ target: saveBtn });
    check("failed save keeps the draft", tm3.getState().blacklistDraft === "Saved\nDraft");
    failWrite = false;
    tm3.exports.renderPage(container);
    await saveBtn.listeners.click[0]({ target: saveBtn });
    check("successful save persisted the draft", saved.tagBlacklist === "Saved\nDraft", JSON.stringify(saved));
    check("successful save clears the draft", tm3.getState().blacklistDraft === null, JSON.stringify(tm3.getState().blacklistDraft));
    check("editor then shows the saved text", editorText() === "Saved\nDraft", editorText());
  }
  finish();
})();

function finish() {
if (failed) { console.log(`${failed} failed`); process.exit(1); }
console.log("all blacklist tests passed");
}
