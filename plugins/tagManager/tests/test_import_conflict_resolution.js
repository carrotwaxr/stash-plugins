/**
 * #125 / F6 / F11: import conflict resolution and the import flag, against the
 * REAL tag-manager.js (via the harness; no mirrored helpers).
 *   - helpers: detectImportConflicts, sanitizeAliasesForImport,
 *     buildMergeIntoExistingInput, summarizeImportResult
 *   - conflict rows (createConflictSession / resolveConflictRow): a failed
 *     reverse merge removes the tag it created; conflicts are re-checked after
 *     every action; strip lists the aliases it dropped; rows whose conflicts are
 *     gone offer Import; replacing a stash_id asks first; one action at a time
 *   - the modal (renderConflictResolutionModal): wiring, result text, Done
 *   - isImporting is reset when handleImportSelected / handleUpdateLinkedTags exit
 * Run with: node plugins/tagManager/tests/test_import_conflict_resolution.js
 */
const { loadTagManager, createElement, createQueryableElement } = require("./harness");

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
const sorted = (a) => [...(a || [])].map(String).sort();
const sameSet = (a, b) => j(sorted(a)) === j(sorted(b));

const EP = "https://stashdb.org/graphql";
const BOX = { endpoint: EP, name: "StashDB" };

async function setup({ localTags = [], responses = {}, confirm } = {}) {
  let nextId = 100;
  const tm = loadTagManager({
    confirm,
    fetchResponses: {
      TagCreate: (body) => {
        const i = body.variables.input;
        return { data: { tagCreate: {
          id: String(nextId++), name: i.name, description: i.description || "",
          aliases: i.aliases || [], stash_ids: i.stash_ids || [],
          parents: (i.parent_ids || []).map((id) => ({ id, name: `P${id}` })),
        } } };
      },
      TagUpdate: (body) => ({ data: { tagUpdate: {
        id: body.variables.input.id, name: "x", stash_ids: body.variables.input.stash_ids || [],
      } } }),
      FindTag: { data: { findTag: { scene_count: 3 } } },
      TagsMerge: (body) => ({ data: { tagsMerge: {
        id: body.variables.input.destination, name: "merged", aliases: ["Barbara", "Bar"], stash_ids: [],
      } } }),
      TagDestroy: { data: { tagDestroy: true } },
      ...responses,
    },
  });
  await tm.settle();
  tm.setState({ localTags: clone(localTags), selectedStashBox: { ...BOX }, stashBoxes: [{ ...BOX }] });
  return { tm, mark: tm.fetchCalls.length };
}
const since = (tm, mark, op) => tm.fetchCalls.slice(mark).filter((c) => c.op === op);
const opsSince = (tm, mark) => tm.fetchCalls.slice(mark).map((c) => c.op).filter(Boolean);
const entryFor = (tm, stashdbTag, parentId = null) =>
  ({ stashdbTag, parentId, conflicts: tm.exports.detectImportConflicts(stashdbTag) });
const localTag = (tm, id) => tm.getState().localTags.find((t) => t.id === id);

const BARBARA = { id: "1", name: "Barbara", aliases: ["Bar"], stash_ids: [], parents: [] };

