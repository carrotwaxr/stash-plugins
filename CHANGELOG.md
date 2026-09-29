# Changelog

Changes to each plugin, newest first. Stash shows the installed and available version of each plugin under **Settings → Plugins**. Older history for mcMetadata and Tag Manager is in their READMEs.

## Missing Scenes

### 1.4.1
- Certificates are now verified for StashDB, ThePornDB and other stash-boxes, and for Whisparr. A new **Whisparr: Skip TLS Verification** setting covers an HTTPS Whisparr with a self-signed certificate.
- The stats bar escapes performer, studio and stash-box names.

## Scene Matcher

### 1.1.1
- Certificates are now verified for stash-box requests.

## Performer Image Search

### 1.4.1
- Only images hosted by the source that found them can be set as a performer image. DuckDuckGo results must be on a public host.

## Tag Manager

### 0.7.0
- Python 3.9+ with no required packages. `thefuzz` is optional. `stashapp-tools` is no longer used (fixes #129).
- A full StashDB tag fetch takes about 5 seconds instead of 30-40+. Stash-box errors show in the UI, and a rejected API key (HTTP 401/403) says so. Requests send a `User-Agent`, which fixes HTTP 403 from ThePornDB and JAVStash.
- Caches and sync history moved to `<Stash config dir>/plugin_data/tagManager/`, so plugin updates no longer wipe them. The old `cache/` folder can be deleted.
- Scene Tag Sync uses every linked stash-box that has an API key, keeps tags added during a long sync, and doesn't add back tags you removed. New "Reset Scene Tag Sync History" task. The first live sync after upgrading can re-add tags you removed before 0.7.0 one last time.
- Blacklist: `/regex/flags` syntax, `,` and `;` separators, a Blacklist editor on the Match tab, and it now applies to searches, Import All and sync.
- Accept/Apply: saved category mappings are pre-selected, and `Create "<category>"` now really creates the parent. Category mappings are stored per stash-box.
- Merging tags asks for confirmation. On Stash 0.31+ the merge is one transaction. Parents and children carry over.
- Import shows progress and can be cancelled. The conflicts dialog re-checks rows after each action.
- Tag Hierarchy, navigation under a sub-path and numeric settings fixes.

### 0.6.1
- The stash-box URL and API key are looked up in Stash's configuration rather than taken from the browser. An endpoint Stash doesn't have is rejected.
- Only the plugin's `assets/` folder is served to the browser.

## mcMetadata

No release this cycle yet.

## Studio Manager

No release this cycle yet.

## Repository

- Tests run on every pull request, and a publish only happens after they pass.
- Plugin zips contain only runtime files and are reproducible. A commit that only changes tests no longer offers an update.
