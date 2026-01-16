/**
 * Unit tests for alias validation functions.
 * Run with: node plugins/tagManager/tests/test_alias_validation.js
 */

// Extract the functions we want to test (these are inline in tag-manager.js)
// We'll redefine them here for testing

function sanitizeAliasesForSave(aliases, finalName, currentLocalName) {
  const cleaned = new Set(aliases);

  // Remove final name (can't alias yourself)
  cleaned.forEach(alias => {
    if (alias.toLowerCase() === finalName.toLowerCase()) {
      cleaned.delete(alias);
    }
  });

  // If keeping local name, also ensure it's not in aliases
  if (finalName.toLowerCase() === currentLocalName.toLowerCase()) {
    cleaned.forEach(alias => {
      if (alias.toLowerCase() === currentLocalName.toLowerCase()) {
        cleaned.delete(alias);
      }
    });
  }

  return Array.from(cleaned);
}

function findConflictingTag(name, excludeTagId, localTags) {
  const lowerName = name.toLowerCase();
  return localTags.find(t =>
    t.id !== excludeTagId && (
      t.name.toLowerCase() === lowerName ||
      t.aliases?.some(a => a.toLowerCase() === lowerName)
    )
  ) || null;
}

function validateBeforeSave(finalName, aliases, currentTagId, localTags) {
  const errors = [];

  // Check if final name conflicts with another tag
  const nameConflict = findConflictingTag(finalName, currentTagId, localTags);
  if (nameConflict) {
    errors.push({
      type: 'name_conflict',
      field: 'name',
      value: finalName,
      conflictsWith: nameConflict
    });
  }

  // Check each alias for conflicts
  for (const alias of aliases) {
    const aliasConflict = findConflictingTag(alias, currentTagId, localTags);
    if (aliasConflict) {
      errors.push({
        type: 'alias_conflict',
        field: 'alias',
        value: alias,
        conflictsWith: aliasConflict
      });
    }
  }

  return errors;
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
  const actualStr = JSON.stringify(actual);
  const expectedStr = JSON.stringify(expected);
  if (actualStr !== expectedStr) {
    throw new Error(`${msg}\n  Expected: ${expectedStr}\n  Actual: ${actualStr}`);
  }
}

function assertIncludes(arr, item, msg = '') {
  if (!arr.includes(item)) {
    throw new Error(`${msg}\n  Array does not include: ${item}\n  Array: ${JSON.stringify(arr)}`);
  }
}

function assertNotIncludes(arr, item, msg = '') {
  if (arr.includes(item)) {
    throw new Error(`${msg}\n  Array should not include: ${item}\n  Array: ${JSON.stringify(arr)}`);
  }
}

// ============================================================================
// sanitizeAliasesForSave tests
// ============================================================================

console.log('\n=== sanitizeAliasesForSave tests ===\n');

test('removes final name from aliases when renaming to StashDB name', () => {
  // Scenario: Local "Attraction" renamed to StashDB "Passion"
  // Aliases include both names, should remove "Passion" (the final name)
  const aliases = new Set(['Attraction', 'Passion', 'Love']);
  const result = sanitizeAliasesForSave(aliases, 'Passion', 'Attraction');

  assertNotIncludes(result, 'Passion', 'Final name should be removed');
  assertIncludes(result, 'Attraction', 'Old name should be kept as alias');
  assertIncludes(result, 'Love', 'Other aliases should be kept');
});

test('removes final name case-insensitively', () => {
  const aliases = new Set(['PASSION', 'Love']);
  const result = sanitizeAliasesForSave(aliases, 'Passion', 'Attraction');

  assertNotIncludes(result, 'PASSION', 'Case-insensitive match should be removed');
});

test('keeps aliases when keeping local name', () => {
  const aliases = new Set(['Alt1', 'Alt2']);
  const result = sanitizeAliasesForSave(aliases, 'LocalName', 'LocalName');

  assertEqual(result.length, 2, 'Should keep all aliases');
});

test('removes local name from aliases when keeping local name', () => {
  // Edge case: local name somehow ended up in aliases
  const aliases = new Set(['LocalName', 'Alt1']);
  const result = sanitizeAliasesForSave(aliases, 'LocalName', 'LocalName');

  assertNotIncludes(result, 'LocalName', 'Local name should be removed from aliases');
  assertIncludes(result, 'Alt1', 'Other aliases should be kept');
});

test('handles empty alias set', () => {
  const aliases = new Set();
  const result = sanitizeAliasesForSave(aliases, 'NewName', 'OldName');

  assertEqual(result.length, 0, 'Should return empty array');
});

// ============================================================================
// findConflictingTag tests
// ============================================================================

console.log('\n=== findConflictingTag tests ===\n');

const sampleTags = [
  { id: '1', name: 'Action', aliases: ['Acts', 'Activities'] },
  { id: '2', name: 'Comedy', aliases: ['Funny', 'Humor'] },
  { id: '3', name: 'Drama', aliases: [] },
];

test('finds conflict by exact name match', () => {
  const result = findConflictingTag('Action', '99', sampleTags);

  assertEqual(result?.id, '1', 'Should find tag with matching name');
});

test('finds conflict by alias match', () => {
  const result = findConflictingTag('Funny', '99', sampleTags);

  assertEqual(result?.id, '2', 'Should find tag with matching alias');
});

test('finds conflict case-insensitively', () => {
  const result = findConflictingTag('ACTION', '99', sampleTags);

  assertEqual(result?.id, '1', 'Should find tag with case-insensitive match');
});

test('excludes current tag from conflict check', () => {
  const result = findConflictingTag('Action', '1', sampleTags);

  assertEqual(result, null, 'Should not find conflict with self');
});

test('returns null when no conflict', () => {
  const result = findConflictingTag('NonexistentTag', '99', sampleTags);

  assertEqual(result, null, 'Should return null for no conflict');
});

// ============================================================================
// validateBeforeSave tests
// ============================================================================

console.log('\n=== validateBeforeSave tests ===\n');

test('returns empty array when no conflicts', () => {
  const errors = validateBeforeSave('NewTag', ['Alias1', 'Alias2'], '99', sampleTags);

  assertEqual(errors.length, 0, 'Should have no errors');
});

test('detects name conflict', () => {
  const errors = validateBeforeSave('Action', ['Alias1'], '99', sampleTags);

  assertEqual(errors.length, 1, 'Should have one error');
  assertEqual(errors[0].type, 'name_conflict', 'Should be name conflict');
  assertEqual(errors[0].value, 'Action', 'Should report conflicting name');
});

test('detects alias conflict', () => {
  const errors = validateBeforeSave('NewTag', ['Funny'], '99', sampleTags);

  assertEqual(errors.length, 1, 'Should have one error');
  assertEqual(errors[0].type, 'alias_conflict', 'Should be alias conflict');
  assertEqual(errors[0].value, 'Funny', 'Should report conflicting alias');
});

test('detects multiple alias conflicts', () => {
  const errors = validateBeforeSave('NewTag', ['Funny', 'Acts'], '99', sampleTags);

  assertEqual(errors.length, 2, 'Should have two errors');
});

test('allows conflicts with self (current tag)', () => {
  // Tag '1' is named 'Action' with aliases 'Acts', 'Activities'
  // When editing tag '1', these shouldn't conflict
  const errors = validateBeforeSave('Action', ['Acts'], '1', sampleTags);

  assertEqual(errors.length, 0, 'Should allow own name and aliases');
});

// ============================================================================
// Summary
// ============================================================================

console.log('\n=== Summary ===\n');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);

if (failed > 0) {
  process.exit(1);
}
