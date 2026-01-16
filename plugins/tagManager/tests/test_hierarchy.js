/**
 * Unit tests for hierarchy editing functions.
 * Run with: node plugins/tagManager/tests/test_hierarchy.js
 */

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

// ============================================================================
// wouldCreateCircularRef tests
// ============================================================================

// Mock hierarchy data for circular reference tests
let hierarchyTags = [];

/**
 * Check if making potentialParentId a parent of tagId would create a circular reference.
 * This happens if tagId is already an ancestor of potentialParentId.
 */
function wouldCreateCircularRef(potentialParentId, tagId) {
  const ancestors = new Set();

  function collectAncestors(id) {
    const tag = hierarchyTags.find(t => t.id === id);
    if (!tag || !tag.parents) return;

    for (const parent of tag.parents) {
      if (ancestors.has(parent.id)) continue;
      ancestors.add(parent.id);
      collectAncestors(parent.id);
    }
  }

  collectAncestors(potentialParentId);
  return ancestors.has(tagId);
}

console.log('\n=== wouldCreateCircularRef tests ===\n');

test('detects direct circular reference (child becoming parent)', () => {
  // Setup: Parent -> Child
  hierarchyTags = [
    { id: '1', name: 'Parent', parents: [] },
    { id: '2', name: 'Child', parents: [{ id: '1' }] },
  ];

  // Adding Child as parent of Parent would create: Parent -> Child -> Parent
  assertEqual(wouldCreateCircularRef('2', '1'), true);
});

test('detects indirect circular reference across multiple levels', () => {
  // Setup: Grandparent -> Parent -> Child
  hierarchyTags = [
    { id: '1', name: 'Grandparent', parents: [] },
    { id: '2', name: 'Parent', parents: [{ id: '1' }] },
    { id: '3', name: 'Child', parents: [{ id: '2' }] },
  ];

  // Adding Child as parent of Grandparent would create a cycle
  assertEqual(wouldCreateCircularRef('3', '1'), true);
});

test('allows valid parent-child relationship', () => {
  hierarchyTags = [
    { id: '1', name: 'TagA', parents: [] },
    { id: '2', name: 'TagB', parents: [] },
  ];

  // Adding TagA as parent of TagB is fine - no existing relationship
  assertEqual(wouldCreateCircularRef('1', '2'), false);
});

test('allows adding sibling as parent', () => {
  // Setup: Parent has two children (siblings)
  hierarchyTags = [
    { id: '1', name: 'Parent', parents: [] },
    { id: '2', name: 'ChildA', parents: [{ id: '1' }] },
    { id: '3', name: 'ChildB', parents: [{ id: '1' }] },
  ];

  // Adding ChildA as parent of ChildB is allowed (creates multi-parent)
  assertEqual(wouldCreateCircularRef('2', '3'), false);
});

test('handles tags with no parents', () => {
  hierarchyTags = [
    { id: '1', name: 'Root', parents: [] },
  ];

  assertEqual(wouldCreateCircularRef('1', '999'), false);
});

test('handles diamond inheritance pattern', () => {
  // Diamond: A -> B, A -> C, B -> D, C -> D
  hierarchyTags = [
    { id: 'A', name: 'A', parents: [] },
    { id: 'B', name: 'B', parents: [{ id: 'A' }] },
    { id: 'C', name: 'C', parents: [{ id: 'A' }] },
    { id: 'D', name: 'D', parents: [{ id: 'B' }, { id: 'C' }] },
  ];

  // Adding D as parent of A would create cycle through either B or C path
  assertEqual(wouldCreateCircularRef('D', 'A'), true);

  // Adding A as additional parent of D is fine (already exists via B and C)
  assertEqual(wouldCreateCircularRef('A', 'D'), false);
});

// ============================================================================
// buildTagTree tests
// ============================================================================

/**
 * Build a tree structure from flat tag list.
 * Tags with multiple parents appear under each parent.
 */
function buildTagTree(tags) {
  const tagMap = new Map();
  tags.forEach(tag => {
    tagMap.set(tag.id, {
      ...tag,
      childNodes: []
    });
  });

  const roots = [];

  tags.forEach(tag => {
    const node = tagMap.get(tag.id);

    if (tag.parents.length === 0) {
      roots.push({ ...node, parentContextId: null });
    } else {
      tag.parents.forEach(parent => {
        const parentNode = tagMap.get(parent.id);
        if (parentNode) {
          parentNode.childNodes.push({ ...node, parentContextId: parent.id });
        }
      });
    }
  });

  const sortByName = (a, b) => a.name.localeCompare(b.name);
  roots.sort(sortByName);

  function sortChildren(node) {
    if (node.childNodes.length > 0) {
      node.childNodes.sort(sortByName);
      node.childNodes.forEach(sortChildren);
    }
  }
  roots.forEach(sortChildren);

  return roots;
}

