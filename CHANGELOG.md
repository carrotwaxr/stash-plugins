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
