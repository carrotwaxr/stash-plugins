/**
 * Test harness that runs the REAL missingScenes UI files inside one node `vm`
 * context with minimal browser/Stash stubs. Not a test itself (name does not
 * match test_*.js).
 *
 *   const { loadMissingScenes } = require("./harness");
 *   const ms = loadMissingScenes({ fetchResponses: { SomeOp: {...} } });
 *   await ms.settle();
 *   ms.exports.core.escapeHtml("<")   // also .modal and .browse
 *
 * Files load in the order missingScenes.yml lists them (FILES). `exports` is
 * window.__MISSING_SCENES_TEST__, filled by the hooks at the end of each file
 * ({ core, modal, browse }).
 *
 * fetch answers by GraphQL operation name (fetchResponses[op], a value or a
 * function of the parsed body) else { data: {} }; every call is in `fetchCalls`.
 * Timers are queued, never real: flushTimers() runs them, pendingTimers() lists them.
 * Elements from document.createElement / getElementById are inert stubs;
 * override `ms.document.getElementById` in a test to supply real ones.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PLUGIN_DIR = path.join(__dirname, "..");
const FILES = ["missing-scenes-core.js", "missing-scenes.js", "missing-scenes-browse.js"];

/** Minimal DOM element stub. Extend as later tests need more. */
function createElement(tagName) {
  const el = {
    tagName: String(tagName).toUpperCase(),
    style: {},
    dataset: {},
    children: [],
    attributes: {},
    innerHTML: "",
    textContent: "",
    className: "",
    disabled: false,
    listeners: {},
    appendChild(child) { el.children.push(child); return child; },
    remove() {},
    setAttribute(k, v) { el.attributes[k] = String(v); },
    getAttribute(k) { return k in el.attributes ? el.attributes[k] : null; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      el.listeners[type] = (el.listeners[type] || []).filter((f) => f !== fn);
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const classes = new Set();
  el.classList = {
    add: (...c) => c.forEach((x) => classes.add(x)),
    remove: (...c) => c.forEach((x) => classes.delete(x)),
    contains: (c) => classes.has(c),
    toggle: (c, force) => {
      const on = force === undefined ? !classes.has(c) : !!force;
      if (on) classes.add(c); else classes.delete(c);
      return on;
    },
  };
  return el;
}

function loadMissingScenes({ fetchResponses = {}, base = "/", pathname = "/" } = {}) {
  // ---- fetch stub ----
  const fetchCalls = [];
  const respond = (json) => Promise.resolve({
    ok: true, status: 200, json: async () => json, text: async () => JSON.stringify(json),
  });
  function fetch(url, opts = {}) {
    let body = null;
    let op = null;
    if (opts.body) {
      try { body = JSON.parse(opts.body); } catch (e) { body = null; }
      const q = body && body.query;
      const m = q && q.match(/(?:query|mutation)\s+(\w+)/);
      op = m ? m[1] : null;
    }
    fetchCalls.push({ url: String(url), op, body, opts });
    const handler = op !== null ? fetchResponses[op] : undefined;
    if (handler === undefined) return respond({ data: {} });
    return respond(typeof handler === "function" ? handler(body) : handler);
  }

  // ---- controllable timers ----
  let timerQueue = [];
  let timerId = 0;
  const setTimeoutStub = (fn, ms) => { timerQueue.push({ id: ++timerId, fn, ms }); return timerId; };
  const clearTimeoutStub = (id) => { timerQueue = timerQueue.filter((t) => t.id !== id); };
  const pendingTimers = () => timerQueue.map((t) => ({ ms: t.ms }));
  function flushTimers() {
    let guard = 0;
    while (timerQueue.length && guard++ < 1000) timerQueue.shift().fn();
  }

  // ---- document / window ----
  const baseEl = { getAttribute: (k) => (k === "href" ? base : null) };
  const documentListeners = {};
  const document = {
    title: "",
    readyState: "complete",
    body: createElement("body"),
    querySelector: (sel) => (sel === "base" ? baseEl : null),
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement,
    addEventListener(type, fn) { (documentListeners[type] = documentListeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      documentListeners[type] = (documentListeners[type] || []).filter((f) => f !== fn);
    },
    listeners: documentListeners,
  };

  const u = new URL(pathname, "http://localhost");
  const location = { origin: u.origin, pathname: u.pathname, search: u.search, hash: u.hash, href: u.href };
  const windowListeners = {};
  const openCalls = [];
  const window = {
    __MISSING_SCENES_TEST__: {},
    location,
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
    removeEventListener() {},
    open: (...args) => { openCalls.push(args); },
    listeners: windowListeners,
  };

  const mutationObservers = [];
  class MutationObserver {
    constructor(cb) { this.cb = cb; this.observing = false; mutationObservers.push(this); }
    observe() { this.observing = true; }
    disconnect() { this.observing = false; }
  }

  // ---- PluginApi ----
  const routes = [];
  const patches = [];
  const eventListeners = [];
  const PluginApi = {
    React: {
      createElement: (type, props, ...children) => ({ type, props, children }),
      useState: (init) => [typeof init === "function" ? init() : init, () => {}],
      useEffect: () => {},
      useRef: (init) => ({ current: init }),
    },
    register: { route: (p, component) => routes.push({ path: p, component }) },
    patch: {
      before: (name, fn) => patches.push({ kind: "before", name, fn }),
      instead: (name, fn) => patches.push({ kind: "instead", name, fn }),
      after: (name, fn) => patches.push({ kind: "after", name, fn }),
    },
    Event: { addEventListener: (type, fn) => eventListeners.push({ type, fn }) },
    libraries: { ReactRouterDOM: { useHistory: () => ({ push() {} }), Link: "a" } },
  };

  const logs = { warn: [], error: [] };
  const consoleStub = {
    log() {}, info() {}, debug() {},
    warn: (...args) => { logs.warn.push(args); },
    error: (...args) => { logs.error.push(args); },
  };

  const context = vm.createContext({
    window, document, PluginApi, fetch, MutationObserver,
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimeoutStub,
    console: consoleStub,
    CSS: { escape: (s) => String(s).replace(/[^\w-]/g, (c) => "\\" + c) },
    URL,
    alert() {},
    confirm: () => true,
  });
  window.window = window;

  for (const file of FILES) {
    const p = path.join(PLUGIN_DIR, file);
    vm.runInContext(fs.readFileSync(p, "utf8"), context, { filename: p });
  }

  async function settle() {
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  }

  return {
    exports: window.__MISSING_SCENES_TEST__,
    fetchCalls, routes, patches, eventListeners, document, window,
    settle, flushTimers, pendingTimers, mutationObservers, openCalls, logs,
  };
}

module.exports = { loadMissingScenes, createElement, FILES };
