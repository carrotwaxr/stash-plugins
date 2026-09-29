/**
 * F5: atomic, confirmed tag merges, against the REAL tag-manager.js.
 *   - Stash >= 0.31 (TagsMergeInput has `values`): ONE tagsMerge carries the
 *     aliases, stash_ids, parent/child unions and description.
 *   - Stash 0.30: merge, then tagUpdate; a failed update says the merge already
 *     happened and what to fix, and local state drops the (deleted) source.
 *   - The user confirms first (scene/child/parent counts); cancel sends nothing.
 * Run with: node plugins/tagManager/tests/test_merge.js
 */
const { loadTagManager, createElement } = require("./harness");

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
const sorted = (a) => [...(a || [])].map(String).sort();
const sameSet = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
const sidKey = (s) => `${s.endpoint}|${s.stash_id}`;

const EP = "https://stashdb.org/graphql";

// Source "Old Name" (1) is being merged into destination "Dest" (2).
// Source is a parent of Dest (so 2 is in source.children and 1 in dest.parents).
const FRESH_SOURCE = {
  id: "1", name: "Old Name", aliases: ["oa", "Shared"],
  stash_ids: [{ endpoint: "https://other", stash_id: "o1" }, { endpoint: EP, stash_id: "srcid" }],
  parents: [{ id: "10" }, { id: "11" }],
  children: [{ id: "20" }, { id: "21" }, { id: "2" }],
};
const FRESH_DEST = {
  id: "2", name: "Dest", description: "old desc", aliases: ["da", "shared"],
  stash_ids: [{ endpoint: EP, stash_id: "stale" }, { endpoint: "https://x", stash_id: "x1" }],
  parents: [{ id: "10" }, { id: "12" }, { id: "1" }],
  children: [{ id: "22" }],
};
const STASHDB_TAG = { id: "SBID", name: "Dest", description: "StashDB description", aliases: ["SB Alias"] };

function introspection(fields) {
  return { data: { __type: { inputFields: fields.map((name) => ({ name })) } } };
}

function makeModal(descChoice) {
  const modal = createElement("div");
  modal.removed = false;
  modal.remove = () => { modal.removed = true; };
  modal.querySelector = (sel) => (String(sel).includes("tm-desc") ? { value: descChoice } : null);
  return modal;
}

function setup({ supportsValues = true, responses = {}, confirm, fresh = {} } = {}) {
  const src = fresh.source || FRESH_SOURCE;
  const dst = fresh.destination || FRESH_DEST;
  const tm = loadTagManager({
    confirm,
    fetchResponses: {
      TagsMergeInputFields: introspection(supportsValues
        ? ["source", "destination", "values"] : ["source", "destination"]),
      FindTagsForMerge: { data: { source: src, destination: dst } },
      FindTag: { data: { findTag: { scene_count: 42 } } },
      TagsMerge: (body) => ({ data: { tagsMerge: { id: body.variables.input.destination, name: "Dest", aliases: [], stash_ids: [] } } }),
      TagUpdate: (body) => ({ data: { tagUpdate: { id: body.variables.input.id, name: "Dest", stash_ids: [] } } }),
      ...responses,
    },
  });
  tm.setState({
    localTags: [
      { id: "1", name: "Old Name", aliases: ["oa"], stash_ids: [], parents: [{ id: "10", name: "P10" }] },
      { id: "2", name: "Dest", aliases: ["da"], stash_ids: [], parents: [] },
      { id: "10", name: "P10", aliases: [] }, { id: "12", name: "P12", aliases: [] },
    ],
    matchResults: { "1": [{ tag: STASHDB_TAG }] },
  });
  return tm;
}

function mergeArgs(overrides = {}) {
  return {
    sourceTag: { id: "1", name: "Old Name", aliases: ["oa"], stash_ids: [] },
    destinationId: "2",
    stashdbTag: STASHDB_TAG,
    endpoint: EP,
    sanitizedAliases: ["Old Name", "SB Alias", "dest"],
    modal: makeModal("stashdb"),
    container: createElement("div"),
    ...overrides,
  };
}

const callsTo = (tm, op) => tm.fetchCalls.filter((c) => c.op === op);

