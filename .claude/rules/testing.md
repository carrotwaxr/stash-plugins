# Testing

Run from the repo root. Each plugin's tests run from its own directory (the root `pytest.ini` is shared).

```bash
cd plugins/<plugin> && python -m pytest -q          # Python tests for one plugin
for f in plugins/*/tests/test_*.js; do node "$f" || exit 1; done   # JS tests
python .github/scripts/lint_manifests.py            # manifest lint; check the exit code
```

CI (`.github/workflows/test.yml`) runs the same three, plus `node --check` on every plugin JS file.

- Write the failing test first, watch it fail, then fix.
- Tests marked `network` are opt-in (`-m network`); `pytest.ini` deselects them.
- Live integration tests run only with `STASH_PLUGINS_INTEGRATION=1`. Never point tests at a real Stash instance.
- No credentials, API keys or real hostnames in tests or fixtures. Fixtures use fake names.
- Suites must pass offline.
