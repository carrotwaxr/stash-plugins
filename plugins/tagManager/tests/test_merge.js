/**
 * F5: atomic, confirmed tag merges, against the REAL tag-manager.js.
 *   - Stash >= 0.31 (TagsMergeInput has `values`): ONE tagsMerge carries the
 *     aliases, stash_ids, parent/child unions and description.
 *   - Stash 0.30: merge, then tagUpdate; a failed update says the merge already
 *     happened and what to fix, and local state drops the (deleted) source.
 *   - The user confirms first (scene/child/parent counts); cancel sends nothing.
 *   - The dialog's Parent choice is merged in like Apply does: a selected id is
 *     added to parent_ids, '__create__' is created only after the confirm, none
 *     with "Leave Parent Tags Alone", and a remembered mapping is saved only
 *     once the merge succeeded. Both Merge buttons pass the choice.
 * Run with: node plugins/tagManager/tests/test_merge.js
 */
const { loadTagManager, createElement, createQueryableElement } = require("./harness");

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}
const sorted = (a) => [...(a || [])].map(String).sort();
const sameSet = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
const sidKey = (s) => `${s.endpoint}|${s.stash_id}`;
const j = (x) => JSON.stringify(x);

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

// ---- parent choice ----------------------------------------------------------
const CATEGORY_TAG = { ...STASHDB_TAG, category: { id: "cat1", name: "Hair Style" } };
const BASE_PARENTS = ["10", "11", "12"]; // parent_ids of a merge without a chosen parent

/**
 * Like setup(), plus a stateful plugin config (Configuration/ConfigurePlugin),
 * TagCreate (new tags get id "50") and a settled init, so mapping saves are real.
 * Local tag "27" ("Hairstyles") is an existing candidate parent.
 */
async function setupParent({ supportsValues = true, responses = {}, confirm, leaveParentTagsAlone = false } = {}) {
  let pluginConfig = {};
  const tm = loadTagManager({
    confirm: (msg) => (confirm ? confirm(msg, tm) : true),
    fetchResponses: {
      TagsMergeInputFields: introspection(supportsValues
        ? ["source", "destination", "values"] : ["source", "destination"]),
      FindTagsForMerge: { data: { source: FRESH_SOURCE, destination: FRESH_DEST } },
      FindTag: { data: { findTag: { scene_count: 42 } } },
      TagsMerge: (body) => ({ data: { tagsMerge: { id: body.variables.input.destination, name: "Dest", aliases: [], stash_ids: [] } } }),
      TagUpdate: (body) => ({ data: { tagUpdate: { id: body.variables.input.id, name: "Dest", stash_ids: [] } } }),
      Configuration: () => ({ data: { configuration: { plugins: { tagManager: pluginConfig } } } }),
      ConfigurePlugin: (body) => { pluginConfig = body.variables.input; return { data: { configurePlugin: pluginConfig } }; },
      TagCreate: (body) => ({ data: { tagCreate: {
        id: "50", name: body.variables.input.name, description: "", aliases: [], stash_ids: [], parents: [],
      } } }),
      ...responses,
    },
  });
  await tm.settle();
  tm.setState({
    localTags: [
      { id: "1", name: "Old Name", aliases: ["oa"], stash_ids: [], parents: [{ id: "10", name: "P10" }] },
      { id: "2", name: "Dest", aliases: ["da"], stash_ids: [], parents: [] },
      { id: "10", name: "P10", aliases: [] }, { id: "12", name: "P12", aliases: [] },
      { id: "27", name: "Hairstyles", aliases: [] },
    ],
    settings: { ...tm.getState().settings, leaveParentTagsAlone },
    selectedStashBox: { endpoint: EP, name: "StashDB" },
    stashBoxes: [{ endpoint: EP, name: "StashDB" }],
    categoryMappings: {},
    matchResults: { "1": [{ tag: CATEGORY_TAG, match_type: "exact", score: 100 }] },
  });
  return { tm, mark: tm.fetchCalls.length };
}

const since = (tm, mark, op) => tm.fetchCalls.slice(mark).filter((c) => c.op === op);
const opIndex = (tm, op) => tm.fetchCalls.findIndex((c) => c.op === op);
/** The categoryMappings of each ConfigurePlugin write since `mark`, parsed. */
const mappingWrites = (tm, mark) => since(tm, mark, "ConfigurePlugin")
  .map((c) => c.body.variables.input.categoryMappings)
  .filter((m) => m !== undefined)
  .map((m) => (typeof m === "string" ? JSON.parse(m) : m));

/**
 * Open the real diff dialog for tag 1 on queryable stubs. The Merge buttons
 * the error views render are stubs whose data-conflict-id is "2" (Dest).
 */
