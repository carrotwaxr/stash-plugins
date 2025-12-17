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
  function showDiffDialog(tagId, container, matchIndex = 0) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag || !matches?.length) return;

    const match = matches[matchIndex];
    const stashdbTag = match.tag;

    // Determine defaults: use StashDB value if local is empty
    const nameDefault = tag.name ? 'local' : 'stashdb';
    const descDefault = tag.description ? 'local' : 'stashdb';
    const aliasesDefault = tag.aliases?.length ? 'local' : 'stashdb';

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal">
        <div class="tm-modal-header">
          <h3>Match: ${escapeHtml(tag.name)} - ${escapeHtml(stashdbTag.name)}</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <div class="tm-diff-info">
            <span class="tm-match-type-badge tm-match-${match.match_type}">${match.match_type}</span>
            <span>Score: ${match.score}%</span>
            ${stashdbTag.category ? `<span>Category: ${escapeHtml(stashdbTag.category.name)}</span>` : ''}
          </div>

          <table class="tm-diff-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Your Value</th>
                <th>StashDB Value</th>
                <th>Use</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Name</td>
                <td>${escapeHtml(tag.name) || '<em>empty</em>'}</td>
                <td>${escapeHtml(stashdbTag.name)}</td>
                <td>
                  <label><input type="radio" name="tm-name" value="local" ${nameDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-name" value="stashdb" ${nameDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Description</td>
                <td>${escapeHtml(tag.description) || '<em>empty</em>'}</td>
                <td>${escapeHtml(stashdbTag.description) || '<em>empty</em>'}</td>
                <td>
                  <label><input type="radio" name="tm-desc" value="local" ${descDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-desc" value="stashdb" ${descDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Aliases</td>
                <td>${tag.aliases?.length ? escapeHtml(tag.aliases.join(', ')) : '<em>none</em>'}</td>
                <td>${stashdbTag.aliases?.length ? escapeHtml(stashdbTag.aliases.join(', ')) : '<em>none</em>'}</td>
                <td>
                  <label><input type="radio" name="tm-aliases" value="local" ${aliasesDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-aliases" value="stashdb" ${aliasesDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                  <label><input type="radio" name="tm-aliases" value="merge"> Merge</label>
                </td>
              </tr>
            </tbody>
          </table>

          <div class="tm-stashid-note">
            <strong>StashDB ID will be added:</strong> ${escapeHtml(stashdbTag.id)}
          </div>
        </div>
        <div class="tm-modal-footer">
          <button class="btn btn-secondary tm-cancel-btn">Cancel</button>
          <button class="btn btn-primary tm-apply-btn">Apply</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Event handlers
    modal.querySelector('.tm-close-btn').addEventListener('click', () => modal.remove());
    modal.querySelector('.tm-cancel-btn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    modal.querySelector('.tm-apply-btn').addEventListener('click', async () => {
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;
      const aliasesChoice = modal.querySelector('input[name="tm-aliases"]:checked').value;

      // Build update input
      const updateInput = {
        id: tag.id,
        stash_ids: [{
          endpoint: settings.stashdbEndpoint,
          stash_id: stashdbTag.id,
        }],
      };

      if (nameChoice === 'stashdb') {
        updateInput.name = stashdbTag.name;
      }

      if (descChoice === 'stashdb') {
        updateInput.description = stashdbTag.description || '';
      }

      if (aliasesChoice === 'stashdb') {
        updateInput.aliases = stashdbTag.aliases || [];
      } else if (aliasesChoice === 'merge') {
        const merged = new Set([...(tag.aliases || []), ...(stashdbTag.aliases || [])]);
        updateInput.aliases = Array.from(merged);
      }

      try {
        await updateTag(updateInput);
        modal.remove();

        // Update local state
        const idx = localTags.findIndex(t => t.id === tag.id);
        if (idx >= 0) {
          localTags[idx].stash_ids = updateInput.stash_ids;
          if (updateInput.name) localTags[idx].name = updateInput.name;
          if (updateInput.description !== undefined) localTags[idx].description = updateInput.description;
          if (updateInput.aliases) localTags[idx].aliases = updateInput.aliases;
        }
        delete matchResults[tag.id];

        showStatus(`Matched "${tag.name}" to "${stashdbTag.name}"`, 'success');
        renderPage(container);
      } catch (e) {
        showStatus(`Error: ${e.message}`, 'error');
      }
    });
  }

  /**
   * Update a tag via GraphQL
   */
  async function updateTag(input) {
    const query = `
      mutation TagUpdate($input: TagUpdateInput!) {
        tagUpdate(input: $input) {
          id
          name
          stash_ids {
            endpoint
            stash_id
          }
        }
      }
    `;

    const data = await graphqlRequest(query, { input });
    return data?.tagUpdate;
  }

  /**
   * Show modal with all matches for manual selection
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    const matches = matchResults[tagId];
    if (!tag) return;

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal tm-modal-wide">
        <div class="tm-modal-header">
          <h3>Matches for: ${escapeHtml(tag.name)}</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <div class="tm-search-row">
            <input type="text" id="tm-manual-search" class="form-control" placeholder="Search StashDB..." value="${escapeHtml(tag.name)}">
            <button class="btn btn-primary" id="tm-manual-search-btn">Search</button>
          </div>
          <div class="tm-matches-list" id="tm-matches-list">
            ${matches?.length
              ? matches.map((m, i) => `
                <div class="tm-match-item" data-index="${i}">
                  <div class="tm-match-info">
                    <span class="tm-match-name">${escapeHtml(m.tag.name)}</span>
                    <span class="tm-match-type-badge tm-match-${m.match_type}">${m.match_type} (${m.score}%)</span>
                    ${m.tag.category ? `<span class="tm-match-category">${escapeHtml(m.tag.category.name)}</span>` : ''}
                  </div>
                  <div class="tm-match-desc">${escapeHtml(m.tag.description || '')}</div>
                  <div class="tm-match-aliases">Aliases: ${m.tag.aliases?.join(', ') || 'none'}</div>
                  <button class="btn btn-success btn-sm tm-select-match">Select</button>
                </div>
              `).join('')
              : '<div class="tm-no-matches">No matches found. Try searching manually above.</div>'
            }
          </div>
        </div>
        <div class="tm-modal-footer">
          <button class="btn btn-secondary tm-cancel-btn">Close</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Event handlers
    modal.querySelector('.tm-close-btn').addEventListener('click', () => modal.remove());
    modal.querySelector('.tm-cancel-btn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    // Manual search
    const searchInput = modal.querySelector('#tm-manual-search');
    const searchBtn = modal.querySelector('#tm-manual-search-btn');

    const doSearch = async () => {
      const term = searchInput.value.trim();
      if (!term) return;

      searchBtn.disabled = true;
      searchBtn.textContent = 'Searching...';

      try {
        const result = await callBackend('search', {
          tag_name: term,
          stashdb_tags: stashdbTags,
        });
        matchResults[tagId] = result.matches || [];

        // Re-render matches list
        const listEl = modal.querySelector('#tm-matches-list');
        const newMatches = matchResults[tagId];
        listEl.innerHTML = newMatches?.length
          ? newMatches.map((m, i) => `
            <div class="tm-match-item" data-index="${i}">
              <div class="tm-match-info">
                <span class="tm-match-name">${escapeHtml(m.tag.name)}</span>
                <span class="tm-match-type-badge tm-match-${m.match_type}">${m.match_type} (${m.score}%)</span>
                ${m.tag.category ? `<span class="tm-match-category">${escapeHtml(m.tag.category.name)}</span>` : ''}
              </div>
              <div class="tm-match-desc">${escapeHtml(m.tag.description || '')}</div>
              <div class="tm-match-aliases">Aliases: ${m.tag.aliases?.join(', ') || 'none'}</div>
              <button class="btn btn-success btn-sm tm-select-match">Select</button>
            </div>
          `).join('')
          : '<div class="tm-no-matches">No matches found.</div>';

        // Re-attach select handlers
        attachSelectHandlers();
      } catch (e) {
        showStatus(`Search error: ${e.message}`, 'error');
      } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = 'Search';
      }
    };

    searchBtn.addEventListener('click', doSearch);
    searchInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') doSearch();
    });

    // Select match handlers
    function attachSelectHandlers() {
      modal.querySelectorAll('.tm-select-match').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const idx = parseInt(e.target.closest('.tm-match-item').dataset.index);
          modal.remove();
          showDiffDialog(tagId, container, idx);
        });
      });
    }
    attachSelectHandlers();
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