(async () => {
  // ---------------------------------------------------------------------------
  await section("helpers (real functions)", async () => {
    const { tm } = await setup({ localTags: [{ id: "1", name: "Existing", aliases: [] }] });
    const x = tm.exports;
    check("no conflict when name and aliases are unique",
      j(x.detectImportConflicts({ name: "Fresh", aliases: ["Brand New"] })) === "[]");

    tm.setState({ localTags: [{ id: "1", name: "Barbara", aliases: [] }] });
    let r = x.detectImportConflicts({ name: "barbara", aliases: [] });
    check("name collision (case-insensitive)",
      r.length === 1 && r[0].conflictingValue === "barbara" && r[0].conflictingTag.id === "1", j(r));

    tm.setState({ localTags: [{ id: "1", name: "Barbara", aliases: ["Bar"] }] });
    r = x.detectImportConflicts({ name: "Foo", aliases: ["Bar"] });
    check("alias collision against an existing alias", r.length === 1 && r[0].conflictingValue === "Bar", j(r));

    tm.setState({ localTags: [{ id: "1", name: "Barbara", aliases: ["Bar"] }, { id: "2", name: "Quux", aliases: [] }] });
    r = x.detectImportConflicts({ name: "Quux", aliases: ["Bar"] });
    check("collisions against different tags", r.length === 2 && sameSet(r.map((c) => c.conflictingTag.id), ["1", "2"]), j(r));

    const one = [{ conflictingValue: "Bar", conflictingTag: { id: "1" } }];
    check("strip removes only colliding aliases",
      j(x.sanitizeAliasesForImport({ name: "Foo", aliases: ["Bar", "Keep"] }, one)) === j({ aliases: ["Keep"], removed: ["Bar"] }));
    const two = [...one, { conflictingValue: "Baz", conflictingTag: { id: "2" } }];
    check("strip removes all of several colliding aliases",
      j(x.sanitizeAliasesForImport({ name: "Foo", aliases: ["Bar", "Baz", "Keep"] }, two)) === j({ aliases: ["Keep"], removed: ["Bar", "Baz"] }));
    check("name-only collision strips no alias",
      j(x.sanitizeAliasesForImport({ name: "Foo", aliases: ["Keep"] }, [{ conflictingValue: "Foo", conflictingTag: { id: "1" } }]))
        === j({ aliases: ["Keep"], removed: [] }));

    const existing = { id: "1", name: "Barbara", aliases: ["Bar"], stash_ids: [], parents: [] };
    let input = x.buildMergeIntoExistingInput(existing, { name: "Foo", aliases: ["Bar", "Fooey"] },
      [{ conflictingValue: "Bar", conflictingTag: existing }], EP, "sbid1", null);
    check("merge-into adds name + non-conflicting aliases, links the stash id",
      j(input.aliases) === j(["Bar", "Foo", "Fooey"]) && j(input.stash_ids) === j([{ endpoint: EP, stash_id: "sbid1" }])
        && !("parent_ids" in input), j(input));
    const linked = { id: "1", name: "Barbara", aliases: [], stash_ids: [{ endpoint: EP, stash_id: "OLD" }], parents: [] };
    input = x.buildMergeIntoExistingInput(linked, { name: "Barbara", aliases: [] },
      [{ conflictingValue: "Barbara", conflictingTag: linked }], EP, "NEW", "p9");
    check("merge-into replaces this endpoint's stash id and adds the missing parent",
      j(input.stash_ids) === j([{ endpoint: EP, stash_id: "NEW" }]) && j(input.parent_ids) === j(["p9"]) && j(input.aliases) === "[]",
      j(input));

    check("summary phrasing",
      x.summarizeImportResult({ created: 12, linked: 4, parented: 0, categories: 0, conflicts: 3, skipped: 1, errors: 0 })
        === "Created 12 tags, linked 4 existing, 3 conflicts resolved, 1 skipped");
    check("summary singulars",
      x.summarizeImportResult({ created: 1, linked: 0, parented: 0, categories: 0, conflicts: 1, skipped: 0, errors: 1 })
        === "Created 1 tag, 1 conflict resolved, 1 error");
    check("summary empty",
      x.summarizeImportResult({ created: 0, linked: 0, parented: 0, categories: 0, conflicts: 0, skipped: 0, errors: 0 }) === "No changes");
    check("summary parented phrasing",
      x.summarizeImportResult({ created: 0, linked: 0, parented: 2, categories: 1, conflicts: 0, skipped: 0, errors: 0 })
        === "set parents for 2 (1 category)");
  });

  // ---------------------------------------------------------------------------
  await section("reverse merge failure removes created tag", async () => {
    let mergeFails = true;
    const { tm, mark } = await setup({
      localTags: [BARBARA],
      responses: {
        TagsMerge: (body) => (mergeFails
          ? { errors: [{ message: "merge exploded" }] }
          : { data: { tagsMerge: { id: body.variables.input.destination, name: "Foo", aliases: ["Keep", "Barbara", "Bar"], stash_ids: [] } } }),
      },
    });
    const x = tm.exports;
    const foo = { id: "sb-foo", name: "Foo", description: "d", aliases: ["Bar", "Keep"] };
    const session = x.createConflictSession([entryFor(tm, foo)], EP);
    const r = await x.resolveConflictRow(session, 0, "reverse", "1");
    check("result is a failure", r && r.ok === false, j(r));
    const creates = since(tm, mark, "TagCreate");
    check("the tag was created once", creates.length === 1, `got ${creates.length}`);
    const destroys = since(tm, mark, "TagDestroy");
    check("a TagDestroy was sent for the new tag id",
      destroys.length === 1 && destroys[0].body.variables.input.id === "100", j(destroys.map((c) => c.body.variables)));
    check("destroy came after the failed merge", j(opsSince(tm, mark).filter((o) => /^Tag/.test(o))) === j(["TagCreate", "TagsMerge", "TagDestroy"]),
      j(opsSince(tm, mark)));
    const row = session.rows.get(0);
    check("row is still pending", row && !row.done, j(row && { done: row.done }));
    check("session is not stuck busy", session.busy === false);
    check("row error names the failure and the rollback",
      /merge exploded/.test(row.error) && /removed/i.test(row.error), row.error);
    const html = x.conflictRowHtml(row);
    check("row still offers the reverse merge", html.includes("tm-conflict-reverse") && html.includes('data-tag="1"'), html);
    check("localTags unchanged", j(tm.getState().localTags.map((t) => t.id)) === j(["1"]));
    check("outcome unchanged", j(session.outcome) === j({ created: 0, linked: 0, skipped: 0, resolved: 0 }), j(session.outcome));

    // Retry works: no name collision with a leftover tag.
    mergeFails = false;
    const r2 = await x.resolveConflictRow(session, 0, "reverse", "1");
    check("retry succeeds", r2 && r2.ok === true, j(r2));
    check("row done", session.rows.get(0).done === true);
    check("existing tag gone, new tag present", !localTag(tm, "1") && !!localTag(tm, "101"), j(tm.getState().localTags));
    check("counted as created + resolved", session.outcome.created === 1 && session.outcome.resolved === 1, j(session.outcome));
  });

  // ---------------------------------------------------------------------------
  await section("reverse merge: rollback failure is reported and tracked", async () => {
    const { tm } = await setup({
      localTags: [BARBARA],
      responses: {
        TagsMerge: { errors: [{ message: "merge exploded" }] },
        TagDestroy: { errors: [{ message: "destroy exploded" }] },
      },
    });
    const x = tm.exports;
    const session = x.createConflictSession([entryFor(tm, { id: "sb-foo", name: "Foo", aliases: ["Bar"] })], EP);
    const r = await x.resolveConflictRow(session, 0, "reverse", "1");
    const row = session.rows.get(0);
    check("failure", r.ok === false && !row.done);
    check("error says the new tag is left over and must be deleted",
      /destroy exploded/.test(row.error) && /delete/i.test(row.error) && row.error.includes("Foo"), row.error);
    check("left-over tag tracked in localTags", !!localTag(tm, "100"), j(tm.getState().localTags));
    const html = x.conflictRowHtml(row);
    check("row now offers merging into the left-over tag", html.includes("tm-conflict-merge-into") && html.includes('data-tag="100"'), html);
  });

  // ---------------------------------------------------------------------------
  await section("conflicts re-checked after each action", async () => {
    const { tm, mark } = await setup({ localTags: [BARBARA] });
    const x = tm.exports;
    const a = { id: "sa", name: "Foo", aliases: ["Bar"] };
    const b = { id: "sb", name: "Qux", aliases: ["Bar", "Foo", "Keep"] };
    const session = x.createConflictSession([entryFor(tm, a), entryFor(tm, b)], EP);
    check("B starts with one conflict", j(session.rows.get(1).conflicts.map((c) => c.conflictingValue)) === j(["Bar"]));

    const r = await x.resolveConflictRow(session, 0, "merge-into", "1");
    check("A merged into Barbara", r.ok === true && session.rows.get(0).done, j(r));
    check("Barbara gained A's name as an alias", sameSet(localTag(tm, "1").aliases, ["Bar", "Foo"]), j(localTag(tm, "1")));

    const bRow = session.rows.get(1);
    check("B's conflicts recomputed (Foo now clashes too)",
      sameSet(bRow.conflicts.map((c) => c.conflictingValue), ["Bar", "Foo"]), j(bRow.conflicts.map((c) => c.conflictingValue)));
    check("B's conflicting tag is the fresh localTags object",
      bRow.conflicts.every((c) => c.conflictingTag === localTag(tm, "1")));
    check("B's row text shows the new clash", x.conflictRowHtml(bRow).includes("Foo"), x.conflictRowHtml(bRow));

    // B's strip now drops both clashing aliases, so the create doesn't hit a server conflict.
    const r2 = await x.resolveConflictRow(session, 1, "strip");
    check("B strip ok", r2.ok === true, j(r2));
    const creates = since(tm, mark, "TagCreate");
    check("B created with only the non-clashing alias",
      creates.length === 1 && j(creates[0].body.variables.input.aliases) === j(["Keep"]), j(creates.map((c) => c.body.variables.input)));
  });

  // ---------------------------------------------------------------------------
  await section("strip reports dropped aliases", async () => {
    const { tm, mark } = await setup({
      localTags: [BARBARA, { id: "2", name: "Bazza", aliases: ["Baz"], stash_ids: [], parents: [] }],
    });
    const x = tm.exports;
    const foo = { id: "sb-foo", name: "Foo", description: "d", aliases: ["Bar", "Baz", "Keep"] };
    const session = x.createConflictSession([entryFor(tm, foo, "p9")], EP);
    const r = await x.resolveConflictRow(session, 0, "strip");
    check("ok", r.ok === true, j(r));
    const row = session.rows.get(0);
    check("result text lists the dropped aliases", /Bar/.test(row.result) && /Baz/.test(row.result) && !/Keep/.test(row.result), row.result);
    check("result text names who owns them", /Barbara/.test(row.result) && /Bazza/.test(row.result), row.result);
    const html = x.conflictRowHtml(row);
    check("rendered row shows the result", html.includes("tm-conflict-result") && html.includes("Bar") && html.includes("Baz"), html);
    check("rendered row has no action buttons", !html.includes("<button"), html);
    const input = since(tm, mark, "TagCreate")[0].body.variables.input;
    check("created with kept aliases, stash id and parent",
      j(input.aliases) === j(["Keep"]) && j(input.stash_ids) === j([{ endpoint: EP, stash_id: "sb-foo" }]) && j(input.parent_ids) === j(["p9"]),
      j(input));
    const created = localTag(tm, "100");
    check("full tag object pushed to localTags",
      created && created.name === "Foo" && j(created.aliases) === j(["Keep"]) && Array.isArray(created.parents)
        && j(created.stash_ids) === j([{ endpoint: EP, stash_id: "sb-foo" }]) && typeof created.description === "string",
      j(created));
    check("counted", session.outcome.created === 1 && session.outcome.resolved === 1, j(session.outcome));
  });

  // ---------------------------------------------------------------------------
  await section("rows whose conflicts disappeared become importable", async () => {
    const { tm, mark } = await setup({
      localTags: [BARBARA, { id: "2", name: "Other", aliases: ["Oth"], stash_ids: [], parents: [] }],
    });
    const x = tm.exports;
    const a = { id: "sa", name: "Alpha", aliases: ["Oth"] };
    const b = { id: "sb", name: "Beta", description: "bd", aliases: ["Bar", "B2"] };
    const session = x.createConflictSession([entryFor(tm, a), entryFor(tm, b, "p5")], EP);
    // Barbara loses the clashing alias (e.g. edited via its "Open" link).
    localTag(tm, "1").aliases = [];
    await x.resolveConflictRow(session, 0, "skip");
    check("skip counted", session.outcome.skipped === 1, j(session.outcome));
    const bRow = session.rows.get(1);
    check("B has no conflicts left", bRow.conflicts.length === 0, j(bRow.conflicts));
    const html = x.conflictRowHtml(bRow);
    check("B offers Import instead of the conflict actions",
      html.includes("tm-conflict-import") && !html.includes("tm-conflict-merge-into") && !html.includes("tm-conflict-strip"), html);
    check("B can still be skipped", html.includes("tm-conflict-skip"), html);
    const r = await x.resolveConflictRow(session, 1, "import");
    check("import ok", r.ok === true && bRow.done, j(r));
    const input = since(tm, mark, "TagCreate")[0].body.variables.input;
    check("imported with ALL original aliases, stash id and parent",
      input.name === "Beta" && j(input.aliases) === j(["Bar", "B2"]) && input.description === "bd"
        && j(input.stash_ids) === j([{ endpoint: EP, stash_id: "sb" }]) && j(input.parent_ids) === j(["p5"]), j(input));
    check("counted as created", session.outcome.created === 1 && session.outcome.resolved === 1, j(session.outcome));
  });

  // ---------------------------------------------------------------------------
  await section("merge-into asks before replacing an existing stash id", async () => {
    let answer = false;
    const { tm, mark } = await setup({
      localTags: [{ ...BARBARA, stash_ids: [{ endpoint: EP, stash_id: "OLD-ID" }, { endpoint: "https://x", stash_id: "x1" }] }],
      confirm: () => answer,
    });
    const x = tm.exports;
    const session = x.createConflictSession([entryFor(tm, { id: "NEW-ID", name: "Foo", aliases: ["Bar"] })], EP);
    const r = await x.resolveConflictRow(session, 0, "merge-into", "1");
    check("confirm asked once", tm.confirmCalls.length === 1, j(tm.confirmCalls));
    const msg = tm.confirmCalls[0] || "";
    check("confirm names the existing and the new stash id", msg.includes("OLD-ID") && msg.includes("NEW-ID") && msg.includes("Barbara"), msg);
    check("cancel: no mutation", since(tm, mark, "TagUpdate").length === 0);
    check("cancel: row still pending, no error", r.ok === false && r.cancelled === true && !session.rows.get(0).done && !session.rows.get(0).error, j(r));

    answer = true;
    const r2 = await x.resolveConflictRow(session, 0, "merge-into", "1");
    check("accept: ok", r2.ok === true, j(r2));
    const u = since(tm, mark, "TagUpdate")[0];
    check("accept: this endpoint's id replaced, others kept",
      u && sameSet(u.body.variables.input.stash_ids.map((s) => `${s.endpoint}|${s.stash_id}`), ["https://x|x1", `${EP}|NEW-ID`]),
      u && j(u.body.variables.input.stash_ids));

    // Same id already linked: nothing to replace, no prompt.
    const { tm: tm2 } = await setup({ localTags: [{ ...BARBARA, stash_ids: [{ endpoint: EP, stash_id: "SAME" }] }] });
    const s2 = tm2.exports.createConflictSession([entryFor(tm2, { id: "SAME", name: "Foo", aliases: ["Bar"] })], EP);
    await tm2.exports.resolveConflictRow(s2, 0, "merge-into", "1");
    check("same stash id: no confirm", tm2.confirmCalls.length === 0, j(tm2.confirmCalls));
  });

  // ---------------------------------------------------------------------------
  await section("one row action at a time", async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const { tm, mark } = await setup({
      localTags: [BARBARA],
      responses: {
        TagUpdate: async (body) => {
          await gate;
          return { data: { tagUpdate: { id: body.variables.input.id, name: "x", stash_ids: body.variables.input.stash_ids } } };
        },
      },
    });
    const x = tm.exports;
    const session = x.createConflictSession([
      entryFor(tm, { id: "sa", name: "Foo", aliases: ["Bar"] }),
      entryFor(tm, { id: "sb", name: "Qux", aliases: ["Bar"] }),
    ], EP);
    const p1 = x.resolveConflictRow(session, 0, "merge-into", "1");
    await tm.settle();
    const r2 = await x.resolveConflictRow(session, 1, "merge-into", "1");
    check("second action refused while the first runs", r2.ok === false && r2.busy === true, j(r2));
    release();
    await p1;
    check("only the first action wrote", since(tm, mark, "TagUpdate").length === 1);
    check("second row untouched", !session.rows.get(1).done && !session.rows.get(1).error, j(session.rows.get(1)));
  });

  // ---------------------------------------------------------------------------
  await section("strip/reverse refused on a name conflict", async () => {
    const { tm, mark } = await setup({ localTags: [BARBARA] });
    const x = tm.exports;
    const session = x.createConflictSession([entryFor(tm, { id: "sa", name: "Barbara", aliases: [] })], EP);
    const html = x.conflictRowHtml(session.rows.get(0));
    check("no strip/reverse buttons", !html.includes("tm-conflict-strip") && !html.includes("tm-conflict-reverse"), html);
    const r = await x.resolveConflictRow(session, 0, "strip");
    check("strip refused", r.ok === false && since(tm, mark, "TagCreate").length === 0, j(r));
  });

  // ---------------------------------------------------------------------------
  await section("modal: wiring, result text, stays open until Done", async () => {
    const { tm, mark } = await setup({ localTags: [BARBARA] });
    const x = tm.exports;
    // Buttons rendered into an element's innerHTML, as stubs with dataset (memoized per HTML).
    const queryAll = (sel, el) => {
      const m = /^\.([\w-]+)$/.exec(sel);
      if (!m) return undefined;
      el._btns = el._btns || new Map();
      const key = sel + "\u0000" + el.innerHTML;
      if (!el._btns.has(key)) {
        const out = [];
        for (const b of String(el.innerHTML).matchAll(/<button\b([^>]*)>/g)) {
          const cls = /class="([^"]*)"/.exec(b[1]);
          if (!cls || !cls[1].split(/\s+/).includes(m[1])) continue;
          const btn = createElement("button");
          for (const d of b[1].matchAll(/data-([\w-]+)="([^"]*)"/g)) btn.dataset[d[1]] = d[2];
          out.push(btn);
        }
        el._btns.set(key, out);
      }
      return el._btns.get(key);
    };
    tm.document.createElement = (t) => createQueryableElement(t, { queryAll });
    const done = x.renderConflictResolutionModal([
      entryFor(tm, { id: "sa", name: "Foo", aliases: ["Bar"] }),
      entryFor(tm, { id: "sb", name: "Qux", aliases: ["Bar", "Foo"] }),
    ], EP);
    let resolved = null;
    done.then((o) => { resolved = o; });
    const backdrop = tm.document.body.children[tm.document.body.children.length - 1];
    const listEl = backdrop.querySelector(".tm-conflict-list");
    const footerBtn = backdrop.querySelector("#tm-conflict-skip-all");
    const click = async (cls, i, tag) => {
      const btn = listEl.querySelectorAll("." + cls).find((b) => b.dataset.i === String(i) && (tag === undefined || b.dataset.tag === tag));
      if (!btn || !btn.listeners.click) throw new Error(`no ${cls} button for row ${i}`);
      await btn.listeners.click[0]();
      await tm.settle();
    };
    check("both rows rendered", listEl.innerHTML.includes("Foo") && listEl.innerHTML.includes("Qux"));

    await click("tm-conflict-merge-into", 0, "1");
    check("merge sent", since(tm, mark, "TagUpdate").length === 1);
    check("row 0 shows its result", /tm-conflict-result[^>]*>[^<]*Barbara/.test(listEl.innerHTML), listEl.innerHTML);
    check("row 1 re-rendered with the new clash", /Qux[\s\S]*Foo/.test(listEl.innerHTML), listEl.innerHTML);
    check("footer still offers skipping", footerBtn.textContent === "Skip all remaining", footerBtn.textContent);

    await click("tm-conflict-skip", 1);
    check("modal stays open after the last row", backdrop.removed === false && resolved === null);
    check("footer turns into Done", footerBtn.textContent === "Done", footerBtn.textContent);

    footerBtn.listeners.click[0]();
    await tm.settle();
    check("closed", backdrop.removed === true);
    check("outcome", j(resolved) === j({ created: 0, linked: 1, skipped: 1, resolved: 1 }), j(resolved));
  });

  // ---------------------------------------------------------------------------
  await section("modal: no stash-box endpoint", async () => {
    const { tm } = await setup({ localTags: [BARBARA] });
    tm.setState({ selectedStashBox: null });
    const before = tm.document.body.children.length;
    let outcome = null;
    let threw = null;
    try {
      outcome = await tm.exports.renderConflictResolutionModal([entryFor(tm, { id: "sa", name: "Foo", aliases: ["Bar"] })]);
    } catch (e) { threw = e; }
    check("does not throw", threw === null, threw && threw.message);
    check("everything counted as skipped", outcome && outcome.skipped === 1 && outcome.resolved === 0, j(outcome));
    check("no modal opened", tm.document.body.children.length === before);
  });

  // ---------------------------------------------------------------------------
  await section("isImporting reset on throw", async () => {
    const { tm } = await setup({ localTags: [] });
    tm.setState({
      stashdbTags: null,
      selectedForImport: new Set(["s1"]),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    const container = createQueryableElement("div");
    let threw = null;
    try { await tm.exports.handleImportSelected(container); } catch (e) { threw = e; }
    check("handler does not reject", threw === null, threw && threw.message);
    check("isImporting is false", tm.getState().isImporting === false);
    check("import button re-enabled (selection kept)", container.querySelector("#tm-import-selected").disabled === false);
    check("status says the import failed", /fail/i.test(container.querySelector(".tm-selection-info").textContent),
      container.querySelector(".tm-selection-info").textContent);

    // Same with category parents on (the throw happens before the category modal).
    tm.setState({ settings: { ...tm.getState().settings, leaveParentTagsAlone: false } });
    threw = null;
    try { await tm.exports.handleImportSelected(container); } catch (e) { threw = e; }
    check("parents on: handler does not reject", threw === null, threw && threw.message);
    check("parents on: isImporting is false", tm.getState().isImporting === false);
  });

  // ---------------------------------------------------------------------------
  await section("isImporting reset without a selected endpoint", async () => {
    const { tm } = await setup({ localTags: [] });
    tm.setState({
      selectedStashBox: null,
      stashdbTags: [{ id: "s1", name: "New", aliases: [] }],
      selectedForImport: new Set(["s1"]),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    const container = createQueryableElement("div");
    let threw = null;
    try { await tm.exports.handleImportSelected(container); } catch (e) { threw = e; }
    check("handler does not reject", threw === null, threw && threw.message);
    check("isImporting is false", tm.getState().isImporting === false);
    check("nothing created", tm.fetchCalls.every((c) => c.op !== "TagCreate"));
  });

  // ---------------------------------------------------------------------------
  await section("isImporting reset as the import returns (not after the 1.5s timer)", async () => {
    const { tm, mark } = await setup({ localTags: [] });
    tm.setState({
      stashdbTags: [{ id: "s1", name: "New", aliases: [] }],
      selectedForImport: new Set(["s1"]),
      settings: { ...tm.getState().settings, leaveParentTagsAlone: true },
    });
    const container = createQueryableElement("div");
    await tm.exports.handleImportSelected(container);
    check("tag created", since(tm, mark, "TagCreate").length === 1);
    check("isImporting false before timers run", tm.getState().isImporting === false);
    check("summary shown", /Created 1 tag/.test(container.querySelector(".tm-selection-info").textContent),
      container.querySelector(".tm-selection-info").textContent);
    check("button disabled (selection cleared)", container.querySelector("#tm-import-selected").disabled === true);
  });

  // ---------------------------------------------------------------------------
  await section("handleUpdateLinkedTags resets isImporting and its button", async () => {
    const { tm, mark } = await setup({
      localTags: [{ id: "1", name: "Linked", description: "", aliases: [], stash_ids: [{ endpoint: EP, stash_id: "s1" }], parents: [] }],
    });
    tm.setState({ stashdbTags: [{ id: "s1", name: "Linked", description: "from box", aliases: ["L2"] }] });
    const container = createQueryableElement("div");
    let disabledDuring = null;
    const btn = container.querySelector("#tm-update-linked");
    const p = tm.exports.handleUpdateLinkedTags(container);
    disabledDuring = btn.disabled;
    await p;
    check("update sent", since(tm, mark, "TagUpdate").length === 1);
    check("button disabled while running", disabledDuring === true);
    check("isImporting false before timers run", tm.getState().isImporting === false);
    check("button re-enabled", btn.disabled === false);
  });

  if (failed) { console.log(`${failed} failed`); process.exit(1); }
  console.log("all import conflict resolution tests passed");
})().catch((e) => { console.log("FAIL  uncaught: " + ((e && e.stack) || e)); process.exit(1); });
