(function () {
  "use strict";

  const PLUGIN_ID = "sceneMatcher";

  // State
  let modalRoot = null;
  let currentSceneId = null;
  let currentSceneElement = null;
  let matchResults = [];
  let isLoading = false;
  let isLoadingMore = false;
  let stashdbUrl = "";

  // Cache for local stash_ids (persists across modal opens in the same session)
  let cachedLocalStashIds = null;
  let cacheEndpoint = null;

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
    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      console.error("[SceneMatcher] Failed to parse plugin response:", rawOutput);
      throw new Error("Invalid response from plugin");
    }

    // Check for error
    if (output.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Find matching scenes - Phase 1 (fast text searches)
   */
  async function findMatchesFast(sceneId) {
    const args = {
      operation: "find_matches_fast",
      scene_id: sceneId,
    };

    // Pass cached stash_ids if we have them for this endpoint
    if (cachedLocalStashIds && cacheEndpoint) {
      args.cached_local_stash_ids = cachedLocalStashIds;
      args.cache_endpoint = cacheEndpoint;
    }

    const result = await runPluginOperation(args);

    // Cache the stash_ids from the response for future calls
    // Note: Stash auto-unwraps the "output" field from PluginOutput structure
    if (result.local_stash_ids && result.stashdb_url) {
      cachedLocalStashIds = result.local_stash_ids;
      cacheEndpoint = result.stashdb_url;
      console.log(`[SceneMatcher] Cached ${cachedLocalStashIds.length} local stash_ids for ${cacheEndpoint}`);
    }

    return result;
  }

  /**
   * Find matching scenes - Phase 2 (thorough performer/studio searches)
   */
  async function findMatchesThorough(sceneId, excludeIds) {
    const args = {
      operation: "find_matches_thorough",
      scene_id: sceneId,
      exclude_ids: excludeIds || [],
    };

    // Pass cached stash_ids if we have them for this endpoint
    if (cachedLocalStashIds && cacheEndpoint) {
      args.cached_local_stash_ids = cachedLocalStashIds;
      args.cache_endpoint = cacheEndpoint;
    }

    const result = await runPluginOperation(args);

    // Cache the stash_ids from the response for future calls
    if (result.local_stash_ids && result.stashdb_url) {
      cachedLocalStashIds = result.local_stash_ids;
      cacheEndpoint = result.stashdb_url;
    }

    return result;
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
      const date = new Date(dateStr);
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
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Create the modal UI
   */
  function createModal() {
    // Remove any existing modal
    removeModal();

    // Create backdrop
    const backdrop = document.createElement("div");
    backdrop.className = "sm-modal-backdrop";
    backdrop.onclick = (e) => {
      if (e.target === backdrop) {
        removeModal();
      }
    };

    // Create modal
    const modal = document.createElement("div");
    modal.className = "sm-modal";

    // Header
    const header = document.createElement("div");
    header.className = "sm-modal-header";
    header.innerHTML = `
      <h3>Scene Matcher</h3>
      <button class="sm-close-btn" title="Close">&times;</button>
    `;
    header.querySelector(".sm-close-btn").onclick = removeModal;

    // Stats bar
    const stats = document.createElement("div");
    stats.className = "sm-stats-bar";
    stats.id = "sm-stats";

    // Body (results)
    const body = document.createElement("div");
    body.className = "sm-modal-body";
    body.id = "sm-results";
    body.innerHTML = '<div class="sm-placeholder">Searching for matches...</div>';

    // Footer
    const footer = document.createElement("div");
    footer.className = "sm-modal-footer";
    footer.innerHTML = `
      <div class="sm-status" id="sm-status"></div>
      <div class="sm-footer-actions"></div>
    `;

    // Assemble modal
    modal.appendChild(header);
    modal.appendChild(stats);
    modal.appendChild(body);
    modal.appendChild(footer);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    modalRoot = backdrop;

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
   * Update the stats bar with search attributes
   */
  function updateStats(data, showLoadingMore = false) {
    const statsEl = document.getElementById("sm-stats");
    if (!statsEl) return;

    const attrs = data.search_attributes || {};
    const performers = attrs.performers || [];
    const studio = attrs.studio;

    let attrHtml = '<div class="sm-search-attrs">';

    if (studio) {
      attrHtml += `<span class="sm-attr-tag sm-attr-studio">${escapeHtml(studio)}</span>`;
    }

    for (const perf of performers) {
      attrHtml += `<span class="sm-attr-tag sm-attr-performer">${escapeHtml(perf)}</span>`;
    }

    attrHtml += "</div>";

    const loadingMoreHtml = showLoadingMore
      ? '<span class="sm-loading-more"><span class="sm-spinner-small"></span> Loading more...</span>'
      : "";

    statsEl.innerHTML = `
      <div class="sm-stat">
        <span class="sm-stat-label">Searching by:</span>
        ${attrHtml}
      </div>
      <div class="sm-stat sm-stat-highlight">
        <span class="sm-stat-label">Results:</span>
        <span class="sm-stat-value" id="sm-result-count">${data.total_results || 0}</span>
        ${loadingMoreHtml}
      </div>
    `;
  }

  /**
   * Update just the result count (for progressive loading)
   */
  function updateResultCount(count, showLoadingMore = false) {
    const countEl = document.getElementById("sm-result-count");
    if (countEl) {
      countEl.textContent = count;
    }

    // Update or add loading more indicator
    const statsHighlight = document.querySelector(".sm-stat-highlight");
    if (statsHighlight) {
      const existingLoading = statsHighlight.querySelector(".sm-loading-more");
      if (showLoadingMore && !existingLoading) {
        const loadingSpan = document.createElement("span");
        loadingSpan.className = "sm-loading-more";
        loadingSpan.innerHTML = '<span class="sm-spinner-small"></span> Loading more...';
        statsHighlight.appendChild(loadingSpan);
      } else if (!showLoadingMore && existingLoading) {
        existingLoading.remove();
      }
    }
  }

  /**
   * Build match description text
   */
  function getMatchDescription(scene) {
    const parts = [];

    if (scene.matches_title) {
      parts.push("Title");
    }

    if (scene.matches_studio) {
      parts.push("Studio");
    }

    if (scene.matching_performers > 0) {
      const count = scene.matching_performers;
      parts.push(count === 1 ? "1 Performer" : `${count} Performers`);
    }

    return parts.join(" + ");
  }

  /**
   * Render the results grid
   */
  function renderResults() {
    const container = document.getElementById("sm-results");
    if (!container) return;

    if (matchResults.length === 0) {
      container.innerHTML = `
        <div class="sm-placeholder">
          <div>No matching scenes found on StashDB for these attributes.</div>
          <div style="font-size: 14px; color: #666; margin-top: 8px;">
            Try linking more performers or the studio to StashDB first.
          </div>
        </div>
      `;
      return;
    }

    // Create grid
    const grid = document.createElement("div");
    grid.className = "sm-results-grid";

    for (const scene of matchResults) {
      const item = createSceneCard(scene);
      grid.appendChild(item);
    }

    container.innerHTML = "";
    container.appendChild(grid);
  }

  /**
   * Create a scene card element
   */
  function createSceneCard(scene) {
    const card = document.createElement("div");
    card.className = "sm-scene-card";
    if (scene.in_local_stash) {
      card.classList.add("sm-in-stash");
    }
    card.dataset.stashId = scene.stash_id;

    // Thumbnail with badges
    const thumbContainer = document.createElement("div");
    thumbContainer.className = "sm-scene-thumb";

    if (scene.thumbnail) {
      const img = document.createElement("img");
      img.src = scene.thumbnail;
      img.alt = scene.title;
      img.loading = "lazy";
      img.onload = () => img.classList.add("sm-loaded");
      img.onerror = () => {
        thumbContainer.classList.add("sm-no-image");
        thumbContainer.innerHTML = '<span class="sm-no-image-icon">&#128247;</span>';
      };
      thumbContainer.appendChild(img);
    } else {
      thumbContainer.classList.add("sm-no-image");
      thumbContainer.innerHTML = '<span class="sm-no-image-icon">&#128247;</span>';
    }

    // Badges
    const badges = document.createElement("div");
    badges.className = "sm-badges";

    // Score badge
    const matchDesc = getMatchDescription(scene);
    if (matchDesc) {
      const scoreBadge = document.createElement("span");
      scoreBadge.className = "sm-badge sm-badge-score";
      if (scene.score >= 5) {
        scoreBadge.classList.add("sm-high-score");
      }
      scoreBadge.textContent = matchDesc;
      badges.appendChild(scoreBadge);
    }

    // In stash badge
    if (scene.in_local_stash) {
      const inStashBadge = document.createElement("span");
      inStashBadge.className = "sm-badge sm-badge-in-stash";
      inStashBadge.textContent = "In Stash";
      badges.appendChild(inStashBadge);
    }

    thumbContainer.appendChild(badges);

    // Info overlay
    const info = document.createElement("div");
    info.className = "sm-scene-info";

    // Title
    const title = document.createElement("div");
    title.className = "sm-scene-title";
    title.textContent = scene.title;
    title.title = scene.title;

    // Meta (studio, date, duration)
    const meta = document.createElement("div");
    meta.className = "sm-scene-meta";

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
    performers.className = "sm-scene-performers";
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
    actions.className = "sm-scene-actions";

    // Select button - main action
    const selectBtn = document.createElement("button");
    selectBtn.className = "sm-btn sm-btn-small sm-btn-select";
    selectBtn.textContent = "Select This Match";
    selectBtn.onclick = (e) => {
      e.stopPropagation();
      handleSelectMatch(scene);
    };
    actions.appendChild(selectBtn);

    card.appendChild(thumbContainer);
    card.appendChild(info);
    card.appendChild(actions);

    // Click card to view on StashDB (not select)
    card.onclick = () => {
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank");
    };

    return card;
  }

  /**
   * Handle selecting a match - inject into Tagger search and trigger
   */
  function handleSelectMatch(scene) {
    removeModal();

    if (!currentSceneElement) {
      console.error("[SceneMatcher] No scene element reference");
      return;
    }

    // Find the search input in the scene's row
    const searchInput = currentSceneElement.querySelector('input.text-input, input[type="text"]');

    if (!searchInput) {
      console.error("[SceneMatcher] Could not find search input");
      alert("Could not find the search input field. Please try again.");
      return;
    }

    // Set the value using React-compatible method
    // React tracks input values via the native value setter, so we need to
    // use Object.getOwnPropertyDescriptor to get the native setter
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    ).set;

    // Call the native setter with the input element as context
    nativeInputValueSetter.call(searchInput, scene.stash_id);

    // Dispatch input event - this is what React listens to for controlled inputs
    const inputEvent = new Event("input", { bubbles: true });
    searchInput.dispatchEvent(inputEvent);

    console.log("[SceneMatcher] Set search value to:", scene.stash_id);

    // Find and click the Search button
    // Look for button with "Search" text or the OperationButton
    const buttons = currentSceneElement.querySelectorAll("button");
    let foundSearchBtn = false;

    for (const btn of buttons) {
      const text = btn.textContent.trim().toLowerCase();
      if (text === "search" || text.includes("search")) {
        // Don't click our own button or the fragment button
        if (!btn.classList.contains("sm-match-button") && !text.includes("fragment")) {
          btn.click();
          foundSearchBtn = true;
          console.log("[SceneMatcher] Triggered search with UUID:", scene.stash_id);
          break;
        }
      }
    }

    if (!foundSearchBtn) {
      // Try pressing Enter on the input
      const enterEvent = new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
      });
      searchInput.dispatchEvent(enterEvent);
      console.log("[SceneMatcher] Triggered search via Enter key");
    }
  }

  /**
   * Set status message
   */
  function setStatus(message, type = "") {
    const statusEl = document.getElementById("sm-status");
    if (!statusEl) return;

    statusEl.textContent = message;
    statusEl.className = "sm-status";
    if (type) {
      statusEl.classList.add(`sm-status-${type}`);
    }
  }

  /**
   * Show loading state
   */
  function showLoading() {
    const container = document.getElementById("sm-results");
    if (container) {
      container.innerHTML = `
        <div class="sm-placeholder">
          <div class="sm-spinner"></div>
          <div>Searching StashDB for matching scenes...</div>
        </div>
      `;
    }
  }

  /**
   * Show error state
   */
  function showError(message) {
    const container = document.getElementById("sm-results");
    if (container) {
      container.innerHTML = `
        <div class="sm-placeholder sm-error">
          <div class="sm-error-icon">!</div>
          <div>${escapeHtml(message)}</div>
        </div>
      `;
    }
  }

  /**
   * Merge and sort results from multiple phases
   */
  function mergeResults(existingResults, newResults) {
    // Create a map of existing results by stash_id
    const resultMap = new Map();
    for (const r of existingResults) {
      resultMap.set(r.stash_id, r);
    }

    // Add new results (skip duplicates)
    for (const r of newResults) {
      if (!resultMap.has(r.stash_id)) {
        resultMap.set(r.stash_id, r);
      }
    }

    // Convert back to array and sort
    const merged = Array.from(resultMap.values());

    // Sort: not in local stash first, then by score desc, then by duration_score desc, then by date
    merged.sort((a, b) => {
      // In stash last
      if (a.in_local_stash !== b.in_local_stash) {
        return a.in_local_stash ? 1 : -1;
      }
      // Higher score first
      if (a.score !== b.score) {
        return b.score - a.score;
      }
      // Higher duration score first
      const aDur = a.duration_score || 0.5;
      const bDur = b.duration_score || 0.5;
      if (aDur !== bDur) {
        return bDur - aDur;
      }
      // Newer date first
      const aDate = a.release_date || "";
      const bDate = b.release_date || "";
      return bDate.localeCompare(aDate);
    });

    return merged;
  }

  /**
   * Handle the match button click - Progressive loading with two phases
   */
  async function handleMatchClick(sceneId, sceneElement) {
    if (isLoading) return;

    currentSceneId = sceneId;
    currentSceneElement = sceneElement;

    isLoading = true;
    createModal();
    showLoading();
    setStatus("Searching...", "loading");

    try {
      // Phase 1: Fast text searches
      // Note: Stash auto-unwraps the "output" field, so we access result directly
      console.log("[SceneMatcher] Starting Phase 1 (fast text search)...");
      const phase1Result = await findMatchesFast(sceneId);

      matchResults = phase1Result.results || [];
      stashdbUrl = phase1Result.stashdb_url || "https://stashdb.org";

      // Update modal header with scene title
      const header = document.querySelector(".sm-modal-header h3");
      if (header && phase1Result.scene_title) {
        header.textContent = `Scene Matcher - ${phase1Result.scene_title}`;
        header.title = phase1Result.scene_title;
      }

      // Show Phase 1 results immediately
      const hasMore = phase1Result.has_more;
      updateStats(phase1Result, hasMore);
      renderResults();

      if (matchResults.length > 0) {
        setStatus(
          hasMore ? `Found ${matchResults.length} matches, loading more...` : `Found ${matchResults.length} potential matches`,
          hasMore ? "loading" : "success"
        );
      } else if (hasMore) {
        setStatus("Searching for more matches...", "loading");
      }

      // Phase 2: Thorough performer/studio searches (if has_more is true)
      if (hasMore) {
        isLoadingMore = true;
        console.log("[SceneMatcher] Starting Phase 2 (thorough search)...");

        try {
          const excludeIds = matchResults.map((r) => r.stash_id);
          const phase2Result = await findMatchesThorough(sceneId, excludeIds);

          const phase2Results = phase2Result.results || [];

          if (phase2Results.length > 0) {
            // Merge and re-render
            matchResults = mergeResults(matchResults, phase2Results);
            renderResults();
            console.log(`[SceneMatcher] Phase 2 added ${phase2Results.length} results, total: ${matchResults.length}`);
          }

          // Update final status
          updateResultCount(matchResults.length, false);
          if (matchResults.length > 0) {
            setStatus(`Found ${matchResults.length} potential matches`, "success");
          } else {
            setStatus("No matches found", "");
          }
        } catch (phase2Error) {
          console.warn("[SceneMatcher] Phase 2 failed:", phase2Error);
          // Phase 2 failure is non-fatal, we still have Phase 1 results
          updateResultCount(matchResults.length, false);
          if (matchResults.length > 0) {
            setStatus(`Found ${matchResults.length} matches (thorough search failed)`, "success");
          }
        } finally {
          isLoadingMore = false;
        }
      } else if (matchResults.length === 0) {
        setStatus("No matches found", "");
      }
    } catch (error) {
      console.error("[SceneMatcher] Search failed:", error);
      showError(error.message || "Failed to search for matching scenes");
      setStatus(error.message || "Search failed", "error");
    } finally {
      isLoading = false;
    }
  }

  /**
   * Extract scene ID from a tagger scene element
   */
  function getSceneIdFromElement(element) {
    // Look for scene link in the element
    const sceneLink = element.querySelector('a[href*="/scenes/"]');
    if (sceneLink) {
      const match = sceneLink.href.match(/\/scenes\/(\d+)/);
      if (match) {
        return match[1];
      }
    }

    // Also check for data attributes
    if (element.dataset.sceneId) {
      return element.dataset.sceneId;
    }

    return null;
  }

  /**
   * Check if a scene already has a StashDB ID
   */
  function sceneHasStashId(element) {
    // Look for StashID pill/badge in the element
    const stashIdPill = element.querySelector('[class*="StashIDPill"], [class*="stash-id"]');
    if (stashIdPill) {
      return true;
    }

    // Also check for sub-content with stash_id links
    const subContent = element.querySelector(".sub-content");
    if (subContent && subContent.querySelector("a[href*='stashdb.org/scenes']")) {
      return true;
    }

    return false;
  }

  /**
   * Create the match button
   */
  function createMatchButton(sceneId, sceneElement) {
    const btn = document.createElement("button");
    btn.className = "sm-match-button btn btn-secondary";
    btn.type = "button";
    btn.title = "Find matches by performer/studio on StashDB";
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
      </svg>
      Match
    `;
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      handleMatchClick(sceneId, sceneElement);
    };
    return btn;
  }

  /**
   * Add match buttons to scene tagger items
   */
  function addMatchButtons() {
    // Find all scene items in the tagger
    const sceneItems = document.querySelectorAll(".search-item, .tagger-scene");

    for (const item of sceneItems) {
      // Skip if button already added
      if (item.querySelector(".sm-match-button")) {
        continue;
      }

      // Skip if scene already has a StashDB ID
      if (sceneHasStashId(item)) {
        continue;
      }

      // Get scene ID
      const sceneId = getSceneIdFromElement(item);
      if (!sceneId) {
        continue;
      }

      // Find the query form / input group area
      const inputGroup = item.querySelector(".input-group, .input-group-append");
      if (!inputGroup) {
        continue;
      }

      // Add our button after the existing buttons
      const appendContainer = inputGroup.querySelector(".input-group-append");
      if (appendContainer) {
        const btn = createMatchButton(sceneId, item);
        appendContainer.appendChild(btn);
      } else {
        // Create append container if it doesn't exist
        const btn = createMatchButton(sceneId, item);
        inputGroup.appendChild(btn);
      }
    }
  }

  /**
   * Check if we're on a tagger page
   */
  function isTaggerPage() {
    const path = window.location.pathname;
    const search = window.location.search;

    // Bulk tagger: /scenes?c=tagger
    if (path.includes("/scenes") && search.includes("c=tagger")) {
      return true;
    }

    // Single scene tagger: /scenes/123?... with tagger in the query or component
    if (path.match(/\/scenes\/\d+/) && (search.includes("tagger") || document.querySelector(".tagger-container"))) {
      return true;
    }

    // Also check for tagger container existing on the page
    if (document.querySelector(".tagger-container")) {
      return true;
    }

    return false;
  }

  /**
   * Wait for page to be ready and add buttons
   */
  function waitForPage() {
    // Only run on tagger pages
    if (!isTaggerPage()) {
      return;
    }

    // Check immediately
    addMatchButtons();

    // Also observe for dynamic content loading
    const observer = new MutationObserver(() => {
      if (isTaggerPage()) {
        // Small delay to let React finish rendering
        setTimeout(addMatchButtons, 100);
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    // Also listen to popstate for SPA navigation
    window.addEventListener("popstate", () => {
      setTimeout(() => {
        if (isTaggerPage()) {
          addMatchButtons();
        }
      }, 100);
    });
  }

  /**
   * Initialize the plugin
   */
  function init() {
    console.log("[SceneMatcher] Initializing...");

    // Wait for DOM to be ready
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", waitForPage);
    } else {
      waitForPage();
    }

    // Also listen for URL changes (SPA navigation)
    let lastUrl = window.location.href;
    const urlObserver = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(waitForPage, 200);
      }
    });

    urlObserver.observe(document.body, { childList: true, subtree: true });
  }

  // Start the plugin
  init();
})();
