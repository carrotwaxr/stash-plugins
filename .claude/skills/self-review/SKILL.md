---
name: self-review
description: Perform a code review before creating a PR
---

# Code Review Quality Check

Perform a code review for the current stash-plugins branch. Your job is to ensure code quality, identify potential bugs or improvements, and find gaps in test coverage.

## Step 1: Understand What Changed

```bash
git diff main...HEAD --stat
git diff main...HEAD
```

Review the diff to understand the scope and intent of the changes before running automated checks.

## Step 2: Invoke Relevant Best-Practice Skills

Based on which files changed, invoke the corresponding skills to have their guidelines in context during review. Check the diff stat and invoke all that apply:

| Files changed | Skill to invoke |
|---|---|
| `plugins/*/tag-manager.js` or other frontend JS | `web-design-guidelines` |
| `plugins/*/*.py` (Python backend) | `python-fastapi` |
| `plugins/*/tests/*.py` (pytest) | None (follow existing patterns) |
| Plugin manifests (`*.yml`) | `stash-plugin-dev` |
| GraphQL queries (in JS or Python) | `graphql-patterns` |

Invoke these skills using the Skill tool before proceeding to the code quality review. You don't need to invoke every skill — only those relevant to the diff.

## Step 3: Code Quality Review

Review the diff against these guidelines (supplemented by the skills invoked above):

### General Principles

- **DRY** - Don't repeat yourself; extract shared logic
- **YAGNI** - Don't build features that aren't needed yet
- **Single Responsibility** - Functions do one thing well
- **Readable code over comments** - Code should be self-documenting through clear naming and structure. Use comments only for:
  - Explaining _why_ something unusual is done (not _what_)
  - Highlighting important gotchas or edge cases
  - Do NOT add comments that just describe what readable code already shows

### JavaScript (Stash Plugin Frontend)

- Vanilla JS only (no frameworks) — must work inside Stash's plugin system
- Use `escapeHtml()` for all user-facing dynamic strings (XSS prevention)
- Use existing `.tm-*` CSS class patterns for consistency
- Modal/dialog patterns should follow existing `tm-modal-backdrop > tm-modal` structure
- GraphQL queries should be minimal — only request needed fields
- State management via module-scoped variables (existing pattern)
- Event listeners should be cleaned up when modals are removed

### Python (Stash Plugin Backend)

- Use `stashapp-tools` library patterns for Stash API access
- Rate limiting for external API calls (StashDB)
- Proper error handling with meaningful log messages
- Cache management for expensive operations

### UI/UX

- Uses CSS variables (`var(--bs-*)`) not hardcoded colors (Stash uses Bootstrap)
- Consistent with existing plugin UI patterns
- Dialogs should have clear cancel/confirm flows

### Code Hygiene

- No unused imports or variables
- No leftover `console.log` statements (use `console.debug` for intentional logging)
- No hardcoded values that should be constants or config
- No commented-out code blocks

## Step 4: Automated Testing Checklist

Run each JS test file and fix any failures:

```bash
for f in plugins/tagManager/tests/test_*.js; do
  echo "--- Running $f ---"
  node "$f"
  echo
done
```

Expected: All tests pass

For Python tests (if changed):

```bash
cd plugins/tagManager && python -m pytest tests/ -v
```

Expected: All tests pass

## Step 5: Issue Severity Guide

**Blocking (must fix before PR):**

- Test failures
- Security issues (XSS, injection, exposed secrets)
- Broken functionality
- Missing error handling that could crash the plugin
- GraphQL mutations that could corrupt data

**Should fix (fix now or create follow-up issue):**

- Missing test coverage for new logic
- Performance issues (unnecessary API calls, O(n^2) loops on large tag sets)
- Inconsistent UI patterns
- Missing `escapeHtml()` on dynamic content

**Note for later (document but don't block):**

- Minor refactoring opportunities
- Nice-to-have improvements
- Tech debt observations

## Step 6: Create Pull Request

After all blocking issues are fixed, create a GitHub PR using `superpowers:finishing-a-development-branch`.
