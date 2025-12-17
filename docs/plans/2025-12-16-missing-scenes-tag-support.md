# Missing Scenes Tag Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Missing Scenes functionality to Tag Detail pages in Stash 0.30+, allowing users to discover scenes from StashDB that are tagged with a specific tag but not in their local library.

**Architecture:** Extend the existing Missing Scenes plugin to support tags as a third entity type alongside performers and studios. The frontend detects tag pages, the backend queries StashDB for scenes by tag ID, and when multiple stash-box endpoints are linked to a tag, a dropdown allows users to choose which endpoint to search.

**Tech Stack:** Python (backend), JavaScript (frontend), GraphQL (Stash + StashDB APIs)

---

## Task 1: Add Tag Query Function to StashDB API Module

**Files:**
- Modify: `plugins/missingScenes/stashbox_api.py:466-506` (after `query_scenes_by_studio`)

**Step 1: Write the query_scenes_by_tag function**

Add after line 506 (after `query_scenes_by_studio`):

```python
def query_scenes_by_tag(url, api_key, tag_id, plugin_settings=None):
    """Query StashDB for all scenes with a specific tag."""
    query = f"""
    query QueryScenes($input: SceneQueryInput!) {{
        queryScenes(input: $input) {{
            count
            scenes {{
                {SCENE_FIELDS}
            }}
        }}
    }}
    """

    def build_variables(page, per_page):
        return {
            "input": {
                "tags": {
                    "value": [tag_id],
                    "modifier": "INCLUDES"
                },
                "page": page,
                "per_page": per_page,
                "sort": "DATE",
                "direction": "DESC"
            }
        }

    def extract(data):
        query_data = data.get("queryScenes", {})
        return query_data.get("scenes", []), query_data.get("count", 0)

    # Use same max_pages as studio queries
    max_pages = get_config(plugin_settings, "max_pages_studio")
    scenes = paginated_query(
        url, api_key, query, build_variables, extract,
        plugin_settings=plugin_settings,
        operation_name="scenes for tag",
        max_pages=max_pages
    )

    log.LogInfo(f"StashDB: Found {len(scenes)} scenes for tag")
    return scenes
```

**Step 2: Verify syntax by running Python check**

Run: `python -m py_compile plugins/missingScenes/stashbox_api.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/stashbox_api.py
git commit -m "feat(missingScenes): add query_scenes_by_tag to stashbox_api"
```

---

## Task 2: Add get_local_tag Function to Backend

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:187-204` (after `get_local_studio`)

**Step 1: Add the get_local_tag function**

Add after `get_local_studio` function (after line 204):

```python
def get_local_tag(tag_id):
    """Get a tag from local Stash with its stash_ids."""
    query = """
    query FindTag($id: ID!) {
        findTag(id: $id) {
            id
            name
            stash_ids {
                endpoint
                stash_id
            }
        }
    }
    """
    data = stash_graphql(query, {"id": tag_id})
    if data:
        return data.get("findTag")
    return None
```

**Step 2: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): add get_local_tag function"
```

---

## Task 3: Add query_stashdb_tag_scenes Wrapper Function

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:288-292` (after `query_stashdb_studio_scenes`)

**Step 1: Add the wrapper function**

Add after `query_stashdb_studio_scenes` function (after line 292):

```python
def query_stashdb_tag_scenes(stashdb_url, api_key, tag_stash_id, plugin_settings=None):
    """Query StashDB for all scenes with a tag.

    Uses stashbox_api module for:
    - Retry with exponential backoff on 504/503/connection errors
    - Rate limit detection and pause on 429
    - Configurable delays between paginated requests
    - Graceful degradation with partial results on failure
    """
    return stashbox_api.query_scenes_by_tag(
        stashdb_url, api_key, tag_stash_id,
        plugin_settings=plugin_settings
    )
```

**Step 2: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): add query_stashdb_tag_scenes wrapper"
```

---

