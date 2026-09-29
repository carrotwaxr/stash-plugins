(function () {
  "use strict";

  const PLUGIN_ID = "sceneMatcher";

  // State
  let modalRoot = null;
  let currentSceneId = null;
  let currentSceneElement = null;
  let matchResults = [];
  let isLoading = false;
  let isLoadingDeep = false;
  let stashdbUrl = "";
  let canSearchDeep = false;
  let phase1SearchAttrs = null;
  let currentEndpoint = null; // endpoint the open modal was searched against
  let requestToken = 0; // bumped by every Match click; late responses from older searches are dropped
  let currentBoxName = null; // display name of the stash-box the open modal searched
  const boxLabel = () => currentBoxName || "StashDB"; // fallback only until a response names the box
  let searchInfo = null; // notices from the search responses: warnings, partial, truncated, error
  const REQUEST_TIMEOUT_MS = 120000;

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
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let result;
    try {
      const response = await fetch(getGraphQLUrl(), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ query, variables }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`GraphQL request failed: ${response.status}`);
      }

      result = await response.json();
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw new Error(`The request timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }

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

    // Structured stash-box results (they carry `phase`) are returned as-is so the UI can show
    // the error, its auth hint, warnings and partial results. Anything else with an error throws.
    if (output.error && !("phase" in output)) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Find matching scenes - Phase 1 (fast text searches)
   */
  async function findMatchesFast(sceneId, endpoint) {
    const args = {
      operation: "find_matches_fast",
      scene_id: sceneId,
    };
    if (endpoint) args.endpoint = endpoint;
    // Stash unwraps the plugin's "output" field. The server caches the local IDs itself.
    return runPluginOperation(args);
  }

  /**
   * Find matching scenes - Phase 2 (thorough performer/studio searches)
   */
  async function findMatchesThorough(sceneId, excludeIds, endpoint) {
    const args = {
      operation: "find_matches_thorough",
      scene_id: sceneId,
      exclude_ids: excludeIds || [],
    };
    if (endpoint) args.endpoint = endpoint;
    return runPluginOperation(args);
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
  const PARTIAL_DATE = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/;

  /**
   * Parse a stash-box date ("YYYY", "YYYY-MM" or "YYYY-MM-DD") into
   * { year, month, day } (month/day null when absent), or null when missing,
   * malformed or impossible. Built from the parts, never via Date(string).
   */
  function parsePartialDate(value) {
    if (typeof value !== "string") return null;
    const m = PARTIAL_DATE.exec(value.trim().slice(0, 10));
    if (!m) return null;
    const year = Number(m[1]);
    const month = m[2] ? Number(m[2]) : null;
    const day = m[3] ? Number(m[3]) : null;
    if (month !== null && (month < 1 || month > 12)) return null;
    if (day !== null && (day < 1 || day > new Date(year, month, 0).getDate())) return null;
    return { year, month, day };
  }

  /**
   * Format a stash-box date for display: "2024", "May 2024" or "May 3, 2024".
   * A malformed date is shown as-is.
   */
  function formatDate(dateStr) {
    if (!dateStr) return "";
    const p = parsePartialDate(dateStr);
    if (!p) return String(dateStr);
    if (p.month === null) return String(p.year);
    const date = new Date(p.year, p.month - 1, p.day || 1);
    if (p.day === null) {
      return date.toLocaleDateString(undefined, { year: "numeric", month: "long" });
    }
    return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  /**
   * Sort date for a result, as Python's _sort_date: [end-of-period day number, precision].
   * A partial date counts as the last day of its period; missing or malformed is [0, 0].
   */
  function sortDate(value) {
    const p = parsePartialDate(value);
    if (!p) return [0, 0];
    const month = p.month === null ? 12 : p.month;
    const day = p.day === null ? new Date(p.year, month, 0).getDate() : p.day;
    const precision = 1 + (p.month !== null) + (p.day !== null);
    return [Date.UTC(p.year, month - 1, day) / 86400000, precision];
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

    if (scene.matches_date) {
      parts.push("Date");
    }

    if (scene.matching_performers > 0) {
      const count = scene.matching_performers;
      parts.push(count === 1 ? "1 Performer" : `${count} Performers`);
    }

    return parts.join(" + ");
  }

  /**
   * Create the "Search by performer/studio" button for Phase 2
   */
  function createDeepSearchButton() {
    const wrapper = document.createElement("div");
    wrapper.className = "sm-deep-search-prompt";

    const description = document.createElement("div");
    description.className = "sm-deep-search-description";

    // Build description of what will be searched
    const attrs = phase1SearchAttrs || {};
    const performers = attrs.performers || [];
    const studio = attrs.studio;

    let searchDesc = `Search ${escapeHtml(boxLabel())} for all scenes`;
    if (performers.length > 0 && studio) {
      searchDesc += ` from <strong>${escapeHtml(studio)}</strong> featuring <strong>${escapeHtml(performers.slice(0, 2).join(", "))}${performers.length > 2 ? "..." : ""}</strong>`;
    } else if (studio) {
      searchDesc += ` from <strong>${escapeHtml(studio)}</strong>`;
    } else if (performers.length > 0) {
      searchDesc += ` featuring <strong>${escapeHtml(performers.slice(0, 2).join(", "))}${performers.length > 2 ? "..." : ""}</strong>`;
    }

    description.innerHTML = searchDesc;

    const btn = document.createElement("button");
    btn.className = "sm-btn sm-btn-deep-search";
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <circle cx="11" cy="11" r="8"></circle>
        <path d="m21 21-4.35-4.35"></path>
      </svg>
      Search by performer/studio
    `;
    btn.onclick = handleDeepSearchClick;

    wrapper.appendChild(description);
    wrapper.appendChild(btn);

    return wrapper;
  }

  /**
   * Notices above the results: warnings, partial failure, truncation, and a phase error.
   * Every server-provided string is escaped.
   */
  function renderNotices(container) {
    if (!searchInfo) return;
    const lines = [];
    for (const w of searchInfo.warnings) lines.push(escapeHtml(String(w)));
    if (searchInfo.partial) lines.push("Some pages failed to load, so these results may be incomplete.");
    if (searchInfo.truncated) {
      const shown = escapeHtml(String(searchInfo.shown));
      if (searchInfo.pageCapped && searchInfo.searched != null) {
        // Only the newest scenes were fetched (queries sort by date), so say what was ranked
        const has = Number(searchInfo.total) > Number(searchInfo.searched)
          ? `the stash-box has ${escapeHtml(String(searchInfo.total))}` : "the stash-box has more";
        lines.push(`Showing the top ${shown} of the newest ${escapeHtml(String(searchInfo.searched))} scenes searched (${has}).`);
      } else {
        lines.push(`Showing the top ${shown} of ${escapeHtml(String(searchInfo.total))} candidates.`);
      }
    }
    // An auth error's message already names the box and points to its API key setting
    if (searchInfo.error) lines.push(escapeHtml(String(searchInfo.error)));
    if (!lines.length) return;
    const notice = document.createElement("div");
    notice.className = "sm-notice" + (searchInfo.error ? " sm-error" : "");
    notice.style.cssText = "margin: 0 0 12px; padding: 8px 12px; border-radius: 4px; background: rgba(255,193,7,0.12); border: 1px solid rgba(255,193,7,0.4); font-size: 13px;";
    notice.innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
    container.appendChild(notice);
  }

  /**
   * Fold a search response's notices into searchInfo.
   */
  function absorbNotices(result) {
    if (!searchInfo) {
      searchInfo = {
        warnings: [], partial: false, truncated: false, shown: 0, total: 0, searched: null, pageCapped: false,
        error: null,
      };
    }
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    for (const w of warnings) if (!searchInfo.warnings.includes(w)) searchInfo.warnings.push(w);
    if (result.partial) searchInfo.partial = true;
    if (result.truncated) {
      searchInfo.truncated = true;
      searchInfo.shown = (result.results || []).length;
      searchInfo.total = result.total_candidates != null ? result.total_candidates : searchInfo.shown;
      searchInfo.searched = result.searched_candidates != null ? result.searched_candidates : null;
      searchInfo.pageCapped = !!result.page_capped;
    }
  }

  /**
   * Render the results grid
   */
  function renderResults() {
    const container = document.getElementById("sm-results");
    if (!container) return;

    container.innerHTML = "";

    renderNotices(container);

    // Show deep search button if available and not already loading
    if (canSearchDeep && !isLoadingDeep) {
      const deepSearchBtn = createDeepSearchButton();
      container.appendChild(deepSearchBtn);
    }

    // Show loading indicator for deep search
    if (isLoadingDeep) {
      const loadingDiv = document.createElement("div");
      loadingDiv.className = "sm-deep-search-loading";
      loadingDiv.innerHTML = `
        <span class="sm-spinner-small"></span>
        <span>Searching by performer/studio...</span>
      `;
      container.appendChild(loadingDiv);
    }

    if (matchResults.length === 0 && !canSearchDeep && !isLoadingDeep) {
      const placeholder = document.createElement("div");
      placeholder.className = "sm-placeholder";
      placeholder.innerHTML = `
        <div>No matching scenes found on ${escapeHtml(boxLabel())}.</div>
        <div style="font-size: 14px; color: #666; margin-top: 8px;">
          Try linking more performers or the studio to ${escapeHtml(boxLabel())} first.
        </div>
      `;
      container.appendChild(placeholder);
      return;
    }

    if (matchResults.length === 0) {
      // No results yet but deep search available or loading
      return;
    }

    // Create grid
    const grid = document.createElement("div");
    grid.className = "sm-results-grid";

    for (const scene of matchResults) {
      const item = createSceneCard(scene);
      grid.appendChild(item);
    }

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
      handleSelectMatch(currentSceneId, scene.stash_id);
    };
    actions.appendChild(selectBtn);

    card.appendChild(thumbContainer);
    card.appendChild(info);
    card.appendChild(actions);

    // Click card to view on the stash-box (not select)
    card.onclick = () => {
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank", "noopener,noreferrer");
    };

    return card;
  }

  /**
   * Show a short-lived message over the page (the modal is already closed by then).
   */
  function showToast(message) {
    const el = document.createElement("div");
    el.className = "sm-toast";
    el.setAttribute("role", "alert");
    el.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:10001;background:#7a1f1f;color:#fff;padding:12px 16px;border-radius:6px;max-width:360px;";
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => { if (el.remove) el.remove(); }, 6000);
    return el;
  }

  /**
   * Hand the chosen match to the Tagger: put its UUID in the row's search box and run the search.
   * The row is looked up again by scene link, since React may have replaced it since the click.
   */
  function handleSelectMatch(sceneId, stashId) {
    removeModal();

    // Matched on the row's own scene id, exactly: a substring selector for "/scenes/12"
    // would also hit "/scenes/123" or a stash-box link ".../scenes/12ab..." in an earlier row.
    const row = Array.from(document.querySelectorAll(".search-item"))
      .find((r) => getSceneIdFromElement(r) === String(sceneId));
    if (!row) {
      console.error("[SceneMatcher] Could not find the Tagger row for scene", sceneId);
      showToast("Could not find this scene in the Tagger. Copy the ID and paste it into the search box: " + stashId);
      return;
    }
    const searchInput = row.querySelector("input.text-input");
    if (!searchInput) {
      console.error("[SceneMatcher] Could not find search input");
      showToast("Could not find the Tagger's search box for this scene. Search for this ID instead: " + stashId);
      return;
    }

    // React tracks controlled inputs through the native value setter.
    const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
    const desc = proto && Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(searchInput, stashId);
    else searchInput.value = stashId;
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));

    // Click the row's own Search button; if it is unavailable, press Enter (Stash listens for keypress).
    const searchBtn = Array.from(row.querySelectorAll(".input-group-append button"))
      .find((b) => !b.classList.contains("sm-match-button"));
    if (searchBtn && !searchBtn.disabled) {
      searchBtn.click();
    } else {
      searchInput.dispatchEvent(new KeyboardEvent("keypress", {
        key: "Enter", code: "Enter", keyCode: 13, charCode: 13, which: 13, bubbles: true,
      }));
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
          <div>Searching ${escapeHtml(boxLabel())} for matching scenes...</div>
        </div>
      `;
    }
  }

  /**
   * Show error state. An auth error's message comes from the server with the API-key hint.
   */
  function showError(message, { onRetry = null } = {}) {
    const container = document.getElementById("sm-results");
    if (!container) return;
    container.innerHTML = `
      <div class="sm-placeholder sm-error">
        <div class="sm-error-icon">!</div>
        <div>${escapeHtml(message)}</div>
      </div>
    `;
    if (onRetry) {
      const btn = document.createElement("button");
      btn.className = "sm-btn sm-btn-retry";
      btn.textContent = "Retry";
      btn.onclick = onRetry;
      container.appendChild(btn);
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

    // Same key as Python's result_sort_key: not in local stash first, then score,
    // duration score and end-of-period date (more precise first), all descending.
    const key = (r) => {
      const [ordinal, precision] = sortDate(r.release_date);
      return [r.in_local_stash ? 1 : 0, -r.score, -(r.duration_score ?? 0.5), -ordinal, -precision];
    };
    const keyed = merged.map((r) => [key(r), r]);
    keyed.sort((a, b) => {
      for (let i = 0; i < a[0].length; i++) {
        if (a[0][i] !== b[0][i]) return a[0][i] < b[0][i] ? -1 : 1;
      }
      return 0;
    });
    merged.splice(0, merged.length, ...keyed.map((k) => k[1]));

    return merged;
  }

  /**
   * Handle deep search button click (Phase 2)
   */
  async function handleDeepSearchClick() {
    if (isLoadingDeep || !currentSceneId) return;

    const token = requestToken;
    isLoadingDeep = true;
    canSearchDeep = false; // Hide the button
    renderResults();
    setStatus("Searching by performer/studio...", "loading");

    try {
      const excludeIds = matchResults.map((r) => r.stash_id);
      const phase2Result = await findMatchesThorough(currentSceneId, excludeIds, currentEndpoint);
      if (token !== requestToken) return; // a newer search owns the modal

      absorbNotices(phase2Result);
      const phase2Results = phase2Result.results || [];

      if (phase2Results.length > 0) {
        // Merge and re-render
        matchResults = mergeResults(matchResults, phase2Results);
        console.log(`[SceneMatcher] Deep search added ${phase2Results.length} results, total: ${matchResults.length}`);
      }

      if (phase2Result.error) {
        searchInfo.error = phase2Result.error;
        canSearchDeep = true; // the button doubles as retry
        setStatus("Deep search failed: " + phase2Result.error, "error");
        updateResultCount(matchResults.length, false);
        return;
      }

      // Update final status
      updateResultCount(matchResults.length, false);
      if (matchResults.length > 0) {
        setStatus(`Found ${matchResults.length} potential matches`, "success");
      } else {
        setStatus("No matches found", "");
      }
    } catch (error) {
      if (token !== requestToken) return;
      console.warn("[SceneMatcher] Deep search failed:", error);
      setStatus("Deep search failed: " + (error.message || "Unknown error"), "error");
      // Re-enable the button so user can retry
      canSearchDeep = true;
    } finally {
      if (token === requestToken) {
        isLoadingDeep = false;
        renderResults();
      }
    }
  }

  /**
   * Handle the match button click - Phase 1 only, Phase 2 is user-initiated.
   * Each click takes a new request token; responses from older clicks are ignored.
   */
  async function handleMatchClick(sceneId, sceneElement, endpoint, boxName = null) {
    const token = ++requestToken;
    if (endpoint === undefined) endpoint = await effectiveEndpoint();
    if (token !== requestToken) return;
    currentEndpoint = endpoint || null;
    currentBoxName = boxName || null;

    currentSceneId = sceneId;
    currentSceneElement = sceneElement;
    matchResults = [];
    canSearchDeep = false;
    phase1SearchAttrs = null;
    isLoadingDeep = false;
    searchInfo = null;

    isLoading = true;
    createModal();
    showLoading();
    setStatus("Searching...", "loading");
    const retry = () => handleMatchClick(sceneId, sceneElement, endpoint, boxName);

    try {
      // Phase 1: Fast text searches
      console.log("[SceneMatcher] Starting Phase 1 (fast text search)...");
      const phase1Result = await findMatchesFast(sceneId, currentEndpoint);
      if (token !== requestToken) return;

      if (phase1Result.error) {
        const partialResults = phase1Result.results || [];
        if (partialResults.length === 0) {
          showError(phase1Result.error, { onRetry: retry });
          setStatus(phase1Result.error, "error");
          return;
        }
        absorbNotices(phase1Result);
        searchInfo.error = phase1Result.error;
      } else {
        absorbNotices(phase1Result);
      }

      matchResults = phase1Result.results || [];
      if (phase1Result.endpoint_name) currentBoxName = phase1Result.endpoint_name;
      stashdbUrl = phase1Result.stashdb_url || "https://stashdb.org";
      phase1SearchAttrs = phase1Result.search_attributes;

      // Check if deeper search is available (has linked performers or studio)
      canSearchDeep = phase1Result.has_more || false;

      // Update modal header with scene title
      const header = document.querySelector(".sm-modal-header h3");
      if (header && phase1Result.scene_title) {
        header.textContent = `Scene Matcher - ${phase1Result.scene_title}`;
        header.title = phase1Result.scene_title;
      }

      // Show Phase 1 results
      updateStats(phase1Result, false);
      renderResults();

      if (matchResults.length > 0) {
        const moreAvailable = canSearchDeep ? " (more search options available)" : "";
        setStatus(`Found ${matchResults.length} potential matches${moreAvailable}`, "success");
      } else if (canSearchDeep) {
        setStatus("No matches from title search. Try searching by performer/studio.", "");
      } else {
        setStatus("No matches found", "");
      }
    } catch (error) {
      if (token !== requestToken) return;
      console.error("[SceneMatcher] Search failed:", error);
      showError(error.message || "Failed to search for matching scenes", { onRetry: retry });
      setStatus(error.message || "Search failed", "error");
    } finally {
      if (token === requestToken) isLoading = false;
    }
  }

  // A local scene page: ".../scenes/<numeric id>", optionally followed by "/", "?" or "#".
  const LOCAL_SCENE_PATH = /\/scenes\/(\d+)(?:[/?#]|$)/;

  /**
   * The scene id of a Tagger row, from the row's own link to the local scene page.
   * Stash-box links in the same row (stash-id pills, search results) point to another
   * origin, like "https://stashdb.org/scenes/<uuid>", and are skipped.
   */
  function getSceneIdFromElement(element) {
    const links = element.querySelectorAll ? Array.from(element.querySelectorAll('a[href*="/scenes/"]')) : [];
    for (const link of links) {
      const raw = link.getAttribute("href") || link.href || "";
      let url;
      try {
        url = new URL(raw, window.location.href);
      } catch (e) {
        continue;
      }
      if (url.origin !== window.location.origin) continue;
      const match = LOCAL_SCENE_PATH.exec(url.pathname + url.search + url.hash);
      if (match) return match[1];
    }
    return null;
  }

  const normalizeEndpoint = (url) => (url || "").trim().replace(/\/+$/, "").toLowerCase();

  // Stash configuration (stash-boxes, saved Tagger choice, plugin setting): fetched once.
  let configPromise = null;
  function getConfig() {
    if (!configPromise) {
      const query = `query SceneMatcherConfig {
        configuration { general { stashBoxes { endpoint name } } ui plugins }
      }`;
      configPromise = graphqlRequest(query)
        .then((data) => {
          const cfg = (data && data.configuration) || {};
          const ui = cfg.ui || {};
          const plugins = cfg.plugins || {};
          return {
            boxes: (cfg.general && cfg.general.stashBoxes) || [],
            uiEndpoint: (ui.taggerConfig && ui.taggerConfig.selectedEndpoint) || null,
            settingEndpoint: (plugins[PLUGIN_ID] && plugins[PLUGIN_ID].stashBoxEndpoint) || null,
          };
        })
        .catch((e) => {
          console.warn("[SceneMatcher] Could not load Stash configuration:", e);
          configPromise = null; // retry next time
          return { boxes: [], uiEndpoint: null, settingEndpoint: null };
        });
    }
    return configPromise;
  }

  function getScraperSelect() {
    return document.querySelector("select#scraper");
  }

  /**
   * The stash-box endpoint the Tagger targets right now, or null if none is known
   * (the backend then uses the first box). React sets the select's value without firing
   * `change`, so it is read fresh on every call. Returns { scraper: true } semantics via
   * isScraperSource() for non-stash-box sources.
   */
  function isScraperSource() {
    const select = getScraperSelect();
    return !!(select && String(select.value || "").startsWith("scraper:"));
  }

  async function effectiveEndpoint() {
    const select = getScraperSelect();
    if (select) {
      const value = String(select.value || "");
      if (value.startsWith("stashbox:")) return value.slice("stashbox:".length) || null;
      if (value.startsWith("scraper:")) return null;
    }
    const cfg = await getConfig();
    return cfg.uiEndpoint || cfg.settingEndpoint || null;
  }

  async function resolveEndpoint(endpoint) {
    if (endpoint) return endpoint;
    const cfg = await getConfig();
    return cfg.boxes.length ? cfg.boxes[0].endpoint : null;
  }

  async function boxNameFor(endpoint) {
    const cfg = await getConfig();
    const norm = normalizeEndpoint(endpoint);
    const box = cfg.boxes.find((b) => normalizeEndpoint(b.endpoint) === norm);
    return box && box.name ? box.name : null;
  }

  // Gate cache: `${normalized endpoint}|${scene id}` -> { sig, unlinked }
  const gateCache = new Map();

  /**
   * Of the given scene ids, return those with no stash_id for `endpoint` (null = first
   * configured box). One batched findScenes query for ids not already cached; a cached
   * result is reused while the row's pill signature (sigs[id]) is unchanged.
   */
  async function gateScenes(ids, endpoint, sigs = {}) {
    const resolved = await resolveEndpoint(endpoint);
    const norm = normalizeEndpoint(resolved);
    const key = (id) => `${norm}|${id}`;
    const sigOf = (id) => sigs[id] || "";

    const missing = ids.filter((id) => {
      const hit = gateCache.get(key(id));
      return !hit || hit.sig !== sigOf(id);
    });

    if (missing.length) {
      const query = `query SceneMatcherGate($ids: [ID!]) {
        findScenes(ids: $ids, filter: {per_page: -1}) { scenes { id stash_ids { endpoint } } }
      }`;
      const data = await graphqlRequest(query, { ids: missing });
      const scenes = (data && data.findScenes && data.findScenes.scenes) || [];
      const byId = new Map(scenes.map((sc) => [String(sc.id), sc]));
      for (const id of missing) {
        const sc = byId.get(String(id));
        const linked = !!sc && (sc.stash_ids || []).some((x) => normalizeEndpoint(x.endpoint) === norm);
        gateCache.set(key(id), { sig: sigOf(id), unlinked: !linked });
      }
    }
    return ids.filter((id) => gateCache.get(key(id)).unlinked);
  }

  /**
   * Create the match button
   */
  function createMatchButton(sceneId, sceneElement, endpoint, boxName) {
    const label = boxName ? `Match on ${boxName}` : "Match";
    const btn = document.createElement("button");
    btn.className = "sm-match-button btn btn-secondary";
    btn.type = "button";
    btn.dataset.endpoint = normalizeEndpoint(endpoint);
    btn.title = `Find matches by performer/studio${boxName ? ` on ${boxName}` : ""}`;
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
      </svg>
      ${label.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)}
    `;
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      // Re-read the selection at click time; fall back to what the button was built for.
      effectiveEndpoint().then((ep) => handleMatchClick(sceneId, sceneElement, ep || endpoint || null, boxName));
    };
    return btn;
  }

  /** Signature of the row's own stash-id pills (which endpoints it is linked to). */
  function rowSignature(row) {
    return Array.from(row.querySelectorAll(".stash-id-pill"))
      .map((p) => p.getAttribute("data-endpoint") || "")
      .join("|");
  }

  let syncRunning = false;
  let syncAgain = false;

  /**
   * Make the Match buttons reflect the Tagger's selected endpoint: a button on each row whose
   * scene is not linked to it, none on linked rows, none at all for a scraper source.
   * Idempotent and safe to call often; overlapping calls coalesce into one re-run.
   */
  async function syncMatchButtons() {
    if (syncRunning) { syncAgain = true; return; }
    syncRunning = true;
    try {
      do {
        syncAgain = false;
        await syncOnce();
      } while (syncAgain);
    } catch (e) {
      console.warn("[SceneMatcher] Could not update Match buttons:", e);
    } finally {
      syncRunning = false;
    }
  }

  async function syncOnce() {
    const select = getScraperSelect();
    if (select && !select.__smBound) {
      select.__smBound = true;
      select.addEventListener("change", () => { syncMatchButtons(); });
    }

    const rows = Array.from(document.querySelectorAll(".search-item"));
    if (isScraperSource()) {
      for (const row of rows) {
        const btn = row.querySelector(".sm-match-button");
        if (btn) btn.remove();
      }
      return;
    }

    const endpoint = await effectiveEndpoint();
    const resolved = await resolveEndpoint(endpoint);
    const boxName = resolved ? await boxNameFor(resolved) : null;

    const entries = [];
    for (const row of rows) {
      const id = getSceneIdFromElement(row);
      if (id) entries.push({ row, id, sig: rowSignature(row) });
    }
    const sigs = {};
    entries.forEach((e) => { sigs[e.id] = e.sig; });
    const ids = [...new Set(entries.map((e) => e.id))];
    const unlinked = new Set(ids.length ? await gateScenes(ids, resolved, sigs) : []);

    // The selection may have changed while we waited; a re-run is already queued if so.
    if (isScraperSource()) { syncAgain = true; return; }

    for (const { row, id } of entries) {
      const existing = row.querySelector(".sm-match-button");
      if (!unlinked.has(id)) {
        if (existing) existing.remove();
        continue;
      }
      if (existing) {
        if (existing.dataset.endpoint === normalizeEndpoint(resolved)) continue;
        existing.remove();
      }
      const inputGroup = row.querySelector(".input-group, .input-group-append");
      if (!inputGroup) continue;
      const btn = createMatchButton(id, row, endpoint || resolved, boxName);
      const appendContainer = inputGroup.querySelector(".input-group-append");
      (appendContainer || inputGroup).appendChild(btn);
    }
  }

  // Kept for callers that predate syncMatchButtons.
  function addMatchButtons() {
    return syncMatchButtons();
  }

  /**
   * Check if we're on a tagger page
   */
  function isTaggerPage() {
    const path = window.location.pathname;
    const search = window.location.search;

    // Must be on the scenes route
    if (!/\/scenes(\/|$)/.test(path)) {
      return false;
    }

    // Tagger display mode, or the Tagger's own elements once rendered
    if (search.includes("disp=3") || search.includes("c=tagger")) {
      return true;
    }
    return !!(document.querySelector("select#scraper") || document.querySelector(".search-item"));
  }

  /**
   * Wait for Tagger elements to appear, then sync buttons.
   * Uses polling to handle React rendering timing; a newer call cancels an older poll.
   */
  let pollToken = 0;
  function waitForTaggerElements(maxAttempts = 20, interval = 250) {
    const mine = ++pollToken;
    let attempts = 0;

    function check() {
      if (mine !== pollToken || !isTaggerPage()) return;
      attempts++;
      const sceneItems = document.querySelectorAll(".search-item");

      if (sceneItems.length > 0) {
        syncMatchButtons();
        return;
      }

      if (attempts < maxAttempts) {
        setTimeout(check, interval);
      }
    }

    check();
  }

  /**
   * Called on navigation and at startup: on a Tagger page, wait for its rows and sync.
   */
  function waitForPage() {
    if (!isTaggerPage()) {
      return;
    }
    waitForTaggerElements();
  }

  // Debounced sync for DOM mutations (React re-renders in bursts).
  let syncTimer = null;
  function scheduleSync() {
    if (syncTimer !== null) clearTimeout(syncTimer);
    syncTimer = setTimeout(() => {
      syncTimer = null;
      if (isTaggerPage()) syncMatchButtons();
    }, 150);
  }

  let initialized = false;

  /**
   * Initialize the plugin: one MutationObserver for the page lifetime, no popstate listener.
   */
  function init() {
    if (initialized) return;
    initialized = true;
    console.log("[SceneMatcher] Initializing...");

    // Wait for DOM to be ready
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", waitForPage);
    } else {
      waitForPage();
    }

    // SPA navigation: Stash's own location event when available, else the observer below.
    const hasLocationEvent =
      typeof PluginApi !== "undefined" && PluginApi && PluginApi.Event &&
      typeof PluginApi.Event.addEventListener === "function";
    if (hasLocationEvent) {
      PluginApi.Event.addEventListener("stash:location", () => {
        setTimeout(waitForPage, 100);
      });
    }

    let lastUrl = window.location.href;
    const observer = new MutationObserver(() => {
      if (!hasLocationEvent && window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        setTimeout(waitForPage, 200);
      }
      // Off the Tagger, mutations are ignored: no timers, no queries.
      if (isTaggerPage()) scheduleSync();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // Start the plugin
  init();

  // Test hook: only active when a test harness sets window.__SCENE_MATCHER_TEST__ first.
  if (window.__SCENE_MATCHER_TEST__) {
    window.__SCENE_MATCHER_TEST__.exports = {
      graphqlRequest, runPluginOperation, findMatchesFast, findMatchesThorough,
      formatDuration, formatDate, escapeHtml, renderResults, createSceneCard,
      handleSelectMatch, mergeResults, handleDeepSearchClick, handleMatchClick,
      getSceneIdFromElement, createMatchButton, addMatchButtons, syncMatchButtons,
      effectiveEndpoint, gateScenes,
      isTaggerPage, waitForPage, waitForTaggerElements, init,
      createModal, removeModal, updateStats, setStatus, showLoading, showError,
    };
    window.__SCENE_MATCHER_TEST__.getState = () => ({
      modalRoot, currentSceneId, currentSceneElement, matchResults, isLoading, isLoadingDeep,
      stashdbUrl, canSearchDeep, phase1SearchAttrs,
    });
    window.__SCENE_MATCHER_TEST__.setState = (patch) => {
      if ("modalRoot" in patch) modalRoot = patch.modalRoot;
      if ("currentSceneId" in patch) currentSceneId = patch.currentSceneId;
      if ("currentSceneElement" in patch) currentSceneElement = patch.currentSceneElement;
      if ("matchResults" in patch) matchResults = patch.matchResults;
      if ("isLoading" in patch) isLoading = patch.isLoading;
      if ("isLoadingDeep" in patch) isLoadingDeep = patch.isLoadingDeep;
      if ("stashdbUrl" in patch) stashdbUrl = patch.stashdbUrl;
      if ("canSearchDeep" in patch) canSearchDeep = patch.canSearchDeep;
      if ("phase1SearchAttrs" in patch) phase1SearchAttrs = patch.phase1SearchAttrs;
    };
  }
})();
