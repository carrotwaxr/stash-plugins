/**
 * Per-source status chips, client timeout, and superseded-search handling.
 * Run with: node plugins/performerImageSearch/tests/test_status.js
 */
const assert = require("assert");
const { loadPlugin, createElement } = require("./harness");

let failures = 0;
async function test(name, fn) {
  try { await fn(); console.log("ok   - " + name); }
  catch (e) { failures++; console.log("FAIL - " + name + "\n" + (e.stack || e)); }
}

const img = (n) => ({ image: `http://x/${n}.jpg`, thumbnail: `http://x/t${n}.jpg`, title: "t", source: "s" });
const same = (a, b) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b));
const wrap = (output) => ({ data: { runPluginOperation: output } });

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

// Sets up a loaded plugin with a fake open modal; `byCall` maps source -> array of
// responses, one per call (a value or a promise).
function setup(bySource, sources) {
  const calls = {};
  const p = loadPlugin({
    fetchResponses: {
      RunPluginOperation: (body) => {
        const s = body.variables.args.source;
        calls[s] = (calls[s] || 0) + 1;
        const r = bySource[s];
        const v = Array.isArray(r) ? r[calls[s] - 1] : r;
        return v && typeof v.then === "function" ? v.then(wrap) : wrap(v);
      },
    },
  });
  const els = p.document.elements;
  els["pis-search-query"] = { value: "jane", addEventListener() {}, focus() {} };
  els["pis-status"] = { textContent: "", className: "" };
  els["pis-results"] = createElement("div");
  els["pis-source-chips"] = { innerHTML: "" };
  p.setState({ SOURCES: sources, currentPerformerName: "Jane", currentPerformerId: "1" });
  return p;
}

