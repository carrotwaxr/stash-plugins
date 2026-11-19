/**
 * Where the Missing Scenes header button goes, and that it follows the page (#189).
 * Run with: node plugins/missingScenes/tests/test_header_button.js
 */
const assert = require("assert");
const { loadMissingScenes } = require("./harness");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** A tiny DOM tree: enough for the placement code (children, classes, parent links). */
function node(tag, className = "", kids = []) {
  const n = {
    tagName: tag.toUpperCase(),
    className,
    dataset: {},
    children: [],
    parentNode: null,
    offsetParent: {}, // shown; set to null to hide
    get isConnected() {
      let p = n;
      while (p.parentNode) p = p.parentNode;
      return p.isRoot === true;
    },
    classList: {
      contains: (c) => n.className.split(/\s+/).includes(c),
    },
    appendChild(child) {
      if (child.parentNode) child.remove();
      child.parentNode = n;
      n.children.push(child);
      return child;
    },
    remove() {
      if (!n.parentNode) return;
      const siblings = n.parentNode.children;
      siblings.splice(siblings.indexOf(n), 1);
      n.parentNode = null;
    },
    contains(other) {
      for (let p = other; p; p = p.parentNode) if (p === n) return true;
      return false;
    },
  };
  kids.forEach((k) => n.appendChild(k));
  return n;
}

function all(root) {
  return [root, ...root.children.flatMap(all)];
}

/**
 * A fake document over `root`. querySelector understands the few selectors the
 * placement code uses; anything else throws so a new selector can't slip by untested.
 */
function fakeDocument(root, ms) {
  root.isRoot = true;
  const has = (n, c) => n.classList && n.classList.contains(c);
  const doc = {
    createElement: (tag) => node(tag),
    querySelectorAll(sel) {
      if (sel === ".ms-search-button") return all(root).filter((n) => has(n, "ms-search-button"));
      throw new Error("unsupported selector " + sel);
    },
    querySelector(sel) {
      if (sel === ".detail-header:not(.sticky)") {
        return all(root).find((n) => has(n, "detail-header") && !has(n, "sticky")) || null;
      }
      throw new Error("unsupported selector " + sel);
    },
  };
  // The button itself comes from the plugin; give it our node methods.
  const orig = ms.document.createElement;
  ms.document.createElement = (tag) => {
    const real = orig(tag);
    const n = node(tag);
    Object.defineProperty(n, "innerHTML", { set() {}, get: () => "" });
    n.type = real.type;
    return n;
  };
  // Header lookups go through the header element; answer them from the tree too.
  for (const n of all(root)) {
    n.querySelector = (sel) => {
      if (sel === ".detail-container") return all(n).find((x) => has(x, "detail-container")) || null;
      const m = sel.match(/^\.([\w-]+)-head > h2$/);
      if (!m) throw new Error("unsupported selector " + sel);
      const head = all(n).find((x) => has(x, `${m[1]}-head`));
      return (head && head.children.find((c) => c.tagName === "H2")) || null;
    };
  }
  return doc;
}

/** Stash's detail header for an entity type: image, then the head with the name row. */
function stashHeader(type, { titleShown = true, themePanel = false } = {}) {
  const icons = node("span", "name-icons", [node("button", "favorite-button")]);
  const h2 = node("h2", "", [node("span", `${type}-name`), icons]);
  if (!titleShown) h2.offsetParent = null;
  const head = node("div", `${type}-head col`, [h2, node("div", "details-edit")]);
  const image = node("div", "detail-header-image");
  const row = node("div", "row", [head]);
  // A theme's own panel first, Stash's parts hidden after it (Sticky's tag page)
  const panel = themePanel ? node("div", "theme-panel") : null;
  if (panel) {
    image.offsetParent = null;
    row.offsetParent = null;
  }
  const container = node("div", "detail-container", panel ? [panel, image, row] : [image, row]);
  const header = node("div", "detail-header full-width", [container]);
  const root = node("div", "root", [header]);
  return { root, header, container, panel, h2, icons };
}

function load() {
  const ms = loadMissingScenes();
  return { ms, place: ms.exports.modal.placeSearchButton };
}

const buttons = (root) => all(root).filter((n) => n.classList.contains("ms-search-button"));

test("the button goes in the name row, beside Stash's icons and not inside them", () => {
  const { ms, place } = load();
  const { root, h2, icons } = stashHeader("performer");
  place(fakeDocument(root, ms), "/performers/6");
  const [btn] = buttons(root);
  assert.ok(btn, "button placed");
  assert.ok(h2.contains(btn), "in the name row");
  assert.ok(!icons.contains(btn), "not inside .name-icons, which themes hide");
  assert.strictEqual(btn.dataset.entity, "performer:6");
});

