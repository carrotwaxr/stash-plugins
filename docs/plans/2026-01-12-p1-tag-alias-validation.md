# P1: Tag Alias Validation Bug Fix

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the tag alias bug where accepting a match creates illegal aliases (self-referential or conflicts).

**Architecture:** Add pre-validation before save to auto-fix self-references and detect conflicts. Add enhanced error handling with actionable options (merge/edit/remove) when server rejects. All changes in JavaScript frontend.

**Tech Stack:** JavaScript (Stash plugin API), CSS

---

## Background

When accepting a tag match in Tag Manager, two bugs occur:

1. **Self-referential alias:** When renaming to StashDB name, the new name ends up in aliases (illegal)
2. **Name not changing:** When selecting "StashDB" for name, the rename sometimes doesn't happen

Root cause: `editableAliases` is initialized with merged aliases from both sources, but the final name is never removed from this set before save.

**Stash validation rules:**
- Tag names globally unique
- Aliases globally unique
- No name↔alias overlap (including own name)
- Case sensitive

---

## Task 1: Add sanitizeAliasesForSave function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (insert after `escapeHtml` function, around line 390)

**Step 1: Add the sanitization function**

Insert after the `escapeHtml` function (around line 390):

```javascript
  /**
   * Sanitize aliases before saving - removes the final name from alias set
   * to prevent self-referential aliases (tag can't have its own name as alias).
   *
   * @param {Set} aliases - The editable aliases set
   * @param {string} finalName - The name the tag will have after save
   * @param {string} currentLocalName - The tag's current local name
   * @returns {string[]} - Cleaned array of aliases
   */
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
```

**Step 2: Verify syntax**

Run in browser console or check file loads without errors.

**Step 3: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add sanitizeAliasesForSave helper function"
```

---

## Task 2: Add findConflictingTag function for pre-validation

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (insert after `sanitizeAliasesForSave`)

**Step 1: Add the conflict detection function**

```javascript
  /**
   * Find a local tag that conflicts with a given name (as name or alias).
   * Used for pre-validation before saving.
   *
   * @param {string} name - The name to check for conflicts
   * @param {string} excludeTagId - Tag ID to exclude from search (the tag being edited)
   * @returns {object|null} - The conflicting tag or null
   */
  function findConflictingTag(name, excludeTagId) {
    const lowerName = name.toLowerCase();
    return localTags.find(t =>
      t.id !== excludeTagId && (
        t.name.toLowerCase() === lowerName ||
        t.aliases?.some(a => a.toLowerCase() === lowerName)
      )
    ) || null;
  }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add findConflictingTag helper for pre-validation"
