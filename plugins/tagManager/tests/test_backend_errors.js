/**
 * Backend error formatting, tag loading without fuzzy search, the
 * once-per-visit hint when a search reports fuzzy matching unavailable, and
 * failed searches shown as failures (not as "no matches").
 * Run with: node plugins/tagManager/tests/test_backend_errors.js
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { loadTagManager, createElement, createQueryableElement } = require("./harness");

const RPO = (out) => ({ data: { runPluginOperation: out } });
const BOX = { endpoint: "https://stashdb.org/graphql", name: "StashDB" };

/** Record every showStatus text (the harness has no #tm-status element). */
function recordStatuses(tm) {
  const statuses = [];
  const el = { className: "" };
  Object.defineProperty(el, "textContent", {
    get: () => statuses[statuses.length - 1] || "",
    set: (v) => { statuses.push(String(v)); },
  });
  tm.document.getElementById = (id) => (id === "tm-status" ? el : null);
  return statuses;
}

/** A Match-tab page with `tags` unlinked local tags. */
function matchPage(tm, tags, settingsPatch = {}) {
  tm.setState({
    settings: { ...tm.getState().settings, pageSize: 25, enableFuzzySearch: true, ...settingsPatch },
    selectedStashBox: { ...BOX }, stashBoxes: [{ ...BOX }], localTags: tags, matchResults: {},
  });
}
const TAGS3 = [{ id: "1", name: "One" }, { id: "2", name: "Two" }, { id: "3", name: "Three" }];
const HINT = /Fuzzy matching is unavailable until the stash-box tags are cached\. Use Refresh Cache\./;

