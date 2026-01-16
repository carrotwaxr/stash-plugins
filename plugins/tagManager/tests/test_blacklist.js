/**
 * Unit tests for blacklist functions.
 * Run with: node plugins/tagManager/tests/test_blacklist.js
 */

// Copy of parseBlacklist for testing
function parseBlacklist(blacklistStr) {
  if (!blacklistStr) return [];

  return blacklistStr.split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
    .map(pattern => {
      if (pattern.startsWith('/')) {
        const regexStr = pattern.slice(1);
        try {
          return { type: 'regex', pattern: regexStr, regex: new RegExp(regexStr, 'i') };
        } catch (e) {
          return null;
        }
      } else {
        return { type: 'literal', pattern: pattern.toLowerCase() };
      }
    })
    .filter(p => p !== null);
}

// Copy of isBlacklisted for testing
let tagBlacklist = [];

function isBlacklisted(tagName) {
  if (!tagName || tagBlacklist.length === 0) return false;

  const lowerName = tagName.toLowerCase();

  for (const entry of tagBlacklist) {
    if (entry.type === 'literal') {
      if (lowerName === entry.pattern) return true;
    } else if (entry.type === 'regex') {
      if (entry.regex.test(tagName)) return true;
    }
  }

  return false;
}

// Test runner
let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`✓ ${name}`);
    passed++;
  } catch (e) {
    console.log(`✗ ${name}`);
    console.log(`  Error: ${e.message}`);
    failed++;
  }
}

function assertEqual(actual, expected, msg = '') {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

// Tests
console.log('\n=== parseBlacklist tests ===\n');

test('returns empty array for null/undefined', () => {
  assertEqual(parseBlacklist(null).length, 0);
  assertEqual(parseBlacklist(undefined).length, 0);
  assertEqual(parseBlacklist('').length, 0);
});

test('parses literal patterns', () => {
  const result = parseBlacklist('4K Available\nFull HD');
  assertEqual(result.length, 2);
  assertEqual(result[0].type, 'literal');
  assertEqual(result[0].pattern, '4k available');
});

test('parses regex patterns', () => {
  const result = parseBlacklist('/Available$');
  assertEqual(result.length, 1);
  assertEqual(result[0].type, 'regex');
  assertEqual(result[0].pattern, 'Available$');
});

test('skips invalid regex', () => {
  const result = parseBlacklist('/[invalid/');
  assertEqual(result.length, 0);
});

test('ignores blank lines', () => {
  const result = parseBlacklist('Pattern1\n\n\nPattern2');
  assertEqual(result.length, 2);
});

console.log('\n=== isBlacklisted tests ===\n');

test('literal exact match (case-insensitive)', () => {
  tagBlacklist = parseBlacklist('4K Available');
  assertEqual(isBlacklisted('4K Available'), true);
  assertEqual(isBlacklisted('4k available'), true);
  assertEqual(isBlacklisted('Action'), false);
});

test('regex pattern matching', () => {
  tagBlacklist = parseBlacklist('/Available$');
  assertEqual(isBlacklisted('4K Available'), true);
  assertEqual(isBlacklisted('Full HD Available'), true);
  assertEqual(isBlacklisted('Available Now'), false);
});

test('resolution pattern', () => {
  tagBlacklist = parseBlacklist('/^\\d+p$');
  assertEqual(isBlacklisted('1080p'), true);
  assertEqual(isBlacklisted('720p'), true);
  assertEqual(isBlacklisted('1080p Video'), false);
});

test('empty name returns false', () => {
  tagBlacklist = parseBlacklist('Pattern');
  assertEqual(isBlacklisted(''), false);
  assertEqual(isBlacklisted(null), false);
});

test('empty blacklist returns false', () => {
  tagBlacklist = [];
  assertEqual(isBlacklisted('Anything'), false);
});

// Summary
console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