```

---

## Task 3: Add validateBeforeSave function

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (insert after `findConflictingTag`)

**Step 1: Add the validation function**

```javascript
  /**
   * Validate tag update before attempting to save.
   * Checks for name and alias conflicts with other local tags.
   *
   * @param {string} finalName - The name the tag will have
   * @param {string[]} aliases - The aliases to save
   * @param {string} currentTagId - The ID of the tag being edited
   * @returns {object[]} - Array of error objects, empty if valid
   */
  function validateBeforeSave(finalName, aliases, currentTagId) {
    const errors = [];

    // Check if final name conflicts with another tag
    const nameConflict = findConflictingTag(finalName, currentTagId);
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
      const aliasConflict = findConflictingTag(alias, currentTagId);
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
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add validateBeforeSave function for conflict detection"
```

---

## Task 4: Update showDiffDialog Apply handler - sanitize aliases

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in `showDiffDialog` function, Apply click handler around line 863-890)

**Step 1: Update the Apply handler to sanitize aliases**

Find this code (around line 863-890):

```javascript
    modal.querySelector('.tm-apply-btn').addEventListener('click', async () => {
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;

      // Use the selected stash-box endpoint
      const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
      console.debug(`[tagManager] Saving stash_id with endpoint: ${endpoint}`);

      // Build update input
      const updateInput = {
        id: tag.id,
        stash_ids: [{
          endpoint: endpoint,
          stash_id: stashdbTag.id,
        }],
      };

      if (nameChoice === 'stashdb') {
        updateInput.name = stashdbTag.name;
      }

      if (descChoice === 'stashdb') {
        updateInput.description = stashdbTag.description || '';
      }

      // Use the edited aliases directly (user has full control via pill UI)
      updateInput.aliases = Array.from(editableAliases);
```

Replace with:

```javascript
    modal.querySelector('.tm-apply-btn').addEventListener('click', async () => {
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;
      const errorEl = modal.querySelector('#tm-diff-error');

      // Hide any previous error
      errorEl.style.display = 'none';
      errorEl.innerHTML = '';

      // Use the selected stash-box endpoint
      const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
      console.debug(`[tagManager] Saving stash_id with endpoint: ${endpoint}`);

      // Determine final name
      const finalName = nameChoice === 'stashdb' ? stashdbTag.name : tag.name;

      // Sanitize aliases - remove final name to prevent self-referential alias
      const sanitizedAliases = sanitizeAliasesForSave(editableAliases, finalName, tag.name);

      // Build update input
      const updateInput = {
        id: tag.id,
        stash_ids: [{
          endpoint: endpoint,
          stash_id: stashdbTag.id,
        }],
      };

      if (nameChoice === 'stashdb') {
        updateInput.name = stashdbTag.name;
      }

      if (descChoice === 'stashdb') {
        updateInput.description = stashdbTag.description || '';
      }

      // Use sanitized aliases
      updateInput.aliases = sanitizedAliases;
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "fix(tagManager): sanitize aliases before save to prevent self-reference"
```

---

## Task 5: Add pre-validation check before save

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in Apply handler, after building updateInput)

**Step 1: Add pre-validation**

After the line `updateInput.aliases = sanitizedAliases;`, add:

```javascript
      // Pre-validation: check for conflicts before hitting API
      const validationErrors = validateBeforeSave(finalName, sanitizedAliases, tag.id);
      if (validationErrors.length > 0) {
        const err = validationErrors[0]; // Show first error
        const conflictTag = err.conflictsWith;

        if (err.type === 'name_conflict') {
          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot rename to "${escapeHtml(err.value)}" - this name already exists.
            </div>
            <div class="tm-error-actions">
              <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                Edit "${escapeHtml(conflictTag.name)}"
              </a>
              <button type="button" class="btn btn-secondary btn-sm tm-error-keep-local">
                Keep local name instead
              </button>
            </div>
          `;
        } else {
          errorEl.innerHTML = `
            <div class="tm-error-message">
              Alias "${escapeHtml(err.value)}" conflicts with tag "${escapeHtml(conflictTag.name)}".
            </div>
            <div class="tm-error-actions">
              <button type="button" class="btn btn-secondary btn-sm tm-error-remove-alias" data-alias="${escapeHtml(err.value)}">
                Remove from aliases
              </button>
              <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                Edit "${escapeHtml(conflictTag.name)}"
              </a>
            </div>
          `;
        }

        errorEl.style.display = 'block';

        // Attach action handlers
        const keepLocalBtn = errorEl.querySelector('.tm-error-keep-local');
        if (keepLocalBtn) {
          keepLocalBtn.addEventListener('click', () => {
            modal.querySelector('input[name="tm-name"][value="local"]').checked = true;
            errorEl.style.display = 'none';
          });
        }

        const removeAliasBtn = errorEl.querySelector('.tm-error-remove-alias');
        if (removeAliasBtn) {
          removeAliasBtn.addEventListener('click', () => {
            const aliasToRemove = removeAliasBtn.dataset.alias;
            editableAliases.delete(aliasToRemove);
            renderAliasPills();
            errorEl.style.display = 'none';
          });
        }

        return; // Don't proceed with save
      }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): add pre-validation with actionable error options"
```

---

## Task 6: Enhance server error handling

**Files:**
- Modify: `plugins/tagManager/tag-manager.js` (in Apply handler catch block, around line 907-919)

**Step 1: Replace the simple error handler**

Find this code:

```javascript
      } catch (e) {
        const errorEl = modal.querySelector('#tm-diff-error');
        if (errorEl) {
          // Parse error message for friendlier display
          let errorMsg = e.message;
          const aliasConflictMatch = errorMsg.match(/tag with name '([^']+)' already exists/i);
          if (aliasConflictMatch) {
            errorMsg = `Cannot save: "${aliasConflictMatch[1]}" conflicts with an existing tag name. Remove it from aliases to continue.`;
          }
          errorEl.textContent = errorMsg;
          errorEl.style.display = 'block';
        }
      }
```

Replace with:

```javascript
      } catch (e) {
        console.error('[tagManager] Save error:', e.message);

        // Parse "tag with name 'X' already exists"
        const nameExistsMatch = e.message.match(/tag with name '([^']+)' already exists/i);
        if (nameExistsMatch) {
          const conflictName = nameExistsMatch[1];
          const conflictTag = findConflictingTag(conflictName, tag.id);

          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot save: "${escapeHtml(conflictName)}" conflicts with an existing tag.
            </div>
            <div class="tm-error-actions">
              ${conflictTag ? `
                <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                  Edit "${escapeHtml(conflictTag.name)}"
                </a>
              ` : ''}
              <button type="button" class="btn btn-secondary btn-sm tm-error-remove-alias" data-alias="${escapeHtml(conflictName)}">
                Remove from aliases
              </button>
            </div>
          `;
          errorEl.style.display = 'block';

          const removeBtn = errorEl.querySelector('.tm-error-remove-alias');
          if (removeBtn) {
            removeBtn.addEventListener('click', () => {
              editableAliases.delete(removeBtn.dataset.alias);
              renderAliasPills();
              errorEl.style.display = 'none';
            });
          }
          return;
        }

        // Parse "name 'X' is used as alias for 'Y'"
        const aliasUsedMatch = e.message.match(/name '([^']+)' is used as alias for '([^']+)'/i);
        if (aliasUsedMatch) {
          const [, conflictName, otherTagName] = aliasUsedMatch;
          const otherTag = localTags.find(t => t.name === otherTagName);

          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot use "${escapeHtml(conflictName)}" - it's an alias on "${escapeHtml(otherTagName)}".
            </div>
            <div class="tm-error-actions">
              ${otherTag ? `
                <a href="/tags/${otherTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                  Edit "${escapeHtml(otherTagName)}"
                </a>
              ` : ''}
              <button type="button" class="btn btn-secondary btn-sm tm-error-keep-local">
                Keep local name instead
              </button>
            </div>
          `;
          errorEl.style.display = 'block';

          const keepLocalBtn = errorEl.querySelector('.tm-error-keep-local');
          if (keepLocalBtn) {
            keepLocalBtn.addEventListener('click', () => {
              modal.querySelector('input[name="tm-name"][value="local"]').checked = true;
              errorEl.style.display = 'none';
            });
          }
          return;
        }

        // Fallback for unknown errors
        errorEl.innerHTML = `<div class="tm-error-message">${escapeHtml(e.message)}</div>`;
        errorEl.style.display = 'block';
      }
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.js
git commit -m "feat(tagManager): enhance server error handling with actionable options"
```

---

## Task 7: Add CSS for error action buttons

**Files:**
- Modify: `plugins/tagManager/tag-manager.css` (after `.tm-modal-error` styles, around line 469)

**Step 1: Add error action styles**

After the `.tm-modal-error` block (around line 469), add:

```css
.tm-error-message {
  margin-bottom: 10px;
}

.tm-error-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tm-error-actions .btn {
  font-size: 0.85em;
}
```

**Step 2: Commit**

```bash
git add plugins/tagManager/tag-manager.css
git commit -m "style(tagManager): add CSS for error action buttons"
```

---

## Task 8: Manual testing

**No code changes - testing only**

**Step 1: Test self-reference fix**

1. Open Tag Manager in Stash
2. Find a tag that matches to a different StashDB name (e.g., local "Passion" matches StashDB "Attraction")
3. Select "StashDB" for name
4. Click Apply
5. **Verify:** The StashDB name is NOT in the aliases after save

**Step 2: Test pre-validation**

1. Create two tags locally with names "TestA" and "TestB"
2. Try to match "TestA" to a StashDB tag
3. Manually add "TestB" to the aliases in the dialog
4. Click Apply
5. **Verify:** Pre-validation catches the conflict, shows error with "Remove from aliases" button
6. Click "Remove from aliases"
7. **Verify:** Alias is removed, error clears

**Step 3: Test name conflict**

1. Try to rename a tag to match another existing tag's name
2. **Verify:** Error shows with "Keep local name instead" button
3. Click the button
4. **Verify:** Radio switches to "Keep", error clears

**Step 4: Commit test notes (optional)**

If tests pass, no additional commit needed.

---

## Task 9: Final commit and push

**Step 1: Verify all changes**

```bash
git log --oneline -10
git diff main..HEAD --stat
```

**Step 2: Push branch**

```bash
git push -u origin bugfix/tag-alias-validation
```

---

## Summary of Changes

| File | Changes |
|------|---------|
| `tag-manager.js` | Added `sanitizeAliasesForSave()`, `findConflictingTag()`, `validateBeforeSave()`. Updated Apply handler with sanitization and pre-validation. Enhanced error handling with actionable buttons. |
| `tag-manager.css` | Added `.tm-error-message` and `.tm-error-actions` styles |

**Commits:**
1. `feat(tagManager): add sanitizeAliasesForSave helper function`
2. `feat(tagManager): add findConflictingTag helper for pre-validation`
3. `feat(tagManager): add validateBeforeSave function for conflict detection`
4. `fix(tagManager): sanitize aliases before save to prevent self-reference`
5. `feat(tagManager): add pre-validation with actionable error options`
6. `feat(tagManager): enhance server error handling with actionable options`
7. `style(tagManager): add CSS for error action buttons`
