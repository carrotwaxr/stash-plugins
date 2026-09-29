/**
 * Test harness that runs the REAL studio-manager.js inside a node `vm` context
 * with minimal browser/Stash stubs. Not a test itself (name does not match test_*.js).
 *
 *   const { loadStudioManager } = require("./harness");
 *   const sm = loadStudioManager({ fetchResponses: { FindStudios: {...} } });
 *   await sm.settle();
 *
 * Returns { exports, getState, setState, fetchCalls, routes, document, window,
 *           settle, flushTimers, pendingTimers, mutationObservers, logs, ... }.
 * `exports`/`getState`/`setState` come from the hook at the end of studio-manager.js
 * (active only when window.__STUDIO_MANAGER_TEST__ is set).
 * fetch answers by GraphQL operation name (fetchResponses[op], a value or function(body)).
 * Timers are queued, never real; flushTimers() runs them. Every MutationObserver is
 * kept in `mutationObservers`. console.warn/error are recorded in `logs`.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PLUGIN_DIR = path.join(__dirname, "..");
const SRC_PATH = path.join(PLUGIN_DIR, "studio-manager.js");

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
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
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

/**
 * An element stub on which every querySelector(sel) finds something: `query(sel)`
 * when it returns a value other than undefined, else one memoized queryable child
 * per selector (in `el.found`). querySelectorAll(sel) returns `queryAll(sel, el)`
 * when given and not undefined, else []. `remove()` sets `el.removed`. Lets a
 * test drive real dialog handlers, e.g.
 *   tm.document.createElement = (t) => createQueryableElement(t, { query, queryAll });
 */
function createQueryableElement(tagName, { query, queryAll } = {}) {
  const el = createElement(tagName);
  el.found = new Map();
  el.removed = false;
  el.remove = () => { el.removed = true; };
  el.querySelector = (sel) => {
    const custom = query ? query(sel) : undefined;
    if (custom !== undefined) return custom;
    if (!el.found.has(sel)) el.found.set(sel, createQueryableElement("div", { query, queryAll }));
    return el.found.get(sel);
  };
  el.querySelectorAll = (sel) => {
    const custom = queryAll ? queryAll(sel, el) : undefined;
    return custom !== undefined ? custom : [];
  };
  return el;
}

