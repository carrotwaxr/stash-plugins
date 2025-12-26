# Missing Scenes Shared Code Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract shared code from missing-scenes.js into a core module that both the modal UI and browse page can use.

**Architecture:** Create `missing-scenes-core.js` exposing utilities and components on `window.MissingScenesCore`. Update both existing files to use the shared module. Load order in YAML ensures core loads first.

**Tech Stack:** Vanilla JavaScript, Stash PluginApi

---

## Task 1: Create the Core Module

**Files:**
- Create: `plugins/missingScenes/missing-scenes-core.js`

**Step 1: Create the core module file with utility functions**

```javascript
/**
 * Missing Scenes Core - Shared utilities and components
 * Loaded first, exposes API on window.MissingScenesCore
 */
(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";

  /**
   * Get the GraphQL endpoint URL
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request using fetch
   */
  async function graphqlRequest(query, variables = {}) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables }),
    });

    if (!response.ok) {
      throw new Error(`GraphQL request failed: ${response.status}`);
    }

    const result = await response.json();

    if (result.errors && result.errors.length > 0) {
      throw new Error(result.errors[0].message);
    }

    return result.data;
  }

  /**
   * Run a plugin operation via GraphQL
   */
  async function runPluginOperation(args) {
    const query = `
      mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
        runPluginOperation(plugin_id: $plugin_id, args: $args)
      }
    `;

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: args,
    });

    const rawOutput = data?.runPluginOperation;

    if (!rawOutput) {
      throw new Error("No response from plugin");
    }

    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      console.error("[MissingScenes] Failed to parse plugin response:", rawOutput);
      throw new Error("Invalid response from plugin");
    }

    if (output.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Format date for display
   */
  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      const [year, month, day] = dateStr.split("-").map(Number);
      const date = new Date(year, month - 1, day);
      return date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return dateStr;
    }
  }

  /**
   * Format duration from seconds to HH:MM:SS or MM:SS
   */
  function formatDuration(seconds) {
    if (!seconds) return "";
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    }
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }

  /**
   * Add a scene to Whisparr
   */
  async function addToWhisparr(stashId, title) {
    return runPluginOperation({
      operation: "add_to_whisparr",
      stash_id: stashId,
      title: title,
    });
  }

  /**
   * Handle adding a single scene to Whisparr (manages button state)
   * @param {Object} scene - Scene object with stash_id and title
   * @param {HTMLElement} button - The button element to update
   * @param {Object} config - Configuration object
   * @param {Function} config.onSuccess - Optional callback on success
   * @param {Function} config.onError - Optional callback on error
   */
  async function handleAddToWhisparr(scene, button, config = {}) {
    const originalText = button.textContent;
    button.textContent = "Adding...";
    button.disabled = true;
    button.classList.add("ms-btn-loading");

    try {
      await addToWhisparr(scene.stash_id, scene.title);

      // Update button state
      button.textContent = "Added!";
      button.classList.remove("ms-btn-loading");
      button.classList.add("ms-btn-success");

      // Mark scene as in Whisparr
      scene.in_whisparr = true;

      if (config.onSuccess) {
        config.onSuccess(scene);
      }

      // After a delay, update button to show final state
      setTimeout(() => {
        button.textContent = "In Whisparr";
        button.classList.remove("ms-btn-success");
        button.classList.add("ms-btn-disabled");
      }, 2000);
    } catch (error) {
      console.error("[MissingScenes] Failed to add to Whisparr:", error);
      button.textContent = "Failed";
      button.classList.remove("ms-btn-loading");
      button.classList.add("ms-btn-error");
      button.disabled = false;

      if (config.onError) {
        config.onError(error, scene);
      }

      setTimeout(() => {
        button.textContent = originalText;
        button.classList.remove("ms-btn-error");
      }, 3000);
    }
  }

  /**
   * Create a scene card element
   * @param {Object} scene - Scene data from API
   * @param {Object} config - Configuration
   * @param {string} config.stashdbUrl - Base URL for StashDB links
   * @param {boolean} config.whisparrConfigured - Whether Whisparr is configured
   * @param {Function} config.onWhisparrAdd - Callback when Whisparr add completes (success or error)
   * @returns {HTMLElement} The scene card element
   */
  function createSceneCard(scene, config) {
    const { stashdbUrl = "https://stashdb.org", whisparrConfigured = false, onWhisparrAdd } = config;

    const card = document.createElement("div");
    card.className = "ms-scene-card";
    card.dataset.stashId = scene.stash_id;

    // Thumbnail
    const thumbContainer = document.createElement("div");
    thumbContainer.className = "ms-scene-thumb";

    if (scene.thumbnail) {
      const img = document.createElement("img");
      img.src = scene.thumbnail;
      img.alt = scene.title || "Scene thumbnail";
      img.loading = "lazy";
      img.onload = () => img.classList.add("ms-loaded");
      img.onerror = () => {
        thumbContainer.classList.add("ms-no-image");
        thumbContainer.innerHTML = '<span class="ms-no-image-icon">&#128247;</span>';
      };
      thumbContainer.appendChild(img);
    } else {
      thumbContainer.classList.add("ms-no-image");
      thumbContainer.innerHTML = '<span class="ms-no-image-icon">&#128247;</span>';
    }

    // Info section
    const info = document.createElement("div");
    info.className = "ms-scene-info";

    // Title
    const title = document.createElement("div");
    title.className = "ms-scene-title";
    title.textContent = scene.title || "Unknown";
    title.title = scene.title || "Unknown";

    // Meta (studio, date, duration)
    const meta = document.createElement("div");
    meta.className = "ms-scene-meta";

    const metaParts = [];
    if (scene.studio?.name) {
      metaParts.push(scene.studio.name);
    }
    if (scene.release_date) {
      metaParts.push(formatDate(scene.release_date));
    }
    if (scene.duration) {
      metaParts.push(formatDuration(scene.duration));
    }
    meta.textContent = metaParts.join(" - ");

    // Performers
    const performers = document.createElement("div");
    performers.className = "ms-scene-performers";
    if (scene.performers && scene.performers.length > 0) {
      const names = scene.performers.map((p) => p.name).slice(0, 3);
      performers.textContent = names.join(", ");
      if (scene.performers.length > 3) {
        performers.textContent += ` +${scene.performers.length - 3}`;
      }
    }

    info.appendChild(title);
    info.appendChild(meta);
    info.appendChild(performers);

    // Actions
    const actions = document.createElement("div");
    actions.className = "ms-scene-actions";

    // StashDB link
    const stashdbLink = document.createElement("a");
    stashdbLink.className = "ms-btn ms-btn-small";
    stashdbLink.href = `${stashdbUrl}/scenes/${scene.stash_id}`;
    stashdbLink.target = "_blank";
    stashdbLink.rel = "noopener noreferrer";
    stashdbLink.textContent = "View";
    stashdbLink.onclick = (e) => e.stopPropagation();
    actions.appendChild(stashdbLink);

    // Whisparr button (if configured)
    if (whisparrConfigured) {
      const whisparrBtn = document.createElement("button");
      whisparrBtn.className = "ms-btn ms-btn-small ms-btn-whisparr";

      if (scene.in_whisparr && scene.whisparr_status) {
        // Show detailed status based on whisparr_status object
        const status = scene.whisparr_status.status;
        const progress = scene.whisparr_status.progress;

        switch (status) {
          case "downloaded":
            whisparrBtn.textContent = "Downloaded";
            whisparrBtn.classList.add("ms-btn-success");
            break;
          case "downloading":
            whisparrBtn.textContent = progress ? `Downloading ${progress}%` : "Downloading...";
            whisparrBtn.classList.add("ms-btn-downloading");
            break;
          case "queued":
            whisparrBtn.textContent = "Queued";
            whisparrBtn.classList.add("ms-btn-queued");
            break;
          case "stalled":
            whisparrBtn.textContent = progress ? `Stalled ${progress}%` : "Stalled";
            whisparrBtn.classList.add("ms-btn-stalled");
            whisparrBtn.title = scene.whisparr_status.error || "Download stalled";
            break;
          case "waiting":
            whisparrBtn.textContent = "Waiting";
            whisparrBtn.classList.add("ms-btn-waiting");
            break;
          default:
            whisparrBtn.textContent = "In Whisparr";
        }
        whisparrBtn.disabled = true;
        whisparrBtn.classList.add("ms-btn-disabled");
      } else if (scene.in_whisparr) {
        // Fallback for backwards compatibility
        whisparrBtn.textContent = "In Whisparr";
        whisparrBtn.disabled = true;
        whisparrBtn.classList.add("ms-btn-disabled");
      } else {
        whisparrBtn.textContent = "Add to Whisparr";
        whisparrBtn.onclick = (e) => {
          e.stopPropagation();
          handleAddToWhisparr(scene, whisparrBtn, {
            onSuccess: onWhisparrAdd ? () => onWhisparrAdd(scene, true) : undefined,
            onError: onWhisparrAdd ? (err) => onWhisparrAdd(scene, false, err) : undefined,
          });
        };
      }
      actions.appendChild(whisparrBtn);
    }

    card.appendChild(thumbContainer);
    card.appendChild(info);
    card.appendChild(actions);

    // Click card to open on StashDB
    card.onclick = () => {
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank");
    };

    return card;
  }

  // Expose API on window
  window.MissingScenesCore = {
    // Utilities
    getGraphQLUrl,
    graphqlRequest,
    runPluginOperation,
    escapeHtml,
    formatDate,
    formatDuration,

    // Whisparr
    addToWhisparr,
    handleAddToWhisparr,

    // Components
    createSceneCard,
  };

  console.log("[MissingScenes] Core module loaded");
})();
```