## Task 4: Extend find_missing_scenes to Support Tags

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:634-776` (find_missing_scenes function)

**Step 1: Update entity retrieval logic**

Find the section at lines 678-683:

```python
    # Get the local entity and its stash_id
    if entity_type == "performer":
        entity = get_local_performer(entity_id)
    elif entity_type == "studio":
        entity = get_local_studio(entity_id)
    else:
        return {"error": f"Unknown entity type: {entity_type}"}
```

Replace with:

```python
    # Get the local entity and its stash_id
    if entity_type == "performer":
        entity = get_local_performer(entity_id)
    elif entity_type == "studio":
        entity = get_local_studio(entity_id)
    elif entity_type == "tag":
        entity = get_local_tag(entity_id)
    else:
        return {"error": f"Unknown entity type: {entity_type}"}
```

**Step 2: Update StashDB query logic**

Find the section at lines 703-707:

```python
    # Query StashDB for all scenes (with retry/rate limit handling)
    if entity_type == "performer":
        stashdb_scenes = query_stashdb_performer_scenes(stashdb_url, stashdb_api_key, stash_id, plugin_settings)
    else:
        stashdb_scenes = query_stashdb_studio_scenes(stashdb_url, stashdb_api_key, stash_id, plugin_settings)
```

Replace with:

```python
    # Query StashDB for all scenes (with retry/rate limit handling)
    if entity_type == "performer":
        stashdb_scenes = query_stashdb_performer_scenes(stashdb_url, stashdb_api_key, stash_id, plugin_settings)
    elif entity_type == "studio":
        stashdb_scenes = query_stashdb_studio_scenes(stashdb_url, stashdb_api_key, stash_id, plugin_settings)
    else:  # tag
        stashdb_scenes = query_stashdb_tag_scenes(stashdb_url, stashdb_api_key, stash_id, plugin_settings)
```

**Step 3: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 4: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): extend find_missing_scenes to support tags"
```

---

## Task 5: Add Tag Page Detection to Frontend

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:674-691` (after `getStudioPageInfo`)

**Step 1: Add getTagPageInfo function**

Add after `getStudioPageInfo` function (after line 691):

```javascript
  /**
   * Check if we're on a tag page
   */
  function getTagPageInfo() {
    const match = window.location.pathname.match(/\/tags\/(\d+)/);
    if (match) {
      return { type: "tag", id: match[1] };
    }
    return null;
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add tag page detection to frontend"
```

---

## Task 6: Update addSearchButton to Include Tags

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:696-729` (addSearchButton function)

**Step 1: Update the function to check tag pages**

Find the section at lines 702-710:

```javascript
    // Determine page type
    const performerInfo = getPerformerPageInfo();
    const studioInfo = getStudioPageInfo();

    if (!performerInfo && !studioInfo) {
      return;
    }

    const entityInfo = performerInfo || studioInfo;
```

Replace with:

```javascript
    // Determine page type
    const performerInfo = getPerformerPageInfo();
    const studioInfo = getStudioPageInfo();
    const tagInfo = getTagPageInfo();

    if (!performerInfo && !studioInfo && !tagInfo) {
      return;
    }

    const entityInfo = performerInfo || studioInfo || tagInfo;
```

**Step 2: Update button container selectors to include tag pages**

Find the section at lines 715-720:

```javascript
    // Find a place to add the button - use same selectors as Performer Image Search
    // to ensure buttons appear together
    const buttonContainer =
      document.querySelector(".detail-header-buttons") ||
      document.querySelector('[class*="detail"] [class*="button"]')?.parentElement ||
      document.querySelector(".performer-head") ||
      document.querySelector(".studio-head");
```

Replace with:

```javascript
    // Find a place to add the button - use same selectors as Performer Image Search
    // to ensure buttons appear together
    const buttonContainer =
      document.querySelector(".detail-header-buttons") ||
      document.querySelector('[class*="detail"] [class*="button"]')?.parentElement ||
      document.querySelector(".performer-head") ||
      document.querySelector(".studio-head") ||
      document.querySelector(".tag-head");
```

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): update addSearchButton to include tag pages"
```

---

## Task 7: Add Endpoint Selection State Variables

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:7-14` (state variables section)

