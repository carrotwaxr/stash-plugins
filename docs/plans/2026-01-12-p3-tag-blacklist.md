# P3: Tag Blacklist/Exclusion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to blacklist unwanted StashDB tags (e.g., "4K Available") from appearing in matches and scene syncs.

**Architecture:** Store blacklist patterns in plugin settings (YAML). Create shared `isBlacklisted()` function. Filter at: JS match display, Python search results, Python scene sync. Show count of hidden tags.

**Tech Stack:** JavaScript (Stash plugin API), Python

---

## Background

StashDB has tags users don't want (quality indicators like "4K Available", "Full HD Available"). Running tag operations floods local database with unwanted tags.

**Blacklist format:**
- One pattern per line
- Literal strings for exact match (case-insensitive)
- Prefix with `/` for regex: `/^\d+p$/`, `/Available$/`

**Touchpoints:**
1. Tag Manager match suggestions (JS)
2. Scene tag sync (Python)
3. Backend search results (Python)

---

## Task 1: Add blacklist setting to YAML

**Files:**
- Modify: `plugins/tagManager/tagManager.yml` (settings section)

**Step 1: Add setting definition**

Add after `syncDryRun` setting:

```yaml
  tagBlacklist:
    displayName: Tag Blacklist
    description: Tags to exclude from matching and sync. One pattern per line. Prefix with / for regex (e.g., /Available$/).
    type: STRING
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tagManager.yml
git commit -m "feat(tagManager): add tagBlacklist setting to YAML"
```

---

## Task 2: Add blacklist state and load function (JS)

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (State section ~line 30, after categoryMappings)

**Step 1: Add state variable**

After `let categoryMappings = {};`:

```javascript
  let tagBlacklist = []; // Parsed blacklist patterns [{type: 'literal'|'regex', pattern: string, regex?: RegExp}]
```

**Step 2: Add parse and load functions**

After `loadCategoryMappings()` function:

```javascript
  /**
   * Parse blacklist string into pattern objects
   */
  function parseBlacklist(blacklistStr) {
    if (!blacklistStr) return [];

    return blacklistStr.split('\n')
      .map(line => line.trim())
      .filter(line => line.length > 0)
      .map(pattern => {
        if (pattern.startsWith('/')) {
          // Regex pattern - extract pattern without leading /
          const regexStr = pattern.slice(1);
          try {
            return { type: 'regex', pattern: regexStr, regex: new RegExp(regexStr, 'i') };
          } catch (e) {
            console.warn(`[tagManager] Invalid regex in blacklist: ${pattern}`, e);
            return null;
          }
        } else {
          // Literal pattern - case-insensitive
          return { type: 'literal', pattern: pattern.toLowerCase() };
        }
      })
      .filter(p => p !== null);
  }

  /**
   * Check if a tag name matches any blacklist pattern
   */
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

  /**
   * Load blacklist from plugin settings
   */
  async function loadBlacklist() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID] || {};

      if (pluginConfig.tagBlacklist) {
        tagBlacklist = parseBlacklist(pluginConfig.tagBlacklist);
        console.debug("[tagManager] Loaded blacklist:", tagBlacklist.length, "patterns");
      }
    } catch (e) {
      console.error("[tagManager] Failed to load blacklist:", e);
    }
  }
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add blacklist state and parsing functions"
```

---

## Task 3: Load blacklist on page init

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in TagManagerPage init, after loadCategoryMappings)

**Step 1: Add loadBlacklist call**

Find `await loadCategoryMappings();` and add after it:

```javascript
        await loadBlacklist();
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): load blacklist on page init"
```

---

## Task 4: Filter matches in showMatchesModal

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in showMatchesModal, around line 1186)

**Step 1: Add filtering and count**

Find `showMatchesModal` function. At the start of the function (after getting `matches`), add filtering:

```javascript
    // Filter out blacklisted matches
    const originalCount = matches.length;
    const filteredMatches = matches.filter(m => !isBlacklisted(m.name));
    const hiddenCount = originalCount - filteredMatches.length;
    matches = filteredMatches;
```

**Step 2: Show hidden count in modal header**

Find the modal header HTML (around `<h3>Select Match for ...`) and add after the h3:

```javascript
                ${hiddenCount > 0 ? `<div class="tm-blacklist-notice">${hiddenCount} tag${hiddenCount > 1 ? 's' : ''} hidden by blacklist</div>` : ''}
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): filter blacklisted tags from match modal"
```

---

## Task 5: Add blacklist notice CSS

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (at end of file)

**Step 1: Add styles**

