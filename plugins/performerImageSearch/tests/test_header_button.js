/**
 * Where the Search Images button goes on a performer page, and that it follows
 * the page (#193, the same bug Missing Scenes fixed in #189).
 * Run with: node plugins/performerImageSearch/tests/test_header_button.js
 */
const assert = require("assert");
const { loadPlugin } = require("./harness");

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
    listeners: {},
    offsetParent: {}, // shown; set to null to hide
    get isConnected() {
      let p = n;
      while (p.parentNode) p = p.parentNode;
      return p.isRoot === true;
    },
    classList: {
      contains: (c) => n.className.split(/\s+/).includes(c),
    },
    addEventListener(type, fn) { (n.listeners[type] = n.listeners[type] || []).push(fn); },
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
function fakeDocument(root, p) {
  root.isRoot = true;
  const has = (n, c) => n.classList && n.classList.contains(c);
  const doc = {
    createElement: (tag) => node(tag),
    querySelectorAll(sel) {
      if (sel === ".pis-search-button") return all(root).filter((n) => has(n, "pis-search-button"));
      throw new Error("unsupported selector " + sel);
    },
    querySelector(sel) {
      if (sel === ".detail-header:not(.sticky)") {
        return all(root).find((n) => has(n, "detail-header") && !has(n, "sticky")) || null;
      }
      throw new Error("unsupported selector " + sel);
    },
  };
  // The button itself comes from the plugin's document; give it our node methods.
  p.document.createElement = (tag) => {
    const n = node(tag);
    Object.defineProperty(n, "innerHTML", { set() {}, get: () => "" });
    Object.defineProperty(n, "id", {
      set(v) { n._id = v; }, get: () => n._id || "",
    });
    return n;
  };
  for (const n of all(root)) {
    n.querySelector = (sel) => {
      if (sel === ".detail-container") return all(n).find((x) => has(x, "detail-container")) || null;
      if (sel === ".performer-head > h2") {
        const head = all(n).find((x) => has(x, "performer-head"));
        return (head && head.children.find((c) => c.tagName === "H2")) || null;
      }
      throw new Error("unsupported selector " + sel);
    };
  }
  return doc;
}

/** Stash's performer header: image, then the head with the name row. */
function stashHeader({ titleShown = true, themePanel = false } = {}) {
  const icons = node("span", "name-icons", [node("button", "favorite-button btn")]);
  const h2 = node("h2", "", [node("span", "performer-name"), icons]);
  if (!titleShown) h2.offsetParent = null;
  const head = node("div", "performer-head col", [h2, node("div", "details-edit")]);
  const image = node("div", "detail-header-image");
  const row = node("div", "row", [head]);
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
  const p = loadPlugin();
  assert.strictEqual(typeof p.exports.placeSearchButton, "function", "placeSearchButton exported");
  return { p, place: p.exports.placeSearchButton };
}

const buttons = (root) => all(root).filter((n) => n.classList.contains("pis-search-button"));

test("the button goes in the name row, beside Stash's icons and not inside them", () => {
  const { p, place } = load();
  const { root, h2, icons } = stashHeader();
  place(fakeDocument(root, p), "/performers/6");
  const [btn] = buttons(root);
  assert.ok(btn, "button placed");
  assert.ok(h2.contains(btn), "in the name row");
  assert.ok(!icons.contains(btn), "not inside .name-icons, which themes hide");
  assert.ok(btn.parentNode.classList.contains("pis-header-actions"), "in our own span");
  assert.strictEqual(btn.dataset.performerId, "6");
});

test("a theme that hides the name row gets the button in its own visible panel", () => {
  const { p, place } = load();
  const { root, panel, h2 } = stashHeader({ titleShown: false, themePanel: true });
  place(fakeDocument(root, p), "/performers/6/scenes");
  const [btn] = buttons(root);
  assert.ok(btn, "button placed");
  assert.ok(!h2.contains(btn), "not in the hidden name row");
  assert.ok(btn.parentNode.parentNode === panel, "in our own row at the end of the visible panel");
});

test("name row hidden and nothing else visible: the button still goes in the header", () => {
  const { p, place } = load();
  const { root, container } = stashHeader({ titleShown: false });
  container.children.forEach((c) => { c.offsetParent = null; });
  place(fakeDocument(root, p), "/performers/6");
  const [btn] = buttons(root);
  assert.ok(btn && btn.isConnected, "button placed");
});

test("placing again on the same page changes nothing", () => {
  const { p, place } = load();
  const { root } = stashHeader();
  const doc = fakeDocument(root, p);
  const first = place(doc, "/performers/6");
  const parent = first.parentNode;
  const second = place(doc, "/performers/6");
  assert.strictEqual(second, first);
  assert.strictEqual(second.parentNode, parent);
  assert.strictEqual(buttons(root).length, 1);
});

test("a button left from another performer is replaced, and clicks open the new one", () => {
  const { p, place } = load();
  const { root } = stashHeader();
  const doc = fakeDocument(root, p);
  const old = place(doc, "/performers/6");
  const now = place(doc, "/performers/7");
  assert.notStrictEqual(now, old);
  assert.strictEqual(old.isConnected, false, "old button removed");
  assert.deepStrictEqual(buttons(root).map((b) => b.dataset.performerId), ["7"]);
  // showModal sets the performer first; the fake DOM may not carry the render after it
  try { now.listeners.click.forEach((fn) => fn()); } catch (e) { /* render only */ }
  assert.strictEqual(p.getState().currentPerformerId, "7", "click opens the current performer");
});

test("leaving the performer page removes the button and its row", () => {
  const { p, place } = load();
  const { root, h2 } = stashHeader();
  const doc = fakeDocument(root, p);
  place(doc, "/performers/6");
  assert.strictEqual(place(doc, "/scenes/1"), null);
  assert.strictEqual(buttons(root).length, 0);
  assert.ok(!h2.children.some((c) => c.classList.contains("pis-header-actions")), "empty row removed");
});

test("the button moves when the name row is hidden after it was placed", () => {
  const { p, place } = load();
  const { root, container, h2 } = stashHeader();
  const doc = fakeDocument(root, p);
  const btn = place(doc, "/performers/6");
  h2.offsetParent = null;
  assert.strictEqual(place(doc, "/performers/6"), btn, "same button, moved");
  assert.ok(btn.parentNode.parentNode === container.children[container.children.length - 1],
    "in the last visible part of the header");
  assert.ok(!h2.children.some((c) => c.classList.contains("pis-header-actions")), "old row removed");
});

test("Stash's compact sticky header is never used", () => {
  const { p, place } = load();
  const compact = node("div", "sticky detail-header", [node("div", "sticky detail-header-group")]);
  const { root, h2 } = stashHeader();
  root.children.unshift(compact);
  compact.parentNode = root;
  place(fakeDocument(root, p), "/performers/6");
  assert.ok(h2.contains(buttons(root)[0]));
});

test("no header yet: nothing placed, nothing thrown", () => {
  const { p, place } = load();
  const root = node("div", "root");
  assert.strictEqual(place(fakeDocument(root, p), "/performers/6"), null);
});

test("performerIdFromPath reads the id under a base path, and nothing else", () => {
  const { p } = load();
  const id = p.exports.performerIdFromPath;
  assert.strictEqual(id("/performers/6"), "6");
  assert.strictEqual(id("/stash/performers/8/scenes"), "8");
  assert.strictEqual(id("/performers"), null);
  assert.strictEqual(id("/performers/new"), null);
  assert.strictEqual(id("/scenes/1"), null);
  assert.strictEqual(id("/studios/6/performers"), null);
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
