/**
 * Backend error formatting, tag loading without fuzzy search, and the
 * once-per-visit hint when a search reports fuzzy matching unavailable.
 * Run with: node plugins/tagManager/tests/test_backend_errors.js
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { loadTagManager } = require("./harness");

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
