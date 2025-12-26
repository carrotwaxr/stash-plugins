(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";
  const BROWSE_PATH = "/plugin/missingScenes/browse";

  // State
  let browsePageRoot = null;
  let missingScenes = [];
  let isLoading = false;
  let currentCursor = null;
  let hasMore = true;
  let sortField = "DATE";
  let sortDirection = "DESC";
  let filterFavoritePerformers = false;
  let filterFavoriteStudios = false;
  let filterFavoriteTags = false;
  let whisparrConfigured = false;
  let stashdbUrl = "";

  /**
   * Get the GraphQL endpoint URL
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request
   */
  async function graphqlRequest(query, variables = {}) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
   * Run a plugin operation
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
    if (!rawOutput) throw new Error("No response from plugin");

    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      throw new Error("Invalid response from plugin");
    }

    if (output.error) throw new Error(output.error);
    return output;
  }

  /**
   * Browse StashDB for missing scenes
   */
  async function browseStashdb(options = {}) {
    return runPluginOperation({
      operation: "browse_stashdb",
      page_size: options.pageSize || 50,
      cursor: options.cursor || null,
      sort: options.sort || "DATE",
      direction: options.direction || "DESC",
      filter_favorite_performers: options.filterFavoritePerformers || false,
      filter_favorite_studios: options.filterFavoriteStudios || false,
      filter_favorite_tags: options.filterFavoriteTags || false,
    });
  }

  /**
   * Check if we're on the browse page
   */
  function isOnBrowsePage() {
    return window.location.pathname === BROWSE_PATH;
  }

  /**
   * Initialize browse page if on correct route
   */
  function initBrowsePage() {
    if (!isOnBrowsePage()) return;
    if (browsePageRoot) return; // Already initialized

    // Create the browse page
    createBrowsePage();

    // Load initial results
    performSearch(true);
  }

  /**
   * Create the browse page structure
   */
  function createBrowsePage() {
    // Clear existing content
    const mainContainer = document.querySelector(".main > div") ||
                          document.querySelector("#root > div > div");

    if (!mainContainer) {
      console.error("[MissingScenes] Could not find main container");
      return;
    }

    // Create browse page container
    const page = document.createElement("div");
    page.className = "ms-browse-page";
    page.innerHTML = `
      <div class="ms-browse-header">
        <h1>Missing Scenes</h1>
        <p>Browse StashDB scenes you don't have locally</p>
      </div>

      <div class="ms-browse-controls">
        <div class="ms-filter-controls">
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-performers">
            <span>Favorite Performers</span>
          </label>
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-studios">
            <span>Favorite Studios</span>
          </label>
          <label class="ms-filter-checkbox">
            <input type="checkbox" id="ms-filter-tags">
            <span>Favorite Tags</span>
          </label>
        </div>

        <div class="ms-sort-controls">
          <label>Sort by:</label>
          <select id="ms-sort-field" class="ms-sort-select">
            <option value="DATE">Release Date</option>
            <option value="TITLE">Title</option>
            <option value="CREATED_AT">Added to StashDB</option>
            <option value="UPDATED_AT">Last Updated</option>
            <option value="TRENDING">Trending</option>
          </select>
          <select id="ms-sort-direction" class="ms-sort-select">
            <option value="DESC">Newest First</option>
            <option value="ASC">Oldest First</option>
          </select>
        </div>
      </div>

      <div class="ms-browse-stats" id="ms-browse-stats"></div>

      <div class="ms-browse-results" id="ms-browse-results">
        <div class="ms-placeholder">Loading...</div>
      </div>

      <div class="ms-browse-footer">
        <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: none;">
          Load More
        </button>
      </div>
    `;

    // Replace content
    mainContainer.innerHTML = "";
    mainContainer.appendChild(page);
    browsePageRoot = page;

    // Add event listeners
    setupEventListeners();
  }

  /**
   * Setup event listeners for controls
   */
  function setupEventListeners() {
    // Filter checkboxes
    document.getElementById("ms-filter-performers")?.addEventListener("change", (e) => {
      filterFavoritePerformers = e.target.checked;
      performSearch(true);
    });

    document.getElementById("ms-filter-studios")?.addEventListener("change", (e) => {
      filterFavoriteStudios = e.target.checked;
      performSearch(true);
    });

    document.getElementById("ms-filter-tags")?.addEventListener("change", (e) => {
      filterFavoriteTags = e.target.checked;
      performSearch(true);
    });

    // Sort controls
    document.getElementById("ms-sort-field")?.addEventListener("change", (e) => {
      sortField = e.target.value;
      performSearch(true);
    });

    document.getElementById("ms-sort-direction")?.addEventListener("change", (e) => {
      sortDirection = e.target.value;
      performSearch(true);
    });

    // Load more button
    document.getElementById("ms-load-more-btn")?.addEventListener("click", () => {
      performSearch(false);
    });
  }

  /**
   * Perform search/browse
   */
  async function performSearch(reset = true) {
    if (isLoading) return;

    if (reset) {
      currentCursor = null;
      missingScenes = [];
      hasMore = true;
    }

    isLoading = true;
    updateLoadingState(true);

    try {
      const result = await browseStashdb({
        pageSize: 50,
        cursor: currentCursor,
        sort: sortField,
        direction: sortDirection,
        filterFavoritePerformers,
        filterFavoriteStudios,
        filterFavoriteTags,
      });

      missingScenes = reset ? result.missing_scenes : [...missingScenes, ...result.missing_scenes];
      currentCursor = result.cursor;
      hasMore = result.has_more;
      whisparrConfigured = result.whisparr_configured;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";

      updateStats(result);
      renderResults();
    } catch (error) {
      console.error("[MissingScenes] Browse failed:", error);
      showError(error.message);
    } finally {
      isLoading = false;
      updateLoadingState(false);
    }
  }

  /**
   * Update stats display
   */
  function updateStats(result) {
    const statsEl = document.getElementById("ms-browse-stats");
    if (!statsEl) return;

    const loaded = missingScenes.length;
    const total = result.total_on_stashdb;
    const filtersActive = result.filters_active;
    const excludedApplied = result.excluded_tags_applied;

    let text = `Showing ${loaded}`;
    if (!result.is_complete) {
      text += ` of ~${total.toLocaleString()}`;
    }
    text += " missing scenes";

    if (filtersActive) text += " (filtered)";
    if (excludedApplied) text += " (content filtered)";

    statsEl.textContent = text;
  }

  /**
   * Render scene results
   */
  function renderResults() {
    const container = document.getElementById("ms-browse-results");
    if (!container) return;

    if (missingScenes.length === 0) {
      container.innerHTML = `
        <div class="ms-placeholder ms-success">
          <div class="ms-success-icon">&#10003;</div>
          <div>No missing scenes found!</div>
        </div>
      `;
      updateLoadMoreButton();
      return;
    }

    // Create grid - reuse scene card structure from main plugin
    const grid = document.createElement("div");
    grid.className = "ms-results-grid";

    for (const scene of missingScenes) {
      const card = createSceneCard(scene);
      grid.appendChild(card);
    }

    container.innerHTML = "";
    container.appendChild(grid);
    updateLoadMoreButton();
  }

  /**
   * Create a scene card (matches existing modal style)
   */
  function createSceneCard(scene) {
    const card = document.createElement("div");
    card.className = "ms-scene-card";
    card.dataset.stashId = scene.stash_id;

    // Build card HTML
    const thumbUrl = scene.thumbnail || "";
    const title = scene.title || "Unknown";
    const studio = scene.studio?.name || "";
    const date = scene.release_date ? formatDate(scene.release_date) : "";
    const performers = (scene.performers || []).slice(0, 3).map(p => p.name).join(", ");

    card.innerHTML = `
      <div class="ms-scene-thumb ${thumbUrl ? '' : 'ms-no-image'}">
        ${thumbUrl ? `<img src="${thumbUrl}" alt="${escapeHtml(title)}" loading="lazy">` : '<span class="ms-no-image-icon">&#128247;</span>'}
      </div>
      <div class="ms-scene-info">
        <div class="ms-scene-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="ms-scene-meta">${escapeHtml([studio, date].filter(Boolean).join(" - "))}</div>
        <div class="ms-scene-performers">${escapeHtml(performers)}</div>
      </div>
      <div class="ms-scene-actions">
        <a class="ms-btn ms-btn-small" href="${stashdbUrl}/scenes/${scene.stash_id}" target="_blank" rel="noopener">View</a>
      </div>
    `;

    card.onclick = (e) => {
      // Don't navigate if clicking the View button
      if (e.target.closest('.ms-scene-actions')) return;
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank");
    };

    return card;
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
      return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch {
      return dateStr;
    }
  }

  /**
   * Update load more button
   */
  function updateLoadMoreButton() {
    const btn = document.getElementById("ms-load-more-btn");
    if (!btn) return;

    if (hasMore && missingScenes.length > 0) {
      btn.style.display = "inline-block";
      btn.disabled = isLoading;
    } else {
      btn.style.display = "none";
    }
  }

  /**
   * Update loading state
   */
  function updateLoadingState(loading) {
    const btn = document.getElementById("ms-load-more-btn");
    if (btn) btn.disabled = loading;

    const resultsContainer = document.getElementById("ms-browse-results");
    if (resultsContainer && loading && missingScenes.length === 0) {
      resultsContainer.innerHTML = '<div class="ms-placeholder">Loading...</div>';
    }
  }

  /**
   * Show error message
   */
  function showError(message) {
    const container = document.getElementById("ms-browse-results");
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
   * Add "Missing Scenes" button to Scenes page
   */
  function addScenesPageButton() {
    if (!window.location.pathname.startsWith("/scenes")) return;
    if (document.querySelector(".ms-browse-button")) return;

    // Find the header area - Stash uses different class names in different versions
    const header = document.querySelector(".scenes-header") ||
                   document.querySelector('[class*="ListHeader"]') ||
                   document.querySelector(".content-header") ||
                   document.querySelector(".btn-toolbar");

    if (!header) return;

    const btn = document.createElement("button");
    btn.className = "ms-browse-button btn btn-secondary";
    btn.type = "button";
    btn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 1em; height: 1em; margin-right: 0.5em;">
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      Missing Scenes
    `;
    btn.onclick = () => {
      window.location.href = BROWSE_PATH;
    };

    header.appendChild(btn);
  }

  /**
   * Watch for navigation changes
   */
  function watchNavigation() {
    // Check on page load
    initBrowsePage();
    addScenesPageButton();

    // Watch for SPA navigation
    const observer = new MutationObserver(() => {
      setTimeout(() => {
        // Reset state when leaving browse page
        if (!isOnBrowsePage() && browsePageRoot) {
          browsePageRoot = null;
          missingScenes = [];
          currentCursor = null;
        }
        initBrowsePage();
        addScenesPageButton();
      }, 100);
    });

    observer.observe(document.body, { childList: true, subtree: true });

    window.addEventListener("popstate", () => {
      setTimeout(() => {
        if (!isOnBrowsePage() && browsePageRoot) {
          browsePageRoot = null;
          missingScenes = [];
          currentCursor = null;
        }
        initBrowsePage();
        addScenesPageButton();
      }, 100);
    });
  }

  /**
   * Initialize
   */
  function init() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", watchNavigation);
    } else {
      watchNavigation();
    }
  }

  init();
})();