(async () => {
  await test("chips show each source's status and keep partial results", async () => {
    const p = setup({
      a: { results: [img(1), img(2)], status: "ok" },
      b: { results: [], status: "empty" },
      c: { results: [img(3)], status: "partial", warnings: ["page 2 <failed>"] },
      d: { results: [], status: "error", error: "boom \"x\"" },
      e: { results: [], status: "blocked", error: "HTTP 403" },
      f: { results: [img(4)], status: "error", error: "died late" },
    }, ["a", "b", "c", "d", "e", "f"]);
    const run = p.window.pisSearch();
    assert.ok(/pending/.test(p.document.elements["pis-source-chips"].innerHTML), "pending chips first");
    await run;
    const html = p.document.elements["pis-source-chips"].innerHTML;
    assert.ok(/a: ok \(2\)/.test(html), html);
    assert.ok(/b: empty/.test(html));
    assert.ok(/c: partial \(1\)/.test(html));
    assert.ok(/d: error/.test(html));
    assert.ok(/e: blocked/.test(html));
    assert.ok(/page 2 &lt;failed&gt;/.test(html), "warning escaped in title");
    assert.ok(/boom &quot;x&quot;/.test(html), "error escaped in title");
    assert.ok(!/<failed>/.test(html));
    assert.strictEqual(p.getState().allResults.length, 4, "results kept alongside error");
  });

  await test("status line says All sources finished when nothing is pending", async () => {
    const p = setup({ a: { results: [img(1)], status: "ok" } }, ["a"]);
    await p.window.pisSearch();
    // images never load (loaded < total), yet sources are done
    assert.ok(/All sources finished/.test(p.document.elements["pis-status"].textContent),
      p.document.elements["pis-status"].textContent);
  });

  await test("a plugin call aborts after 45s and marks the source timeout", async () => {
    const hang = new Promise(() => {});
    const p = setup({ a: hang, b: { results: [img(1)], status: "ok" } }, ["a", "b"]);
    const run = p.window.pisSearch();
    await p.settle();
    assert.ok(p.pendingTimers().some((t) => t.ms === 45000), "45s timer queued");
    const call = p.fetchCalls.find((c) => c.source === "a");
    assert.ok(call.opts.signal, "signal passed to fetch");
    p.flushTimers();
    await run;
    assert.ok(call.opts.signal.aborted);
    assert.ok(/a: timeout/.test(p.document.elements["pis-source-chips"].innerHTML));
    assert.ok(/b: ok \(1\)/.test(p.document.elements["pis-source-chips"].innerHTML));
    same(p.getState().pendingSources, []);
    assert.strictEqual(p.getState().isLoading, false);
  });

  await test("a new search ignores responses from the old one", async () => {
    const old = deferred();
    const p = setup({ a: [old.promise, { results: [img(9)], status: "ok" }] }, ["a"]);
    const first = p.window.pisSearch();
    await p.settle();
    const second = p.window.pisSearch();
    await second;
    const before = p.getState();
    same(before.allResults.map((r) => r.image), ["http://x/9.jpg"]);
    old.resolve({ results: [img(1), img(2)], status: "ok" });
    await first;
    await p.settle();
    const after = p.getState();
    same(after.allResults.map((r) => r.image), ["http://x/9.jpg"]);
    same(after.pendingSources, []);
    assert.strictEqual(after.isLoading, false);
  });

  await test("old responses do not touch a search that is still running", async () => {
    const old = deferred();
    const cur = deferred();
    const p = setup({ a: [old.promise, cur.promise] }, ["a"]);
    const first = p.window.pisSearch();
    await p.settle();
    const second = p.window.pisSearch();
    await p.settle();
    old.resolve({ results: [img(1)], status: "ok" });
    await first;
    await p.settle();
    const s = p.getState();
    assert.strictEqual(s.allResults.length, 0);
    same(s.pendingSources, ["a"]);
    assert.strictEqual(s.isLoading, true);
    cur.resolve({ results: [img(2)], status: "ok" });
    await second;
    assert.strictEqual(p.getState().allResults.length, 1);
  });

  await test("opening another performer supersedes pending requests", async () => {
    const old = deferred();
    const p = setup({ a: old.promise }, ["a"]);
    const first = p.window.pisSearch();
    await p.settle();
    p.exports.showModal("2", "Other");
    old.resolve({ results: [img(1)], status: "ok" });
    await first;
    await p.settle();
    const s = p.getState();
    assert.strictEqual(s.allResults.length, 0);
    assert.strictEqual(s.currentPerformerId, "2");
  });

  await test("a modal closed during the settings fetch does not come back", async () => {
    const cfg = deferred();
    const p = loadPlugin({ fetchResponses: { Configuration: () => cfg.promise } });
    p.exports.showModal("1", "Jane");
    await p.settle();
    p.exports.hideModal();
    cfg.resolve({ data: { configuration: { plugins: {} } } });
    await p.settle();
    assert.strictEqual(p.getState().modalRoot.innerHTML, "");
    assert.strictEqual(p.getState().currentPerformerId, null);
  });

  await test("Escape closes the modal (no preview open); hideModal removes the listener", async () => {
    const p = loadPlugin({ fetchResponses: { Configuration: { data: { configuration: { plugins: {} } } } } });
    p.exports.showModal("1", "Jane");
    await p.settle();
    const l = p.documentListeners.concat(p.windowListeners).find((x) => x.type === "keydown");
    assert.ok(l, "keydown listener registered");
    l.fn({ key: "Escape" });
    assert.strictEqual(p.getState().currentPerformerId, null);
    assert.strictEqual(p.getState().modalRoot.innerHTML, "");
    const removed = p.documentRemoved.concat(p.windowRemoved).find((x) => x.type === "keydown" && x.fn === l.fn);
    assert.ok(removed, "listener removed");
  });

  await test("the chips sit in the footer, below the results, where the README says they are", async () => {
    const p = loadPlugin({ fetchResponses: { Configuration: { data: { configuration: { plugins: {} } } } } });
    p.exports.showModal("1", "Jane");
    await p.settle();
    const html = p.getState().modalRoot.innerHTML;
    const results = html.indexOf('id="pis-results"');
    const footer = html.indexOf('class="pis-modal-footer"');
    const chips = html.indexOf('id="pis-source-chips"');
    assert.ok(results >= 0 && results < footer && footer < chips, "chips are in the footer, after the results");
    const readme = require("fs").readFileSync(require("path").join(__dirname, "..", "README.md"), "utf8");
    const section = readme.split("## Source status chips")[1].split("\n## ")[0];
    assert.ok(/below the results/.test(section), "README says where the chips are");
    assert.ok(!/above the results/.test(readme));
  });

  process.exit(failures ? 1 : 0);
})();