```css
/* Blacklist notice */
.tm-blacklist-notice {
  font-size: 0.85em;
  color: var(--bs-warning, #ffc107);
  margin-top: 4px;
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "style(tagManager): add blacklist notice styling"
```

---

## Task 6: Add Python blacklist utility module

**Files:**
- Create: `plugins/tagManager/blacklist.py`

**Step 1: Create the module**

```python
"""
Blacklist utility for filtering unwanted StashDB tags.

Blacklist format (stored in plugin settings):
- One pattern per line
- Literal strings for exact match (case-insensitive)
- Prefix with / for regex: /^\d+p$/, /Available$/
"""

import re
from typing import List, Optional


class Blacklist:
    """Parsed blacklist with literal and regex patterns."""

    def __init__(self, blacklist_str: Optional[str] = None):
        self.literals: set[str] = set()  # Lowercase literal patterns
        self.regexes: list[re.Pattern] = []

        if blacklist_str:
            self._parse(blacklist_str)

    def _parse(self, blacklist_str: str) -> None:
        """Parse blacklist string into patterns."""
        for line in blacklist_str.split('\n'):
            pattern = line.strip()
            if not pattern:
                continue

            if pattern.startswith('/'):
                # Regex pattern
                regex_str = pattern[1:]  # Remove leading /
                try:
                    self.regexes.append(re.compile(regex_str, re.IGNORECASE))
                except re.error as e:
                    print(f"[tagManager] Invalid regex in blacklist: {pattern} - {e}")
            else:
                # Literal pattern (case-insensitive)
                self.literals.add(pattern.lower())

    def is_blacklisted(self, tag_name: str) -> bool:
        """Check if a tag name matches any blacklist pattern."""
        if not tag_name:
            return False

        lower_name = tag_name.lower()

        # Check literal matches first (faster)
        if lower_name in self.literals:
            return True

        # Check regex patterns
        for regex in self.regexes:
            if regex.search(tag_name):
                return True

        return False

    def filter_tags(self, tags: list, name_key: str = 'name') -> tuple[list, int]:
        """
        Filter a list of tag objects, removing blacklisted ones.

        Args:
            tags: List of tag objects/dicts
            name_key: Key to access tag name (default 'name')

        Returns:
            Tuple of (filtered_tags, hidden_count)
        """
        if not self.literals and not self.regexes:
            return tags, 0

        filtered = []
        hidden = 0

        for tag in tags:
            name = tag.get(name_key) if isinstance(tag, dict) else getattr(tag, name_key, None)
            if name and self.is_blacklisted(name):
                hidden += 1
            else:
                filtered.append(tag)

        return filtered, hidden

    @property
    def count(self) -> int:
        """Total number of patterns."""
        return len(self.literals) + len(self.regexes)
```

**Step 2: Commit**

```bash
git add plugins/tagManager/blacklist.py
git commit -m "feat(tagManager): add Python blacklist utility module"
```

---

## Task 7: Filter matches in tag_manager.py handle_search

**Files:**
- Modify: `plugins/tagManager/tag_manager.py` (around line 223-297)

**Step 1: Import blacklist module**

At top of file with other imports:

```python
from blacklist import Blacklist
```

**Step 2: Add blacklist loading to handle_search**

Find `handle_search` function. At the start, load the blacklist from settings:

```python
def handle_search(args: dict) -> dict:
    """Search for matching tags in StashDB for a local tag."""
    # Load blacklist from settings
    settings = get_plugin_settings()
    blacklist = Blacklist(settings.get('tagBlacklist', ''))
```

**Step 3: Filter combined_matches before returning**

Find where `combined_matches` is built (around line 262-286). After the matches are combined, add:

```python
    # Filter out blacklisted tags
    combined_matches, hidden_count = blacklist.filter_tags(combined_matches)

    if hidden_count > 0:
        log.debug(f"Filtered {hidden_count} blacklisted tags from search results")
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag_manager.py
git commit -m "feat(tagManager): filter blacklisted tags from search results"
```

---

## Task 8: Filter tags in stashdb_scene_sync.py

**Files:**
- Modify: `plugins/tagManager/stashdb_scene_sync.py` (around line 70-146)

**Step 1: Import blacklist module**

At top with other imports:

```python
from blacklist import Blacklist
```

**Step 2: Add blacklist to sync_scene_tags function**

Find `sync_scene_tags()` function. At the start, load the blacklist:

```python
def sync_scene_tags(stash, settings: dict, dry_run: bool = True):
    """Main entry point for scene tag sync."""
    # Load blacklist
    blacklist = Blacklist(settings.get('tagBlacklist', ''))
    log.debug(f"Loaded blacklist with {blacklist.count} patterns")
```

**Step 3: Pass blacklist to process_scene**

Update `process_scene` call to pass blacklist:

```python
        result = process_scene(
            stash=stash,
            scene=scene,
            stashdb_scene=stashdb_scene,
            tag_cache=tag_cache,
            blacklist=blacklist,
            dry_run=dry_run
        )
```

**Step 4: Update process_scene to filter tags**

Add blacklist parameter and filter:

```python
def process_scene(
    stash,
    scene: dict,
    stashdb_scene: dict,
    tag_cache: LocalTagCache,
    blacklist: Blacklist,
    dry_run: bool = True
) -> dict:
    """Process a single scene's tag merge."""

    # Get StashDB tags, filtering out blacklisted ones
    stashdb_tags = stashdb_scene.get('tags', [])
    stashdb_tags, hidden_count = blacklist.filter_tags(stashdb_tags)

    if hidden_count > 0:
        log.debug(f"Scene {scene['id']}: Filtered {hidden_count} blacklisted tags")
```

**Step 5: Commit**

```bash
git add plugins/tagManager/stashdb_scene_sync.py
git commit -m "feat(tagManager): filter blacklisted tags during scene sync"
```

---

## Task 9: Write unit tests for blacklist

**Files:**
- Create: `plugins/tagManager/tests/test_blacklist.py`

**Step 1: Create test file**

```python
"""
Unit tests for blacklist module.
Run with: python -m pytest plugins/tagManager/tests/test_blacklist.py -v
"""

import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blacklist import Blacklist


class TestBlacklistParsing:
    """Test blacklist pattern parsing."""

    def test_empty_blacklist(self):
        bl = Blacklist(None)
        assert bl.count == 0

    def test_empty_string(self):
        bl = Blacklist('')
        assert bl.count == 0

    def test_literal_patterns(self):
        bl = Blacklist('4K Available\nFull HD Available')
        assert bl.count == 2
        assert len(bl.literals) == 2
        assert len(bl.regexes) == 0

    def test_regex_patterns(self):
        bl = Blacklist('/^\\d+p$/\n/Available$/')
        assert bl.count == 2
        assert len(bl.literals) == 0
        assert len(bl.regexes) == 2

    def test_mixed_patterns(self):
        bl = Blacklist('4K Available\n/Available$/')
        assert bl.count == 2
        assert len(bl.literals) == 1
        assert len(bl.regexes) == 1

    def test_invalid_regex_skipped(self):
        bl = Blacklist('/[invalid/')
        assert bl.count == 0  # Invalid regex should be skipped

    def test_blank_lines_ignored(self):
        bl = Blacklist('Pattern1\n\n\nPattern2\n  \n')
        assert bl.count == 2


class TestBlacklistMatching:
    """Test tag name matching."""

    def test_literal_exact_match(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('4K Available') is True

    def test_literal_case_insensitive(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('4k available') is True
        assert bl.is_blacklisted('4K AVAILABLE') is True

    def test_literal_no_partial_match(self):
        bl = Blacklist('4K')
        assert bl.is_blacklisted('4K Available') is False

    def test_regex_match(self):
        bl = Blacklist('/Available$/')
        assert bl.is_blacklisted('4K Available') is True
        assert bl.is_blacklisted('Full HD Available') is True
        assert bl.is_blacklisted('Available Now') is False  # Not at end

    def test_regex_resolution_pattern(self):
        bl = Blacklist('/^\\d+p$/')
        assert bl.is_blacklisted('1080p') is True
        assert bl.is_blacklisted('720p') is True
        assert bl.is_blacklisted('1080p Video') is False

    def test_non_blacklisted_tag(self):
        bl = Blacklist('4K Available')
        assert bl.is_blacklisted('Action') is False

    def test_empty_tag_name(self):
        bl = Blacklist('Pattern')
        assert bl.is_blacklisted('') is False
        assert bl.is_blacklisted(None) is False


class TestBlacklistFilter:
    """Test tag list filtering."""

    def test_filter_dict_tags(self):
        bl = Blacklist('4K Available')
        tags = [
            {'name': 'Action'},
            {'name': '4K Available'},
            {'name': 'Comedy'}
        ]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 2
        assert hidden == 1
        assert all(t['name'] != '4K Available' for t in filtered)

    def test_filter_empty_blacklist(self):
        bl = Blacklist('')
        tags = [{'name': 'Action'}, {'name': 'Comedy'}]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 2
        assert hidden == 0

    def test_filter_all_blacklisted(self):
        bl = Blacklist('Action\nComedy')
        tags = [{'name': 'Action'}, {'name': 'Comedy'}]
        filtered, hidden = bl.filter_tags(tags)
        assert len(filtered) == 0
        assert hidden == 2
```