**Step 1: Add new state variables for endpoint selection**

Find the state variables section at lines 7-14:

```javascript
  // State
  let modalRoot = null;
  let currentEntityId = null;
  let currentEntityType = null; // "performer" or "studio"
  let currentEntityName = null;
  let missingScenes = [];
  let isLoading = false;
  let whisparrConfigured = false;
  let stashdbUrl = "";
```

Replace with:

```javascript
  // State
  let modalRoot = null;
  let currentEntityId = null;
  let currentEntityType = null; // "performer", "studio", or "tag"
  let currentEntityName = null;
  let missingScenes = [];
  let isLoading = false;
  let whisparrConfigured = false;
  let stashdbUrl = "";

  // Endpoint selection state (for tags with multiple stash-box links)
  let availableEndpoints = []; // [{endpoint, name, stash_id}]
  let selectedEndpoint = null;
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add endpoint selection state variables"
```

---

## Task 8: Add getAvailableEndpoints Function to Backend

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py` (after get_stashbox_config, around line 165)

**Step 1: Add the function**

Add after `get_stashbox_config` function (after line 164):

```python
def get_available_endpoints_for_entity(entity_stash_ids):
    """Get stash-box endpoints that both the entity is linked to AND are configured in Stash.

    Args:
        entity_stash_ids: List of {endpoint, stash_id} dicts from the entity

    Returns:
        List of {endpoint, name, stash_id} dicts for valid endpoints
    """
    configured_boxes = get_stashbox_config()
    if not configured_boxes:
        return []

    # Build lookup of configured endpoints
    configured_lookup = {box["endpoint"]: box for box in configured_boxes}

    available = []
    for sid in entity_stash_ids:
        endpoint = sid.get("endpoint")
        if endpoint in configured_lookup:
            box = configured_lookup[endpoint]
            available.append({
                "endpoint": endpoint,
                "name": box.get("name", endpoint),
                "stash_id": sid.get("stash_id")
            })

    return available
```

**Step 2: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): add get_available_endpoints_for_entity function"
```

---

## Task 9: Add get_endpoints Operation to Backend

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:1276-1296` (main operation handling)

**Step 1: Add the new operation handler**

Find the section at lines 1291-1296:

```python
        elif operation == "add_to_whisparr":
            stash_id = args.get("stash_id", "")
            title = args.get("title", "Unknown")

            if not stash_id:
                output = {"error": "stash_id is required"}
            else:
                output = add_to_whisparr(stash_id, title, plugin_settings)
```

Add after this block (before `elif operation:`):

```python
        elif operation == "get_endpoints":
            entity_type = args.get("entity_type", "")
            entity_id = args.get("entity_id", "")

            if not entity_id or not entity_type:
                output = {"error": "entity_type and entity_id are required"}
            else:
                # Get the entity
                if entity_type == "performer":
                    entity = get_local_performer(entity_id)
                elif entity_type == "studio":
                    entity = get_local_studio(entity_id)
                elif entity_type == "tag":
                    entity = get_local_tag(entity_id)
                else:
                    entity = None

                if not entity:
                    output = {"error": f"{entity_type.title()} not found: {entity_id}"}
                else:
                    stash_ids = entity.get("stash_ids", [])
                    available = get_available_endpoints_for_entity(stash_ids)

                    # Determine which endpoint to use by default
                    preferred = plugin_settings.get("stashBoxEndpoint", "").strip()
                    default_endpoint = None

                    if preferred:
                        # Check if preferred endpoint is in available list
                        for ep in available:
                            if ep["endpoint"] == preferred:
                                default_endpoint = preferred
                                break

                    if not default_endpoint and available:
                        default_endpoint = available[0]["endpoint"]

                    output = {
                        "entity_name": entity.get("name"),
                        "available_endpoints": available,
                        "default_endpoint": default_endpoint,
                        "show_selector": len(available) > 1 and not default_endpoint
                    }
