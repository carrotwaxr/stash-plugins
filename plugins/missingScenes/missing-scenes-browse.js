(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";
  const BROWSE_PATH = "/plugins/missing-scenes";

  // Use shared core module
  const Core = window.MissingScenesCore;
  const {
    runPluginOperation,
    escapeHtml,
    createSceneCard,
  } = Core;

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
  let activeFilterTagIds = [];
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

    // Build results content - placeholder for now, will be replaced with DOM elements
    let resultsPlaceholder;
    if (loading && scenes.length === 0) {
      resultsPlaceholder = '<div class="ms-placeholder">Loading...</div>';
    } else if (error) {
      resultsPlaceholder = `
        <div class="ms-placeholder ms-error">
          <div class="ms-error-icon">!</div>
          <div>${escapeHtml(error)}</div>
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
          ${resultsPlaceholder}
        </div>

        <div class="ms-browse-footer">
          <button class="ms-btn ms-btn-secondary" id="ms-load-more-btn" style="display: ${showLoadMore ? 'inline-block' : 'none'};" ${loading ? 'disabled' : ''}>
            Load More
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
            activeFilterTagIds: activeFilterTagIds,
          });
          grid.appendChild(card);
        }

        resultsDiv.appendChild(grid);
      }
    }
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
      activeFilterTagIds = result.active_filter_tag_ids || [];

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
