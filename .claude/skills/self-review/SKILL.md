---
name: self-review
description: Perform a code review before creating a PR
---

# Repo self-review

Repo-specific checks before opening a PR. Read the diff first:

```bash
git diff main...HEAD --stat
git diff main...HEAD
```

Then confirm each item for the plugin(s) touched:

- Version bumped in the plugin `.yml`; root `CHANGELOG.md` and the plugin README changelog have a matching `### x.y.z` entry.
- `python .github/scripts/lint_manifests.py` exits 0.
- Tests added for the change and passing: `cd plugins/<plugin> && python -m pytest -q`, and `for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done` for UI changes.
- New UI functions are added to the `window.__<PLUGIN>_TEST__` export block.
- No runtime state written into the plugin dir; it belongs in `<config dir>/plugin_data/<plugin>/`.
- Errors are surfaced (`error` / `partial` / `warnings`), not swallowed, and never an empty result that reads as "nothing found".
- New outgoing requests send the `stash-plugins-<plugin>/<version>` User-Agent and verify TLS.
- Server strings put into `innerHTML` are escaped.
- No secrets, hostnames, paths or real names in code, tests or fixtures.
- README and USERGUIDE claims match the code.
- PR body has one `Closes #n` line per issue fixed.

See `.claude/rules/` for the conventions behind these checks.

For a general review run `/code-review`. To open the PR follow `fluffer:git-pr`.