```

**Step 2: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): add get_endpoints operation to backend"
```

---

## Task 10: Add getEndpoints Function to Frontend

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:93-99` (after findMissingScenes)

**Step 1: Add the function**

Add after `findMissingScenes` function (after line 99):

```javascript
  /**
   * Get available endpoints for an entity
   */
  async function getEndpoints(entityType, entityId) {
    return runPluginOperation({
      operation: "get_endpoints",
      entity_type: entityType,
      entity_id: entityId,
    });
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add getEndpoints function to frontend"
```

---

## Task 11: Update find_missing_scenes to Accept Endpoint Override

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:634-670` (find_missing_scenes function signature and endpoint selection)

**Step 1: Update function signature**

Find line 634:

```python
def find_missing_scenes(entity_type, entity_id, plugin_settings):
```

Replace with:

```python
def find_missing_scenes(entity_type, entity_id, plugin_settings, endpoint_override=None):
```

**Step 2: Update endpoint selection logic**

Find the section at lines 652-669:

```python
    # Check if user specified a preferred endpoint
    preferred_endpoint = plugin_settings.get("stashBoxEndpoint", "").strip()

    # Find the matching stash-box config
    stashbox = None
    if preferred_endpoint:
        # User specified an endpoint - find it
        for config in stashbox_configs:
            if config["endpoint"] == preferred_endpoint:
                stashbox = config
                break
        if not stashbox:
            # Endpoint not found in configured list
            available = ", ".join([c.get("name", c["endpoint"]) for c in stashbox_configs])
            return {"error": f"Configured stash-box endpoint '{preferred_endpoint}' not found. Available: {available}"}
    else:
        # Use the first stash-box (usually StashDB)
        stashbox = stashbox_configs[0]
```

Replace with:

```python
    # Determine which endpoint to use:
    # 1. endpoint_override from frontend (user selected from dropdown)
    # 2. stashBoxEndpoint from plugin settings (user's configured preference)
    # 3. First configured stash-box
    target_endpoint = endpoint_override or plugin_settings.get("stashBoxEndpoint", "").strip()

    # Find the matching stash-box config
    stashbox = None
    if target_endpoint:
        # User specified an endpoint - find it
        for config in stashbox_configs:
            if config["endpoint"] == target_endpoint:
                stashbox = config
                break
        if not stashbox:
            # Endpoint not found in configured list
            available = ", ".join([c.get("name", c["endpoint"]) for c in stashbox_configs])
            return {"error": f"Stash-box endpoint '{target_endpoint}' not found. Available: {available}"}
    else:
        # Use the first stash-box (usually StashDB)
        stashbox = stashbox_configs[0]
```

**Step 3: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 4: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): add endpoint_override parameter to find_missing_scenes"
```

---

## Task 12: Update find_missing Operation to Accept Endpoint

**Files:**
- Modify: `plugins/missingScenes/missing_scenes.py:1277-1284` (find_missing operation handler)

**Step 1: Update the operation handler**

Find the section at lines 1277-1284:

```python
        if operation == "find_missing":
            entity_type = args.get("entity_type", "performer")
            entity_id = args.get("entity_id", "")

            if not entity_id:
                output = {"error": "entity_id is required"}
            else:
                output = find_missing_scenes(entity_type, entity_id, plugin_settings)
```

Replace with:

```python
        if operation == "find_missing":
            entity_type = args.get("entity_type", "performer")
            entity_id = args.get("entity_id", "")
            endpoint = args.get("endpoint")  # Optional endpoint override

            if not entity_id:
                output = {"error": "entity_id is required"}
            else:
                output = find_missing_scenes(entity_type, entity_id, plugin_settings, endpoint_override=endpoint)
