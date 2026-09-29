/**
 * F10: local tag index, Import All candidates, import cancel/progress, and the
 * per-endpoint selection of "search all on page" -- against the REAL tag-manager.js.
 * Run with: node plugins/tagManager/tests/test_local_index.js
 */
const { loadTagManager, createQueryableElement } = require("./harness");

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
async function section(name, fn) {
  console.log(name);
  try { await fn(); }
  catch (e) { failed++; console.log(`FAIL  ${name} threw: ${(e && e.stack) || e}`); }
}

const EP = "https://stashdb.org/graphql";
const EP2 = "https://theporndb.net/graphql";
const BOX = { endpoint: EP, name: "StashDB" };

async function setup(responses = {}) {
  const tm = loadTagManager({ fetchResponses: responses });
  await tm.settle();
  tm.setState({ selectedStashBox: { ...BOX }, stashBoxes: [{ ...BOX }] });
  return tm;
}

// ---- REFERENCE: the old linear lookups, kept only to compare against the index ----
const refByName = (tags, name, excludeId) => {
  const lower = name.toLowerCase();
  return tags.find((t) => t.id !== excludeId && (
    t.name.toLowerCase() === lower || t.aliases?.some((a) => a.toLowerCase() === lower))) || undefined;
};
const refByStash = (tags, endpoint, stashId) =>
  tags.find((t) => t.stash_ids?.some((s) => s.stash_id === stashId && s.endpoint === endpoint));

function fixture50() {
  const tags = [];
  for (let i = 0; i < 50; i++) {
    tags.push({
      id: String(i),
      name: i % 7 === 0 ? `Name ${i % 10}` : `Tag ${i}`, // repeated names
      aliases: i % 3 === 0 ? [`Alias ${i % 5}`, `TAG ${i + 1}`] : (i % 3 === 1 ? [] : undefined),
      stash_ids: i % 4 === 0 ? [{ endpoint: EP, stash_id: `s${i % 9}` }, { endpoint: EP2, stash_id: `p${i}` }]
        : (i % 4 === 1 ? [{ endpoint: EP2, stash_id: `s${i % 9}` }] : []),
    });
  }
  return tags;
}

