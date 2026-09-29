# Deploying for manual testing

Deploy to the test instance only. Host, paths and API key are in your local `CLAUDE.md` and `.env` (both git-ignored); do not copy them into tracked files.

```bash
rsync -av --delete --delete-excluded \
  --exclude tests --exclude __pycache__ --exclude .pytest_cache \
  --exclude conftest.py --exclude 'test_*.py' --exclude data --exclude '.env*' \
  plugins/<plugin>/ <user>@<host>:<test plugins path>/<plugin>/
```

`--delete --delete-excluded` makes the target match the runtime file set that `build_site.sh` ships. Then reload plugins in the Stash UI (Settings > Plugins > Reload).

- The test instance is shared with other sessions. Snapshot anything you change (database, settings) and restore it exactly.
- Never deploy to production. The maintainer installs through the plugin index.