(async () => {
  // formatBackendError
  {
    const tm = loadTagManager();
    await tm.settle();
    const f = tm.exports.formatBackendError;
    const auth = f({ error: "HTTP 403", auth_error: true });
    assert.ok(/API key/.test(auth), auth);
    assert.ok(/Metadata Providers/.test(auth), auth);
    assert.ok(/HTTP 403/.test(auth), auth);
    assert.ok(/StashDB rejected/.test(f({ error: "HTTP 401", auth_error: true }, "StashDB")));
    assert.strictEqual(f({ error: "x" }), "x");
    assert.strictEqual(f({ error: "x", auth_error: false }), "x");
    assert.strictEqual(f(null), "Unknown error");
    assert.strictEqual(f("str"), "Unknown error");
    assert.strictEqual(f({}), "Unknown error");
  }

  // callBackend error carries the output
  {
    const tm = loadTagManager({
      fetchResponses: { RunPluginOperation: RPO({ error: "HTTP 401", auth_error: true }) },
    });
    await tm.settle();
    tm.setState({ selectedStashBox: { endpoint: "https://stashdb.org/graphql", name: "StashDB" } });
    let err;
    try { await tm.exports.callBackend("fetch_all", {}); } catch (e) { err = e; }
    assert.ok(err, "should throw");
    assert.strictEqual(err.output.auth_error, true);
  }

  // loadTagsFromCache works with fuzzy search disabled
  {
    const tm = loadTagManager({
      fetchResponses: {
        RunPluginOperation: RPO({ tags: [{ id: "a", name: "A" }], count: 1, from_cache: true }),
      },
    });
    await tm.settle();
    tm.setState({
      settings: { ...tm.getState().settings, enableFuzzySearch: false },
      selectedStashBox: { endpoint: "https://stashdb.org/graphql", name: "StashDB" },
    });
    await tm.exports.loadTagsFromCache(null);
    assert.strictEqual(tm.getState().stashdbTags.length, 1);
  }

  // loadTagsFromCache surfaces a formatted auth error
  {
    const tm = loadTagManager({
      fetchResponses: { RunPluginOperation: RPO({ error: "HTTP 403", auth_error: true }) },
    });
    await tm.settle();
    tm.setState({ selectedStashBox: { endpoint: "https://stashdb.org/graphql", name: "StashDB" } });
    await tm.exports.loadTagsFromCache(null);
    assert.strictEqual(tm.getState().stashdbTags, null);
    assert.ok(/Metadata Providers/.test(tm.getState().cacheStatus.error));
  }

  // fuzzy_unavailable: one status hint per page visit, not one per tag
  {
    const tm = loadTagManager({
      fetchResponses: { RunPluginOperation: RPO({ matches: [], fuzzy_unavailable: true }) },
    });
    await tm.settle();
    const statuses = recordStatuses(tm);
    matchPage(tm, TAGS3);
    const container = { innerHTML: "", querySelector: () => null, querySelectorAll: () => [] };
    await tm.exports.searchAllOnPage(container);
    assert.strictEqual(statuses.filter((s) => HINT.test(s)).length, 1, JSON.stringify(statuses));
    await tm.exports.searchAllOnPage(container);
    await tm.exports.searchSingleTag("2", container);
    assert.strictEqual(statuses.filter((s) => HINT.test(s)).length, 1, "shown once: " + JSON.stringify(statuses));
  }
  {
    // not when fuzzy search is off, nor when the result lacks the flag
    for (const [out, patch] of [
      [{ matches: [], fuzzy_unavailable: true }, { enableFuzzySearch: false }],
      [{ matches: [] }, {}],
    ]) {
      const tm = loadTagManager({ fetchResponses: { RunPluginOperation: RPO(out) } });
      await tm.settle();
      const statuses = recordStatuses(tm);
      matchPage(tm, TAGS3, patch);
      const container = { innerHTML: "", querySelector: () => null, querySelectorAll: () => [] };
      await tm.exports.searchAllOnPage(container);
      await tm.exports.searchSingleTag("1", container);
      assert.ok(!statuses.some((s) => HINT.test(s)), JSON.stringify([out, patch, statuses]));
    }
  }

  // A failed search shows as a failure (with a retry), not as "No matches found"
  {
    const failing = new Set(["Two"]);
    const tm = loadTagManager({
      fetchResponses: {
        RunPluginOperation: (body) => {
          const a = body.variables.args;
          if (a.mode === "get_cache_status") return RPO({ exists: false });
          if (failing.has(a.tag_name)) return RPO({ error: "HTTP 502" });
          return RPO({ matches: [{ tag: { id: "sb-" + a.tag_name, name: a.tag_name }, match_type: "exact", score: 100 }] });
        },
      },
    });
    await tm.settle();
    const statuses = recordStatuses(tm);
    matchPage(tm, TAGS3);
    const container = { innerHTML: "", querySelector: () => null, querySelectorAll: () => [] };
    await tm.exports.searchAllOnPage(container);
    let st = tm.getState();
    assert.deepStrictEqual(Object.keys(st.matchResults).sort(), ["1", "3"], JSON.stringify(st.matchResults));
    assert.strictEqual(JSON.stringify(st.matchErrors), JSON.stringify({ "2": "HTTP 502" }));
    assert.ok(statuses.some((s) => /HTTP 502/.test(s)), JSON.stringify(statuses));
    let row = tm.exports.renderTagRow(TAGS3[1]);
    assert.ok(/Search failed: HTTP 502/.test(row), row);
    assert.ok(!/No matches/.test(row), row);
    assert.ok(/class="[^"]*\btm-search\b[^"]*" data-tag-id="2"/.test(row), "retry uses the Find Match handler: " + row);
    assert.ok(/tm-match-name">One</.test(tm.exports.renderTagRow(TAGS3[0])));

    // the retry succeeds: error cleared, results shown
    failing.clear();
    await tm.exports.searchSingleTag("2", container);
    st = tm.getState();
    assert.ok(!("2" in st.matchErrors), JSON.stringify(st.matchErrors));
    assert.strictEqual(st.matchResults["2"].length, 1);
    assert.ok(!/Search failed/.test(tm.exports.renderTagRow(TAGS3[1])));

    // a failed single-tag search is recorded the same way (old results dropped)
    failing.add("One");
    await tm.exports.searchSingleTag("1", container);
    st = tm.getState();
    assert.ok(!("1" in st.matchResults), JSON.stringify(st.matchResults));
    assert.strictEqual(st.matchErrors["1"], "HTTP 502");
    assert.ok(/Search failed: HTTP 502/.test(tm.exports.renderTagRow(TAGS3[0])));

    // a page search that succeeds for it clears the error as well
    failing.clear();
    await tm.exports.searchAllOnPage(container);
    assert.strictEqual(JSON.stringify(tm.getState().matchErrors), "{}");

    // switching stash-box drops the errors along with the results
    tm.setState({ matchErrors: { "3": "HTTP 502" }, stashBoxes: [{ ...BOX }, { endpoint: "https://tpdb/graphql", name: "TPDB" }] });
    const select = createElement("select");
    const page = createQueryableElement("div", { query: (sel) => (sel === "#tm-stashbox" ? select : undefined) });
    tm.exports.renderPage(page);
    await select.listeners.change[0]({ target: { value: "https://tpdb/graphql" } });
    assert.strictEqual(JSON.stringify(tm.getState().matchErrors), "{}");
  }

  // Init path (React component, not reachable via harness): source-level guards
  {
    const src = fs.readFileSync(path.join(__dirname, "..", "tag-manager.js"), "utf8");
    const init = src.slice(src.indexOf("// Load cache status for selected endpoint"));
    const callIdx = init.indexOf("await loadTagsFromCache(containerRef.current)");
    assert.ok(callIdx > -1, "init calls loadTagsFromCache");
    assert.ok(!/if \(settings\.enableFuzzySearch\)[^]{0,40}$/.test(init.slice(0, callIdx)),
      "init must not gate loadTagsFromCache on enableFuzzySearch");
    assert.ok(!/loadStashdbTags/.test(src));
  }

  console.log("test_backend_errors: all passed");
})().catch((e) => { console.error(e); process.exit(1); });
