(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";
  const BROWSE_PATH = "/plugin/missingScenes/browse";

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

  // Page state (module-scoped for persistence)
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
   * Set page title with retry to overcome Stash's title management
   */
  function setPageTitle(title) {
    const doSet = () => { document.title = title; };
    doSet();
    setTimeout(doSet, 50);
    setTimeout(doSet, 200);
    setTimeout(doSet, 500);
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
   * Create a scene card HTML (for use in renderPage)
   */
  function createSceneCardHtml(scene) {
    const thumbUrl = scene.thumbnail || "";
    const title = scene.title || "Unknown";
    const studio = scene.studio?.name || "";
    const date = scene.release_date ? formatDate(scene.release_date) : "";
    const performers = (scene.performers || []).slice(0, 3).map(p => p.name).join(", ");
    const baseUrl = stashdbUrl || "https://stashdb.org";

    return `
      <div class="ms-scene-card" data-stash-id="${escapeHtml(scene.stash_id)}" data-url="${escapeHtml(baseUrl)}/scenes/${escapeHtml(scene.stash_id)}">
        <div class="ms-scene-thumb ${thumbUrl ? '' : 'ms-no-image'}">
          ${thumbUrl ? `<img src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(title)}" loading="lazy">` : '<span class="ms-no-image-icon">&#128247;</span>'}
        </div>
        <div class="ms-scene-info">
          <div class="ms-scene-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
          <div class="ms-scene-meta">${escapeHtml([studio, date].filter(Boolean).join(" - "))}</div>
          <div class="ms-scene-performers">${escapeHtml(performers)}</div>
        </div>
        <div class="ms-scene-actions">
          <a class="ms-btn ms-btn-small" href="${escapeHtml(baseUrl)}/scenes/${escapeHtml(scene.stash_id)}" target="_blank" rel="noopener">View</a>
        </div>
      </div>
    `;
  }

  /**
   * Render the browse page content into the container
   */
  function renderPage(container, state) {
    const { loading, error, scenes, stats } = state;

    // Build filter checkboxes
    const filterPerformersChecked = filterFavoritePerformers ? 'checked' : '';
    const filterStudiosChecked = filterFavoriteStudios ? 'checked' : '';
    const filterTagsChecked = filterFavoriteTags ? 'checked' : '';

    // Build sort options
    const sortOptions = [
      { value: "DATE", label: "Release Date" },
      { value: "TITLE", label: "Title" },
      { value: "CREATED_AT", label: "Added to StashDB" },
      { value: "UPDATED_AT", label: "Last Updated" },
      { value: "TRENDING", label: "Trending" },
    ].map(opt => `<option value="${opt.value}" ${sortField === opt.value ? 'selected' : ''}>${opt.label}</option>`).join('');

    const directionOptions = [
      { value: "DESC", label: "Newest First" },
      { value: "ASC", label: "Oldest First" },
    ].map(opt => `<option value="${opt.value}" ${sortDirection === opt.value ? 'selected' : ''}>${opt.label}</option>`).join('');

    // Build stats text
    let statsText = '';
    if (stats) {
      statsText = `Showing ${scenes.length}`;
      if (!stats.is_complete) {
        statsText += ` of ~${stats.total_on_stashdb.toLocaleString()}`;
      }
      statsText += " missing scenes";
      if (stats.filters_active) statsText += " (filtered)";
      if (stats.excluded_tags_applied) statsText += " (content filtered)";
    }

    // Build results content
    let resultsContent;
    if (loading && scenes.length === 0) {
      resultsContent = '<div class="ms-placeholder">Loading...</div>';
    } else if (error) {
      resultsContent = `
        <div class="ms-placeholder ms-error">
          <div class="ms-error-icon">!</div>
          <div>${escapeHtml(error)}</div>
        </div>
      `;
    } else if (scenes.length === 0) {
      resultsContent = `
        <div class="ms-placeholder ms-success">
          <div class="ms-success-icon">&#10003;</div>
          <div>No missing scenes found!</div>
        </div>
      `;
    } else {
      const cardsHtml = scenes.map(s => createSceneCardHtml(s)).join('');
      resultsContent = `<div class="ms-results-grid">${cardsHtml}</div>`;
    }

    // Load more button visibility
    const showLoadMore = hasMore && scenes.length > 0;

    container.innerHTML = `
      <div class="ms-browse-page">
        <div class="ms-browse-header">
          <h1>Missing Scenes</h1>
          <p>Browse StashDB scenes you don't have locally</p>
        </div>

        <div class="ms-browse-controls">
          <div class="ms-filter-controls">
            <label class="ms-filter-checkbox">
              <input type="checkbox" id="ms-filter-performers" ${filterPerformersChecked}>
              <span>Favorite Performers</span>
            </label>
            <label class="ms-filter-checkbox">
              <input type="checkbox" id="ms-filter-studios" ${filterStudiosChecked}>
              <span>Favorite Studios</span>
            </label>
            <label class="ms-filter-checkbox">
              <input type="checkbox" id="ms-filter-tags" ${filterTagsChecked}>
              <span>Favorite Tags</span>
            </label>
          </div>

          <div class="ms-sort-controls">
            <label>Sort by:</label>
            <select id="ms-sort-field" class="ms-sort-select">
              ${sortOptions}
            </select>
            <select id="ms-sort-direction" class="ms-sort-select">
              ${directionOptions}
            </select>
          </div>
        </div>

        <div class="ms-browse-stats">${statsText}</div>

        <div class="ms-browse-results">
          ${resultsContent}
        </div>

        <div class="ms-browse-footer">
          <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: ${showLoadMore ? 'inline-block' : 'none'};" ${loading ? 'disabled' : ''}>
            Load More
          </button>
        </div>
      </div>
    `;

    // Attach event handlers
    attachEventHandlers(container);
  }

  /**
   * Attach event handlers after rendering
   */
  function attachEventHandlers(container) {
    // Click handler for scene cards (open in new tab)
    container.querySelectorAll('.ms-scene-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('.ms-scene-actions')) return;
        const url = card.dataset.url;
        if (url) window.open(url, '_blank');
      });
    });
  }

  /**
   * Perform search/browse and update state
   */
  async function performSearch(container, reset = true) {
    if (isLoading) return;

    if (reset) {
      currentCursor = null;
      missingScenes = [];
      hasMore = true;
    }

    isLoading = true;
    renderPage(container, { loading: true, error: null, scenes: missingScenes, stats: null });

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

      isLoading = false;
      renderPage(container, {
        loading: false,
        error: null,
        scenes: missingScenes,
        stats: {
          total_on_stashdb: result.total_on_stashdb,
          is_complete: result.is_complete,
          filters_active: result.filters_active,
          excluded_tags_applied: result.excluded_tags_applied,
        }
      });

      // Re-attach filter/sort handlers after render
      setupControlHandlers(container);
    } catch (error) {
      console.error("[MissingScenes] Browse failed:", error);
      isLoading = false;
      renderPage(container, { loading: false, error: error.message, scenes: [], stats: null });
      setupControlHandlers(container);
    }
  }

  /**
   * Setup control handlers (filters, sort, load more)
   */
  function setupControlHandlers(container) {
    // Filter checkboxes
    container.querySelector('#ms-filter-performers')?.addEventListener('change', (e) => {
      filterFavoritePerformers = e.target.checked;
      performSearch(container, true);
    });

    container.querySelector('#ms-filter-studios')?.addEventListener('change', (e) => {
      filterFavoriteStudios = e.target.checked;
      performSearch(container, true);
    });

    container.querySelector('#ms-filter-tags')?.addEventListener('change', (e) => {
      filterFavoriteTags = e.target.checked;
      performSearch(container, true);
    });

    // Sort controls
    container.querySelector('#ms-sort-field')?.addEventListener('change', (e) => {
      sortField = e.target.value;
      performSearch(container, true);
    });

    container.querySelector('#ms-sort-direction')?.addEventListener('change', (e) => {
      sortDirection = e.target.value;
      performSearch(container, true);
    });

    // Load more button
    container.querySelector('#ms-load-more-btn')?.addEventListener('click', () => {
      performSearch(container, false);
    });
  }

  /**
   * Missing Scenes Browse Page component (React-based for PluginApi.register.route)
   */
  function MissingScenesBrowsePage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);

    React.useEffect(() => {
      async function init() {
        if (!containerRef.current) return;

        console.debug("[MissingScenes] Initializing browse page...");
        setPageTitle("Missing Scenes | Stash");

        // Reset state for fresh page load
        missingScenes = [];
        currentCursor = null;
        hasMore = true;
        isLoading = false;

        // Initial render and load
        renderPage(containerRef.current, { loading: true, error: null, scenes: [], stats: null });
        setupControlHandlers(containerRef.current);
        performSearch(containerRef.current, true);
      }

      init();
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'ms-browse-container'
    });
  }

  /**
   * Add "Missing Scenes" button to Scenes page toolbar
   */
  function addScenesPageButton() {
    if (!window.location.pathname.startsWith("/scenes")) return;
    if (document.querySelector(".ms-browse-button")) return;

    // Find the toolbar - Stash uses different class names in different versions
    const toolbar = document.querySelector(".filtered-list-toolbar") ||
                    document.querySelector(".scenes-header") ||
                    document.querySelector('[class*="ListHeader"]') ||
                    document.querySelector(".content-header") ||
                    document.querySelector(".btn-toolbar");

    if (!toolbar) return;

    // Find insertion point (similar to TagManager approach)
    let insertionPoint = toolbar.querySelector('.zoom-slider-container') ||
                         toolbar.querySelector('.display-mode-select');

    if (!insertionPoint) {
      const btnGroups = toolbar.querySelectorAll('.btn-group');
      for (const group of btnGroups) {
        const hasIcons = group.querySelector('.fa-icon') || group.querySelector('svg');
        if (hasIcons) {
          insertionPoint = group;
        }
      }
    }

    const btn = document.createElement("button");
    btn.className = "ms-browse-button btn btn-secondary";
    btn.type = "button";
    btn.title = "Missing Scenes";
    btn.style.marginLeft = "0.5rem";
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

    if (insertionPoint) {
      insertionPoint.parentNode.insertBefore(btn, insertionPoint.nextSibling);
    } else {
      toolbar.appendChild(btn);
    }

    console.debug('[MissingScenes] Nav button injected on Scenes page');
  }

  /**
   * Watch for navigation to Scenes page and inject button
   */
  function setupNavButtonInjection() {
    // Try to inject immediately
    addScenesPageButton();

    // Watch for URL changes (SPA navigation)
    let lastUrl = window.location.href;
    const observer = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        // Wait a bit for DOM to update after navigation
        setTimeout(addScenesPageButton, 100);
        setTimeout(addScenesPageButton, 500);
        setTimeout(addScenesPageButton, 1000);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Also try on initial load with delays (for refresh on Scenes page)
    setTimeout(addScenesPageButton, 100);
    setTimeout(addScenesPageButton, 500);
    setTimeout(addScenesPageButton, 1000);
    setTimeout(addScenesPageButton, 2000);
  }

  /**
   * Register the route with Stash's plugin API
   */
  function registerRoute() {
    PluginApi.register.route(BROWSE_PATH, MissingScenesBrowsePage);
    console.log('[MissingScenes] Route registered:', BROWSE_PATH);
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();
  console.log('[MissingScenes] Browse plugin loaded');
})();