```

**Step 2: Verify syntax**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add plugins/missingScenes/missing_scenes.py
git commit -m "feat(missingScenes): update find_missing operation to accept endpoint"
```

---

## Task 13: Update findMissingScenes Frontend Function

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:93-99` (findMissingScenes function)

**Step 1: Update the function to accept endpoint parameter**

Find the section at lines 93-99:

```javascript
  /**
   * Find missing scenes for the current entity
   */
  async function findMissingScenes(entityType, entityId) {
    return runPluginOperation({
      operation: "find_missing",
      entity_type: entityType,
      entity_id: entityId,
    });
  }
```

Replace with:

```javascript
  /**
   * Find missing scenes for the current entity
   */
  async function findMissingScenes(entityType, entityId, endpoint = null) {
    const args = {
      operation: "find_missing",
      entity_type: entityType,
      entity_id: entityId,
    };
    if (endpoint) {
      args.endpoint = endpoint;
    }
    return runPluginOperation(args);
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): update findMissingScenes to accept endpoint parameter"
```

---

## Task 14: Create Endpoint Selector Dropdown Component

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js` (after createModal function, around line 218)

**Step 1: Add createEndpointSelector function**

Add after `createModal` function (after line 218):

```javascript
  /**
   * Create the endpoint selector dropdown
   */
  function createEndpointSelector(endpoints, defaultEndpoint, onSelect) {
    const container = document.createElement("div");
    container.className = "ms-endpoint-selector";
    container.id = "ms-endpoint-selector";

    const label = document.createElement("label");
    label.textContent = "Search on: ";
    label.htmlFor = "ms-endpoint-dropdown";

    const select = document.createElement("select");
    select.id = "ms-endpoint-dropdown";
    select.className = "ms-endpoint-dropdown";

    for (const ep of endpoints) {
      const option = document.createElement("option");
      option.value = ep.endpoint;
      option.textContent = ep.name;
      if (ep.endpoint === defaultEndpoint) {
        option.selected = true;
      }
      select.appendChild(option);
    }

    select.onchange = () => {
      selectedEndpoint = select.value;
      if (onSelect) {
        onSelect(select.value);
      }
    };

    container.appendChild(label);
    container.appendChild(select);

    return container;
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): add endpoint selector dropdown component"
```

---

