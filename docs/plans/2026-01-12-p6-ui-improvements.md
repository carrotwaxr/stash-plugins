# P6: Tag Manager UI Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix UI issues in Tag Manager: button layout with long aliases, match dialog clarity, and alias handling UX.

**Architecture:** CSS fixes for layout issues, enhanced diff dialog with visual selection indicators and difference highlighting.

**Tech Stack:** CSS flexbox improvements, JavaScript string comparison for diff highlighting

---

## Task 1: Fix Tag Row Layout for Long Aliases

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (lines 129-168)

**Problem:** When tags have long alias lists, the "Find Match" button wraps awkwardly and becomes half-width.

**Step 1: Update CSS for tag row layout**

The current `.tm-tag-row` uses `justify-content: space-between` which can cause issues when content wraps. The `.tm-tag-match` section needs to stay together and not break across lines.

Update the CSS:

```css
.tm-tag-row {
  display: flex;
  align-items: flex-start; /* Changed from center - allows multi-line aliases */
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--bs-secondary-bg, #2d2d44);
  border-radius: 6px;
  gap: 20px;
}

.tm-tag-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 200px;
  flex: 1; /* Take available space */
  max-width: 50%; /* Don't push match section off */
}

.tm-tag-aliases {
  font-size: 0.85em;
  color: var(--bs-secondary-color, #888);
  word-break: break-word; /* Allow long aliases to wrap */
  line-height: 1.4;
}

.tm-tag-match {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0; /* Don't shrink - keep buttons full size */
  flex-wrap: nowrap; /* Keep match info and buttons on same line */
}

.tm-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0; /* Keep buttons full size */
}
```

**Step 2: Verify the layout**

Test with tags that have many aliases to ensure:
- Aliases wrap within their column
- Buttons stay full-width and don't wrap
- Match info and buttons stay together

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "fix(tagManager): prevent button layout issues with long alias lists"
```

---

## Task 2: Add Visual Selection Indicators to Match Dialog

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (showDiffDialog function)
- Modify: `plugins/tagManager/tag-manager.css`

**Problem:** Hard to tell which value will be used in the diff dialog. Need checkmarks or highlighting.

**Step 1: Add CSS for selection indicators**

Add to `tag-manager.css`:

```css
/* Match dialog selection indicators */
.tm-diff-table td {
  position: relative;
}

.tm-diff-value {
  padding: 8px 12px;
  border-radius: 4px;
  transition: all 0.2s ease;
}

