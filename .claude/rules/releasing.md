# Releasing

- Publishing is merging to `main`. The deploy workflow runs the tests, then builds the Pages plugin index.
- For each plugin change: bump `version` in the plugin's `.yml`, add a `### x.y.z` entry under that plugin in the root `CHANGELOG.md`, and add the same to the plugin README's changelog.
- Put `Closes #n` on its own line in the PR body for each issue it fixes. Only the first keyword on a line links, so one issue per line.
- Squash merges.
- No AI attribution in commits or PRs.
