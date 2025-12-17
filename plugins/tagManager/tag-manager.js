(function () {
  "use strict";

  const PLUGIN_ID = "tagManager";
  const ROUTE_PATH = "/plugin/tag-manager";

  // Default settings
  const DEFAULTS = {
    stashdbEndpoint: "https://stashdb.org/graphql",
    stashdbApiKey: "",
    enableFuzzySearch: true,
    enableSynonymSearch: true,
    fuzzyThreshold: 80,
    pageSize: 25,
  };

  // State
  let settings = { ...DEFAULTS };
  let stashdbTags = null; // Cached StashDB tags
  let localTags = []; // Local Stash tags
  let currentPage = 1;
  let isLoading = false;
  let matchResults = {}; // Cache of tag_id -> matches

  /**
   * Get the GraphQL endpoint URL for local Stash
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request to local Stash
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
    if (result.errors?.length > 0) {
      throw new Error(result.errors[0].message);
    }

    return result.data;
  }

  /**
   * Get plugin settings from Stash configuration
   */
  async function loadSettings() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID] || {};

      settings = {
        stashdbEndpoint: pluginConfig.stashdbEndpoint || DEFAULTS.stashdbEndpoint,
        stashdbApiKey: pluginConfig.stashdbApiKey || DEFAULTS.stashdbApiKey,
        enableFuzzySearch: pluginConfig.enableFuzzySearch !== false,
        enableSynonymSearch: pluginConfig.enableSynonymSearch !== false,
        fuzzyThreshold: parseInt(pluginConfig.fuzzyThreshold) || DEFAULTS.fuzzyThreshold,
        pageSize: parseInt(pluginConfig.pageSize) || DEFAULTS.pageSize,
      };

      console.debug("[tagManager] Settings loaded:", settings);
    } catch (e) {
      console.error("[tagManager] Failed to load settings:", e);
    }
  }

  /**
   * Fetch local tags from Stash
   */
  async function fetchLocalTags() {
    const query = `
      query FindTags {
        findTags(filter: { per_page: -1 }) {
          count
          tags {
            id
            name
            description
            aliases
            stash_ids {
              endpoint
              stash_id
            }
          }
        }
      }
    `;

    const data = await graphqlRequest(query);
    return data?.findTags?.tags || [];
  }

  /**
   * Call Python backend via runPluginOperation
   */
  async function callBackend(mode, args = {}) {
    const query = `
      mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
        runPluginOperation(plugin_id: $plugin_id, args: $args)
      }
    `;

    const fullArgs = {
      mode,
      stashdb_url: settings.stashdbEndpoint,
      stashdb_api_key: settings.stashdbApiKey,
      settings: settings,
      ...args,
    };

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: fullArgs,
    });

    const output = data?.runPluginOperation;
    if (output?.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /**
   * Render the main page content
   */
  function renderPage(container) {
    const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
    const matchedTags = localTags.filter(t => t.stash_ids && t.stash_ids.length > 0);

    const totalPages = Math.ceil(unmatchedTags.length / settings.pageSize);
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = unmatchedTags.slice(startIdx, startIdx + settings.pageSize);

    container.innerHTML = `
      <div class="tag-manager">
        <div class="tag-manager-header">
          <h2>Tag Manager</h2>
          <div class="tag-manager-stats">
            <span class="stat stat-unmatched">${unmatchedTags.length} unmatched</span>
            <span class="stat stat-matched">${matchedTags.length} matched</span>
          </div>
          <button class="btn btn-secondary" id="tm-settings-btn">Settings</button>
        </div>

        <div class="tag-manager-filters">
          <select id="tm-filter" class="form-control">
            <option value="unmatched">Show Unmatched</option>
            <option value="matched">Show Matched</option>
            <option value="all">Show All</option>
          </select>
          <button class="btn btn-primary" id="tm-search-all-btn" ${isLoading ? 'disabled' : ''}>
            ${isLoading ? 'Searching...' : 'Find Matches for Page'}
          </button>
        </div>

        <div class="tag-manager-list" id="tm-tag-list">
          ${pageTags.length === 0
            ? '<div class="tm-empty">No unmatched tags found</div>'
            : pageTags.map(tag => renderTagRow(tag)).join('')
          }
        </div>

        <div class="tag-manager-pagination">
          <button class="btn btn-secondary" id="tm-prev" ${currentPage <= 1 ? 'disabled' : ''}>Previous</button>
          <span>Page ${currentPage} of ${totalPages || 1}</span>
          <button class="btn btn-secondary" id="tm-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next</button>
        </div>

        <div id="tm-status" class="tag-manager-status"></div>
      </div>
    `;

    // Attach event handlers
    attachEventHandlers(container);
  }

  /**
   * Render a single tag row
   */
  function renderTagRow(tag) {
    const matches = matchResults[tag.id];
    const hasMatches = matches && matches.length > 0;
    const bestMatch = hasMatches ? matches[0] : null;

    let matchContent = '';
    if (isLoading) {
      matchContent = '<span class="tm-loading">Searching...</span>';
    } else if (hasMatches) {
      const matchTypeClass = bestMatch.match_type === 'exact' ? 'exact' :
                             bestMatch.match_type === 'alias' ? 'alias' : 'fuzzy';
      matchContent = `
        <div class="tm-match tm-match-${matchTypeClass}">
          <span class="tm-match-name">${escapeHtml(bestMatch.tag.name)}</span>
          <span class="tm-match-type">${bestMatch.match_type} (${bestMatch.score}%)</span>
          <span class="tm-match-category">${escapeHtml(bestMatch.tag.category?.name || '')}</span>
        </div>
        <div class="tm-actions">
          <button class="btn btn-success btn-sm tm-accept" data-tag-id="${tag.id}">Accept</button>
          <button class="btn btn-secondary btn-sm tm-more" data-tag-id="${tag.id}">More</button>
        </div>
      `;
    } else if (matches !== undefined) {
      matchContent = `
        <span class="tm-no-match">No matches found</span>
        <button class="btn btn-secondary btn-sm tm-search" data-tag-id="${tag.id}">Search</button>
      `;
    } else {
      matchContent = `
        <button class="btn btn-primary btn-sm tm-search" data-tag-id="${tag.id}">Find Match</button>
      `;
    }

    return `
      <div class="tm-tag-row" data-tag-id="${tag.id}">
        <div class="tm-tag-info">
          <span class="tm-tag-name">${escapeHtml(tag.name)}</span>
          ${tag.aliases?.length ? `<span class="tm-tag-aliases">${escapeHtml(tag.aliases.join(', '))}</span>` : ''}
        </div>
        <div class="tm-tag-match">
          ${matchContent}
        </div>
      </div>
    `;
  }

  /**
   * Attach event handlers to rendered elements
   */
  function attachEventHandlers(container) {
    // Pagination
    container.querySelector('#tm-prev')?.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        renderPage(container);
      }
    });

    container.querySelector('#tm-next')?.addEventListener('click', () => {
      const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
      const totalPages = Math.ceil(unmatchedTags.length / settings.pageSize);
      if (currentPage < totalPages) {
        currentPage++;
        renderPage(container);
      }
    });

    // Search all on page
    container.querySelector('#tm-search-all-btn')?.addEventListener('click', () => {
      searchAllOnPage(container);
    });

    // Individual search buttons
    container.querySelectorAll('.tm-search').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        searchSingleTag(tagId, container);
      });
    });

    // Accept buttons
    container.querySelectorAll('.tm-accept').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        showDiffDialog(tagId, container);
      });
    });

    // More buttons
    container.querySelectorAll('.tm-more').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        showMatchesModal(tagId, container);
      });
    });
  }

  /**
   * Search for matches for all tags on current page
   */
  async function searchAllOnPage(container) {
    const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = unmatchedTags.slice(startIdx, startIdx + settings.pageSize);

    isLoading = true;
    renderPage(container);

    for (const tag of pageTags) {
      try {
        const result = await callBackend('search', {
          tag_name: tag.name,
          stashdb_tags: stashdbTags,
        });
        matchResults[tag.id] = result.matches || [];
      } catch (e) {
        console.error(`[tagManager] Error searching for ${tag.name}:`, e);
        matchResults[tag.id] = [];
      }
    }

    isLoading = false;
    renderPage(container);
  }

  /**
   * Search for a single tag
   */
  async function searchSingleTag(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    if (!tag) return;

    try {
      const result = await callBackend('search', {
        tag_name: tag.name,
        stashdb_tags: stashdbTags,
      });
      matchResults[tagId] = result.matches || [];
      renderPage(container);
    } catch (e) {
      console.error(`[tagManager] Error searching for ${tag.name}:`, e);
      showStatus(`Error: ${e.message}`, 'error');
    }
  }

  /**
   * Show diff dialog for accepting a match
   */
  function showDiffDialog(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    const match = matches[0];
    // TODO: Implement full diff dialog (Task 6)
    console.log('Show diff dialog for', tag.name, 'with match', match.tag.name);
    showStatus('Diff dialog coming in next task...', 'info');
  }

  /**
   * Show modal with all matches
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    // TODO: Implement matches modal (Task 6)
    console.log('Show matches modal for', tag.name, matches);
    showStatus('Matches modal coming in next task...', 'info');
  }

  /**
   * Show status message
   */
  function showStatus(message, type = 'info') {
    const statusEl = document.getElementById('tm-status');
    if (statusEl) {
      statusEl.textContent = message;
      statusEl.className = `tag-manager-status tm-status-${type}`;
    }
  }

  /**
   * Main page component
   */
  function TagManagerPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);
    const [initialized, setInitialized] = React.useState(false);

    React.useEffect(() => {
      async function init() {
        if (!containerRef.current) return;

        await loadSettings();

        // Check if API key is configured
        if (!settings.stashdbApiKey) {
          containerRef.current.innerHTML = `
            <div class="tag-manager">
              <div class="tag-manager-error">
                <h3>StashDB API Key Required</h3>
                <p>Please configure your StashDB API key in Settings - Plugins - Tag Manager</p>
              </div>
            </div>
          `;
          return;
        }

        // Fetch local tags
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading tags...</div></div>';
        try {
          localTags = await fetchLocalTags();
        } catch (e) {
          containerRef.current.innerHTML = `<div class="tag-manager"><div class="tag-manager-error">Error loading tags: ${escapeHtml(e.message)}</div></div>`;
          return;
        }

        // Optionally fetch all StashDB tags for fuzzy matching
        // (Can be done in background for performance)
        if (settings.enableFuzzySearch) {
          try {
            const result = await callBackend('fetch_all');
            stashdbTags = result.tags || [];
            console.debug(`[tagManager] Cached ${stashdbTags.length} StashDB tags`);
          } catch (e) {
            console.warn('[tagManager] Could not cache StashDB tags:', e);
          }
        }

        setInitialized(true);
        renderPage(containerRef.current);
      }

      init();
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'tag-manager-container'
    });
  }

  /**
   * Register the route
   */
  function registerRoute() {
    PluginApi.register.route(ROUTE_PATH, TagManagerPage);
    console.log('[tagManager] Route registered:', ROUTE_PATH);
  }

  // Initialize
  registerRoute();
  console.log('[tagManager] Plugin loaded');
})();