**Step 2: Verify the file was created correctly**

Open the file in your editor and check it has no syntax errors.

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing-scenes-core.js
git commit -m "feat(missingScenes): add shared core module"
```

---

## Task 2: Update YAML to Load Core First

**Files:**
- Modify: `plugins/missingScenes/missingScenes.yml:7-12`

**Step 1: Update the javascript array to include core.js first**

Change lines 7-12 from:
```yaml
ui:
  javascript:
    - missing-scenes.js
    - missing-scenes-browse.js
  css:
    - missing-scenes.css
```

To:
```yaml
ui:
  javascript:
    - missing-scenes-core.js
    - missing-scenes.js
    - missing-scenes-browse.js
  css:
    - missing-scenes.css
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missingScenes.yml
git commit -m "chore(missingScenes): load core.js first in YAML"
```

---

## Task 3: Update Modal UI to Use Core Module

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js`

**Step 1: Remove the duplicated utility functions**

Delete these functions (approximately lines 34-106, 162-195, 596-738, 743-783, 879-885):
- `getGraphQLUrl()` (lines 37-41)
- `graphqlRequest()` (lines 46-66)
- `runPluginOperation()` (lines 71-106)
- `formatDuration()` (lines 164-174)
- `formatDate()` (lines 179-195)
- `escapeHtml()` (lines 881-885)
- `addToWhisparr()` (lines 153-159)
- `createSceneCard()` (lines 596-738)
- `handleAddToWhisparr()` (lines 743-783)

