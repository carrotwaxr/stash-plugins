(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";

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

  // Pagination state
  let currentCursor = null;
  let hasMore = true;
  let isComplete = false;
  let totalOnStashdb = 0;
  let totalLocal = 0;
  let sortField = "DATE";
  let sortDirection = "DESC";

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

    // Parse JSON string response from plugin
    // Stash may return either a string (needs parsing) or already-parsed object
    // It also auto-unwraps the "output" field from PluginOutput structure
    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      console.error("[MissingScenes] Failed to parse plugin response:", rawOutput);
      throw new Error("Invalid response from plugin");
    }

    // Check for error
    if (output.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Find missing scenes for the current entity (paginated)
   */
  async function findMissingScenes(entityType, entityId, endpoint = null, options = {}) {
    const args = {
      operation: "find_missing",
      entity_type: entityType,
      entity_id: entityId,
      page_size: options.pageSize || 50,
      sort: options.sort || "DATE",
      direction: options.direction || "DESC",
    };
    if (endpoint) {
      args.endpoint = endpoint;
    }
    if (options.cursor) {
      args.cursor = options.cursor;
    }
    return runPluginOperation(args);
  }

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
   * Format date for display
   */
  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      // Parse as local date components to avoid timezone shift
      // new Date("2025-12-07") is interpreted as UTC midnight, which displays
      // as the previous day for users west of UTC
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
   * Create the modal UI
   */
  function createModal() {
    // Remove any existing modal
    removeModal();

    // Create backdrop
    const backdrop = document.createElement("div");
    backdrop.className = "ms-modal-backdrop";
    backdrop.onclick = (e) => {
      if (e.target === backdrop) {
        removeModal();
      }
    };

    // Create modal
    const modal = document.createElement("div");
    modal.className = "ms-modal";

    // Header
    const header = document.createElement("div");
    header.className = "ms-modal-header";
    header.innerHTML = `
      <h3>Missing Scenes</h3>
      <button class="ms-close-btn" title="Close">&times;</button>
    `;
    header.querySelector(".ms-close-btn").onclick = removeModal;

    // Stats bar
    const stats = document.createElement("div");
    stats.className = "ms-stats-bar";
    stats.id = "ms-stats";

    // Sort controls
    const sortControls = document.createElement("div");
    sortControls.className = "ms-sort-controls";
    sortControls.id = "ms-sort-controls";
    sortControls.innerHTML = `
      <label for="ms-sort-field">Sort by:</label>
      <select id="ms-sort-field" class="ms-sort-select">
        <option value="DATE">Release Date</option>
        <option value="TITLE">Title</option>
        <option value="CREATED_AT">Added to StashDB</option>
        <option value="UPDATED_AT">Last Updated</option>
      </select>
      <select id="ms-sort-direction" class="ms-sort-select">
        <option value="DESC">Newest First</option>
        <option value="ASC">Oldest First</option>
      </select>
    `;

    // Body (results)
    const body = document.createElement("div");
    body.className = "ms-modal-body";
    body.id = "ms-results";
    body.innerHTML = '<div class="ms-placeholder">Click "Find Missing Scenes" to search...</div>';

    // Footer
    const footer = document.createElement("div");
    footer.className = "ms-modal-footer";
    footer.innerHTML = `
      <div class="ms-status" id="ms-status"></div>
      <div class="ms-footer-actions">
        <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: none;">Load More</button>
        <button class="ms-btn" id="ms-add-all-btn" style="display: none;">Add All to Whisparr</button>
      </div>
    `;

    // Assemble modal
    modal.appendChild(header);
    modal.appendChild(stats);
    modal.appendChild(sortControls);
    modal.appendChild(body);
    modal.appendChild(footer);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    modalRoot = backdrop;

    // Sort control event handlers
    const sortFieldSelect = sortControls.querySelector("#ms-sort-field");
    const sortDirSelect = sortControls.querySelector("#ms-sort-direction");
    sortFieldSelect.onchange = handleSortChange;
    sortDirSelect.onchange = handleSortChange;

    // Keyboard handler for escape
    const keyHandler = (e) => {
      if (e.key === "Escape") {
        removeModal();
      }
    };
    document.addEventListener("keydown", keyHandler);
    backdrop._keyHandler = keyHandler;

    return modal;
  }

  /**
   * Handle sort control changes
   */
  function handleSortChange() {
    const sortFieldEl = document.getElementById("ms-sort-field");
    const sortDirEl = document.getElementById("ms-sort-direction");
    if (sortFieldEl && sortDirEl) {
      sortField = sortFieldEl.value;
      sortDirection = sortDirEl.value;
      // Reset pagination and re-search
      performSearch(true);
    }
  }

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

  /**
   * Remove the modal
   */
  function removeModal() {
    if (modalRoot) {
      if (modalRoot._keyHandler) {
        document.removeEventListener("keydown", modalRoot._keyHandler);
      }
      modalRoot.remove();
      modalRoot = null;
    }
  }

  /**
   * Update the stats bar (supports both legacy and paginated responses)
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

    // Handle both legacy (missing_count) and paginated (missing_count_estimate/loaded) responses
    let missingDisplay;
    if (data.is_complete !== undefined) {
      // Paginated response
      const estimate = data.missing_count_estimate;
      const loaded = missingScenes.length;
      if (data.is_complete) {
        missingDisplay = `${loaded}`;
      } else if (estimate !== null) {
        missingDisplay = `~${estimate} (${loaded} loaded)`;
      } else {
        missingDisplay = `${loaded} loaded`;
      }
    } else {
      // Legacy response
      missingDisplay = `${data.missing_count || 0}`;
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
        <span class="ms-stat-value">${missingDisplay}</span>
      </div>
    `;

    // Update pagination state
    totalOnStashdb = data.total_on_stashdb || 0;
    totalLocal = data.total_local || 0;
  }

  /**
   * Render the results grid
   */
  function renderResults() {
    const container = document.getElementById("ms-results");
    if (!container) return;

    if (missingScenes.length === 0) {
      container.innerHTML = `
        <div class="ms-placeholder ms-success">
          <div class="ms-success-icon">&#10003;</div>
          <div>You have all available scenes!</div>
        </div>
      `;
      updateLoadMoreButton();
      return;
    }

    // Create grid
    const grid = document.createElement("div");
    grid.className = "ms-results-grid";

    for (const scene of missingScenes) {
      const item = createSceneCard(scene);
      grid.appendChild(item);
    }

    container.innerHTML = "";
    container.appendChild(grid);

    // Show "Add All" button if Whisparr is configured
    const addAllBtn = document.getElementById("ms-add-all-btn");
    if (addAllBtn && whisparrConfigured) {
      const notInWhisparr = missingScenes.filter((s) => !s.in_whisparr);
      if (notInWhisparr.length > 0) {
        addAllBtn.style.display = "inline-block";
        addAllBtn.textContent = `Add All to Whisparr (${notInWhisparr.length})`;
        addAllBtn.onclick = () => handleAddAll(notInWhisparr);
      }
    }

    updateLoadMoreButton();
  }

  /**
   * Update the Load More button visibility and text
   */
  function updateLoadMoreButton() {
    const loadMoreBtn = document.getElementById("ms-load-more-btn");
    if (!loadMoreBtn) return;

    if (hasMore && !isComplete && missingScenes.length > 0) {
      loadMoreBtn.style.display = "inline-block";
      const estimate = totalOnStashdb - totalLocal;
      loadMoreBtn.textContent = `Load More (${missingScenes.length} of ~${estimate})`;
      loadMoreBtn.onclick = handleLoadMore;
      loadMoreBtn.disabled = isLoading;
    } else {
      loadMoreBtn.style.display = "none";
    }
  }

  /**
   * Handle Load More button click
   */
  async function handleLoadMore() {
    if (isLoading || !hasMore) return;
    await performSearch(false); // false = don't reset, load more
  }

  /**
   * Create a scene card element
   */
  function createSceneCard(scene) {
    const card = document.createElement("div");
    card.className = "ms-scene-card";
    card.dataset.stashId = scene.stash_id;

    // Thumbnail
    const thumbContainer = document.createElement("div");
    thumbContainer.className = "ms-scene-thumb";

    if (scene.thumbnail) {
      const img = document.createElement("img");
      img.src = scene.thumbnail;
      img.alt = scene.title;
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

    // Info overlay
    const info = document.createElement("div");
    info.className = "ms-scene-info";

    // Title
    const title = document.createElement("div");
    title.className = "ms-scene-title";
    title.textContent = scene.title;
    title.title = scene.title;

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
    meta.textContent = metaParts.join(" • ");

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
    stashdbLink.textContent = "View on StashDB";
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
          handleAddToWhisparr(scene, whisparrBtn);
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

  /**
   * Handle adding a single scene to Whisparr
   */
  async function handleAddToWhisparr(scene, button) {
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

      // Update status
      setStatus(`Added "${scene.title}" to Whisparr`, "success");

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

      setStatus(`Failed to add: ${error.message}`, "error");

      setTimeout(() => {
        button.textContent = originalText;
        button.classList.remove("ms-btn-error");
      }, 3000);
    }
  }

  /**
   * Handle adding all scenes to Whisparr
   */
  async function handleAddAll(scenes) {
    const addAllBtn = document.getElementById("ms-add-all-btn");
    if (!addAllBtn) return;

    addAllBtn.disabled = true;
    addAllBtn.classList.add("ms-btn-loading");

    let added = 0;
    let failed = 0;

    for (const scene of scenes) {
      if (scene.in_whisparr) continue;

      setStatus(`Adding ${added + failed + 1}/${scenes.length}: ${scene.title}...`, "loading");

      try {
        await addToWhisparr(scene.stash_id, scene.title);
        scene.in_whisparr = true;
        added++;

        // Update the card's button
        const card = document.querySelector(`[data-stash-id="${scene.stash_id}"]`);
        if (card) {
          const btn = card.querySelector(".ms-btn-whisparr");
          if (btn) {
            btn.textContent = "In Whisparr";
            btn.disabled = true;
            btn.classList.add("ms-btn-disabled");
          }
        }
      } catch (error) {
        console.error(`[MissingScenes] Failed to add ${scene.title}:`, error);
        failed++;
      }

      // Small delay between requests
      await new Promise((resolve) => setTimeout(resolve, 500));
    }

    addAllBtn.classList.remove("ms-btn-loading");

    if (failed === 0) {
      addAllBtn.textContent = `Added ${added} scenes!`;
      addAllBtn.classList.add("ms-btn-success");
      setStatus(`Successfully added ${added} scenes to Whisparr`, "success");
    } else {
      addAllBtn.textContent = `Added ${added}, ${failed} failed`;
      addAllBtn.classList.add("ms-btn-error");
      setStatus(`Added ${added} scenes, ${failed} failed`, "error");
    }

    // Hide button after all are added
    if (failed === 0) {
      setTimeout(() => {
        addAllBtn.style.display = "none";
      }, 3000);
    } else {
      addAllBtn.disabled = false;
    }
  }

  /**
   * Set status message
   */
  function setStatus(message, type = "") {
    const statusEl = document.getElementById("ms-status");
    if (!statusEl) return;

    statusEl.textContent = message;
    statusEl.className = "ms-status";
    if (type) {
      statusEl.classList.add(`ms-status-${type}`);
    }
  }

  /**
   * Show loading state
   */
  function showLoading() {
    const container = document.getElementById("ms-results");
    if (container) {
      container.innerHTML = `
        <div class="ms-placeholder">
          <div class="ms-spinner"></div>
          <div>Searching StashDB for missing scenes...</div>
        </div>
      `;
    }
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Show error state
   */
  function showError(message) {
    const container = document.getElementById("ms-results");
    if (container) {
      container.innerHTML = `
        <div class="ms-placeholder ms-error">
          <div class="ms-error-icon">!</div>
          <div>${escapeHtml(message)}</div>
        </div>
      `;
    }
  }

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
   * Perform the actual search (called by handleSearch, endpoint selector, and Load More)
   * @param {boolean} reset - If true, reset pagination state for new search
   */
  async function performSearch(reset = true) {
    if (reset) {
      // Reset pagination state for new search
      currentCursor = null;
      hasMore = true;
      isComplete = false;
      missingScenes = [];
      showLoading();
    }

    isLoading = true;
    setStatus(reset ? "Searching..." : "Loading more...", "loading");

    try {
      const result = await findMissingScenes(currentEntityType, currentEntityId, selectedEndpoint, {
        pageSize: 50,
        cursor: currentCursor,
        sort: sortField,
        direction: sortDirection,
      });

      // Append new scenes to existing list
      const newScenes = result.missing_scenes || [];
      missingScenes = [...missingScenes, ...newScenes];

      whisparrConfigured = result.whisparr_configured || false;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";

      // Update pagination state
      currentCursor = result.cursor;
      hasMore = result.has_more || false;
      isComplete = result.is_complete || false;

      updateStats(result);
      renderResults();

      if (missingScenes.length > 0) {
        const statusText = isComplete
          ? `Found ${missingScenes.length} missing scenes`
          : `Loaded ${missingScenes.length} missing scenes`;
        setStatus(statusText, "success");
      } else {
        setStatus("You have all available scenes!", "success");
      }
    } catch (error) {
      console.error("[MissingScenes] Search failed:", error);
      showError(error.message || "Failed to search for missing scenes");
      setStatus(error.message || "Search failed", "error");
    } finally {
      isLoading = false;
      updateLoadMoreButton();
    }
  }

  /**
   * Create the search button
   */
  function createSearchButton() {
    const btn = document.createElement("button");
    btn.className = "ms-search-button btn btn-secondary";
    btn.type = "button";
    btn.title = "Find scenes from StashDB that you don't have";
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        <line x1="8" y1="11" x2="14" y2="11"></line>
      </svg>
      Missing Scenes
    `;
    btn.onclick = handleSearch;
    return btn;
  }

  /**
   * Check if we're on a performer page
   */
  function getPerformerPageInfo() {
    const match = window.location.pathname.match(/\/performers\/(\d+)/);
    if (match) {
      return { type: "performer", id: match[1] };
    }
    return null;
  }

  /**
   * Check if we're on a studio page
   */
  function getStudioPageInfo() {
    const match = window.location.pathname.match(/\/studios\/(\d+)/);
    if (match) {
      return { type: "studio", id: match[1] };
    }
    return null;
  }

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

  /**
   * Add the search button to the page
   */
  function addSearchButton() {
    // Check if button already exists
    if (document.querySelector(".ms-search-button")) {
      return;
    }

    // Determine page type
    const performerInfo = getPerformerPageInfo();
    const studioInfo = getStudioPageInfo();
    const tagInfo = getTagPageInfo();

    if (!performerInfo && !studioInfo && !tagInfo) {
      return;
    }

    const entityInfo = performerInfo || studioInfo || tagInfo;
    currentEntityType = entityInfo.type;
    currentEntityId = entityInfo.id;

    // Find a place to add the button - use same selectors as Performer Image Search
    // to ensure buttons appear together
    const buttonContainer =
      document.querySelector(".detail-header-buttons") ||
      document.querySelector('[class*="detail"] [class*="button"]')?.parentElement ||
      document.querySelector(".performer-head") ||
      document.querySelector(".studio-head") ||
      document.querySelector(".tag-head");

    if (!buttonContainer) {
      // Try again later - page might not be fully loaded
      return;
    }

    const searchBtn = createSearchButton();
    buttonContainer.appendChild(searchBtn);
  }

  /**
   * Wait for page to be ready and add button
   */
  function waitForPage() {
    // Check immediately
    addSearchButton();

    // Also observe for SPA navigation
    const observer = new MutationObserver(() => {
      // Small delay to let React finish rendering
      setTimeout(addSearchButton, 100);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    // Also listen to popstate for SPA navigation
    window.addEventListener("popstate", () => {
      setTimeout(addSearchButton, 100);
    });
  }

  /**
   * Initialize the plugin
   */
  function init() {
    // Wait for DOM to be ready
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", waitForPage);
    } else {
      waitForPage();
    }
  }

  // Start the plugin
  init();
})();
