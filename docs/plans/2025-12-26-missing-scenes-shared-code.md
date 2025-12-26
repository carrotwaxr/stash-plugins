# Missing Scenes Shared Code Refactor

**Date**: 2025-12-26
**Status**: Approved
**Branch**: `refactor/missing-scenes-shared-code`

## Overview

Refactor the Missing Scenes plugin JavaScript to share code between the modal UI (`missing-scenes.js`) and browse page (`missing-scenes-browse.js`). This eliminates code duplication and ensures feature parity.

## Problem

The browse page was missing features (images not loading, no Whisparr button) because:
- Code was duplicated between two files
- Browse page used simplified `createSceneCardHtml()` that lacked image handlers and Whisparr integration
- No shared module meant features drifted apart

## Solution

Create a shared module `missing-scenes-core.js` that both UIs import from.

## File Structure

```
plugins/missingScenes/
├── missing-scenes-core.js   # NEW - Shared utilities and components
├── missing-scenes.js        # Modal UI (uses core)
├── missing-scenes-browse.js # Browse page (uses core)
├── missing-scenes.css
├── missingScenes.yml        # Updated to load core.js first
└── missing_scenes.py
```

## Module API

Exposed on `window.MissingScenesCore`:

```javascript
window.MissingScenesCore = {
  // Utilities
  getGraphQLUrl(),
  graphqlRequest(query, variables),
  runPluginOperation(args),
  escapeHtml(text),
  formatDate(dateStr),
  formatDuration(seconds),

  // Components
  createSceneCard(scene, config),

  // Whisparr integration
  addToWhisparr(stashId, title),
  handleAddToWhisparr(scene, button, config),
};
```

### Config Object

```javascript
{
  stashdbUrl: "https://stashdb.org",
  whisparrConfigured: true,
  onCardClick: (scene) => { /* optional custom behavior */ }
}
```

## Scene Card Component

`createSceneCard(scene, config)` returns an HTMLElement with:
- Thumbnail with `onload`/`onerror` handlers (placeholder on failure)
- Title, studio, date, duration, performers
- "View on StashDB" link
- "Add to Whisparr" button (when configured and scene not in Whisparr)
- Click handler to open StashDB page

## Responsibilities by File

| File | Keeps |
|------|-------|
| `core.js` | Utilities, scene card, Whisparr functions |
| `missing-scenes.js` | Modal UI, entity detection, "Add All" batch |
| `browse.js` | Browse page, filters, sort controls, pagination |

## YAML Load Order

```yaml
javascript:
  - missing-scenes-core.js  # First - defines MissingScenesCore
  - missing-scenes.js       # Second - uses core
  - missing-scenes-browse.js # Third - uses core
```

## Testing

1. Modal on performer page - verify scene cards render with images and Whisparr buttons
2. Modal on studio page - same verification
3. Browse page - verify scene cards match modal appearance and functionality
4. Console - check for errors
