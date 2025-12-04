# Check Issues

Check for new issues from Discourse forum posts and GitHub, then update the issue tracker.

## Sources to Check

### Discourse Forum Posts
- Missing Scenes: https://discourse.stashapp.cc/t/missing-scenes/4620
- mcMetadata: https://discourse.stashapp.cc/t/mcmetadata/1751
- Performer Image Search: https://discourse.stashapp.cc/t/performer-image-search/4581

### GitHub
- Repository: https://github.com/carrotwaxr/stash-plugins/issues

## Instructions

1. **Fetch current issues** from all sources above using WebFetch for Discourse and `gh issue list` for GitHub

2. **Read the issue tracker** at `ISSUES.md` in the repo root to see what's already tracked

3. **Identify new issues** by comparing fetched content against tracked issues:
   - Look for new replies/questions in Discourse threads
   - Look for new GitHub issues not in the tracker
   - Ignore already-resolved items marked as CLOSED in the tracker

4. **Update ISSUES.md** with any new issues found:
   - Add new issues with status `OPEN`
   - Include source (Discourse/GitHub), date discovered, plugin affected, and description
   - Preserve existing entries

5. **Present findings to the developer**:
   - Summarize new issues found
   - For each new issue, ask if it's valid and worth addressing
   - Discuss priority and approach
   - Update status in ISSUES.md based on discussion (OPEN, IN_PROGRESS, WONTFIX, CLOSED)

6. **Clean up closed items**:
   - Check if any OPEN issues have been resolved (GitHub issue closed, Discourse question answered)
   - Mark resolved issues as CLOSED with resolution date

## Issue Tracker Format

The ISSUES.md file uses this format:

```markdown
# Issue Tracker

## Open Issues

| ID | Source | Plugin | Summary | Status | Date | Notes |
|----|--------|--------|---------|--------|------|-------|
| 1 | Discourse | missingScenes | User can't configure endpoint | OPEN | 2024-12-03 | |

## Closed Issues

| ID | Source | Plugin | Summary | Status | Date | Resolution |
|----|--------|--------|---------|--------|------|------------|
| 0 | GitHub | mcMetadata | XML escaping bug | CLOSED | 2024-11-15 | Fixed in v1.2.0 |
```