**Step 2: Add aliases at the top of the IIFE**

After `const PLUGIN_ID = "missingScenes";` add:

```javascript
  // Use shared core module
  const Core = window.MissingScenesCore;
  const {
    getGraphQLUrl,
    graphqlRequest,
    runPluginOperation,
    escapeHtml,
    formatDate,
    formatDuration,
    addToWhisparr,
    handleAddToWhisparr: coreHandleAddToWhisparr,
    createSceneCard: coreCreateSceneCard,
  } = Core;
```

**Step 3: Update createSceneCard usage in renderResults()**

In the `renderResults()` function (around line 545), change:
```javascript
const item = createSceneCard(scene);
```

To:
```javascript
const item = coreCreateSceneCard(scene, {
  stashdbUrl: stashdbUrl,
  whisparrConfigured: whisparrConfigured,
  onWhisparrAdd: (scene, success, error) => {
    if (success) {
      setStatus(`Added "${scene.title}" to Whisparr`, "success");
    } else {
      setStatus(`Failed to add: ${error?.message || "Unknown error"}`, "error");
    }
  }
});
```

**Step 4: Update handleAddAll() to use core's handleAddToWhisparr**

In `handleAddAll()`, the card button update can stay as-is since we're updating buttons by selector. But change the `addToWhisparr` call to use the imported one (it already will via the alias).

**Step 5: Test modal functionality**

1. Open Stash in browser
2. Navigate to a performer page
3. Click "Missing Scenes" button
4. Verify modal opens and shows scenes with:
   - Thumbnails loading correctly (with placeholder on error)
   - "View on StashDB" links working
   - "Add to Whisparr" buttons appearing (if Whisparr configured)
