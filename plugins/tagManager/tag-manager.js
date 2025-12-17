(function () {
  "use strict";

  const PLUGIN_ID = "tagManager";
  const ROUTE_PATH = "/plugins/tag-manager";

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
  let stashBoxes = []; // Configured stash-box endpoints from Stash
  let selectedStashBox = null; // Currently selected stash-box
  let stashdbTags = null; // Cached tags for selected endpoint
  let cacheStatus = null; // Cache status for selected endpoint
  let localTags = []; // Local Stash tags
  let currentPage = 1;
  let isLoading = false;
  let isCacheLoading = false;
  let matchResults = {}; // Cache of tag_id -> matches
  let currentFilter = 'unmatched'; // 'unmatched', 'matched', or 'all'

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
   * Get plugin settings and stash-box endpoints from Stash configuration
   */
  async function loadSettings() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
            general {
              stashBoxes {
                endpoint
                api_key
                name
              }
            }
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

      // Load configured stash-boxes
      stashBoxes = data?.configuration?.general?.stashBoxes || [];
      console.debug("[tagManager] Found stash-boxes:", stashBoxes.length);

      // Select first stash-box by default, or use plugin setting as fallback
      if (stashBoxes.length > 0) {
        selectedStashBox = stashBoxes[0];
        console.debug("[tagManager] Selected default stash-box:", selectedStashBox.name);
      } else if (settings.stashdbEndpoint && settings.stashdbApiKey) {
        // Fallback to plugin settings if no stash-boxes configured
        selectedStashBox = {
          endpoint: settings.stashdbEndpoint,
          api_key: settings.stashdbApiKey,
          name: "Plugin Settings"
        };
        stashBoxes = [selectedStashBox];
        console.debug("[tagManager] Using plugin settings as fallback stash-box");
      }

      console.debug("[tagManager] Settings loaded:", {
        ...settings,
        stashdbApiKey: settings.stashdbApiKey ? "[REDACTED]" : ""
      });
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

    // Use selected stash-box or fall back to plugin settings
    const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
    const apiKey = selectedStashBox?.api_key || settings.stashdbApiKey;

    console.debug(`[tagManager] callBackend mode=${mode} endpoint=${endpoint}`);

    const fullArgs = {
      mode,
      stashdb_url: endpoint,
      stashdb_api_key: apiKey,
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
   * Load cache status for current endpoint
   */
  async function loadCacheStatus() {
    if (!selectedStashBox) return;

    try {
      console.debug("[tagManager] Loading cache status for", selectedStashBox.endpoint);
      cacheStatus = await callBackend('get_cache_status');
      console.debug("[tagManager] Cache status:", cacheStatus);
    } catch (e) {
      console.error("[tagManager] Failed to load cache status:", e);
      cacheStatus = null;
    }
  }

  /**
   * Refresh tag cache for current endpoint
   */
  async function refreshCache(container) {
    if (!selectedStashBox) {
      showStatus('No stash-box selected', 'error');
      return;
    }

    isCacheLoading = true;
    renderPage(container);
    showStatus(`Building cache for ${selectedStashBox.name}... This may take 30+ seconds.`, 'info');

    try {
      console.debug("[tagManager] Refreshing cache for", selectedStashBox.endpoint);
      const result = await callBackend('fetch_all', { force_refresh: true });

      stashdbTags = result.tags || [];
      cacheStatus = {
        exists: true,
        count: result.count,
        age_hours: 0,
        expired: false
      };

      const msg = result.from_cache
        ? `Loaded ${result.count} tags from cache (${result.cache_age_hours}h old)`
        : `Fetched ${result.count} tags in ${result.fetch_time_seconds}s`;

      console.debug("[tagManager] Cache refresh complete:", msg);
      showStatus(msg, 'success');
    } catch (e) {
      console.error("[tagManager] Cache refresh failed:", e);
      showStatus(`Cache refresh failed: ${e.message}`, 'error');
    } finally {
      isCacheLoading = false;
      renderPage(container);
    }
  }

  /**
   * Load tags from cache (or fetch if no cache)
   */
  async function loadTagsFromCache(container) {
    if (!selectedStashBox) return;

    try {
      console.debug("[tagManager] Loading tags for", selectedStashBox.endpoint);
      const result = await callBackend('fetch_all', { force_refresh: false });

      stashdbTags = result.tags || [];
      cacheStatus = {
        exists: true,
        count: result.count,
        age_hours: result.cache_age_hours || 0,
        expired: false,
        from_cache: result.from_cache
      };

      if (result.from_cache) {
        console.debug(`[tagManager] Loaded ${result.count} tags from cache (${result.cache_age_hours}h old)`);
      } else {
        console.debug(`[tagManager] Fetched ${result.count} tags fresh (${result.fetch_time_seconds}s)`);
      }
    } catch (e) {
      console.error("[tagManager] Failed to load tags:", e);
      stashdbTags = null;
      cacheStatus = { exists: false, error: e.message };
    }
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
   * Get filtered tags based on current filter setting
   */
  function getFilteredTags() {
    const unmatchedTags = localTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);
    const matchedTags = localTags.filter(t => t.stash_ids && t.stash_ids.length > 0);

    switch (currentFilter) {
      case 'matched':
        return { filtered: matchedTags, unmatched: unmatchedTags, matched: matchedTags };
      case 'all':
        return { filtered: localTags, unmatched: unmatchedTags, matched: matchedTags };
      default: // 'unmatched'
        return { filtered: unmatchedTags, unmatched: unmatchedTags, matched: matchedTags };
    }
  }

  /**
   * Render cache status badge
   */
  function renderCacheStatus() {
    if (isCacheLoading) {
      return '<span class="tm-cache-status tm-cache-loading">Building cache...</span>';
    }
    if (!cacheStatus) {
      return '<span class="tm-cache-status tm-cache-unknown">Cache unknown</span>';
    }
    if (!cacheStatus.exists) {
      return '<span class="tm-cache-status tm-cache-none">No cache</span>';
    }
    if (cacheStatus.expired) {
      return `<span class="tm-cache-status tm-cache-expired">${cacheStatus.count} tags (expired)</span>`;
    }
    return `<span class="tm-cache-status tm-cache-valid">${cacheStatus.count} tags (${cacheStatus.age_hours}h old)</span>`;
  }

  /**
   * Render the main page content
   */
  function renderPage(container) {
    const { filtered, unmatched, matched } = getFilteredTags();

    const totalPages = Math.ceil(filtered.length / settings.pageSize);
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = filtered.slice(startIdx, startIdx + settings.pageSize);

    const emptyMessage = currentFilter === 'matched'
      ? 'No matched tags found'
      : currentFilter === 'all'
        ? 'No tags found'
        : 'No unmatched tags found';

    // Build stash-box dropdown options
    const stashBoxOptions = stashBoxes.map(sb => {
      const selected = selectedStashBox?.endpoint === sb.endpoint ? 'selected' : '';
      return `<option value="${escapeHtml(sb.endpoint)}" ${selected}>${escapeHtml(sb.name)}</option>`;
    }).join('');

    const hasStashBox = stashBoxes.length > 0;

    container.innerHTML = `
      <div class="tag-manager">
        <div class="tag-manager-header">
          <h2>Tag Manager</h2>
          <div class="tag-manager-stats">
            <span class="stat stat-unmatched">${unmatched.length} unmatched</span>
            <span class="stat stat-matched">${matched.length} matched</span>
          </div>
        </div>

        ${!hasStashBox ? `
          <div class="tag-manager-error">
            <h3>No Stash-Box Configured</h3>
            <p>Please configure a stash-box endpoint in Settings → Metadata Providers → Stash-Box Endpoints</p>
          </div>
        ` : `
          <div class="tag-manager-endpoint">
            <label for="tm-stashbox">Stash-Box:</label>
            <select id="tm-stashbox" class="form-control">
              ${stashBoxOptions}
            </select>
            <div class="tm-cache-info">
              ${renderCacheStatus()}
              <button class="btn btn-secondary btn-sm" id="tm-refresh-cache" ${isCacheLoading ? 'disabled' : ''}>
                ${isCacheLoading ? 'Building...' : 'Refresh Cache'}
              </button>
            </div>
          </div>

          <div class="tag-manager-filters">
            <select id="tm-filter" class="form-control">
              <option value="unmatched" ${currentFilter === 'unmatched' ? 'selected' : ''}>Show Unmatched</option>
              <option value="matched" ${currentFilter === 'matched' ? 'selected' : ''}>Show Matched</option>
              <option value="all" ${currentFilter === 'all' ? 'selected' : ''}>Show All</option>
            </select>
            <button class="btn btn-primary" id="tm-search-all-btn" ${isLoading || isCacheLoading ? 'disabled' : ''}>
              ${isLoading ? 'Searching...' : 'Find Matches for Page'}
            </button>
          </div>

          <div class="tag-manager-list" id="tm-tag-list">
            ${pageTags.length === 0
              ? `<div class="tm-empty">${emptyMessage}</div>`
              : pageTags.map(tag => renderTagRow(tag)).join('')
            }
          </div>

          <div class="tag-manager-pagination">
            <button class="btn btn-secondary" id="tm-prev" ${currentPage <= 1 ? 'disabled' : ''}>Previous</button>
            <span>Page ${currentPage} of ${totalPages || 1}</span>
            <button class="btn btn-secondary" id="tm-next" ${currentPage >= totalPages ? 'disabled' : ''}>Next</button>
          </div>
        `}

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
    // Stash-box dropdown
    container.querySelector('#tm-stashbox')?.addEventListener('change', async (e) => {
      const endpoint = e.target.value;
      const newStashBox = stashBoxes.find(sb => sb.endpoint === endpoint);
      if (newStashBox && newStashBox.endpoint !== selectedStashBox?.endpoint) {
        console.debug("[tagManager] Switching to stash-box:", newStashBox.name);
        selectedStashBox = newStashBox;
        // Clear cached data for previous endpoint
        stashdbTags = null;
        matchResults = {};
        cacheStatus = null;
        // Load cache for new endpoint
        await loadCacheStatus();
        renderPage(container);
      }
    });

    // Cache refresh button
    container.querySelector('#tm-refresh-cache')?.addEventListener('click', () => {
      refreshCache(container);
    });

    // Filter dropdown
    container.querySelector('#tm-filter')?.addEventListener('change', (e) => {
      currentFilter = e.target.value;
      currentPage = 1; // Reset to first page on filter change
      renderPage(container);
    });

    // Pagination
    container.querySelector('#tm-prev')?.addEventListener('click', () => {
      if (currentPage > 1) {
        currentPage--;
        renderPage(container);
      }
    });

    container.querySelector('#tm-next')?.addEventListener('click', () => {
      const { filtered } = getFilteredTags();
      const totalPages = Math.ceil(filtered.length / settings.pageSize);
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
    const { filtered } = getFilteredTags();
    const startIdx = (currentPage - 1) * settings.pageSize;
    const pageTags = filtered.slice(startIdx, startIdx + settings.pageSize);

    // Only search tags that don't already have StashDB IDs
    const tagsToSearch = pageTags.filter(t => !t.stash_ids || t.stash_ids.length === 0);

    if (tagsToSearch.length === 0) {
      showStatus('All tags on this page are already matched', 'info');
      return;
    }

    isLoading = true;
    renderPage(container);

    for (const tag of tagsToSearch) {
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
          <div class="tm-modal-error" id="tm-diff-error" style="display: none;"></div>
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

      // Use the selected stash-box endpoint
      const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
      console.debug(`[tagManager] Saving stash_id with endpoint: ${endpoint}`);

      // Build update input
      const updateInput = {
        id: tag.id,
        stash_ids: [{
          endpoint: endpoint,
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
        const errorEl = modal.querySelector('#tm-diff-error');
        if (errorEl) {
          // Parse error message for friendlier display
          let errorMsg = e.message;
          const aliasConflictMatch = errorMsg.match(/tag with name '([^']+)' already exists/i);
          if (aliasConflictMatch) {
            errorMsg = `Cannot save: "${aliasConflictMatch[1]}" conflicts with an existing tag name. Remove it from aliases to continue.`;
          }
          errorEl.textContent = errorMsg;
          errorEl.style.display = 'block';
        }
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
                  <div class="tm-match-aliases">Aliases: ${escapeHtml(m.tag.aliases?.join(', ') || 'none')}</div>
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
              <div class="tm-match-aliases">Aliases: ${escapeHtml(m.tag.aliases?.join(', ') || 'none')}</div>
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

        console.debug("[tagManager] Initializing...");
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading configuration...</div></div>';

        await loadSettings();

        // Check if any stash-box is configured
        if (stashBoxes.length === 0) {
          console.warn("[tagManager] No stash-boxes configured");
          containerRef.current.innerHTML = `
            <div class="tag-manager">
              <div class="tag-manager-error">
                <h3>No Stash-Box Configured</h3>
                <p>Please configure a stash-box endpoint in Settings → Metadata Providers → Stash-Box Endpoints</p>
                <p>Or configure a StashDB endpoint in Settings → Plugins → Tag Manager</p>
              </div>
            </div>
          `;
          return;
        }

        // Fetch local tags
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading local tags...</div></div>';
        try {
          localTags = await fetchLocalTags();
          console.debug(`[tagManager] Loaded ${localTags.length} local tags`);
        } catch (e) {
          console.error("[tagManager] Failed to load local tags:", e);
          containerRef.current.innerHTML = `<div class="tag-manager"><div class="tag-manager-error">Error loading tags: ${escapeHtml(e.message)}</div></div>`;
          return;
        }

        // Load cache status for selected endpoint
        await loadCacheStatus();

        // If fuzzy search enabled, load tags from cache (or fetch if no cache)
        if (settings.enableFuzzySearch) {
          containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading tag cache...</div></div>';
          await loadTagsFromCache(containerRef.current);
        }

        setInitialized(true);
        console.debug("[tagManager] Initialization complete");
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

  /**
   * Create the Tag Manager nav button SVG icon
   * Uses a settings/gear-like icon to represent tag management
   */
  function createTagManagerIcon() {
    // Using a tag with gear icon to represent "tag management"
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 512 512');
    svg.setAttribute('class', 'svg-inline--fa fa-icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.style.width = '1em';
    svg.style.height = '1em';

    // Tag with settings/sync icon - represents tag management
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    // FontAwesome "tags" icon path (fa-tags)
    path.setAttribute('d', 'M0 80V229.5c0 17 6.7 33.3 18.7 45.3l176 176c25 25 65.5 25 90.5 0L418.7 317.3c25-25 25-65.5 0-90.5l-176-176c-12-12-28.3-18.7-45.3-18.7H48C21.5 32 0 53.5 0 80zm112 32a32 32 0 1 1 0 64 32 32 0 1 1 0-64z');
    svg.appendChild(path);

    return svg;
  }

  /**
   * Inject Tag Manager button into Tags list page toolbar
   */
  function injectTagManagerButton() {
    // Only run on Tags list page
    if (!window.location.pathname.endsWith('/tags')) {
      return;
    }

    // Check if we already injected the button
    if (document.querySelector('#tm-nav-button')) {
      return;
    }

    // Find the toolbar
    const toolbar = document.querySelector('.filtered-list-toolbar');
    if (!toolbar) {
      console.debug('[tagManager] Toolbar not found yet');
      return;
    }

    // Strategy 1: Find zoom-slider-container (always present after view mode buttons)
    let insertionPoint = toolbar.querySelector('.zoom-slider-container');

    // Strategy 2: Find display-mode-select button (dropdown version in some layouts)
    if (!insertionPoint) {
      insertionPoint = toolbar.querySelector('.display-mode-select');
    }

    // Strategy 3: Find the last btn-group with icon buttons
    if (!insertionPoint) {
      const btnGroups = toolbar.querySelectorAll('.btn-group');
      for (const group of btnGroups) {
        const hasIcons = group.querySelector('.fa-icon') || group.querySelector('svg');
        if (hasIcons) {
          insertionPoint = group;
        }
      }
    }

    if (!insertionPoint) {
      console.debug('[tagManager] No suitable insertion point found in toolbar');
      return;
    }

    // Create our button
    const btn = document.createElement('button');
    btn.id = 'tm-nav-button';
    btn.className = 'btn btn-secondary';
    btn.title = 'Tag Manager';
    btn.style.marginLeft = '0.5rem';
    btn.appendChild(createTagManagerIcon());

    // Add click handler to navigate to Tag Manager
    btn.addEventListener('click', () => {
      window.location.href = ROUTE_PATH;
    });

    // Insert after the insertion point
    insertionPoint.parentNode.insertBefore(btn, insertionPoint.nextSibling);
    console.debug('[tagManager] Nav button injected on Tags page');
  }

  /**
   * Watch for navigation to Tags page and inject button
   */
  function setupNavButtonInjection() {
    // Try to inject immediately
    injectTagManagerButton();

    // Watch for URL changes (SPA navigation)
    let lastUrl = window.location.href;
    const observer = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        // Wait a bit for DOM to update after navigation
        setTimeout(injectTagManagerButton, 100);
        setTimeout(injectTagManagerButton, 500);
        setTimeout(injectTagManagerButton, 1000);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Also try on initial load with delays (for refresh on Tags page)
    setTimeout(injectTagManagerButton, 100);
    setTimeout(injectTagManagerButton, 500);
    setTimeout(injectTagManagerButton, 1000);
    setTimeout(injectTagManagerButton, 2000);
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();
  console.log('[tagManager] Plugin loaded');
})();