.tm-diff-value.selected {
  background: rgba(25, 135, 84, 0.15);
  outline: 2px solid var(--bs-success, #198754);
  outline-offset: -2px;
}

.tm-diff-value.selected::after {
  content: '✓';
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--bs-success, #198754);
  font-weight: bold;
}

.tm-diff-value:not(.selected) {
  opacity: 0.6;
}
```

**Step 2: Update showDiffDialog to wrap values and track selection**

In `showDiffDialog()`, update the table to wrap values in divs with selection classes. The table body should look like:

```javascript
<tbody>
  <tr>
    <td>Name</td>
    <td><div class="tm-diff-value" id="tm-name-local">${escapeHtml(tag.name) || '<em>empty</em>'}</div></td>
    <td><div class="tm-diff-value" id="tm-name-stashdb">${escapeHtml(stashdbTag.name)}</div></td>
    <td>
      <label><input type="radio" name="tm-name" value="local_add_alias" ${nameDefault === 'local_add_alias' ? 'checked' : ''}> Keep + Add alias</label>
      <label><input type="radio" name="tm-name" value="local" ${nameDefault === 'local' ? 'checked' : ''}> Keep</label>
      <label><input type="radio" name="tm-name" value="stashdb" ${nameDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
    </td>
  </tr>
  <tr>
    <td>Description</td>
    <td><div class="tm-diff-value" id="tm-desc-local">${escapeHtml(tag.description) || '<em>empty</em>'}</div></td>
    <td><div class="tm-diff-value" id="tm-desc-stashdb">${escapeHtml(stashdbTag.description) || '<em>empty</em>'}</div></td>
    <td>
      <label><input type="radio" name="tm-desc" value="local" ${descDefault === 'local' ? 'checked' : ''}> Keep</label>
      <label><input type="radio" name="tm-desc" value="stashdb" ${descDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
    </td>
  </tr>
</tbody>
```

**Step 3: Add function to update selection visuals**

Add after `renderAliasPills()` function:

```javascript
function updateSelectionVisuals() {
  // Name selection
  const nameChoice = modal.querySelector('input[name="tm-name"]:checked')?.value;
  modal.querySelector('#tm-name-local')?.classList.toggle('selected', nameChoice === 'local' || nameChoice === 'local_add_alias');
  modal.querySelector('#tm-name-stashdb')?.classList.toggle('selected', nameChoice === 'stashdb');

  // Description selection
  const descChoice = modal.querySelector('input[name="tm-desc"]:checked')?.value;
  modal.querySelector('#tm-desc-local')?.classList.toggle('selected', descChoice === 'local');
  modal.querySelector('#tm-desc-stashdb')?.classList.toggle('selected', descChoice === 'stashdb');
}
```

**Step 4: Call updateSelectionVisuals on load and on change**

After initializing the modal:
```javascript
// Initialize selection visuals
updateSelectionVisuals();

// Update visuals when radio buttons change
modal.querySelectorAll('input[type="radio"]').forEach(radio => {
  radio.addEventListener('change', updateSelectionVisuals);
});
```

**Step 5: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): add visual selection indicators to match dialog"
```

---

## Task 3: Add Difference Highlighting for Near-Identical Values

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Problem:** Hard to spot differences like "A Tag On a Scene" vs "A Tag on a Scene" (case difference).

**Step 1: Add CSS for difference highlighting**

```css
/* Difference highlighting */
.tm-diff-char {
  background: rgba(255, 193, 7, 0.3);
  border-radius: 2px;
  padding: 0 1px;
}

.tm-diff-identical {
  color: var(--bs-success, #198754);
  font-size: 0.85em;
  font-style: italic;
}
```

**Step 2: Add highlightDifferences function**

```javascript
/**
 * Highlight character-level differences between two strings
 * Returns HTML with differing characters wrapped in <span class="tm-diff-char">
 */
function highlightDifferences(str1, str2) {
  if (!str1 || !str2) return { html1: escapeHtml(str1 || ''), html2: escapeHtml(str2 || ''), identical: false };

  if (str1 === str2) {
    return { html1: escapeHtml(str1), html2: escapeHtml(str2), identical: true };
  }

  // Simple character-by-character comparison
  let html1 = '';
  let html2 = '';
  const len = Math.max(str1.length, str2.length);

  for (let i = 0; i < len; i++) {
    const c1 = str1[i] || '';
    const c2 = str2[i] || '';

    if (c1 !== c2) {
      html1 += c1 ? `<span class="tm-diff-char">${escapeHtml(c1)}</span>` : '';
      html2 += c2 ? `<span class="tm-diff-char">${escapeHtml(c2)}</span>` : '';
    } else {
      html1 += escapeHtml(c1);
      html2 += escapeHtml(c2);
    }
  }

  return { html1, html2, identical: false };
}
```

**Step 3: Use highlightDifferences in showDiffDialog**

When rendering the name row:

```javascript
const nameDiff = highlightDifferences(tag.name, stashdbTag.name);
const descDiff = highlightDifferences(tag.description || '', stashdbTag.description || '');

// In the table:
<tr>
  <td>Name</td>
  <td><div class="tm-diff-value" id="tm-name-local">${nameDiff.html1 || '<em>empty</em>'}</div></td>
  <td><div class="tm-diff-value" id="tm-name-stashdb">${nameDiff.html2}${nameDiff.identical ? ' <span class="tm-diff-identical">(identical)</span>' : ''}</div></td>
  ...
</tr>
<tr>
  <td>Description</td>
  <td><div class="tm-diff-value" id="tm-desc-local">${descDiff.html1 || '<em>empty</em>'}</div></td>
  <td><div class="tm-diff-value" id="tm-desc-stashdb">${descDiff.html2 || '<em>empty</em>'}</div></td>
  ...
</tr>
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): highlight character differences in match dialog"
```

---

## Task 4: Improve Alias Handling UI

**Files:**
- Modify: `plugins/tagManager/tag-manager.js`
- Modify: `plugins/tagManager/tag-manager.css`

**Problem:** Current alias UI is confusing. Need clearer separation of local vs StashDB aliases with checkbox interface.

**Step 1: Add CSS for improved alias UI**

```css
/* Improved alias handling */
.tm-alias-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 12px;
}

.tm-alias-column {
  background: var(--bs-tertiary-bg, #3d3d5c);
  border-radius: 6px;
  padding: 12px;
}

.tm-alias-column-header {
  font-weight: 500;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--bs-border-color, #444);
}

.tm-alias-checkbox-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 150px;
  overflow-y: auto;
}

.tm-alias-checkbox-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tm-alias-checkbox-item input[type="checkbox"] {
  flex-shrink: 0;
}

.tm-alias-checkbox-item label {
  word-break: break-word;
  cursor: pointer;
}

.tm-alias-checkbox-item.new-from-other {
  color: var(--bs-info, #0dcaf0);
  font-style: italic;
}

.tm-final-aliases-section {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--bs-border-color, #444);
}

.tm-final-aliases-header {
  font-weight: 500;
  margin-bottom: 8px;
}
```

**Step 2: Update showDiffDialog alias section**

Replace the current alias row with a two-column layout:

```javascript
<tr>
  <td>Aliases</td>
  <td colspan="3">
    <div class="tm-alias-columns">
      <div class="tm-alias-column">
        <div class="tm-alias-column-header">Your Aliases</div>
        <div class="tm-alias-checkbox-list" id="tm-local-aliases">
          ${(tag.aliases || []).map(a => `
            <div class="tm-alias-checkbox-item">
              <input type="checkbox" id="alias-local-${escapeHtml(a)}" data-alias="${escapeHtml(a)}" data-source="local" checked>
              <label for="alias-local-${escapeHtml(a)}">${escapeHtml(a)}</label>
            </div>
          `).join('') || '<em>none</em>'}
          ${(stashdbTag.aliases || []).filter(a => !(tag.aliases || []).some(la => la.toLowerCase() === a.toLowerCase())).map(a => `
            <div class="tm-alias-checkbox-item new-from-other">
              <input type="checkbox" id="alias-add-local-${escapeHtml(a)}" data-alias="${escapeHtml(a)}" data-source="stashdb" checked>
              <label for="alias-add-local-${escapeHtml(a)}">${escapeHtml(a)} (from StashDB)</label>
            </div>
          `).join('')}
        </div>
      </div>
      <div class="tm-alias-column">
        <div class="tm-alias-column-header">StashDB Aliases</div>
        <div class="tm-alias-checkbox-list" id="tm-stashdb-aliases">
          ${(stashdbTag.aliases || []).map(a => `
            <div class="tm-alias-checkbox-item">
              <input type="checkbox" id="alias-stashdb-${escapeHtml(a)}" data-alias="${escapeHtml(a)}" data-source="stashdb" checked>
              <label for="alias-stashdb-${escapeHtml(a)}">${escapeHtml(a)}</label>
            </div>
          `).join('') || '<em>none</em>'}
          ${(tag.aliases || []).filter(a => !(stashdbTag.aliases || []).some(sa => sa.toLowerCase() === a.toLowerCase())).map(a => `
            <div class="tm-alias-checkbox-item new-from-other">
              <input type="checkbox" id="alias-add-stashdb-${escapeHtml(a)}" data-alias="${escapeHtml(a)}" data-source="local" checked>
              <label for="alias-add-stashdb-${escapeHtml(a)}">${escapeHtml(a)} (from local)</label>
            </div>
          `).join('')}
        </div>
      </div>
    </div>
    <div class="tm-final-aliases-section">
      <div class="tm-final-aliases-header">Final aliases:</div>
      <div class="tm-alias-pills" id="tm-alias-pills"></div>
    </div>
  </td>
</tr>
```

**Step 3: Update alias pill rendering to use checkboxes**

Update `renderAliasPills()` to read from checkboxes:

```javascript
function updateAliasesFromCheckboxes() {
  editableAliases.clear();
  modal.querySelectorAll('.tm-alias-checkbox-item input[type="checkbox"]:checked').forEach(cb => {
    editableAliases.add(cb.dataset.alias);
  });
  renderAliasPills();
}

// Attach to checkboxes
modal.querySelectorAll('.tm-alias-checkbox-item input[type="checkbox"]').forEach(cb => {
  cb.addEventListener('change', updateAliasesFromCheckboxes);
});
```

**Step 4: Commit**

```bash
git add plugins/tagManager/tag-manager.js plugins/tagManager/tag-manager.css
git commit -m "feat(tagManager): improve alias merge UI with checkbox columns"
```

---

## Task 5: Final Testing and PR

**Files:**
- Review all changes

**Step 1: Test all UI improvements**

Test scenarios:
1. Tag row with many aliases - buttons should stay full size
2. Match dialog - selected value should have green checkmark
3. Match dialog - character differences should be highlighted in yellow
4. Alias handling - two columns with checkboxes should work
5. Final aliases should update when checkboxes change

**Step 2: Create PR**

```bash
gh pr create --base feature/tag-manager-backlog --title "feat(tagManager): P6 - UI improvements" --body "$(cat <<'EOF'
## Summary

Improves Tag Manager UI based on user feedback:

- **Tag row layout**: Fixed button wrapping issues with long alias lists
- **Match dialog selection**: Added visual indicators (green checkmark) for selected values
- **Difference highlighting**: Character-level highlighting for near-identical values (e.g., case differences)
- **Alias handling**: Two-column checkbox UI for clearer local vs StashDB alias management

## Test Plan

- [ ] Tags with many aliases display correctly (buttons don't wrap)
- [ ] Match dialog shows green checkmark on selected value
- [ ] Character differences are highlighted in yellow
- [ ] Alias columns show local and StashDB aliases separately
- [ ] Checking/unchecking aliases updates the final list
EOF
)"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Fix tag row flexbox to prevent button wrapping |
| 2 | Add green checkmark/outline for selected values |
| 3 | Highlight character differences between similar values |
| 4 | Two-column checkbox UI for alias management |
| 5 | Testing and PR |
