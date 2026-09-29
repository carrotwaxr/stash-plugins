/**
 * Filters on real dimensions, stable grid, preview and keyboard behaviour, defaults.
 * Run with: node plugins/performerImageSearch/tests/test_grid.js
 */
const assert = require("assert");
const { loadPlugin, createElement } = require("./harness");

let failures = 0;
async function test(name, fn) {
  try { await fn(); console.log("ok   - " + name); }
  catch (e) { failures++; console.log("FAIL - " + name + "\n" + (e.stack || e)); }
}

const res = (n, extra = {}) => ({ image: `http://x/${n}.jpg`, thumbnail: `http://x/t${n}.jpg`, title: "t" + n, source: "s", ...extra });
const cfg = (plugins) => ({ data: { configuration: { plugins } } });

function setup(layout = "All") {
  const p = loadPlugin({ fetchResponses: { Configuration: cfg({}) } });
  const els = p.document.elements;
  els["pis-results"] = createElement("div");
  els["pis-layout"] = { value: layout };
  els["pis-status"] = { textContent: "", className: "" };
  els["pis-source-chips"] = { innerHTML: "" };
  els["pis-preview-overlay"] = { style: { display: "none" } };
  els["pis-preview-image"] = createElement("img");
  els["pis-preview-dims"] = { textContent: "" };
  els["pis-confirm-btn"] = { disabled: false, textContent: "Set", classList: { add() {}, remove() {} } };
  p.setState({ SOURCES: ["a", "b"], currentPerformerName: "Jane", currentPerformerId: "1" });
  return p;
}
const urls = (list) => list.map((r) => r.image);
const imgNodes = (p) => p.createdElements.filter((e) => e.tagName === "IMG" && e.src && e.src.includes("/t"));

