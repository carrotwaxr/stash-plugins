# Scene Matcher

Find StashDB matches for untagged scenes using known performer and studio associations.

## Overview

Scene Matcher adds a "Match" button to Stash's Tagger UI. When you have scenes that:
- Don't have a StashDB ID (untagged)
- But DO have performers and/or a studio linked to StashDB

...this plugin helps you find the correct match by searching StashDB for all scenes featuring those performers or from that studio.

## How It Works

1. Navigate to the Tagger (bulk or single scene view)
2. Scenes without a StashDB ID will show a "Match" button
3. Click it to open a modal with potential matches from StashDB
4. Results are scored by relevance:
   - +3 points for matching studio
   - +2 points per matching performer
5. Scenes you don't already have are shown first
6. Click a result to select it - the UUID is injected into Stash's search
7. Stash's native Tagger UI handles the rest (creating performers, studios, etc.)

## Requirements

- Stash with at least one stash-box endpoint configured (e.g., StashDB)
- Scenes must have performers or a studio linked to that stash-box

## Settings

**Stash-Box Endpoint**: Which stash-box to query. Leave empty to use the first configured endpoint (usually StashDB).

## Why Use This?

The built-in Tagger searches by filename or video fingerprint. These don't always work:
- Renamed files with non-standard naming
- Files that don't have matching fingerprints on StashDB
- Scenes from compilations or rips

If you've already tagged the performers or studio, Scene Matcher leverages that information to find the right match.