5. Check browser console for errors

**Step 6: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "refactor(missingScenes): use core module in modal UI"
```

---

## Task 4: Update Browse Page to Use Core Module

**Files:**
- Modify: `plugins/missingScenes/missing-scenes-browse.js`

**Step 1: Remove duplicated utility functions**

Delete these functions:
- `getGraphQLUrl()` (lines 10-14)
- `graphqlRequest()` (lines 19-36)
- `runPluginOperation()` (lines 41-65)
- `escapeHtml()` (lines 110-115)
- `formatDate()` (lines 120-129)
- `createSceneCardHtml()` (lines 134-157)

**Step 2: Add Core imports at the top of the IIFE**

After `const BROWSE_PATH = "/plugins/missing-scenes";` add:

```javascript
  // Use shared core module
  const Core = window.MissingScenesCore;
  const {
    runPluginOperation,
    escapeHtml,
    createSceneCard,
  } = Core;
```

**Step 3: Update renderPage() to use createSceneCard**

In the `renderPage()` function, replace the HTML string card generation:

Change from (around line 215):
```javascript
const cardsHtml = scenes.map(s => createSceneCardHtml(s)).join('');
resultsContent = `<div class="ms-results-grid">${cardsHtml}</div>`;
```

To:
```javascript
// Build results grid with DOM elements
const grid = document.createElement('div');
grid.className = 'ms-results-grid';
for (const scene of scenes) {
  const card = createSceneCard(scene, {
    stashdbUrl: stashdbUrl || "https://stashdb.org",
    whisparrConfigured: whisparrConfigured,
  });
  grid.appendChild(card);
}
resultsContent = grid;
```

**Step 4: Update the container.innerHTML assignment to handle DOM element**

The `renderPage()` function currently sets `container.innerHTML` with a string. We need to modify it to append the grid element instead.

Change the results section handling. After setting `container.innerHTML` with the page structure, find the `.ms-browse-results` div and append the grid:

```javascript
// At the end of the innerHTML assignment, leave results empty:
// <div class="ms-browse-results"></div>

// Then after setting innerHTML:
const resultsDiv = container.querySelector('.ms-browse-results');
if (resultsDiv) {
  if (typeof resultsContent === 'string') {
    resultsDiv.innerHTML = resultsContent;
  } else {
    resultsDiv.innerHTML = '';
    resultsDiv.appendChild(resultsContent);
  }
}
```

**Step 5: Test browse page functionality**

1. Navigate to `/plugins/missing-scenes` in Stash
2. Verify page loads with:
   - Scene cards showing thumbnails (with placeholder on error)
   - Duration displayed on cards
   - "View" and "Add to Whisparr" buttons on each card
   - Filters and sort controls working
   - "Load More" pagination working
3. Check browser console for errors

**Step 6: Commit**

```bash
git add plugins/missingScenes/missing-scenes-browse.js
git commit -m "refactor(missingScenes): use core module in browse page"
```

---

## Task 5: Final Testing and Cleanup

**Step 1: Test both UIs side by side**

1. Open performer page, click "Missing Scenes" - verify modal works
2. Open `/plugins/missing-scenes` - verify browse page works
3. Compare scene cards - should look identical
4. Test Whisparr buttons on both (if configured)
5. Test image loading/error handling on both

**Step 2: Check for console errors**

Open browser dev tools, navigate between pages, check for:
- JavaScript errors
- Failed network requests
- Missing function warnings

**Step 3: Verify no duplicate code remains**

Search both files for these function names - they should NOT be defined locally:
- `getGraphQLUrl`
- `graphqlRequest`
- `runPluginOperation`
- `formatDate`
- `formatDuration`
- `escapeHtml`
- `addToWhisparr`

**Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "fix(missingScenes): final cleanup for shared code refactor"
```

**Step 5: Push branch**

```bash
git push -u origin refactor/missing-scenes-shared-code
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Create core module | missing-scenes-core.js |
| 2 | Update YAML load order | missingScenes.yml |
| 3 | Refactor modal UI | missing-scenes.js |
| 4 | Refactor browse page | missing-scenes-browse.js |
| 5 | Test and cleanup | All files |

Total: 5 tasks, ~5-6 commits
