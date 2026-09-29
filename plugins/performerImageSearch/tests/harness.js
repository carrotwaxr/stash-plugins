/**
 * Test harness that runs the REAL performer-image-search.js inside a node `vm`
 * context with minimal browser/Stash stubs. Not a test itself (name does not
 * match test_*.js).
 *
 *   const { loadPlugin } = require("./harness");
 *   const p = loadPlugin({ fetchResponses: { RunPluginOperation: (body) => ({...}) } });
 *   await p.settle();
 *
 * Returns { exports, getState, setState, window, document, fetchCalls,
 *           windowListeners, documentListeners, pluginEvents, createdElements,
 *           pendingTimers, flushTimers, settle, logs }.
 * `exports`/`getState`/`setState` come from the block at the end of
 * performer-image-search.js (active only when window.__PERFORMER_IMAGE_SEARCH_TEST__
 * is set, which the harness does). The window.pis* handlers are on `window`.
 *
 * fetchCalls: { url, op, variables, body, opts, source } per call. `op` is the
 * GraphQL operation name (e.g. "RunPluginOperation"); `source` is
 * variables.args.source for plugin calls. Responses are looked up in
 * `fetchResponses` by op name (a value, or a function of the parsed body);
 * unknown ops answer { data: {} }.
 *
 * windowListeners / documentListeners: { type, fn, capture } for every
 * addEventListener call (capture is true when the third arg is true or
 * { capture: true }). Removals are recorded in windowRemoved / documentRemoved.
 * pluginEvents: PluginApi.Event.addEventListener registrations { type, fn }.
 * createdElements: every element made by document.createElement.
 * Timers are never real: setTimeout queues, flushTimers() runs them (and any
 * they queue) once each; pendingTimers() lists the queued { ms }.
 * DOM: getElementById/querySelector find nothing by default; a test can
 * register elements with `document.elements[id] = el` (getElementById) or
 * replace document.querySelector.
 * console.warn/error calls are recorded in logs.warn / logs.error.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC_PATH = path.join(__dirname, "..", "performer-image-search.js");

function createElement(tagName, created) {
  const classes = new Set();
  const el = {
    tagName: String(tagName).toUpperCase(),
    style: {},
    dataset: {},
    children: [],
    attributes: {},
    innerHTML: "",
    textContent: "",
    className: "",
    value: "",
    listeners: {},
    appendChild(child) { el.children.push(child); return child; },
    remove() { el.removed = true; },
    setAttribute(k, v) { el.attributes[k] = String(v); },
    getAttribute(k) { return k in el.attributes ? el.attributes[k] : null; },
    addEventListener(type, fn) { (el.listeners[type] = el.listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      el.listeners[type] = (el.listeners[type] || []).filter((f) => f !== fn);
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      contains: (c) => classes.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
    },
  };
  if (created) created.push(el);
  return el;
}

const isCapture = (opts) => opts === true || !!(opts && typeof opts === "object" && opts.capture);

function loadPlugin({ fetchResponses = {}, pathname = "/", base = "/" } = {}) {
  // ---- fetch stub ----
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
    const variables = (body && body.variables) || null;
    const source = variables && variables.args ? variables.args.source : undefined;
    fetchCalls.push({ url: String(url), op, variables, body, opts, source });
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
  const createdElements = [];
  const documentListeners = [];
  const documentRemoved = [];
  const windowListeners = [];
  const windowRemoved = [];
  const baseEl = { getAttribute: (k) => (k === "href" ? base : null) };
  const elements = {};
  const document = {
    title: "",
    body: createElement("body", createdElements),
    elements,
    querySelector: (sel) => (sel === "base" ? baseEl : null),
    querySelectorAll: () => [],
    getElementById: (id) => elements[id] || null,
    createElement: (tag) => createElement(tag, createdElements),
    addEventListener(type, fn, opts) { documentListeners.push({ type, fn, capture: isCapture(opts) }); },
    removeEventListener(type, fn, opts) { documentRemoved.push({ type, fn, capture: isCapture(opts) }); },
  };
  const window = {
    __PERFORMER_IMAGE_SEARCH_TEST__: {},
    location: { origin: "http://localhost", pathname, search: "", hash: "", href: "http://localhost" + pathname },
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener(type, fn, opts) { windowListeners.push({ type, fn, capture: isCapture(opts) }); },
    removeEventListener(type, fn, opts) { windowRemoved.push({ type, fn, capture: isCapture(opts) }); },
  };
  window.window = window;

  const pluginEvents = [];
  const PluginApi = {
    Event: {
      addEventListener: (type, fn) => { pluginEvents.push({ type, fn }); },
    },
  };

  const logs = { warn: [], error: [] };
  const consoleStub = {
    log() {}, info() {}, debug() {},
    warn: (...args) => { logs.warn.push(args); },
    error: (...args) => { logs.error.push(args); },
  };

  const context = vm.createContext({
    window, document, PluginApi, fetch,
    location: window.location,
    setTimeout: setTimeoutStub,
    clearTimeout: clearTimeoutStub,
    console: consoleStub,
    URL,
    alert() {},
  });

  vm.runInContext(fs.readFileSync(SRC_PATH, "utf8"), context, { filename: SRC_PATH });

  const hook = window.__PERFORMER_IMAGE_SEARCH_TEST__;

  async function settle() {
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  }

  return {
    exports: hook.exports || {},
    getState: hook.getState,
    setState: hook.setState,
    window,
    document,
    fetchCalls,
    windowListeners,
    windowRemoved,
    documentListeners,
    documentRemoved,
    pluginEvents,
    createdElements,
    pendingTimers,
    flushTimers,
    settle,
    logs,
  };
}

module.exports = { loadPlugin, createElement };