console.log('\n=== buildTagTree tests ===\n');

test('builds tree with root tags', () => {
  const tags = [
    { id: '1', name: 'Root1', parents: [] },
    { id: '2', name: 'Root2', parents: [] },
  ];

  const tree = buildTagTree(tags);
  assertEqual(tree.length, 2);
  assertEqual(tree[0].name, 'Root1'); // Sorted alphabetically
  assertEqual(tree[1].name, 'Root2');
});

test('builds tree with parent-child relationships', () => {
  const tags = [
    { id: '1', name: 'Parent', parents: [] },
    { id: '2', name: 'Child', parents: [{ id: '1' }] },
  ];

  const tree = buildTagTree(tags);
  assertEqual(tree.length, 1);
  assertEqual(tree[0].name, 'Parent');
  assertEqual(tree[0].childNodes.length, 1);
  assertEqual(tree[0].childNodes[0].name, 'Child');
});

test('sets parentContextId for children', () => {
  const tags = [
    { id: '1', name: 'Parent', parents: [] },
    { id: '2', name: 'Child', parents: [{ id: '1' }] },
  ];

  const tree = buildTagTree(tags);
  assertEqual(tree[0].parentContextId, null); // Root has no parent context
  assertEqual(tree[0].childNodes[0].parentContextId, '1'); // Child knows its parent
});

test('handles multi-parent tags (tag appears under each parent)', () => {
  const tags = [
    { id: '1', name: 'ParentA', parents: [] },
    { id: '2', name: 'ParentB', parents: [] },
    { id: '3', name: 'MultiChild', parents: [{ id: '1' }, { id: '2' }] },
  ];

  const tree = buildTagTree(tags);
  assertEqual(tree.length, 2); // Two roots

  // MultiChild should appear under both parents
  const parentA = tree.find(t => t.name === 'ParentA');
  const parentB = tree.find(t => t.name === 'ParentB');

  assertEqual(parentA.childNodes.length, 1);
  assertEqual(parentB.childNodes.length, 1);
  assertEqual(parentA.childNodes[0].name, 'MultiChild');
  assertEqual(parentB.childNodes[0].name, 'MultiChild');

  // Each copy should have correct parent context
  assertEqual(parentA.childNodes[0].parentContextId, '1');
  assertEqual(parentB.childNodes[0].parentContextId, '2');
});

test('sorts children alphabetically', () => {
  const tags = [
    { id: '1', name: 'Parent', parents: [] },
    { id: '2', name: 'Zebra', parents: [{ id: '1' }] },
    { id: '3', name: 'Apple', parents: [{ id: '1' }] },
    { id: '4', name: 'Mango', parents: [{ id: '1' }] },
  ];

  const tree = buildTagTree(tags);
  const children = tree[0].childNodes;

  assertEqual(children[0].name, 'Apple');
  assertEqual(children[1].name, 'Mango');
  assertEqual(children[2].name, 'Zebra');
});

test('handles empty input', () => {
  const tree = buildTagTree([]);
  assertEqual(tree.length, 0);
});

test('handles deep nesting', () => {
  const tags = [
    { id: '1', name: 'Level1', parents: [] },
    { id: '2', name: 'Level2', parents: [{ id: '1' }] },
    { id: '3', name: 'Level3', parents: [{ id: '2' }] },
    { id: '4', name: 'Level4', parents: [{ id: '3' }] },
  ];

  const tree = buildTagTree(tags);
  assertEqual(tree.length, 1);
  assertEqual(tree[0].childNodes[0].childNodes[0].childNodes[0].name, 'Level4');
});

// ============================================================================
// getTreeStats tests
// ============================================================================

function getTreeStats(tags) {
  const totalTags = tags.length;
  const rootTags = tags.filter(t => t.parents.length === 0).length;
  const tagsWithChildren = tags.filter(t => t.child_count > 0).length;
  const tagsWithParents = tags.filter(t => t.parent_count > 0).length;

  return { totalTags, rootTags, tagsWithChildren, tagsWithParents };
}

console.log('\n=== getTreeStats tests ===\n');

test('calculates correct stats', () => {
  const tags = [
    { id: '1', name: 'Root', parents: [], child_count: 2, parent_count: 0 },
    { id: '2', name: 'Child1', parents: [{ id: '1' }], child_count: 0, parent_count: 1 },
    { id: '3', name: 'Child2', parents: [{ id: '1' }], child_count: 1, parent_count: 1 },
    { id: '4', name: 'Grandchild', parents: [{ id: '3' }], child_count: 0, parent_count: 1 },
  ];

  const stats = getTreeStats(tags);
  assertEqual(stats.totalTags, 4);
  assertEqual(stats.rootTags, 1);
  assertEqual(stats.tagsWithChildren, 2); // Root and Child2
  assertEqual(stats.tagsWithParents, 3); // Child1, Child2, Grandchild
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