## Task 15: Add Endpoint Selector Styles

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.css` (at end of file)

**Step 1: Add styles for the endpoint selector**

Add at the end of the file:

```css
/* Endpoint selector for tags with multiple stash-box links */
.ms-endpoint-selector {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  background: var(--ms-bg-secondary, #1a1a1a);
  border-bottom: 1px solid var(--ms-border, #333);
}

.ms-endpoint-selector label {
  color: var(--ms-text-muted, #888);
  font-size: 0.9rem;
}

.ms-endpoint-dropdown {
  background: var(--ms-bg-tertiary, #2a2a2a);
  color: var(--ms-text, #fff);
  border: 1px solid var(--ms-border, #333);
  border-radius: 4px;
  padding: 0.4rem 0.8rem;
  font-size: 0.9rem;
  cursor: pointer;
}

.ms-endpoint-dropdown:hover {
  border-color: var(--ms-accent, #0d6efd);
}

.ms-endpoint-dropdown:focus {
  outline: none;
  border-color: var(--ms-accent, #0d6efd);
  box-shadow: 0 0 0 2px rgba(13, 110, 253, 0.25);
}
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.css
git commit -m "feat(missingScenes): add endpoint selector styles"
```

---

## Task 16: Update handleSearch to Support Endpoint Selection

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:615-649` (handleSearch function)

**Step 1: Rewrite handleSearch to check endpoints for tags**

Replace the entire `handleSearch` function (lines 615-649):

```javascript
  /**
   * Handle the search button click
   */
  async function handleSearch() {
    if (isLoading) return;
    if (!currentEntityId || !currentEntityType) {
      console.error("[MissingScenes] No entity selected");
      return;
    }

    isLoading = true;
    createModal();
    showLoading();
    setStatus("Checking endpoints...", "loading");

    try {
      // For tags, check available endpoints first
      if (currentEntityType === "tag") {
        const endpointInfo = await getEndpoints(currentEntityType, currentEntityId);
        availableEndpoints = endpointInfo.available_endpoints || [];
        selectedEndpoint = endpointInfo.default_endpoint;

        if (availableEndpoints.length === 0) {
          showError(`This tag is not linked to any configured stash-box. Please link it first.`);
          setStatus("Tag not linked to stash-box", "error");
          isLoading = false;
          return;
        }

        // Show endpoint selector if multiple valid endpoints and no clear default
        if (availableEndpoints.length > 1 && !endpointInfo.default_endpoint) {
          // Insert selector into modal header
          const statsEl = document.getElementById("ms-stats");
          if (statsEl) {
            const selector = createEndpointSelector(
              availableEndpoints,
              availableEndpoints[0].endpoint,
              (newEndpoint) => {
                // Re-run search with new endpoint
                selectedEndpoint = newEndpoint;
                performSearch();
              }
            );
            statsEl.parentNode.insertBefore(selector, statsEl);
          }
          selectedEndpoint = availableEndpoints[0].endpoint;
        }
      } else {
        // For performers/studios, clear endpoint state
        availableEndpoints = [];
        selectedEndpoint = null;
      }

      await performSearch();
    } catch (error) {
      console.error("[MissingScenes] Search failed:", error);
      showError(error.message || "Failed to search for missing scenes");
      setStatus(error.message || "Search failed", "error");
      isLoading = false;
    }
  }

  /**
   * Perform the actual search (called by handleSearch and endpoint selector)
   */
  async function performSearch() {
    showLoading();
    setStatus("Searching...", "loading");

    try {
      const result = await findMissingScenes(currentEntityType, currentEntityId, selectedEndpoint);

      missingScenes = result.missing_scenes || [];
      whisparrConfigured = result.whisparr_configured || false;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";

      updateStats(result);
      renderResults();

      if (missingScenes.length > 0) {
        setStatus(`Found ${missingScenes.length} missing scenes`, "success");
      } else {
        setStatus("You have all available scenes!", "success");
      }
    } catch (error) {
      console.error("[MissingScenes] Search failed:", error);
      showError(error.message || "Failed to search for missing scenes");
      setStatus(error.message || "Search failed", "error");
    } finally {
      isLoading = false;
    }
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): update handleSearch to support endpoint selection"
```

---

## Task 17: Update Stats Bar for Tag Display

**Files:**
- Modify: `plugins/missingScenes/missing-scenes.js:236-260` (updateStats function)

**Step 1: Update the function to handle tag entity type**

Find the section at lines 236-260:

```javascript
  /**
   * Update the stats bar
   */
  function updateStats(data) {
    const statsEl = document.getElementById("ms-stats");
    if (!statsEl) return;

    const entityLabel = data.entity_type === "performer" ? "Performer" : "Studio";

    statsEl.innerHTML = `
      <div class="ms-stat">
        <span class="ms-stat-label">${entityLabel}:</span>
        <span class="ms-stat-value">${data.entity_name || "Unknown"}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">On ${data.stashdb_name || "StashDB"}:</span>
        <span class="ms-stat-value">${data.total_on_stashdb || 0}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">You Have:</span>
        <span class="ms-stat-value">${data.total_local || 0}</span>
      </div>
      <div class="ms-stat ms-stat-highlight">
        <span class="ms-stat-label">Missing:</span>
        <span class="ms-stat-value">${data.missing_count || 0}</span>
      </div>
    `;
  }
```

Replace with:

```javascript
  /**
   * Update the stats bar
   */
  function updateStats(data) {
    const statsEl = document.getElementById("ms-stats");
    if (!statsEl) return;

    let entityLabel;
    switch (data.entity_type) {
      case "performer":
        entityLabel = "Performer";
        break;
      case "studio":
        entityLabel = "Studio";
        break;
      case "tag":
        entityLabel = "Tag";
        break;
      default:
        entityLabel = "Entity";
    }

    statsEl.innerHTML = `
      <div class="ms-stat">
        <span class="ms-stat-label">${entityLabel}:</span>
        <span class="ms-stat-value">${data.entity_name || "Unknown"}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">On ${data.stashdb_name || "StashDB"}:</span>
        <span class="ms-stat-value">${data.total_on_stashdb || 0}</span>
      </div>
      <div class="ms-stat">
        <span class="ms-stat-label">You Have:</span>
        <span class="ms-stat-value">${data.total_local || 0}</span>
      </div>
      <div class="ms-stat ms-stat-highlight">
        <span class="ms-stat-label">Missing:</span>
        <span class="ms-stat-value">${data.missing_count || 0}</span>
      </div>
    `;
  }
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/missing-scenes.js
git commit -m "feat(missingScenes): update stats bar to handle tag entity type"
```

---

## Task 18: Update README Documentation

**Files:**
- Modify: `plugins/missingScenes/README.md`

**Step 1: Update the README to document tag support**

Add to the Features section:

```markdown
### Tag Support (Stash 0.30+)

Starting with Stash 0.30, tags can have Stash ID associations. Missing Scenes now supports discovering scenes by tag:

1. Navigate to any Tag detail page
2. If the tag is linked to a stash-box (e.g., StashDB), you'll see the "Missing Scenes" button
3. Click to discover scenes with that tag that you don't have locally

**Multiple Endpoints:** If a tag is linked to multiple configured stash-boxes, you'll see a dropdown to select which endpoint to search.
```

**Step 2: Commit**

```bash
git add plugins/missingScenes/README.md
git commit -m "docs(missingScenes): document tag support feature"
```

---

## Task 19: Manual Testing Checklist

**No files to modify - testing only**

**Step 1: Test on Performer page (regression)**

1. Navigate to a performer with StashDB link
2. Click "Missing Scenes" button
3. Verify modal opens and shows missing scenes
4. Verify no endpoint dropdown appears (only one endpoint)

**Step 2: Test on Studio page (regression)**

1. Navigate to a studio with StashDB link
2. Click "Missing Scenes" button
3. Verify modal opens and shows missing scenes

**Step 3: Test on Tag page without Stash ID**

1. Navigate to a tag WITHOUT any Stash ID links
2. Verify "Missing Scenes" button does NOT appear

**Step 4: Test on Tag page with single Stash ID**

1. Navigate to a tag with ONE Stash ID link (matching a configured stash-box)
2. Click "Missing Scenes" button
3. Verify modal opens and shows missing scenes
4. Verify no endpoint dropdown appears

**Step 5: Test on Tag page with multiple Stash IDs (if applicable)**

1. Navigate to a tag linked to multiple configured stash-boxes
2. Click "Missing Scenes" button
3. Verify endpoint dropdown appears
4. Select different endpoint and verify results refresh

**Step 6: Record test results**

Document any issues found for follow-up fixes.

---

## Task 20: Final Commit and Branch Summary

**Step 1: Verify all changes**

Run: `git status`
Expected: All changes committed, working tree clean

**Step 2: Run final syntax checks**

Run: `python -m py_compile plugins/missingScenes/missing_scenes.py && python -m py_compile plugins/missingScenes/stashbox_api.py && echo "All Python files OK"`
Expected: "All Python files OK"

**Step 3: View commit history**

Run: `git log --oneline feature/missing-scenes-tag-support ^main`
Expected: List of commits for this feature

**Step 4: Summary**

Branch `feature/missing-scenes-tag-support` is ready for review/merge. Changes include:
- Backend support for tag entity type in `missing_scenes.py`
- New `query_scenes_by_tag` function in `stashbox_api.py`
- Frontend tag page detection and endpoint selector dropdown
- CSS styles for endpoint selector
- Updated documentation