function openDialog(tm, { nameChoice, descChoice = "local" }) {
  const rememberEl = { checked: true };
  const button = () => { const b = createQueryableElement("button"); b.dataset.conflictId = "2"; return b; };
  const mergeBtns = { ".tm-error-merge": button(), ".tm-error-merge-api": button() };
  tm.document.createElement = (t) => createQueryableElement(t, {
    query: (sel) => {
      if (sel === 'input[name="tm-name"]:checked') return { value: nameChoice };
      if (sel === 'input[name="tm-desc"]:checked') return { value: descChoice };
      if (sel === "#tm-remember-mapping") return rememberEl;
      if (sel in mergeBtns) return mergeBtns[sel];
      return undefined;
    },
  });
  tm.exports.showDiffDialog("1", createQueryableElement("div"));
  const modal = tm.document.body.children[tm.document.body.children.length - 1];
  return {
    modal, rememberEl, mergeBtns,
    errorEl: modal.querySelector("#tm-diff-error"),
    selectParent: (value) => modal.querySelector("#tm-parent-select").listeners.change[0]({ target: { value } }),
    apply: () => modal.querySelector(".tm-apply-btn").listeners.click[0](),
    clickMerge: (sel) => mergeBtns[sel].listeners.click[0](),
  };
}

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
    check("no parent sentence without a chosen parent", !msg.includes("added as a parent"), msg);
  }

  // ---------------------------------------------------------------------------
  console.log("merge applies selected parent");
  {
    const { tm, mark } = await setupParent();
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: false }));
    check("success", result && result.success === true, j(result));
    const merge = since(tm, mark, "TagsMerge")[0];
    const v = (merge && merge.body.variables.input.values) || {};
    check("values.parent_ids = the union plus the selected parent",
      sameSet(v.parent_ids, [...BASE_PARENTS, "27"]), j(v.parent_ids));
    check("child_ids unchanged", sameSet(v.child_ids, ["20", "21", "22"]), j(v.child_ids));
    check("no TagCreate", since(tm, mark, "TagCreate").length === 0);
    const msg = tm.confirmCalls[0] || "";
    check("confirmation says the parent is added", msg.includes('"Hairstyles" is added as a parent'), msg);
    check("remember off: no mapping written", mappingWrites(tm, mark).length === 0, j(mappingWrites(tm, mark)));
    check("remember off: no mapping in memory", j(tm.getState().categoryMappings) === "{}");
  }
  {
    // Already a parent of the source: not added twice, and the confirmation doesn't claim it is.
    const { tm, mark } = await setupParent();
    await tm.exports.performTagMerge(mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "10" }));
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("existing parent: parent_ids unchanged", sameSet(v.parent_ids, BASE_PARENTS), j(v.parent_ids));
    check("existing parent: no 'added' sentence", !(tm.confirmCalls[0] || "").includes("added as a parent"), tm.confirmCalls[0]);
  }
  {
    // 22 is the destination's child: making it a parent too would be a cycle.
    const { tm, mark } = await setupParent();
    await tm.exports.performTagMerge(mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "22" }));
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("cycle: not a parent", sameSet(v.parent_ids, BASE_PARENTS), j(v.parent_ids));
    check("cycle: stays a child", sameSet(v.child_ids, ["20", "21", "22"]), j(v.child_ids));
    check("cycle: no 'added' sentence", !(tm.confirmCalls[0] || "").includes("added as a parent"), tm.confirmCalls[0]);
  }
  {
    // No category on the stash-box tag: nothing to map, no parent.
    const { tm, mark } = await setupParent();
    await tm.exports.performTagMerge(mergeArgs({ parentId: "27", rememberMapping: true }));
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("no category: parent_ids unchanged", sameSet(v.parent_ids, BASE_PARENTS), j(v.parent_ids));
    check("no category: no mapping written", mappingWrites(tm, mark).length === 0);
  }
  {
    const { tm, mark } = await setupParent({ supportsValues: false });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: false }));
    check("v0.30: success", result.success === true, j(result));
    const u = since(tm, mark, "TagUpdate")[0];
    check("v0.30: the follow-up update carries the selected parent",
      u && sameSet(u.body.variables.input.parent_ids, [...BASE_PARENTS, "27"]), u && j(u.body.variables.input));
  }

  // ---------------------------------------------------------------------------
  console.log("merge creates parent after confirm");
  {
    let createsAtConfirm = null;
    const { tm, mark } = await setupParent({
      confirm: (msg, t) => { createsAtConfirm = callsTo(t, "TagCreate").length; return true; },
    });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "__create__", rememberMapping: false }));
    check("success", result.success === true, j(result));
    const creates = since(tm, mark, "TagCreate");
    check("one TagCreate for the category", creates.length === 1
      && creates[0].body.variables.input.name === "Hair Style", j(creates.map((c) => c.body.variables)));
    check("nothing created before the confirm", createsAtConfirm === 0, `createsAtConfirm=${createsAtConfirm}`);
    check("TagCreate came after the confirm and before the TagsMerge",
      tm.confirmCalls.length === 1 && opIndex(tm, "TagCreate") >= 0 && opIndex(tm, "TagCreate") < opIndex(tm, "TagsMerge"));
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("values.parent_ids includes the created parent", sameSet(v.parent_ids, [...BASE_PARENTS, "50"]), j(v.parent_ids));
    check("result carries the created parent", result.createdParent && result.createdParent.id === "50", j(result));
    const created = tm.getState().localTags.find((t) => t.id === "50");
    check("created parent in localTags", created && created.name === "Hair Style" && Array.isArray(created.aliases), j(created));
    const msg = tm.confirmCalls[0] || "";
    check("confirmation says the new parent is added", msg.includes('"Hair Style" is added as a parent'), msg);
  }
  {
    const { tm, mark } = await setupParent({ confirm: () => false });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "__create__", rememberMapping: true }));
    check("cancelled", result.success === false && result.cancelled === true, j(result));
    check("cancelled: no TagCreate", since(tm, mark, "TagCreate").length === 0);
    check("cancelled: no TagsMerge", since(tm, mark, "TagsMerge").length === 0);
    check("cancelled: no mapping written", mappingWrites(tm, mark).length === 0);
  }

  // ---------------------------------------------------------------------------
  console.log("parent creation failure aborts the merge");
  {
    const { tm, mark } = await setupParent({
      responses: { TagCreate: { errors: [{ message: "tag with name 'Hair Style' already exists" }] } },
    });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "__create__", rememberMapping: true }));
    check("failure", result.success === false && !result.cancelled && !result.merged, j(result));
    check("error names the parent step and says nothing was merged",
      /Failed to create parent tag/.test(result.error) && /already exists/.test(result.error)
        && /nothing was merged/.test(result.error), result.error);
    check("no TagsMerge", since(tm, mark, "TagsMerge").length === 0);
    check("no mapping written", mappingWrites(tm, mark).length === 0);
    check("source still in localTags", tm.getState().localTags.some((t) => t.id === "1"));
  }

  // ---------------------------------------------------------------------------
  console.log("merge with leaveParentTagsAlone adds no parent");
  {
    const { tm, mark } = await setupParent({ leaveParentTagsAlone: true });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: true }));
    check("success", result.success === true, j(result));
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("parent_ids = the plain union", sameSet(v.parent_ids, BASE_PARENTS), j(v.parent_ids));
    check("no mapping written", mappingWrites(tm, mark).length === 0, j(mappingWrites(tm, mark)));
    check("no 'added' sentence", !(tm.confirmCalls[0] || "").includes("added as a parent"), tm.confirmCalls[0]);
  }
  {
    const { tm, mark } = await setupParent({ leaveParentTagsAlone: true });
    await tm.exports.performTagMerge(mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "__create__", rememberMapping: true }));
    check("__create__: no TagCreate", since(tm, mark, "TagCreate").length === 0);
    const v = since(tm, mark, "TagsMerge")[0].body.variables.input.values;
    check("__create__: parent_ids = the plain union", sameSet(v.parent_ids, BASE_PARENTS), j(v.parent_ids));
  }

  // ---------------------------------------------------------------------------
  console.log("mapping saved only after merge succeeds and remember checked");
  {
    const { tm, mark } = await setupParent();
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: true }));
    check("success", result.success === true, j(result));
    const writes = mappingWrites(tm, mark);
    check("one mapping write, per endpoint", writes.length === 1
      && j(writes[0]) === j({ [EP]: { "Hair Style": "27" } }), j(writes));
    check("mapping write came after the TagsMerge",
      opIndex(tm, "TagsMerge") < tm.fetchCalls.findIndex((c, i) => i >= mark && c.op === "ConfigurePlugin"));
    check("mapping in memory", j(tm.getState().categoryMappings) === j({ [EP]: { "Hair Style": "27" } }),
      j(tm.getState().categoryMappings));
  }
  {
    const { tm, mark } = await setupParent();
    await tm.exports.performTagMerge(mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "__create__", rememberMapping: true }));
    const writes = mappingWrites(tm, mark);
    check("created parent: mapping to the new tag", writes.length === 1
      && j(writes[0]) === j({ [EP]: { "Hair Style": "50" } }), j(writes));
  }
  {
    const { tm, mark } = await setupParent({
      responses: { TagsMerge: { errors: [{ message: "alias 'SB Alias' is already used by 'Other'" }] } },
    });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: true }));
    check("merge failure: failure", result.success === false, j(result));
    check("merge failure: no mapping written", mappingWrites(tm, mark).length === 0, j(mappingWrites(tm, mark)));
    check("merge failure: no mapping in memory", j(tm.getState().categoryMappings) === "{}", j(tm.getState().categoryMappings));
  }
  {
    const { tm, mark } = await setupParent({
      supportsValues: false,
      responses: { TagUpdate: { errors: [{ message: "alias 'SB Alias' is already used by 'Other'" }] } },
    });
    const result = await tm.exports.performTagMerge(
      mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: true }));
    check("v0.30 update failure: merged but failed", result.success === false && result.merged === true, j(result));
    check("v0.30 update failure: no mapping written", mappingWrites(tm, mark).length === 0, j(mappingWrites(tm, mark)));
  }
  {
    const { tm, mark } = await setupParent({ supportsValues: false });
    await tm.exports.performTagMerge(mergeArgs({ stashdbTag: CATEGORY_TAG, parentId: "27", rememberMapping: true }));
    const writes = mappingWrites(tm, mark);
    check("v0.30 success: mapping written", writes.length === 1
      && j(writes[0]) === j({ [EP]: { "Hair Style": "27" } }), j(writes));
    check("v0.30 success: mapping write came after the TagUpdate",
      opIndex(tm, "TagUpdate") < tm.fetchCalls.findIndex((c, i) => i >= mark && c.op === "ConfigurePlugin"));
  }

  // ---------------------------------------------------------------------------
  console.log("both Merge buttons pass the dialog's parent choice");
  {
    // Name conflict found before saving -> .tm-error-merge
    const { tm, mark } = await setupParent();
    const dlg = openDialog(tm, { nameChoice: "stashdb" });
    dlg.selectParent("27");
    await dlg.apply();
    await tm.settle();
    check("validation stopped Apply: nothing written",
      since(tm, mark, "TagUpdate").length === 0 && since(tm, mark, "TagCreate").length === 0
        && mappingWrites(tm, mark).length === 0);
    await dlg.clickMerge(".tm-error-merge");
    await tm.settle();
    const merge = since(tm, mark, "TagsMerge")[0];
    check(".tm-error-merge: TagsMerge carries the selected parent",
      merge && sameSet(merge.body.variables.input.values.parent_ids, [...BASE_PARENTS, "27"]),
      merge && j(merge.body.variables.input.values.parent_ids));
    check(".tm-error-merge: remembered mapping saved",
      j(mappingWrites(tm, mark)) === j([{ [EP]: { "Hair Style": "27" } }]), j(mappingWrites(tm, mark)));
    check(".tm-error-merge: dialog closed", dlg.modal.removed === true);
  }
  {
    // Server rejects the Apply -> .tm-error-merge-api. Apply already created the
    // '__create__' parent; the merge reuses it instead of creating another.
    const { tm, mark } = await setupParent({
      responses: { TagUpdate: { errors: [{ message: "tag with name 'Dest' already exists" }] } },
    });
    const dlg = openDialog(tm, { nameChoice: "local" });
    dlg.selectParent("__create__");
    await dlg.apply();
    await tm.settle();
    check("Apply failed at the update, after creating the parent",
      since(tm, mark, "TagUpdate").length === 1 && since(tm, mark, "TagCreate").length === 1
        && mappingWrites(tm, mark).length === 0);
    await dlg.clickMerge(".tm-error-merge-api");
    await tm.settle();
    const merge = since(tm, mark, "TagsMerge")[0];
    check(".tm-error-merge-api: TagsMerge carries the parent Apply created",
      merge && sameSet(merge.body.variables.input.values.parent_ids, [...BASE_PARENTS, "50"]),
      merge && j(merge.body.variables.input.values.parent_ids));
    check(".tm-error-merge-api: no second TagCreate", since(tm, mark, "TagCreate").length === 1);
    check(".tm-error-merge-api: remembered mapping saved",
      j(mappingWrites(tm, mark)) === j([{ [EP]: { "Hair Style": "50" } }]), j(mappingWrites(tm, mark)));
  }
  {
    // Remember unchecked in the dialog -> no mapping.
    const { tm, mark } = await setupParent();
    const dlg = openDialog(tm, { nameChoice: "stashdb" });
    dlg.selectParent("27");
    dlg.rememberEl.checked = false;
    await dlg.apply();
    await dlg.clickMerge(".tm-error-merge");
    await tm.settle();
    const merge = since(tm, mark, "TagsMerge")[0];
    check("remember unchecked: parent still merged in",
      merge && sameSet(merge.body.variables.input.values.parent_ids, [...BASE_PARENTS, "27"]));
    check("remember unchecked: no mapping written", mappingWrites(tm, mark).length === 0, j(mappingWrites(tm, mark)));
  }

  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log("all merge tests passed");
})().catch((e) => { console.log("FAIL  uncaught: " + (e && e.stack || e)); process.exit(1); });
