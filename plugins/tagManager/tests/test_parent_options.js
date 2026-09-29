/**
 * Parent dropdown options (F4) and parent-control visibility (F19), against the
 * REAL tag-manager.js. Run with: node plugins/tagManager/tests/test_parent_options.js
 */
const { loadTagManager } = require("./harness");
const { buildParentOptions, shouldShowParentControls } = loadTagManager().exports;

let failed = 0;
function check(name, ok, detail) {
  if (ok) console.log(`  ok  ${name}`);
  else { failed++; console.log(`FAIL  ${name}${detail ? "\n      " + detail : ""}`); }
}

const localTags = [
  { id: "10", name: "Action" },
  { id: "20", name: "Pose" },
  { id: "30", name: "Other" },
];
const match = (id, type = "exact") => ({ tag: localTags.find((t) => t.id === id), matchType: type });
const build = (o) => buildParentOptions({
  existingParents: [], parentMatches: [], savedMappingId: null,
  categoryName: "Action", localTags, ...o,
});
const selected = (opts) => opts.filter((o) => o.selected);
const j = (x) => JSON.stringify(x);

// saved mapping that exists
let opts = build({ savedMappingId: "20", parentMatches: [match("10")] });
check("saved option labeled and selected",
  j(opts.find((o) => o.value === "20")) === j({ value: "20", label: "Pose (saved mapping)", selected: true }), j(opts));
check("saved mapping: exactly one selected", selected(opts).length === 1, j(opts));
check("matches still offered alongside saved mapping", opts.some((o) => o.value === "10"), j(opts));

// saved mapping that is also an existing parent and a match: shown once
opts = build({ savedMappingId: "10", existingParents: [{ id: "10", name: "Action" }], parentMatches: [match("10")] });
check("saved tag not duplicated", opts.filter((o) => o.value === "10").length === 1, j(opts));
check("saved tag is the selected one", j(selected(opts).map((o) => o.value)) === j(["10"]), j(opts));

// stale saved mapping
opts = build({ savedMappingId: "999", parentMatches: [match("10")] });
check("stale mapping not offered", !opts.some((o) => o.value === "999"), j(opts));
check("stale mapping: falls back to first match", j(selected(opts).map((o) => o.value)) === j(["10"]), j(opts));

// no saved mapping: today's behavior
opts = build({});
check("no parents/matches: none, create (selected), no trailing create",
  j(opts.map((o) => [o.value, o.selected])) === j([["", false], ["__create__", true]]), j(opts));
opts = build({ existingParents: [{ id: "20", name: "Pose" }, { id: "30", name: "Other" }], parentMatches: [match("10"), match("20")] });
check("existing parents: order and selection",
  j(opts.map((o) => [o.value, o.selected])) ===
  j([["", false], ["20", true], ["30", false], ["10", false], ["__create__", false]]), j(opts));
check("existing parent labeled", opts[1].label === "Pose (current parent)", j(opts));
opts = build({ parentMatches: [match("10", "contains"), match("30", "fuzzy")] });
check("matches only: first match selected, trailing create",
  j(opts.map((o) => [o.value, o.selected])) ===
  j([["", false], ["10", true], ["30", false], ["__create__", false]]), j(opts));
check("match label", opts[1].label === "Action (contains)", j(opts));
check("create label", opts[3].label === 'Create "Action"', j(opts));
check("exactly one selected (matches)", selected(opts).length === 1);

// F19
check("hidden when leaveParentTagsAlone", shouldShowParentControls({ leaveParentTagsAlone: true }, true) === false);
check("hidden without category", shouldShowParentControls({}, false) === false);
check("shown otherwise", shouldShowParentControls({ leaveParentTagsAlone: false }, true) === true);

if (failed) { console.log(`\n${failed} failed`); process.exit(1); }
console.log("\nall passed");
