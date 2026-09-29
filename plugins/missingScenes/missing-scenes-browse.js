(function () {
  "use strict";

  const BROWSE_PATH = "/plugins/missing-scenes";

  // Use shared core module
  const Core = window.MissingScenesCore;
  const {
    runPluginOperation,
    describeFailure,
    escapeHtml,
    describeWhisparrAdd,
    describeWhisparrStatusError,
    createSceneCard,
  } = Core;

  /**
   * Fetch all configured stash-box endpoints
   */
  async function getAllEndpoints() {
    return runPluginOperation({ operation: "get_all_endpoints" });
  }

  /**
   * Browse StashDB for missing scenes
   */
  async function browseStashdb(options = {}) {
    const args = {
      operation: "browse_stashdb",
      page_size: options.pageSize || pageSize,
      cursor: options.cursor || null,
      sort: options.sort || "DATE",
      direction: options.direction || "DESC",
      filter_favorite_performers: options.filterFavoritePerformers || false,
      filter_favorite_studios: options.filterFavoriteStudios || false,
      filter_favorite_tags: options.filterFavoriteTags || false,
    };
    if (selectedEndpoint) {
      args.endpoint = selectedEndpoint;
    }
    return runPluginOperation(args);
  }

  // Page state (module-scoped for persistence)
  let missingScenes = [];
  let isLoading = false;
  let requestToken = 0; // bumped by every new request and when the page is left
  let currentCursor = null;
  let hasMore = true;
  let sortField = "DATE";
  let sortDirection = "DESC";
  let filterFavoritePerformers = false;
  let filterFavoriteStudios = false;
  let filterFavoriteTags = false;
  let activeFilterTagIds = [];
  let pageSize = 50;
  let whisparrConfigured = false;
  let whisparrError = null; // set when the Whisparr status map could not be fetched
  let stashdbUrl = "";
  let availableEndpoints = [];
  let selectedEndpoint = null;

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
   * Render the browse page content into the container
   */
  function renderPage(container, state) {
    const { loading, error, warning, scenes, stats } = state;

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

    const pageSizeOptions = [25, 50, 100]
      .map(n => `<option value="${n}" ${pageSize === n ? 'selected' : ''}>${n}</option>`)
      .join('');

    const directionOptions = [
      { value: "DESC", label: "Newest First" },
      { value: "ASC", label: "Oldest First" },
    ].map(opt => `<option value="${opt.value}" ${sortDirection === opt.value ? 'selected' : ''}>${opt.label}</option>`).join('');

    // Build stats text
    let statsText = '';
    if (stats) {
      // "missing" refers only to the missing estimate; the stash-box total is a different number
      const estimate = stats.missing_count_estimate;
      statsText = `Showing ${scenes.length}`;
      if (!stats.is_complete && typeof estimate === "number" && estimate >= scenes.length) {
        statsText += ` of ~${estimate.toLocaleString()}`;
      }
      statsText += " missing scenes";
      if (typeof stats.total_on_stashdb === "number") {
        statsText += ` (${stats.total_on_stashdb.toLocaleString()} scenes on ${stats.stashdb_name || "StashDB"})`;
      }
      if (stats.filters_active) statsText += " (filtered)";
      if (stats.excluded_tags_applied) statsText += " (content filtered)";
      if (stats.cache_info) {
        const ci = stats.cache_info;
        if (ci.source === "built") {
          statsText += ` | Index: ${ci.count.toLocaleString()} scenes (built in ${(ci.build_time_ms / 1000).toFixed(1)}s)`;
        } else if (ci.source !== "unknown") {
          statsText += ` | Index: ${ci.count.toLocaleString()} scenes (cached)`;
        }
      }
    }

    // Build results content - placeholder for now, will be replaced with DOM elements
    let resultsPlaceholder;
    if (loading && scenes.length === 0) {
      resultsPlaceholder = `
        <div class="ms-placeholder">
          <div class="ms-spinner"></div>
          <div>Loading missing scenes...</div>
          <div class="ms-loading-detail">Building scene index (this may take a moment)</div>
        </div>
      `;
    } else if (loading && scenes.length > 0) {
      // Loading more - don't replace existing content, just show in footer
      resultsPlaceholder = '';
    } else if (error) {
      resultsPlaceholder = `
        <div class="ms-placeholder ms-error">
          <div class="ms-error-icon">!</div>
          <div>${escapeHtml(error)}</div>
          <button class="ms-btn ms-retry-btn" id="ms-retry-btn">Retry</button>
        </div>
      `;
    } else if (scenes.length === 0) {
      resultsPlaceholder = `
        <div class="ms-placeholder ms-success">
          <div class="ms-success-icon">&#10003;</div>
          <div>No missing scenes found!</div>
        </div>
      `;
    } else {
      resultsPlaceholder = ''; // Will be filled with DOM elements below
    }

    // Load more button visibility
    const showLoadMore = hasMore && scenes.length > 0;

    let loadMoreText = 'Load More';
    if (stats && hasMore && typeof stats.missing_count_estimate === "number") {
      const estimatedRemaining = stats.missing_count_estimate - scenes.length;
      if (estimatedRemaining > 0) {
        const nextBatch = Math.min(pageSize, estimatedRemaining);
        loadMoreText = `Load More (${nextBatch})`;
      }
    }

    // Build endpoint dropdown (only if multiple endpoints configured)
    let endpointDropdown = '';
    if (availableEndpoints.length > 1) {
      const epOptions = availableEndpoints.map(ep =>
        `<option value="${escapeHtml(ep.endpoint)}" ${selectedEndpoint === ep.endpoint ? 'selected' : ''}>${escapeHtml(ep.name)}</option>`
      ).join('');
      endpointDropdown = `
        <div class="ms-endpoint-selector ms-browse-endpoint-selector">
          <label for="ms-endpoint-dropdown">Source:</label>
          <select id="ms-endpoint-dropdown" class="ms-endpoint-dropdown">${epOptions}</select>
        </div>
      `;
    }

    container.innerHTML = `
      <div class="ms-browse-page">
        <div class="ms-browse-header">
          <h1>Missing Scenes</h1>
          <p>Browse StashDB scenes you don't have locally</p>
        </div>

        <div class="ms-browse-controls">
          ${endpointDropdown}
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
            <label>Per page:</label>
            <select id="ms-page-size" class="ms-sort-select">
              ${pageSizeOptions}
            </select>
          </div>
        </div>

        <div class="ms-browse-stats">${escapeHtml(statsText)}</div>
        ${whisparrConfigured && whisparrError ? `<div class="ms-warning ms-whisparr-banner"><span class="ms-warning-text">${escapeHtml(describeWhisparrStatusError(whisparrError))}</span></div>` : ''}
        <div class="ms-browse-whisparr-status" id="ms-browse-status"></div>
        ${warning ? `<div class="ms-warning"><span class="ms-warning-text">${escapeHtml(warning)}</span> <button class="ms-btn ms-btn-secondary ms-retry-btn" id="ms-retry-btn">Retry from here</button></div>` : ''}

        <div class="ms-browse-results">
          ${resultsPlaceholder}
        </div>

        <div class="ms-browse-footer">
          <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: ${showLoadMore ? 'inline-block' : 'none'};" ${loading ? 'disabled' : ''}>
            ${loadMoreText}
          </button>
        </div>
      </div>
    `;

    // If we have scenes, render them using the shared createSceneCard component
    if (scenes.length > 0 && !error) {
      const resultsDiv = container.querySelector('.ms-browse-results');
      if (resultsDiv) {
        resultsDiv.innerHTML = '';
        const grid = document.createElement('div');
        grid.className = 'ms-results-grid';

        for (const scene of scenes) {
          const card = createSceneCard(scene, {
            stashdbUrl: stashdbUrl || "https://stashdb.org",
            whisparrConfigured: whisparrConfigured,
            endpoint: selectedEndpoint || stashdbUrl,
            activeFilterTagIds: activeFilterTagIds,
            onWhisparrAdd: (sc, success, detail) => {
              const el = container.querySelector('#ms-browse-status');
              if (!el) return;
              const msg = success
                ? describeWhisparrAdd(sc, detail)
                : `Failed to add: ${detail?.message || "Unknown error"}`;
              el.textContent = msg;
              el.className = "ms-status " +
                (!success || detail?.search_triggered === false ? "ms-status-error" : "ms-status-success");
            },
          });
          grid.appendChild(card);
        }

        resultsDiv.appendChild(grid);
      }
    }

    // Controls are rebuilt on every render; always re-attach so they work during a load
    setupControlHandlers(container);
  }

  /**
   * Perform search/browse and update state
   */
  async function performSearch(container, reset = true) {
    // Load More while another request is in flight would double-append
    if (!reset && isLoading) return;

    if (reset) {
      currentCursor = null;
      missingScenes = [];
      hasMore = true;
    }

    // A newer request (sort/filter/endpoint change) or leaving the page supersedes this one
    const token = ++requestToken;
    isLoading = true;
    renderPage(container, { loading: true, error: null, scenes: missingScenes, stats: null });

    try {
      const result = await browseStashdb({
        pageSize: pageSize,
        cursor: currentCursor,
        sort: sortField,
        direction: sortDirection,
        filterFavoritePerformers,
        filterFavoriteStudios,
        filterFavoriteTags,
      });
      if (token !== requestToken) return;

      const failureText = describeFailure(result);
      if (failureText && !result.partial) {
        // Nothing new loaded: keep the scenes we have; cursor and has_more stay as they were
        isLoading = false;
        hasMore = missingScenes.length > 0 && currentCursor !== null;
        renderPage(container, {
          loading: false,
          error: missingScenes.length === 0 ? failureText : null,
          warning: missingScenes.length > 0 ? failureText : null,
          scenes: missingScenes,
          stats: null,
        });
        return;
      }

      const newScenes = result.missing_scenes || [];
      missingScenes = reset ? newScenes : [...missingScenes, ...newScenes];
      currentCursor = result.cursor;
      hasMore = result.has_more;
      whisparrConfigured = result.whisparr_configured;
      whisparrError = result.whisparr_error || null;
      stashdbUrl = result.stashdb_url || "https://stashdb.org";
      activeFilterTagIds = result.active_filter_tag_ids || [];

      isLoading = false;
      renderPage(container, {
        loading: false,
        error: null,
        warning: failureText || null,
        scenes: missingScenes,
        stats: {
          total_on_stashdb: result.total_on_stashdb,
          missing_count_estimate: result.missing_count_estimate,
          stashdb_name: result.stashdb_name,
          is_complete: result.is_complete,
          filters_active: result.filters_active,
          excluded_tags_applied: result.excluded_tags_applied,
          cache_info: result.cache_info || null,
        }
      });
    } catch (error) {
      if (token !== requestToken) return;
      console.error("[MissingScenes] Browse failed:", error);
      isLoading = false;
      const msg = error.message || "Failed to load missing scenes";
      renderPage(container, {
        loading: false,
        error: missingScenes.length === 0 ? msg : null,
        warning: missingScenes.length > 0 ? msg : null,
        scenes: missingScenes,
        stats: null,
      });
    }
  }

  /**
   * Setup control handlers (filters, sort, load more)
   */
  function setupControlHandlers(container) {
    // Endpoint dropdown
    container.querySelector('#ms-endpoint-dropdown')?.addEventListener('change', (e) => {
      selectedEndpoint = e.target.value;
      performSearch(container, true);
    });

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

    container.querySelector('#ms-page-size')?.addEventListener('change', (e) => {
      pageSize = parseInt(e.target.value, 10);
      performSearch(container, true);
    });

    // Load more button
    container.querySelector('#ms-load-more-btn')?.addEventListener('click', () => {
      performSearch(container, false);
    });

    // Retry: from the returned cursor when scenes are already shown, else from scratch
    container.querySelector('#ms-retry-btn')?.addEventListener('click', () => {
      performSearch(container, missingScenes.length === 0);
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
        requestToken++;
        missingScenes = [];
        currentCursor = null;
        hasMore = true;
        isLoading = false;

        // Fetch available endpoints before first search
        try {
          const epInfo = await getAllEndpoints();
          availableEndpoints = epInfo.available_endpoints || [];
          selectedEndpoint = epInfo.default_endpoint || (availableEndpoints[0]?.endpoint);
        } catch (e) {
          console.error("[MissingScenes] Failed to fetch endpoints:", e);
        }

        // Initial render and load
        renderPage(containerRef.current, { loading: true, error: null, scenes: [], stats: null });
        performSearch(containerRef.current, true);
      }

      init();

      // Leaving the page: ignore any response still in flight
      return () => { requestToken++; };
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

  // Test hook: active only when a test sets window.__MISSING_SCENES_TEST__
  if (window.__MISSING_SCENES_TEST__) {
    window.__MISSING_SCENES_TEST__.browse = {
      getAllEndpoints,
      browseStashdb,
      renderPage,
      performSearch,
      setupControlHandlers,
      MissingScenesBrowsePage,
    };
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();
  console.log('[MissingScenes] Browse plugin loaded');
})();