test("a theme that hides Stash's header gets the button in its own visible panel", () => {
  const { ms, place } = load();
  const { root, panel, h2 } = stashHeader("tag", { titleShown: false, themePanel: true });
  place(fakeDocument(root, ms), "/tags/11/scenes");
  const [btn] = buttons(root);
  assert.ok(btn, "button placed");
  assert.ok(!h2.contains(btn), "not in the hidden name row");
  assert.ok(btn.parentNode.parentNode === panel, "in our own row at the end of the visible panel");
});

test("name row hidden and nothing else visible: the button still goes in the header", () => {
  const { ms, place } = load();
  const { root, container } = stashHeader("tag", { titleShown: false });
  container.children.forEach((c) => { c.offsetParent = null; });
  place(fakeDocument(root, ms), "/tags/11");
  const [btn] = buttons(root);
  assert.ok(btn && btn.isConnected, "button placed");
});

test("placing again on the same page changes nothing", () => {
  const { ms, place } = load();
  const { root } = stashHeader("studio");
  const doc = fakeDocument(root, ms);
  const first = place(doc, "/studios/8");
  const parent = first.parentNode;
  const second = place(doc, "/studios/8");
  assert.strictEqual(second, first);
  assert.strictEqual(second.parentNode, parent);
  assert.strictEqual(buttons(root).length, 1);
});

test("a button left from another performer is replaced", () => {
  const { ms, place } = load();
  const { root } = stashHeader("performer");
  const doc = fakeDocument(root, ms);
  const old = place(doc, "/performers/6");
  const now = place(doc, "/performers/7");
  assert.notStrictEqual(now, old);
  assert.strictEqual(old.isConnected, false, "old button removed");
  assert.deepStrictEqual(buttons(root).map((b) => b.dataset.entity), ["performer:7"]);
});

test("leaving for a page without the feature removes the button and its row", () => {
  const { ms, place } = load();
  const { root, h2 } = stashHeader("performer");
  const doc = fakeDocument(root, ms);
  place(doc, "/performers/6");
  assert.strictEqual(place(doc, "/scenes/1"), null);
  assert.strictEqual(buttons(root).length, 0);
  assert.ok(!h2.children.some((c) => c.classList.contains("ms-header-actions")), "empty row removed");
});

test("the button moves when the name row is hidden after it was placed", () => {
  const { ms, place } = load();
  const { root, container, h2 } = stashHeader("tag");
  const doc = fakeDocument(root, ms);
  const btn = place(doc, "/tags/11");
  h2.offsetParent = null;
  assert.strictEqual(place(doc, "/tags/11"), btn, "same button, moved");
  assert.ok(btn.parentNode.parentNode === container.children[container.children.length - 1],
    "in the last visible part of the header");
  assert.ok(!h2.children.some((c) => c.classList.contains("ms-header-actions")), "old row removed");
});

test("Stash's compact sticky header is never used", () => {
  const { ms, place } = load();
  const compact = node("div", "sticky detail-header", [node("div", "sticky detail-header-group")]);
  const { root, h2 } = stashHeader("performer");
  root.children.unshift(compact);
  compact.parentNode = root;
  place(fakeDocument(root, ms), "/performers/6");
  assert.ok(h2.contains(buttons(root)[0]));
});

test("no header yet: nothing placed, nothing thrown", () => {
  const { ms, place } = load();
  const root = node("div", "root");
  assert.strictEqual(place(fakeDocument(root, ms), "/performers/6"), null);
});

test("pageEntity reads the entity from the path, base path or not", () => {
  const { ms } = load();
  const pe = ms.exports.modal.pageEntity;
  assert.deepStrictEqual({ ...pe("/performers/6") }, { type: "performer", id: "6" });
  assert.deepStrictEqual({ ...pe("/stash/studios/8/scenes") }, { type: "studio", id: "8" });
  assert.deepStrictEqual({ ...pe("/tags/11") }, { type: "tag", id: "11" });
  assert.strictEqual(pe("/scenes/1"), null);
  assert.strictEqual(pe("/performers"), null);
  assert.strictEqual(pe("/performers/new"), null);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log("ok -", name);
    } catch (e) {
      failed++;
      console.log("FAIL -", name, "\n   ", e.message);
    }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