(async () => {
  await section("buildLocalTagIndex agrees with the old linear finds (50-tag fixture)", async () => {
    const tm = await setup();
    const tags = fixture50();
    tm.setState({ localTags: tags });
    const idx = tm.exports.buildLocalTagIndex(tags);
    check("returns maps", typeof idx.byName.get === "function" && typeof idx.byStash.get === "function");

    const values = new Set();
    for (const t of tags) { values.add(t.name); (t.aliases || []).forEach((a) => values.add(a)); }
    values.add("nope");
    let bad = 0, n = 0;
    for (const v of values) for (const variant of [v, v.toUpperCase(), v.toLowerCase()]) {
      n++;
      if (tm.exports.findLocalTagByName(variant) !== refByName(tags, variant)) bad++;
      for (const ex of [null, "0", "7", "14"]) {
        const got = tm.exports.findConflictingTag(variant, ex);
        const want = refByName(tags, variant, ex) || null;
        if (got !== want) bad++;
      }
    }
    check(`name/alias/conflict lookups agree (${n} probes)`, bad === 0, `${bad} mismatches`);

    let badS = 0;
    for (const ep of [EP, EP2, "https://other/graphql"]) {
      for (let k = 0; k < 60; k++) for (const id of [`s${k}`, `p${k}`]) {
        const got = idx.byStash.get(`${ep}|${id}`);
        if (got !== refByStash(tags, ep, id)) badS++;
      }
    }
    check("endpoint|stash_id lookups agree", badS === 0, `${badS} mismatches`);
    check("empty name finds nothing", tm.exports.findLocalTagByName("") === undefined);
  });

  await section("the memoized index follows localTags", async () => {
    const tm = await setup();
    tm.setState({ localTags: [{ id: "1", name: "A", aliases: [], stash_ids: [] }] });
    check("A found", tm.exports.findLocalTagByName("a")?.id === "1");
    tm.getState().localTags.push({ id: "2", name: "B", aliases: ["Bee"], stash_ids: [] });
    check("a push is seen (length changed)", tm.exports.findLocalTagByName("bee")?.id === "2");
    tm.setState({ localTags: [{ id: "3", name: "C", aliases: [], stash_ids: [] }] });
    check("reassignment is seen", tm.exports.findLocalTagByName("b") === undefined && tm.exports.findLocalTagByName("c")?.id === "3");
  });

  await section("import-all candidates fast", async () => {
    const tm = await setup();
    const locals = [];
    for (let i = 0; i < 10000; i++) {
      locals.push({
        id: String(i), name: `Local ${i}`, aliases: [`Alias ${i}`, `Other ${i}`],
        stash_ids: i < 1500 ? [{ endpoint: EP, stash_id: `s${i}` }] : (i < 2000 ? [{ endpoint: EP2, stash_id: `s${i}` }] : []),
      });
    }
    const remote = [];
    for (let i = 0; i < 3000; i++) remote.push({ id: `s${i}`, name: i % 100 === 0 ? `bad ${i}` : `Remote ${i}`, aliases: [] });
    const isBlacklisted = (n) => n.startsWith("bad ");
    const t0 = process.hrtime.bigint();
    const ids = tm.exports.importAllCandidates({ stashdbTags: remote, localTags: locals, endpoint: EP, isBlacklisted });
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    console.log(`      importAllCandidates: ${ms.toFixed(1)} ms for 10000 local x 3000 remote`);
    check("under 300 ms", ms < 300, `${ms} ms`);
    const set = new Set(ids);
    check("excludes blacklisted", !set.has("s2000") && !set.has("s2500") && ![...set].some((id) => Number(id.slice(1)) % 100 === 0));
    check("excludes tags linked for this endpoint", !set.has("s10") && !set.has("s1499"));
    check("keeps tags linked only to another endpoint", set.has("s1501") && set.has("s1999"));
    // 3000 - 30 blacklisted - linked (s0..s1499 minus blacklisted multiples of 100 in that range: 15)
    check("expected count", ids.length === 3000 - 30 - (1500 - 15), String(ids.length));
  });

  await section("import all ignored while importing", async () => {
    const tm = await setup();
    tm.setState({
      localTags: [], stashdbTags: [{ id: "s1", name: "New", aliases: [] }],
      isImporting: true, selectedForImport: new Set(["keep"]),
    });
    const before = tm.fetchCalls.length;
    await tm.exports.handleImportAll(createQueryableElement("div"));
    check("no confirm asked", tm.confirmCalls.length === 0);
    check("selection untouched", [...tm.getState().selectedForImport].join() === "keep");
    check("no requests", tm.fetchCalls.length === before);
    check("still importing", tm.getState().isImporting === true);
  });

  await section("import all skips blacklisted tags", async () => {
    const tm = await setup({
      TagCreate: (body) => ({ data: { tagCreate: { id: "n" + body.variables.input.name, name: body.variables.input.name } } }),
    });
    tm.setState({
      localTags: [], stashdbTags: [{ id: "s1", name: "Good", aliases: [] }, { id: "s2", name: "Bad", aliases: [] }],
      tagBlacklist: [{ type: "literal", pattern: "bad" }],
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    await tm.exports.handleImportAll(createQueryableElement("div"));
    const created = tm.fetchCalls.filter((c) => c.op === "TagCreate").map((c) => c.body.variables.input.name);
    check("only Good created", created.join() === "Good", created.join());
  });

  await section("cancel stops after current item", async () => {
    let tm;
    let creates = 0;
    tm = loadTagManager({
      fetchResponses: {
        TagCreate: (body) => {
          creates++;
          if (creates === 2) tm.exports.requestImportCancel();
          return { data: { tagCreate: { id: "n" + creates, name: body.variables.input.name } } };
        },
      },
    });
    await tm.settle();
    tm.setState({
      selectedStashBox: { ...BOX }, localTags: [],
      stashdbTags: [1, 2, 3, 4, 5].map((i) => ({ id: `s${i}`, name: `T${i}`, aliases: [] })),
      selectedForImport: new Set(["s1", "s2", "s3", "s4", "s5"]),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    const container = createQueryableElement("div");
    const statuses = [];
    const info = container.querySelector(".tm-selection-info");
    let text = "";
    Object.defineProperty(info, "textContent", { get: () => text, set: (v) => { text = v; statuses.push(v); } });
    await tm.exports.handleImportSelected(container);
    check("loop stopped after 2", creates === 2, `creates=${creates}`);
    check("progress shown", statuses.includes("Importing 1 / 5") && statuses.includes("Importing 2 / 5"), statuses.join(" | "));
    check("summary reports how many were done", /Cancelled after 2 of 5/.test(text), text);
    check("untouched tags stay selected", [...tm.getState().selectedForImport].join() === "s3,s4,s5");
    check("isImporting reset", tm.getState().isImporting === false);
    // the flag resets for the next import
    creates = 100;
    await tm.exports.handleImportSelected(container);
    check("next import is not cancelled", !/Cancelled/.test(text) && tm.getState().selectedForImport.size === 0, text);
  });

  await section("cancel button exists only during an import", async () => {
    const tm = await setup({
      TagCreate: (body) => ({ data: { tagCreate: { id: "n1", name: body.variables.input.name } } }),
    });
    tm.setState({
      localTags: [], stashdbTags: [{ id: "s1", name: "T1", aliases: [] }],
      selectedForImport: new Set(["s1"]),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    const created = [];
    tm.document.createElement = (t) => { const el = createQueryableElement(t); if (t === "button") created.push(el); return el; };
    const container = createQueryableElement("div");
    await tm.exports.handleImportSelected(container);
    const cancel = created.find((b) => b.id === "tm-import-cancel");
    check("a Cancel button was made", !!cancel);
    check("and removed afterwards", cancel && cancel.removed === true);
    check("its click sets the cancel flag path (listener wired)", cancel && (cancel.listeners.click || []).length === 1);
  });

  await section("searchAllOnPage selects tags unlinked for the current endpoint only", async () => {
    const tm = await setup();
    const tags = [
      { id: "1", name: "none" },
      { id: "2", name: "empty", stash_ids: [] },
      { id: "3", name: "other", stash_ids: [{ endpoint: EP2, stash_id: "x" }] },
      { id: "4", name: "this", stash_ids: [{ endpoint: EP, stash_id: "y" }] },
      { id: "5", name: "both", stash_ids: [{ endpoint: EP2, stash_id: "x" }, { endpoint: EP, stash_id: "z" }] },
    ];
    const ids = tm.exports.tagsToSearchOnPage(tags, EP).map((t) => t.id).join();
    check("searches none/empty/other-endpoint", ids === "1,2,3", ids);
  });

  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log("all local index tests passed");
})().catch((e) => { console.log("FAIL  uncaught: " + ((e && e.stack) || e)); process.exit(1); });