function loadStudioManager({ fetchResponses = {}, base = "/", pathname = "/", confirm = () => true } = {}) {
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
      if (handler === undefined) return respond({ data: {} });
    } else {
      const key = Object.keys(fetchResponses).find((k) => String(url).endsWith(k));
      handler = key !== undefined ? fetchResponses[key] : undefined;
      if (handler === undefined) {
        return respond({ data: {} });
      }
    }
    return respond(typeof handler === "function" ? handler(body) : handler);
  }

  // ---- controllable timers (never real, so node exits promptly) ----
  let timerQueue = [];
  let timerId = 0;
  const setTimeoutStub = (fn, ms) => { timerQueue.push({ id: ++timerId, fn, ms }); return timerId; };
  const pendingTimers = () => timerQueue.map((t) => ({ ms: t.ms }));
  const clearTimeoutStub = (id) => { timerQueue = timerQueue.filter((t) => t.id !== id); };
  function flushTimers() {
    // Runs queued callbacks (and any they queue) once each.
    let guard = 0;
    while (timerQueue.length && guard++ < 1000) {
      const t = timerQueue.shift();
      t.fn();
    }
  }

  // ---- document / window ----
  const listenerCounts = { add: 0, remove: 0 };
  const baseEl = { getAttribute: (k) => (k === "href" ? base : null) };
  const document = {
    title: "",
    body: createElement("body"),
    querySelector: (sel) => (sel === "base" ? baseEl : null),
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement,
    createElementNS: (ns, tag) => createElement(tag),
    listeners: {},
    addEventListener(type, fn) {
      listenerCounts.add++;
      (document.listeners[type] = document.listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      listenerCounts.remove++;
      document.listeners[type] = (document.listeners[type] || []).filter((f) => f !== fn);
    },
    listenerCounts,
  };
  // location: pushState moves it silently; direct href/assign/replace is recorded.
  const locationAssignments = [];
  let currentHref = new URL(pathname, "http://localhost").href;
  const location = { origin: "http://localhost", pathname: new URL(currentHref).pathname, search: "", hash: "" };
  const moveTo = (url) => {
    const u = new URL(String(url), currentHref);
    currentHref = u.href;
    location.pathname = u.pathname; location.search = u.search; location.hash = u.hash;
  };
  Object.defineProperty(location, "href", {
    enumerable: true,
    get: () => currentHref,
    set: (v) => { locationAssignments.push(String(v)); moveTo(v); },
  });
  location.assign = (v) => { locationAssignments.push(String(v)); moveTo(v); };
  location.replace = (v) => { locationAssignments.push(String(v)); moveTo(v); };

  const historyCalls = [];
  const dispatchedEvents = [];
  const history = {
    state: null,
    pushState(state, title, url) {
      historyCalls.push({ state, title, url: String(url) });
      history.state = state;
      moveTo(url);
    },
  };

  const confirmCalls = [];
  const window = {
    __STUDIO_MANAGER_TEST__: {},
    location,
    history,
    dispatchEvent: (evt) => { dispatchedEvents.push(evt); return true; },
    listeners: {},
    addEventListener(type, fn) { (window.listeners[type] = window.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) { window.listeners[type] = (window.listeners[type] || []).filter((f) => f !== fn); },
    innerWidth: 1280,
    innerHeight: 800,
    confirm: (msg) => { confirmCalls.push(String(msg)); return confirm(msg); },
  };

  class PopStateEvent {
    constructor(type, init = {}) { this.type = type; this.state = init.state === undefined ? null : init.state; }
  }

  const mutationObservers = [];
  class MutationObserver {
    constructor(cb) { this.cb = cb; this.observing = false; mutationObservers.push(this); }
    observe() { this.observing = true; }
    disconnect() { this.observing = false; }
  }

  // ---- PluginApi ----
  const routes = [];
  const eventListeners = {};
  const effects = []; // callbacks passed to React.useEffect (tests run them by hand)
  const PluginApi = {
    React: {
      createElement: (type, props, ...children) => ({ type, props, children }),
      useState: (init) => {
        const v = typeof init === "function" ? init() : init;
        return [v, () => {}];
      },
      useEffect: (fn) => { effects.push(fn); },
      useRef: (init) => ({ current: init }),
    },
    Event: { addEventListener: (type, fn) => { (eventListeners[type] = eventListeners[type] || []).push(fn); } },
    register: { route: (p, component) => routes.push({ path: p, component }) },
    libraries: { ReactRouterDOM: { useHistory: () => ({ push() {} }), Link: "a" } },
  };

  const logs = { warn: [], error: [] };
  const consoleStub = {
    log() {}, info() {}, debug() {},
    warn: (...args) => { logs.warn.push(args); },
    error: (...args) => { logs.error.push(args); },
  };

  const context = vm.createContext({
    window, document, PluginApi, fetch,
    MutationObserver,
    PopStateEvent,
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimeoutStub,
    console: consoleStub,
    CSS: { escape: (s) => String(s).replace(/[^\w-]/g, (c) => "\\" + c) },
    URL,
    alert() {},
    confirm: (msg) => !!window.confirm(msg),
  });
  window.window = window;

  vm.runInContext(fs.readFileSync(SRC_PATH, "utf8"), context, { filename: SRC_PATH });

  const hook = window.__STUDIO_MANAGER_TEST__;

  /** Wait for async init (queued fetches, microtasks) to finish. */
  async function settle() {
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  }

  return {
    exports: hook.exports || {},
    getState: hook.getState,
    setState: hook.setState,
    fetchCalls,
    confirmCalls,
    routes,
    eventListeners,
    effects,
    document,
    window,
    settle,
    flushTimers,
    pendingTimers,
    mutationObservers,
    historyCalls,
    dispatchedEvents,
    locationAssignments,
    logs,
  };
}

module.exports = { loadStudioManager, createElement, createQueryableElement };