**Step 2: Run tests**

```bash
python -m pytest plugins/tagManager/tests/test_blacklist.py -v
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tests/test_blacklist.py
git commit -m "test(tagManager): add unit tests for blacklist module"
```

---

## Task 10: Write JS unit tests for blacklist

**Files:**
- Create: `plugins/tagManager/tests/test_blacklist.js`

**Step 1: Create test file**

```javascript
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
  const result = parseBlacklist('/Available$/');
  assertEqual(result.length, 1);
  assertEqual(result[0].type, 'regex');
  assertEqual(result[0].pattern, 'Available$/');
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
  tagBlacklist = parseBlacklist('/Available$/');
  assertEqual(isBlacklisted('4K Available'), true);
  assertEqual(isBlacklisted('Full HD Available'), true);
  assertEqual(isBlacklisted('Available Now'), false);
});

test('resolution pattern', () => {
  tagBlacklist = parseBlacklist('/^\\d+p$/');
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
```

**Step 2: Run tests**

```bash
node plugins/tagManager/tests/test_blacklist.js
```

**Step 3: Commit**

```bash
git add plugins/tagManager/tests/test_blacklist.js
git commit -m "test(tagManager): add JS unit tests for blacklist"
```

---

## Task 11: Push and create PR

**Step 1: View commits**

```bash
git log --oneline feature/tag-manager-backlog..HEAD
```

**Step 2: Push branch**

```bash
git push -u origin feature/p3-tag-blacklist
```

**Step 3: Create PR**

```bash
gh pr create --base feature/tag-manager-backlog --title "feat(tagManager): tag blacklist/exclusion (P3)" --body "$(cat <<'EOF'
## Summary
- Users can now blacklist unwanted StashDB tags (e.g., "4K Available", quality indicators)
- Supports literal patterns (case-insensitive) and regex patterns (prefix with `/`)
- Blacklisted tags are filtered from:
  - Match suggestions in Tag Manager dialog
  - Backend search results
  - Scene tag sync operations
- Shows count of hidden tags when applicable

## Changes
- Added `tagBlacklist` setting to YAML
- Added `parseBlacklist()` and `isBlacklisted()` functions (JS)
- Added `blacklist.py` Python module with `Blacklist` class
- Updated `tag_manager.py` to filter search results
- Updated `stashdb_scene_sync.py` to filter during sync
- Added CSS for blacklist notice
- Added unit tests for both JS and Python

## Example Blacklist
```
4K Available
Full HD Available
/^\d+p$/
/Available$/
```

## Test plan
- [ ] Add patterns to Settings > Plugins > Tag Manager > Tag Blacklist
- [ ] In Tag Manager, search for a tag - blacklisted matches should be hidden
- [ ] Verify "X tags hidden by blacklist" notice appears
- [ ] Run scene tag sync - blacklisted tags should not be imported
- [ ] Test regex patterns work correctly

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Summary of Changes

| File | Changes |
|------|---------|
| `tagManager.yml` | Added `tagBlacklist` setting |
| `tag-manager.js` | Added `tagBlacklist` state, `parseBlacklist()`, `isBlacklisted()`, `loadBlacklist()`, filtering in match modal |
| `tag-manager.css` | Added blacklist notice styling |
| `blacklist.py` | New Python module with `Blacklist` class |
| `tag_manager.py` | Filter search results through blacklist |
| `stashdb_scene_sync.py` | Filter tags during scene sync |
| `tests/test_blacklist.py` | Python unit tests |
| `tests/test_blacklist.js` | JavaScript unit tests |

**Commits:**
1. `feat(tagManager): add tagBlacklist setting to YAML`
2. `feat(tagManager): add blacklist state and parsing functions`
3. `feat(tagManager): load blacklist on page init`
4. `feat(tagManager): filter blacklisted tags from match modal`
5. `style(tagManager): add blacklist notice styling`
6. `feat(tagManager): add Python blacklist utility module`
7. `feat(tagManager): filter blacklisted tags from search results`
8. `feat(tagManager): filter blacklisted tags during scene sync`
9. `test(tagManager): add unit tests for blacklist module`
10. `test(tagManager): add JS unit tests for blacklist`