(async () => {
  // ---------------------------------------------------------------------------
  console.log("merge with values when supported");
  {
    const tm = setup({ supportsValues: true });
    const args = mergeArgs();
    const result = await tm.exports.performTagMerge(args);
    check("success", result && result.success === true, JSON.stringify(result));
    const merges = callsTo(tm, "TagsMerge");
    check("exactly one TagsMerge", merges.length === 1, `got ${merges.length}`);
    check("no follow-up TagUpdate", callsTo(tm, "TagUpdate").length === 0);
    const input = merges[0] && merges[0].body.variables.input;
    check("source/destination", input && sameSet(input.source, ["1"]) && input.destination === "2", JSON.stringify(input));
    const v = (input && input.values) || {};
    check("values.id is the destination (TagUpdateInput.id is required)", v.id === "2", JSON.stringify(v));
    check("values has no name (no rename)", !("name" in v), JSON.stringify(v));
    check("aliases = dest ∪ source name ∪ source aliases ∪ sanitized, minus dest name",
      sameSet(v.aliases, ["da", "shared", "Old Name", "oa", "SB Alias"]), JSON.stringify(v.aliases));
    const lower = (v.aliases || []).map((a) => a.toLowerCase());
    check("aliases deduped case-insensitively", new Set(lower).size === lower.length, JSON.stringify(v.aliases));
    check("stash_ids: dest ∪ source, this endpoint replaced by the new link",
      sameSet((v.stash_ids || []).map(sidKey), ["https://x|x1", "https://other|o1", `${EP}|SBID`]),
      JSON.stringify(v.stash_ids));
    check("parent_ids = union minus source/destination",
      sameSet(v.parent_ids, ["10", "11", "12"]), JSON.stringify(v.parent_ids));
    check("child_ids = union minus source/destination",
      sameSet(v.child_ids, ["20", "21", "22"]), JSON.stringify(v.child_ids));
    check("description from StashDB", v.description === "StashDB description", JSON.stringify(v.description));
    check("introspection ran", callsTo(tm, "TagsMergeInputFields").length === 1);
    check("confirmed once", tm.confirmCalls.length === 1, JSON.stringify(tm.confirmCalls));

    const st = tm.getState();
    check("source removed from localTags", !st.localTags.some((t) => t.id === "1"));
    const d = st.localTags.find((t) => t.id === "2");
    check("destination patched locally", d && sameSet(d.aliases, v.aliases)
      && sameSet(d.stash_ids.map(sidKey), v.stash_ids.map(sidKey)) && d.description === "StashDB description",
      JSON.stringify(d));
    check("matchResults for source dropped", !("1" in st.matchResults));
    check("modal closed", args.modal.removed === true);

    // Cached: a second merge does not re-introspect.
    await tm.exports.supportsMergeValues();
    check("introspection cached", callsTo(tm, "TagsMergeInputFields").length === 1);
  }

  // ---------------------------------------------------------------------------
  console.log("keep-local description sends no description");
  {
    const tm = setup({ supportsValues: true });
    await tm.exports.performTagMerge(mergeArgs({ modal: makeModal("local") }));
    const v = callsTo(tm, "TagsMerge")[0].body.variables.input.values;
    check("no description key", !("description" in v), JSON.stringify(v));
  }

  // ---------------------------------------------------------------------------
  console.log("no direct cycles in the parent/child unions");
  {
    // 30 is the source's parent AND the destination's child: making it the
    // destination's parent too would be a cycle, so it stays a child only.
    // 31 is the source's child AND the destination's parent: stays a parent only.
    const tm = setup({
      supportsValues: true,
      fresh: {
        source: { ...FRESH_SOURCE, parents: [{ id: "30" }], children: [{ id: "31" }] },
        destination: { ...FRESH_DEST, parents: [{ id: "31" }], children: [{ id: "30" }] },
      },
    });
    await tm.exports.performTagMerge(mergeArgs());
    const v = callsTo(tm, "TagsMerge")[0].body.variables.input.values;
    check("parent_ids", sameSet(v.parent_ids, ["31"]), JSON.stringify(v.parent_ids));
    check("child_ids", sameSet(v.child_ids, ["30"]), JSON.stringify(v.child_ids));
  }

  // ---------------------------------------------------------------------------
  console.log("atomic merge failure changes nothing locally");
  {
    const tm = setup({
      supportsValues: true,
      responses: { TagsMerge: { errors: [{ message: "alias 'SB Alias' is already used by 'Other'" }] } },
    });
    const result = await tm.exports.performTagMerge(mergeArgs());
    check("failure", result.success === false && /SB Alias/.test(result.error), JSON.stringify(result));
    check("no follow-up TagUpdate", callsTo(tm, "TagUpdate").length === 0);
    check("source still in localTags", tm.getState().localTags.some((t) => t.id === "1"));
  }

  // ---------------------------------------------------------------------------
  console.log("v0.30 fallback");
  {
    const tm = setup({
      supportsValues: false,
      responses: { TagUpdate: { errors: [{ message: "alias 'SB Alias' is already used by 'Other'" }] } },
    });
    const args = mergeArgs();
    const result = await tm.exports.performTagMerge(args);
    const merges = callsTo(tm, "TagsMerge");
    check("one plain TagsMerge", merges.length === 1, `got ${merges.length}`);
    const input = merges[0] && merges[0].body.variables.input;
    check("merge has no values", input && !("values" in input), JSON.stringify(input));
    const updates = callsTo(tm, "TagUpdate");
    check("then one TagUpdate", updates.length === 1, `got ${updates.length}`);
    const u = updates[0] && updates[0].body.variables.input;
    check("update targets destination with the same values",
      u && u.id === "2" && sameSet(u.parent_ids, ["10", "11", "12"]) && sameSet(u.child_ids, ["20", "21", "22"])
      && sameSet(u.aliases, ["da", "shared", "Old Name", "oa", "SB Alias"]) && !("name" in u),
      JSON.stringify(u));
    check("update came after the merge",
      tm.fetchCalls.findIndex((c) => c.op === "TagsMerge") < tm.fetchCalls.findIndex((c) => c.op === "TagUpdate"));
    check("result is a failure", result.success === false, JSON.stringify(result));
    check("result flags that the merge happened", result.merged === true, JSON.stringify(result));
    const err = result.error || "";
    check("error says the source was already merged", /Merged 'Old Name' into 'Dest'/.test(err), err);
    check("error carries the server message", err.includes("alias 'SB Alias' is already used by 'Other'"), err);
    check("error says it can't be undone and what to fix",
      /can't be undone/.test(err) && /edit 'Dest' in Stash/.test(err), err);
    const st = tm.getState();
    check("source removed from localTags anyway", !st.localTags.some((t) => t.id === "1"));
    check("matchResults for source dropped", !("1" in st.matchResults));
  }

  // ---------------------------------------------------------------------------
  console.log("v0.30 fallback success");
  {
    const tm = setup({ supportsValues: false });
    const result = await tm.exports.performTagMerge(mergeArgs());
    check("success", result.success === true, JSON.stringify(result));
    check("merge + update", callsTo(tm, "TagsMerge").length === 1 && callsTo(tm, "TagUpdate").length === 1);
    check("source removed", !tm.getState().localTags.some((t) => t.id === "1"));
  }

  // ---------------------------------------------------------------------------
  console.log("introspection failure falls back and is retried");
  {
    let n = 0;
    const tm = setup({
      responses: {
        TagsMergeInputFields: () => (++n === 1
          ? { errors: [{ message: "boom" }] }
          : introspection(["source", "destination", "values"])),
      },
    });
    const first = await tm.exports.supportsMergeValues();
    check("failure -> unsupported", first === false);
    const second = await tm.exports.supportsMergeValues();
    check("failure not cached (retried)", second === true && n === 2, `second=${second} n=${n}`);
    await tm.exports.supportsMergeValues();
    check("success cached", n === 2, `n=${n}`);
  }

  // ---------------------------------------------------------------------------
  console.log("confirm cancelled");
  {
    const tm = setup({ supportsValues: true, confirm: () => false });
    const args = mergeArgs();
    const ok = await tm.exports.confirmTagMerge(FRESH_SOURCE, FRESH_DEST);
    check("confirmTagMerge returns false", ok === false);
    const result = await tm.exports.performTagMerge(args);
    check("result is cancelled", result.success === false && result.cancelled === true, JSON.stringify(result));
    check("no TagsMerge sent", callsTo(tm, "TagsMerge").length === 0);
    check("no TagUpdate sent", callsTo(tm, "TagUpdate").length === 0);
    check("localTags untouched", tm.getState().localTags.some((t) => t.id === "1"));
    check("modal left open", args.modal.removed === false);
  }

  // ---------------------------------------------------------------------------
  console.log("confirmation text");
  {
    const tm = setup();
    // The destination itself (2) is one of the source's children; it does not
    // "move to" itself, so it is not counted.
    const ok = await tm.exports.confirmTagMerge(
      { ...FRESH_SOURCE, children: [{ id: "20" }, { id: "21" }, { id: "23" }, { id: "2" }] }, FRESH_DEST);
    check("returns true when confirmed", ok === true);
    const msg = tm.confirmCalls[0] || "";
    check("names both tags", msg.includes('Merge "Old Name" into "Dest"?'), msg);
    check("scene count", msg.includes("42 scenes"), msg);
    check("child count (destination excluded)", msg.includes("3 child tags"), msg);
    check("parent count", msg.includes("2 parent tags"), msg);
    check("says it deletes the source and can't be undone",
      msg.includes('"Old Name" will be deleted') && msg.includes("can't be undone"), msg);
    const sceneQuery = callsTo(tm, "FindTag")[0];
    check("scene count fetched for the source", sceneQuery && sceneQuery.body.variables.id === "1");

    const tm1 = setup({ responses: { FindTag: { data: { findTag: { scene_count: 1 } } } } });
    await tm1.exports.confirmTagMerge(
      { ...FRESH_SOURCE, parents: [{ id: "10" }], children: [{ id: "20" }] }, FRESH_DEST);
    const msg1 = tm1.confirmCalls[0] || "";
    check("singulars", msg1.includes("1 scene,") && msg1.includes("1 child tag ") && msg1.includes("1 parent tag "), msg1);
  }

  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log("all merge tests passed");
})().catch((e) => { console.log("FAIL  uncaught: " + (e && e.stack || e)); process.exit(1); });
