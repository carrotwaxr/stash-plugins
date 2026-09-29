/**
 * F12: the diff dialog's Apply, against the REAL tag-manager.js.
 *   - validateBeforeSave runs first: a conflict writes nothing (no parent
 *     TagCreate, no mapping ConfigurePlugin, no TagUpdate).
 *   - then: create the '__create__' parent (full tag into localTags), update the
 *     tag, save the mapping; a failed update saves no mapping and a retry reuses
 *     the parent it already created.
 *   - the Apply button is disabled while Apply runs; a double click sends one update.
 * Run with: node plugins/tagManager/tests/test_apply_order.js
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
const j = (x) => JSON.stringify(x);
const clone = (x) => JSON.parse(JSON.stringify(x));

const EP = "https://stashdb.org/graphql";
const CATEGORY = { id: "c1", name: "Hair Color" };
const LOCAL = [
  { id: "1", name: "Blonde", description: "", aliases: [], stash_ids: [], parents: [] },
  { id: "2", name: "Blond Hair", description: "", aliases: [], stash_ids: [], parents: [] },
];
// Renaming tag 1 to this collides with tag 2.
const CLASHING = { id: "SB1", name: "Blond Hair", description: "box desc", aliases: [], category: CATEGORY };
const CLEAN = { id: "SB2", name: "Blonde", description: "box desc", aliases: [], category: CATEGORY };

async function setup({ stashdbTag = CLEAN, responses = {} } = {}) {
  let pluginConfig = {};
  const tm = loadTagManager({
    fetchResponses: {
      Configuration: () => ({ data: { configuration: { plugins: { tagManager: pluginConfig } } } }),
      ConfigurePlugin: (body) => { pluginConfig = body.variables.input; return { data: { configurePlugin: pluginConfig } }; },
      TagCreate: (body) => ({ data: { tagCreate: {
        id: "50", name: body.variables.input.name, description: "", aliases: [], stash_ids: [], parents: [],
      } } }),
      FindTag: { data: { findTag: { parents: [{ id: "7" }] } } },
      TagUpdate: (body) => ({ data: { tagUpdate: {
        id: body.variables.input.id, name: body.variables.input.name || "Blonde", stash_ids: body.variables.input.stash_ids,
      } } }),
      ...responses,
    },
  });
  await tm.settle();
  tm.setState({
    localTags: clone(LOCAL),
    settings: { ...tm.getState().settings, leaveParentTagsAlone: false },
    selectedStashBox: { endpoint: EP, name: "StashDB" },
    stashBoxes: [{ endpoint: EP, name: "StashDB" }],
    categoryMappings: {},
    matchResults: { "1": [{ tag: stashdbTag, match_type: "exact", score: 100 }] },
  });
  return { tm, mark: tm.fetchCalls.length };
}
const since = (tm, mark, op) => tm.fetchCalls.slice(mark).filter((c) => c.op === op);
const writesSince = (tm, mark) => tm.fetchCalls.slice(mark).map((c) => c.op)
  .filter((op) => ["TagCreate", "TagUpdate", "ConfigurePlugin", "FindTag"].includes(op));

/** Open the real diff dialog on queryable stubs and return its Apply button. */
function openDialog(tm, { nameChoice, descChoice = "local", remember = true }) {
  const rememberEl = { checked: remember };
  tm.document.createElement = (t) => createQueryableElement(t, {
    query: (sel) => {
      if (sel === 'input[name="tm-name"]:checked') return { value: nameChoice };
      if (sel === 'input[name="tm-desc"]:checked') return { value: descChoice };
      if (sel === "#tm-remember-mapping") return rememberEl;
      return undefined;
    },
  });
  const container = createQueryableElement("div");
  tm.exports.showDiffDialog("1", container);
  const modal = tm.document.body.children[tm.document.body.children.length - 1];
  const applyBtn = modal.querySelector(".tm-apply-btn");
  return {
    modal, applyBtn, rememberEl,
    errorEl: modal.querySelector("#tm-diff-error"),
    click: () => applyBtn.listeners.click[0](),
  };
}