(async () => {
  await test("aspect thresholds: portrait < 0.9, square 0.9..1.1 inclusive, landscape > 1.1", () => {
    const p = setup();
    const all = [
      res(1, { width: 899, height: 1000 }), // 0.899
      res(2, { width: 900, height: 1000 }), // 0.9
      res(3, { width: 1100, height: 1000 }), // 1.1
      res(4, { width: 1101, height: 1000 }), // 1.101
    ];
    p.setState({ allResults: all });
    const run = (layout) => { p.document.elements["pis-layout"].value = layout; p.exports.applyFilters(); return urls(p.getState().filteredResults).map((u) => u.match(/(\d)\.jpg/)[1]).join(""); };
    assert.strictEqual(run("Portrait"), "1");
    assert.strictEqual(run("Square"), "23");
    assert.strictEqual(run("Landscape"), "4");
    assert.strictEqual(run("All"), "1234");
  });

  await test("dimensions: backend, then loaded full image, then thumbnail; unknown passes", () => {
    const p = setup("Landscape");
    const backend = res(1, { width: 500, height: 1000 }); // portrait per backend
    const thumbOnly = res(2);
    const fullWins = res(3);
    const broken = res(4);
    const unloaded = res(5);
    p.setState({ allResults: [backend, thumbOnly, fullWins, broken, unloaded] });
    p.exports.renderResults();
    // thumbnail of backend result says landscape: backend still wins
    p.window.pisImageLoaded({ naturalWidth: 2000, naturalHeight: 1000 }, backend.image);
    p.window.pisImageLoaded({ naturalWidth: 100, naturalHeight: 200 }, thumbOnly.image); // portrait
    p.window.pisImageLoaded({ naturalWidth: 100, naturalHeight: 200 }, fullWins.image); // thumb: portrait
    p.window.pisImageErrored(broken.image);
    let f = urls(p.getState().filteredResults);
    assert.deepStrictEqual(f, [broken.image, unloaded.image], "only unknown ones pass Landscape");
    // full image of fullWins turns out landscape; it overrides the thumbnail
    p.window.pisShowPreview(2);
    const img = p.document.elements["pis-preview-image"];
    img.naturalWidth = 3000; img.naturalHeight = 1000;
    img.onload();
    f = urls(p.getState().filteredResults);
    assert.deepStrictEqual(f, [fullWins.image, broken.image, unloaded.image]);
    // a later thumbnail load does not override the full size
    p.window.pisImageLoaded({ naturalWidth: 100, naturalHeight: 200 }, fullWins.image);
    assert.ok(urls(p.getState().filteredResults).includes(fullWins.image));
    assert.strictEqual(Object.keys(p.getState().imageDimensions).length, 4, "onerror counts as loaded; keyed by URL");
  });

  await test("grid nodes are kept across filter changes and new sources", () => {
    const p = setup();
    p.setState({ isLoading: true });
    p.exports.addResultsFromSource([res(1, { width: 1000, height: 500 }), res(2, { width: 500, height: 1000 })], "a");
    p.exports.applyFilters(); p.exports.renderResults();
    const first = imgNodes(p).slice();
    assert.strictEqual(first.length, 2);
    p.document.elements["pis-layout"].value = "Portrait";
    p.exports.applyFilters(); p.exports.renderResults();
    const items = p.document.elements["pis-results"].children.filter((c) => c.className === "pis-result-item");
    assert.deepStrictEqual(items.map((i) => i.style.display), ["none", ""]);
    p.exports.addResultsFromSource([res(3, { width: 400, height: 800 })], "b");
    p.exports.applyFilters(); p.exports.renderResults();
    const now = imgNodes(p);
    assert.strictEqual(now.length, 3, "only the new result got a node");
    assert.ok(first.every((n, i) => now[i] === n), "existing img nodes are the same objects");
    p.document.elements["pis-layout"].value = "All";
    p.exports.applyFilters(); p.exports.renderResults();
    assert.strictEqual(imgNodes(p).length, 3);
    assert.ok(p.document.elements["pis-results"].children.filter((c) => c.className === "pis-result-item").every((i) => i.style.display === ""));
  });

  function previewSetup(layout = "All") {
    const p = setup(layout);
    const all = [0, 1, 2, 3, 4].map((n) => res(n, { width: n % 2 ? 500 : 1000, height: n % 2 ? 1000 : 500 }));
    p.setState({ allResults: all });
    p.document.elements["pis-layout"].value = layout;
    p.exports.applyFilters();
    p.exports.renderResults();
    return { p, all, img: p.document.elements["pis-preview-image"], dims: p.document.elements["pis-preview-dims"], btn: p.document.elements["pis-confirm-btn"] };
  }

  await test("preview navigation recomputes the index and survives filter changes", () => {
    const { p, all, img } = previewSetup();
    p.window.pisShowPreview(2);
    assert.strictEqual(p.getState().previewResult, all[2]);
    p.window.pisNextPreview();
    assert.strictEqual(img.src, all[3].image);
    // filter to Landscape (0, 2, 4): result 3 is filtered out
    p.document.elements["pis-layout"].value = "Landscape";
    p.exports.applyFilters(); p.exports.renderResults();
    p.window.pisNextPreview();
    assert.strictEqual(img.src, all[4].image, "nearest visible after");
    p.window.pisShowPreview(3);
    p.window.pisPrevPreview();
    assert.strictEqual(img.src, all[2].image, "nearest visible before");
    p.window.pisNextPreview();
    p.window.pisNextPreview();
    assert.strictEqual(img.src, all[4].image, "stays at the end");
  });

  await test("full image failure falls back to the thumbnail once; both failing disables Confirm", () => {
    const { p, all, img, dims, btn } = previewSetup();
    p.window.pisShowPreview(1);
    img.onerror();
    assert.strictEqual(img.src, all[1].thumbnail);
    assert.strictEqual(dims.textContent, "Full-size image unavailable; showing the thumbnail");
    assert.strictEqual(p.getState().previewImage, all[1].thumbnail);
    assert.strictEqual(btn.disabled, false);
    img.onerror(); // the thumbnail fails too: no loop
    assert.strictEqual(img.src, all[1].thumbnail);
    assert.strictEqual(dims.textContent, "Image unavailable");
    assert.strictEqual(btn.disabled, true);
    p.window.pisShowPreview(2);
    assert.strictEqual(btn.disabled, false, "next preview re-enables Confirm");
  });

  await test("keys: window capture listener swallows them before Mousetrap; removed with the modal", async () => {
    const p = loadPlugin({ fetchResponses: { Configuration: cfg({}) } });
    const els = p.document.elements;
    els["pis-preview-overlay"] = { style: { display: "none" } };
    els["pis-preview-image"] = createElement("img");
    els["pis-preview-dims"] = { textContent: "" };
    p.setState({ allResults: [res(0), res(1)], filteredResults: [res(0), res(1)] });
    p.setState({ filteredResults: p.getState().allResults });
    p.exports.showModal("1", "Jane");
    await p.settle();
    assert.ok(p.windowListeners.some((l) => l.type === "keydown" && l.capture), "window capture listener");
    assert.ok(!p.documentListeners.some((l) => l.type === "keydown"), "no document listener");
    let mousetrap = 0;
    p.document.addEventListener("keydown", () => { mousetrap++; });

    // no preview open: arrows pass through
    let e = p.dispatchEvent("keydown", { key: "ArrowRight" });
    assert.strictEqual(mousetrap, 1);
    assert.strictEqual(e.defaultPrevented, false);

    // preview open: arrows handled and swallowed
    p.setState({ allResults: [res(0), res(1)] });
    const all = p.getState().allResults;
    p.setState({ filteredResults: all });
    p.window.pisShowPreview(0);
    e = p.dispatchEvent("keydown", { key: "ArrowRight" });
    assert.strictEqual(mousetrap, 1, "Mousetrap did not see it");
    assert.ok(e.defaultPrevented);
    assert.strictEqual(els["pis-preview-image"].src, all[1].image);

    // typing in the query input: arrows untouched, Escape still works
    e = p.dispatchEvent("keydown", { key: "ArrowLeft", target: { tagName: "INPUT" } });
    assert.strictEqual(mousetrap, 2);
    assert.strictEqual(els["pis-preview-image"].src, all[1].image);
    e = p.dispatchEvent("keydown", { key: "Escape", target: { tagName: "INPUT" } });
    assert.strictEqual(els["pis-preview-overlay"].style.display, "none", "Escape closed the preview");
    assert.strictEqual(p.getState().currentPerformerId, "1", "modal still open");
    assert.strictEqual(mousetrap, 2);

    // Escape with no preview closes the modal and removes the listener
    e = p.dispatchEvent("keydown", { key: "Escape" });
    assert.strictEqual(p.getState().currentPerformerId, null);
    assert.strictEqual(mousetrap, 2);
    assert.ok(p.windowRemoved.some((l) => l.type === "keydown" && l.capture));
    p.dispatchEvent("keydown", { key: "Escape" });
    assert.strictEqual(mousetrap, 3, "listener gone");
  });

  // A plugin whose single source "a" answers the n-th search with outputs[n]
  function searchSetup(outputs, layout = "All") {
    let call = 0;
    const p = loadPlugin({
      fetchResponses: { RunPluginOperation: () => ({ data: { runPluginOperation: outputs[call++] } }) },
    });
    const els = p.document.elements;
    els["pis-search-query"] = { value: "jane" };
    els["pis-results"] = createElement("div");
    els["pis-layout"] = { value: layout };
    els["pis-status"] = { textContent: "", className: "" };
    els["pis-source-chips"] = { innerHTML: "" };
    p.setState({ SOURCES: ["a"], currentPerformerName: "Jane", currentPerformerId: "1" });
    return p;
  }
  const loadImg = (node) => { node.naturalWidth = 100; node.naturalHeight = 100; node.onload(); };
  const refreshTimers = (p) => p.pendingTimers().filter((t) => t.ms === 250);

  await test("loading x/N counts only the current search's thumbnails; a late load from the last search is ignored", async () => {
    const p = searchSetup([
      { results: [res(1), res(2)], status: "ok" },
      { results: [res(3), res(4), res(5)], status: "ok" },
    ]);
    await p.window.pisSearch();
    const old = imgNodes(p).slice();
    assert.strictEqual(old.length, 2);
    await p.window.pisSearch();
    const now = imgNodes(p).filter((n) => !old.includes(n));
    assert.strictEqual(now.length, 3);
    loadImg(old[0]); // the first search's thumbnail finishes late
    now[0].onerror();
    p.flushTimers();
    const status = p.document.elements["pis-status"].textContent;
    assert.ok(/loading 1\/3/.test(status), status);
    assert.ok(!(res(1).image in p.getState().imageDimensions), "stale load not recorded");
  });

  await test("the status line follows thumbnail loads with the Any filter, refreshed at most every 250 ms", async () => {
    const p = searchSetup([{ results: [res(1), res(2), res(3)], status: "ok" }]);
    await p.window.pisSearch();
    const status = () => p.document.elements["pis-status"].textContent;
    assert.ok(/loading 0\/3/.test(status()), status());
    const nodes = imgNodes(p);
    loadImg(nodes[0]);
    loadImg(nodes[1]);
    assert.strictEqual(refreshTimers(p).length, 1, "one refresh for the burst");
    p.flushTimers();
    assert.ok(/loading 2\/3/.test(status()), status());
    nodes[2].onerror();
    assert.strictEqual(refreshTimers(p).length, 1);
    p.flushTimers();
    assert.ok(!/loading/.test(status()) && /3 of 3 images match filters/.test(status()), status());
  });

  await test("defaults: DuckDuckGo off, others on; failure keeps the defaults; layout any case", async () => {
    let p = loadPlugin({ fetchResponses: { Configuration: cfg({}) } });
    let s = await p.exports.getPluginSettings();
    assert.ok(!p.getState().SOURCES.includes("duckduckgo"));
    assert.strictEqual(p.getState().SOURCES.length, 6);
    assert.strictEqual(s.layout, "All");

    p = loadPlugin({ fetchResponses: { Configuration: cfg({ performerImageSearch: { enableDuckDuckGo: true, enableBabepedia: false } }) } });
    await p.exports.getPluginSettings();
    assert.ok(p.getState().SOURCES.includes("duckduckgo"));
    assert.ok(!p.getState().SOURCES.includes("babepedia"));

    p = loadPlugin({ fetchResponses: { Configuration: () => { throw new Error("down"); } } });
    s = await p.exports.getPluginSettings();
    assert.ok(!p.getState().SOURCES.includes("duckduckgo"), "failure does not turn everything on");
    assert.strictEqual(p.getState().SOURCES.length, 6);
    assert.strictEqual(s.enableDuckDuckGo, false);

    for (const [v, want] of [["any", "All"], ["ALL", "All"], ["Any", "All"], ["portrait", "Portrait"], ["LANDSCAPE", "Landscape"], ["square", "Square"], ["weird", "All"]]) {
      p = loadPlugin({ fetchResponses: { Configuration: cfg({ performerImageSearch: { defaultLayout: v } }) } });
      assert.strictEqual((await p.exports.getPluginSettings()).layout, want, v);
    }
  });

  process.exit(failures ? 1 : 0);
})();
