/**
 * Test harness that runs the REAL scene-matcher.js inside a node `vm` context
 * with minimal browser/Stash stubs. Not a test itself (name does not match test_*.js).
 *
 *   const { loadSceneMatcher } = require("./harness");
 *   const sm = loadSceneMatcher({ pathname: "/scenes", search: "?disp=3" });
 *   await sm.settle();
 *
 * Options: fetchResponses (by GraphQL operation name, or URL suffix; a value may be a
 * function(body) returning JSON), base, pathname, search, readyState, bodyHtml.
 *
 * Returns { exports, getState, setState, fetchCalls, mutationObservers, listeners,
 *           pluginEvents, document, window, settle, flushTimers, pendingTimers, logs }.
 * `exports`/`getState`/`setState` come from the hook at the end of scene-matcher.js
 * (active only when window.__SCENE_MATCHER_TEST__ is set).
 *
 * Recorded for tests:
 *  - fetchCalls: { url, op, body, opts } for every fetch()
 *  - mutationObservers: every MutationObserver created ({ cb, observing, target, options });
 *    call `obs.cb([], obs)` to simulate a mutation
 *  - listeners: { window: [{type, fn}], document: [{type, fn}] } added via addEventListener
 *  - pluginEvents: [{ type, fn }] added via PluginApi.Event.addEventListener
 *  - logs: console.warn/error argument arrays in logs.warn / logs.error
 * Timers never run for real; flushTimers() runs queued callbacks (and any they queue).
 * document.querySelector/querySelectorAll find nothing unless a test overrides them.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC_PATH = path.join(__dirname, "..", "scene-matcher.js");

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
    classList: null,
    listeners: {},
    parentElement: null,
    appendChild(child) { child.parentElement = el; el.children.push(child); return child; },
    remove() { el.removed = true; },
    setAttribute(k, v) { el.attributes[k] = String(v); },
    getAttribute(k) { return k in el.attributes ? el.attributes[k] : null; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      el.listeners[type] = (el.listeners[type] || []).filter((f) => f !== fn);
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest() { return null; },
    click() { (el.listeners.click || []).forEach((f) => f({ preventDefault() {}, stopPropagation() {}, target: el })); },
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

function loadSceneMatcher({
  fetchResponses = {}, base = "/", pathname = "/", search = "", readyState = "complete",
} = {}) {
  // ---- fetch stub: answers by GraphQL operation name, or by URL suffix ----
  const fetchCalls = [];
  function respond(json) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => json,
      text: async () => JSON.stringify(json),
    });
  }
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

    let handler;
    if (op !== null) {
      handler = fetchResponses[op];
    } else {
      const key = Object.keys(fetchResponses).find((k) => String(url).endsWith(k));
      handler = key !== undefined ? fetchResponses[key] : undefined;
    }
    if (handler === undefined) return respond({ data: {} });
    return respond(typeof handler === "function" ? handler(body) : handler);
  }

  // ---- controllable timers (never real, so node exits promptly) ----
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
  const listeners = { window: [], document: [] };
  const document = {
    title: "",
    readyState,
    body: createElement("body"),
    head: createElement("head"),
    querySelector: (sel) => (sel === "base" ? { getAttribute: (k) => (k === "href" ? base : null) } : null),
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement,
    addEventListener(type, fn) { listeners.document.push({ type, fn }); },
    removeEventListener(type, fn) {
      listeners.document = listeners.document.filter((l) => !(l.type === type && l.fn === fn));
    },
  };
  const location = { origin: "http://localhost", pathname, search, hash: "" };
  Object.defineProperty(location, "href", {
    enumerable: true,
    get: () => location.origin + location.pathname + location.search + location.hash,
    set: (v) => {
      const u = new URL(String(v), location.origin);
      location.pathname = u.pathname; location.search = u.search; location.hash = u.hash;
    },
  });

  const window = {
    __SCENE_MATCHER_TEST__: {},
    location,
    innerWidth: 1280,
    innerHeight: 800,
    HTMLInputElement: { prototype: {} },
    open() {},
    addEventListener(type, fn) { listeners.window.push({ type, fn }); },
    removeEventListener(type, fn) {
      listeners.window = listeners.window.filter((l) => !(l.type === type && l.fn === fn));
    },
    dispatchEvent: () => true,
  };

  const mutationObservers = [];
  class MutationObserver {
    constructor(cb) { this.cb = cb; this.observing = false; mutationObservers.push(this); }
    observe(target, options) { this.observing = true; this.target = target; this.options = options; }
    disconnect() { this.observing = false; }
  }

  // ---- PluginApi ----
  const pluginEvents = [];
  const PluginApi = {
    Event: { addEventListener: (type, fn) => { pluginEvents.push({ type, fn }); } },
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
    URL,
    Event: class Event { constructor(type, init = {}) { this.type = type; Object.assign(this, init); } },
    KeyboardEvent: class KeyboardEvent { constructor(type, init = {}) { this.type = type; Object.assign(this, init); } },
    alert() {},
    confirm: () => true,
  });
  window.window = window;

  vm.runInContext(fs.readFileSync(SRC_PATH, "utf8"), context, { filename: SRC_PATH });

  const hook = window.__SCENE_MATCHER_TEST__;

  /** Wait for async work (queued fetches, microtasks) to finish. */
  async function settle() {
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  }

  return {
    exports: hook.exports || {},
    getState: hook.getState,
    setState: hook.setState,
    fetchCalls,
    mutationObservers,
    listeners,
    pluginEvents,
    document,
    window,
    settle,
    flushTimers,
    pendingTimers,
    logs,
  };
}

module.exports = { loadSceneMatcher, createElement };
