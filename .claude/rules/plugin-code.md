# Plugin code conventions

- UI files are a single no-build IIFE per file. Expose functions to the vm test harness through a `window.__<PLUGIN>_TEST__` export block (see `plugins/tagManager/tag-manager.js` and `plugins/tagManager/tests/harness.js`).
- Data functions return data or raise. Responses carry `error`, `partial` and `warnings` fields that the UI renders. Never return an empty result that reads as "nothing found" when something failed.
- Runtime state (caches, history, settings) goes in `<Stash config dir>/plugin_data/<plugin>/` via the plugin's `plugin_data.py`, never in the plugin dir (updates replace it).
- Every outgoing HTTP request sends a `stash-plugins-<plugin>/<version>` User-Agent. ThePornDB's Cloudflare blocks Python's default.
- Verify TLS for public hosts.
- Escape every server-provided string before putting it in `innerHTML`.
- Python standard library only, unless the plugin already depends on something (see its `requirements.txt`).