(async () => {
  // ---------------------------------------------------------------------------
  await section("apply validates before creating parent (applyDiff)", async () => {
    const { tm, mark } = await setup({ stashdbTag: CLASHING });
    const tag = tm.getState().localTags.find((t) => t.id === "1");
    const r = await tm.exports.applyDiff({
      tag, stashdbTag: CLASHING, endpoint: EP, nameChoice: "stashdb", descChoice: "stashdb",
      aliases: [], parentId: "__create__", rememberMapping: true,
    });
    check("not ok", r && r.ok === false, j(r));
    check("name conflict reported", r.validationErrors && r.validationErrors[0].type === "name_conflict"
      && r.validationErrors[0].conflictsWith.id === "2", j(r && r.validationErrors));
    check("nothing written or fetched", writesSince(tm, mark).length === 0, j(writesSince(tm, mark)));
    check("no parent pushed", tm.getState().localTags.length === 2);
    check("no mapping", j(tm.getState().categoryMappings) === "{}", j(tm.getState().categoryMappings));
  });

  // ---------------------------------------------------------------------------
  await section("apply validates before creating parent (dialog)", async () => {
    const { tm, mark } = await setup({ stashdbTag: CLASHING });
    const dlg = openDialog(tm, { nameChoice: "stashdb", remember: true });
    await dlg.click();
    await tm.settle();
    check("no TagCreate for the __create__ parent", since(tm, mark, "TagCreate").length === 0);
    check("no ConfigurePlugin for the mapping", since(tm, mark, "ConfigurePlugin").length === 0);
    check("no TagUpdate", since(tm, mark, "TagUpdate").length === 0);
    check("conflict shown", dlg.errorEl.style.display === "block" && /already exists/.test(dlg.errorEl.innerHTML), dlg.errorEl.innerHTML);
    check("dialog open, Apply enabled again", dlg.modal.removed === false && dlg.applyBtn.disabled === false);
    check("no mapping", j(tm.getState().categoryMappings) === "{}");
  });

  // ---------------------------------------------------------------------------
  await section("apply order on success: parent, update, mapping", async () => {
    const { tm, mark } = await setup();
    const dlg = openDialog(tm, { nameChoice: "local", descChoice: "stashdb", remember: true });
    await dlg.click();
    await tm.settle();
    const ops = writesSince(tm, mark);
    check("TagCreate, FindTag, TagUpdate, then ConfigurePlugin",
      j(ops) === j(["TagCreate", "FindTag", "TagUpdate", "ConfigurePlugin"]), j(ops));
    const create = since(tm, mark, "TagCreate")[0];
    check("parent created from the category", create && create.body.variables.input.name === "Hair Color", create && j(create.body.variables));
    const u = since(tm, mark, "TagUpdate")[0].body.variables.input;
    check("update keeps current parents and adds the new one", j(u.parent_ids) === j(["7", "50"]), j(u));
    check("update links the stash id", j(u.stash_ids) === j([{ endpoint: EP, stash_id: "SB2" }]), j(u));
    check("update carries the chosen description", u.description === "box desc", j(u));
    const parent = tm.getState().localTags.find((t) => t.id === "50");
    check("full parent tag in localTags",
      parent && parent.name === "Hair Color" && Array.isArray(parent.aliases) && Array.isArray(parent.stash_ids)
        && Array.isArray(parent.parents) && typeof parent.description === "string", j(parent));
    check("mapping saved to the new parent", tm.getState().categoryMappings["Hair Color"] === "50", j(tm.getState().categoryMappings));
    check("dialog closed", dlg.modal.removed === true);
    check("match result consumed", !("1" in tm.getState().matchResults));
    const t1 = tm.getState().localTags.find((t) => t.id === "1");
    check("local tag updated", t1.description === "box desc" && j(t1.stash_ids) === j([{ endpoint: EP, stash_id: "SB2" }]), j(t1));
  });

  // ---------------------------------------------------------------------------
  await section("failed update: no mapping; retry reuses the created parent", async () => {
    let fail = true;
    const { tm, mark } = await setup({
      responses: {
        TagUpdate: (body) => (fail
          ? { errors: [{ message: "server said no" }] }
          : { data: { tagUpdate: { id: body.variables.input.id, name: "Blonde", stash_ids: body.variables.input.stash_ids } } }),
      },
    });
    const dlg = openDialog(tm, { nameChoice: "local", remember: true });
    await dlg.click();
    await tm.settle();
    check("error shown", dlg.errorEl.style.display === "block" && /server said no/.test(dlg.errorEl.innerHTML), dlg.errorEl.innerHTML);
    check("no mapping saved", since(tm, mark, "ConfigurePlugin").length === 0 && j(tm.getState().categoryMappings) === "{}");
    check("dialog open, Apply enabled again", dlg.modal.removed === false && dlg.applyBtn.disabled === false);

    fail = false;
    await dlg.click();
    await tm.settle();
    check("parent created only once", since(tm, mark, "TagCreate").length === 1, `got ${since(tm, mark, "TagCreate").length}`);
    const updates = since(tm, mark, "TagUpdate");
    check("retry update uses the created parent", updates.length === 2 && j(updates[1].body.variables.input.parent_ids) === j(["7", "50"]),
      j(updates.map((c) => c.body.variables.input.parent_ids)));
    check("mapping saved after the successful retry", tm.getState().categoryMappings["Hair Color"] === "50");
    check("dialog closed", dlg.modal.removed === true);
  });

  // ---------------------------------------------------------------------------
  await section("apply button disabled while running", async () => {
    let dlg = null;
    let disabledDuringUpdate = null;
    const { tm, mark } = await setup({
      responses: {
        TagUpdate: (body) => {
          disabledDuringUpdate = dlg && dlg.applyBtn.disabled;
          return { data: { tagUpdate: { id: body.variables.input.id, name: "Blonde", stash_ids: body.variables.input.stash_ids } } };
        },
      },
    });
    dlg = openDialog(tm, { nameChoice: "local", remember: false });
    const first = dlg.click();
    check("disabled as soon as Apply is clicked", dlg.applyBtn.disabled === true);
    const second = dlg.click(); // double click
    await Promise.all([first, second]);
    await tm.settle();
    check("disabled while the update ran", disabledDuringUpdate === true);
    check("a double click sends one update", since(tm, mark, "TagUpdate").length === 1, `got ${since(tm, mark, "TagUpdate").length}`);
    check("one parent created", since(tm, mark, "TagCreate").length === 1);
    check("remember off: no mapping written", since(tm, mark, "ConfigurePlugin").length === 0);
    check("dialog closed on success", dlg.modal.removed === true);
  });

  // ---------------------------------------------------------------------------
  await section("leave parents alone: no parent work", async () => {
    const { tm, mark } = await setup();
    tm.setState({ settings: { ...tm.getState().settings, leaveParentTagsAlone: true } });
    const tag = tm.getState().localTags.find((t) => t.id === "1");
    const r = await tm.exports.applyDiff({
      tag, stashdbTag: CLEAN, endpoint: EP, nameChoice: "local", descChoice: "local",
      aliases: [], parentId: "__create__", rememberMapping: true,
    });
    check("ok", r && r.ok === true, j(r));
    check("only the update", j(writesSince(tm, mark)) === j(["TagUpdate"]), j(writesSince(tm, mark)));
    check("no parent_ids", !("parent_ids" in since(tm, mark, "TagUpdate")[0].body.variables.input));
  });

  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log("all apply order tests passed");
})().catch((e) => { console.log("FAIL  uncaught: " + ((e && e.stack) || e)); process.exit(1); });
