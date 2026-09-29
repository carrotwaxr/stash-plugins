/**
 * Tests that load the REAL tag-manager.js (no mirrored copies).
 * Run with: node plugins/tagManager/tests/test_real_file.js
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { loadTagManager } = require("./harness");

const SRC_PATH = path.join(__dirname, "..", "tag-manager.js");

// Names called but never defined. Every entry must still be found (keeps the list honest).
const KNOWN_UNDEFINED = ["loadStashdbTags"]; // removed by Task 10

const KEYWORDS = new Set(
  ("if for while switch catch return typeof void delete throw await yield new function class " +
    "else do in of instanceof case default try finally break continue with super import export async get set static")
    .split(/\s+/)
);

// Real browser / JS globals the file may call.
const GLOBALS = new Set(
  ("fetch setTimeout clearTimeout setInterval clearInterval confirm alert parseInt parseFloat isNaN " +
    "Number String Boolean Array Object JSON Promise Math Date Set Map WeakMap RegExp Error TypeError " +
    "encodeURIComponent decodeURIComponent CSS URL MutationObserver require requestAnimationFrame Symbol")
    .split(/\s+/)
);

/** Blank out comments, string contents and regex literals; keep template `${}` expressions as code. */
function stripSource(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  const tplStack = []; // brace depth at which a template expression started
  let depth = 0;
  let lastSig = ""; // last significant (non-space) char emitted
  let lastWord = "";
  const regexPrev = "(,=:[!&|?{};+-*%<>~^";
  function readTemplate() {
    // positioned just after opening backtick or after closing } of an expression
    while (i < n) {
      const c = src[i];
      if (c === "\\") { i += 2; continue; }
      if (c === "`") { i++; out += "`"; lastSig = "`"; return; }
      if (c === "$" && src[i + 1] === "{") {
        i += 2; out += " ( "; tplStack.push(depth); depth++; lastSig = "("; return;
      }
      i++;
    }
  }
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === "/" && d === "/") { while (i < n && src[i] !== "\n") i++; continue; }
    if (c === "/" && d === "*") { i = src.indexOf("*/", i + 2) + 2; out += " "; continue; }
    if (c === "'" || c === '"') {
      i++;
      while (i < n && src[i] !== c) { if (src[i] === "\\") i++; i++; }
      i++; out += '""'; lastSig = '"'; continue;
    }
    if (c === "`") { i++; out += "`"; readTemplate(); continue; }
    if (c === "/" && (regexPrev.includes(lastSig) || lastSig === "" || lastWord === "return" || lastWord === "typeof")) {
      i++;
      let inClass = false;
      while (i < n) {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === "[") inClass = true;
        else if (src[i] === "]") inClass = false;
        else if (src[i] === "/" && !inClass) break;
        i++;
      }
      i++;
      while (/[a-z]/i.test(src[i] || "")) i++;
      out += " 0 "; lastSig = "0"; lastWord = ""; continue;
    }
    if (c === "{") depth++;
    if (c === "}") {
      depth--;
      if (tplStack.length && tplStack[tplStack.length - 1] === depth) {
        tplStack.pop(); out += " ) "; i++; readTemplate(); continue;
      }
    }
    out += c;
    i++;
    if (!/\s/.test(c)) {
      lastSig = c;
      if (/[\w$]/.test(c)) {
        lastWord = /[\w$]/.test(out[out.length - 2] || "") ? lastWord + c : c;
      } else lastWord = "";
    }
  }
  return out;
}

function matchingOpen(s, closeIdx) {
  let d = 0;
  for (let k = closeIdx; k >= 0; k--) {
    if (s[k] === ")") d++;
    else if (s[k] === "(" && --d === 0) return k;
  }
  return -1;
}
function matchingClose(s, openIdx) {
  let d = 0;
  for (let k = openIdx; k < s.length; k++) {
    if (s[k] === "(") d++;
    else if (s[k] === ")" && --d === 0) return k;
  }
  return -1;
}
const idents = (s) => s.match(/[A-Za-z_$][\w$]*/g) || [];

function findUndefinedCalls(src) {
  const s = stripSource(src);
  const declared = new Set();
  let m;

  // function/class/const/let/var NAME
  const declRe = /\b(?:const|let|var|function\s*\*?|class)\s+([A-Za-z_$][\w$]*)/g;
  while ((m = declRe.exec(s))) declared.add(m[1]);
  // destructuring declarations
  const destrRe = /\b(?:const|let|var)\s*([[{][^=;]*?[\]}])\s*=/g;
  while ((m = destrRe.exec(s))) idents(m[1]).forEach((x) => declared.add(x));
  // parameters of `function [name](...)`
  const fnRe = /\bfunction\s*\*?\s*(?:[A-Za-z_$][\w$]*)?\s*\(/g;
  while ((m = fnRe.exec(s))) {
    const open = m.index + m[0].length - 1;
    idents(s.slice(open, matchingClose(s, open))).forEach((x) => declared.add(x));
  }
  // arrow parameters
  const arrowRe = /=>/g;
  while ((m = arrowRe.exec(s))) {
    let k = m.index - 1;
    while (/\s/.test(s[k])) k--;
    if (s[k] === ")") {
      const open = matchingOpen(s, k);
      idents(s.slice(open, k)).forEach((x) => declared.add(x));
    } else {
      const w = /([A-Za-z_$][\w$]*)$/.exec(s.slice(0, k + 1));
      if (w) declared.add(w[1]);
    }
  }
  // catch (e)
  const catchRe = /\bcatch\s*\(([^)]*)\)/g;
  while ((m = catchRe.exec(s))) idents(m[1]).forEach((x) => declared.add(x));

  // call sites
  const callRe = /(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g;
  const called = new Set();
  while ((m = callRe.exec(s))) {
    const name = m[1];
    const open = m.index + m[0].length - 1;
    const close = matchingClose(s, open);
    // `name(...) {` is a method definition (or keyword statement), not a call
    if (/^\s*\{/.test(s.slice(close + 1, close + 20))) { declared.add(name); continue; }
    called.add(name);
  }
  return [...called].filter((x) => !declared.has(x) && !KEYWORDS.has(x) && !GLOBALS.has(x)).sort();
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("loads without throwing", async () => {
  const tm = loadTagManager({});
  await tm.settle();
  const paths = tm.routes.map((r) => r.path);
  assert.deepStrictEqual(paths, ["/plugins/tag-manager", "/plugins/tag-hierarchy"]);
});

test("exports reachable", async () => {
  const tm = loadTagManager({});
  assert.strictEqual(typeof tm.exports.parseBlacklist, "function");
  assert.strictEqual(typeof tm.exports.isBlacklisted, "function");
  assert.strictEqual(typeof tm.exports.callBackend, "function");
  assert.deepStrictEqual(Object.keys(tm.getState()).includes("localTags"), true);
});

test("no undefined calls", () => {
  const found = findUndefinedCalls(fs.readFileSync(SRC_PATH, "utf8"));
  const unexpected = found.filter((x) => !KNOWN_UNDEFINED.includes(x));
  const stale = KNOWN_UNDEFINED.filter((x) => !found.includes(x));
  assert.deepStrictEqual(unexpected, [], "undefined function calls: " + unexpected.join(", "));
  assert.deepStrictEqual(stale, [], "KNOWN_UNDEFINED entries no longer found (remove them): " + stale.join(", "));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log("ok   - " + name);
    } catch (e) {
      failed++;
      console.log("FAIL - " + name + "\n       " + (e && e.message));
    }
  }
  if (failed) process.exitCode = 1;
})();
