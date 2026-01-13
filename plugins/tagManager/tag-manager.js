(function () {
  "use strict";

  const PLUGIN_ID = "tagManager";
  const ROUTE_PATH = "/plugins/tag-manager";
  const HIERARCHY_ROUTE_PATH = "/plugins/tag-hierarchy";

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
   * Set page title with retry to overcome Stash's title management
   */
  function setPageTitle(title) {
    const doSet = () => { document.title = title; };
    // Set immediately
    doSet();
    // Retry after short delays to override any framework title changes
    setTimeout(doSet, 50);
    setTimeout(doSet, 200);
    setTimeout(doSet, 500);
  }

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
   * Fetch all tags with hierarchy information (parents, children)
   */
  async function fetchAllTagsWithHierarchy() {
    const query = `
      query AllTagsWithHierarchy {
        allTags {
          id
          name
          image_path
          scene_count
          parent_count
          child_count
          parents {
            id
          }
          children {
            id
          }
        }
      }
    `;

    const result = await graphqlRequest(query);
    return result.allTags || [];
  }

  /**
   * Build a tree structure from flat tag list
   * Tags with multiple parents appear under each parent
   * @param {Array} tags - Flat array of tags with parent/children info
   * @returns {Array} - Array of root nodes (tags with no parents)
   */
  function buildTagTree(tags) {
    // Create a map for quick lookup
    const tagMap = new Map();
    tags.forEach(tag => {
      tagMap.set(tag.id, {
        ...tag,
        childNodes: []
      });
    });

    // Find root tags (no parents) and build children arrays
    const roots = [];

    tags.forEach(tag => {
      const node = tagMap.get(tag.id);

      if (tag.parents.length === 0) {
        // Root tag
        roots.push(node);
      } else {
        // Add this tag as a child to each of its parents
        tag.parents.forEach(parent => {
          const parentNode = tagMap.get(parent.id);
          if (parentNode) {
            parentNode.childNodes.push(node);
          }
        });
      }
    });

    // Sort roots and all children alphabetically by name
    const sortByName = (a, b) => a.name.localeCompare(b.name);
    roots.sort(sortByName);

    function sortChildren(node) {
      if (node.childNodes.length > 0) {
        node.childNodes.sort(sortByName);
        node.childNodes.forEach(sortChildren);
      }
    }
    roots.forEach(sortChildren);

    return roots;
  }

  /**
   * Get stats from tag tree
   */
  function getTreeStats(tags) {
    const totalTags = tags.length;
    const rootTags = tags.filter(t => t.parents.length === 0).length;
    const tagsWithChildren = tags.filter(t => t.child_count > 0).length;
    const tagsWithParents = tags.filter(t => t.parent_count > 0).length;

    return {
      totalTags,
      rootTags,
      tagsWithChildren,
      tagsWithParents
    };
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
   * Highlight character-level differences between two strings
   * Returns HTML with differing characters wrapped in spans
   */
  function highlightDifferences(str1, str2) {
    if (!str1 && !str2) return { html1: '', html2: '', identical: true };
    if (!str1) return { html1: '', html2: escapeHtml(str2), identical: false };
    if (!str2) return { html1: escapeHtml(str1), html2: '', identical: false };

    if (str1 === str2) {
      return { html1: escapeHtml(str1), html2: escapeHtml(str2), identical: true };
    }

    // Character-by-character comparison
    let html1 = '';
    let html2 = '';
    const len = Math.max(str1.length, str2.length);

    for (let i = 0; i < len; i++) {
      const c1 = str1[i] || '';
      const c2 = str2[i] || '';

      if (c1 !== c2) {
        html1 += c1 ? `<span class="tm-diff-char">${escapeHtml(c1)}</span>` : '';
        html2 += c2 ? `<span class="tm-diff-char">${escapeHtml(c2)}</span>` : '';
      } else {
        html1 += escapeHtml(c1);
        html2 += escapeHtml(c2);
      }
    }

    return { html1, html2, identical: false };
  }

  /**
   * Sanitize aliases before saving - removes the final name from alias set
   * to prevent self-referential aliases (tag can't have its own name as alias).
   *
   * @param {Set} aliases - The editable aliases set
   * @param {string} finalName - The name the tag will have after save
   * @param {string} currentLocalName - The tag's current local name
   * @returns {string[]} - Cleaned array of aliases
   */
  function sanitizeAliasesForSave(aliases, finalName, currentLocalName) {
    const cleaned = new Set(aliases);

    // Remove final name (can't alias yourself)
    cleaned.forEach(alias => {
      if (alias.toLowerCase() === finalName.toLowerCase()) {
        cleaned.delete(alias);
      }
    });

    // If keeping local name, also ensure it's not in aliases
    if (finalName.toLowerCase() === currentLocalName.toLowerCase()) {
      cleaned.forEach(alias => {
        if (alias.toLowerCase() === currentLocalName.toLowerCase()) {
          cleaned.delete(alias);
        }
      });
    }

    return Array.from(cleaned);
  }

  /**
   * Find a local tag that conflicts with a given name (as name or alias).
   * Used for pre-validation before saving.
   *
   * @param {string} name - The name to check for conflicts
   * @param {string} excludeTagId - Tag ID to exclude from search (the tag being edited)
   * @returns {object|null} - The conflicting tag or null
   */
  function findConflictingTag(name, excludeTagId) {
    const lowerName = name.toLowerCase();
    return localTags.find(t =>
      t.id !== excludeTagId && (
        t.name.toLowerCase() === lowerName ||
        t.aliases?.some(a => a.toLowerCase() === lowerName)
      )
    ) || null;
  }

  /**
   * Validate tag update before attempting to save.
   * Checks for name and alias conflicts with other local tags.
   *
   * @param {string} finalName - The name the tag will have
   * @param {string[]} aliases - The aliases to save
   * @param {string} currentTagId - The ID of the tag being edited
   * @returns {object[]} - Array of error objects, empty if valid
   */
  function validateBeforeSave(finalName, aliases, currentTagId) {
    const errors = [];

    // Check if final name conflicts with another tag
    const nameConflict = findConflictingTag(finalName, currentTagId);
    if (nameConflict) {
      errors.push({
        type: 'name_conflict',
        field: 'name',
        value: finalName,
        conflictsWith: nameConflict
      });
    }

    // Check each alias for conflicts
    for (const alias of aliases) {
      const aliasConflict = findConflictingTag(alias, currentTagId);
      if (aliasConflict) {
        errors.push({
          type: 'alias_conflict',
          field: 'alias',
          value: alias,
          conflictsWith: aliasConflict
        });
      }
    }

    return errors;
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
        <button class="btn btn-secondary btn-sm tm-manual-search" data-tag-id="${tag.id}">Search</button>
      `;
    } else {
      matchContent = `
        <button class="btn btn-primary btn-sm tm-search" data-tag-id="${tag.id}">Find Match</button>
      `;
    }

    return `
      <div class="tm-tag-row" data-tag-id="${tag.id}">
        <div class="tm-tag-info">
          <a href="/tags/${tag.id}" class="tm-tag-name">${escapeHtml(tag.name)}</a>
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

    // Manual search buttons (shown when no matches found)
    container.querySelectorAll('.tm-manual-search').forEach(btn => {
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
    // For name: if local differs from StashDB, default to "keep + add alias"
    const namesMatch = tag.name.toLowerCase() === stashdbTag.name.toLowerCase();
    const nameDefault = !tag.name ? 'stashdb' : (namesMatch ? 'local' : 'local_add_alias');
    const descDefault = tag.description ? 'local' : 'stashdb';

    // Alias editing state - start with merged aliases
    let editableAliases = new Set([...(tag.aliases || []), ...(stashdbTag.aliases || [])]);

    // Helper function to render alias checkboxes for a column
    function renderAliasCheckboxes(primaryAliases, otherAliases, source) {
      const newAliases = otherAliases.filter(a => !primaryAliases.some(pa => pa.toLowerCase() === a.toLowerCase()));

      if (primaryAliases.length === 0 && newAliases.length === 0) {
        return '<em class="tm-alias-empty">none</em>';
      }

      let html = '';

      // Primary aliases (from this source)
      primaryAliases.forEach(alias => {
        const safeId = alias.replace(/[^a-zA-Z0-9]/g, '_');
        html += `
          <div class="tm-alias-checkbox-item">
            <input type="checkbox" id="alias-${source}-${safeId}" data-alias="${escapeHtml(alias)}" checked>
            <label for="alias-${source}-${safeId}">${escapeHtml(alias)}</label>
          </div>
        `;
      });

      // Aliases from other source that aren't in this one (shown in blue)
      newAliases.forEach(alias => {
        const safeId = alias.replace(/[^a-zA-Z0-9]/g, '_');
        const fromLabel = source === 'local' ? 'from StashDB' : 'from local';
        html += `
          <div class="tm-alias-checkbox-item new-from-other">
            <input type="checkbox" id="alias-new-${source}-${safeId}" data-alias="${escapeHtml(alias)}" checked>
            <label for="alias-new-${source}-${safeId}">${escapeHtml(alias)} (${fromLabel})</label>
          </div>
        `;
      });

      return html || '<em class="tm-alias-empty">none</em>';
    }

    // Function to update editableAliases from checkbox state
    function updateAliasesFromCheckboxes() {
      editableAliases.clear();
      // Collect from both columns, but deduplicate
      const seen = new Set();
      modal.querySelectorAll('.tm-alias-checkbox-item input[type="checkbox"]:checked').forEach(cb => {
        const alias = cb.dataset.alias;
        const lowerAlias = alias.toLowerCase();
        if (!seen.has(lowerAlias)) {
          seen.add(lowerAlias);
          editableAliases.add(alias);
        }
      });
      renderAliasPills();
    }

    // Function to render alias pills (read-only display of final aliases)
    function renderAliasPills() {
      const pillsContainer = modal.querySelector('#tm-alias-pills');
      if (!pillsContainer) return;

      if (editableAliases.size === 0) {
        pillsContainer.innerHTML = '<span class="tm-alias-empty">No aliases</span>';
        return;
      }

      pillsContainer.innerHTML = Array.from(editableAliases).map(alias => `
        <span class="tm-alias-pill">${escapeHtml(alias)}</span>
      `).join('');
    }

    // Function to update visual selection indicators
    function updateSelectionVisuals() {
      // Name selection - local_add_alias means keeping local name (so local is "selected")
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked')?.value;
      const localNameSelected = nameChoice === 'local' || nameChoice === 'local_add_alias';
      modal.querySelector('#tm-name-local')?.classList.toggle('selected', localNameSelected);
      modal.querySelector('#tm-name-stashdb')?.classList.toggle('selected', nameChoice === 'stashdb');

      // Description selection
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked')?.value;
      modal.querySelector('#tm-desc-local')?.classList.toggle('selected', descChoice === 'local');
      modal.querySelector('#tm-desc-stashdb')?.classList.toggle('selected', descChoice === 'stashdb');
    }

    // Calculate differences for highlighting
    const nameDiff = highlightDifferences(tag.name, stashdbTag.name);
    const descDiff = highlightDifferences(tag.description || '', stashdbTag.description || '');

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
                <td><div class="tm-diff-value" id="tm-name-local">${nameDiff.html1 || '<em>empty</em>'}</div></td>
                <td><div class="tm-diff-value" id="tm-name-stashdb">${nameDiff.html2}${nameDiff.identical ? ' <span class="tm-diff-identical">(identical)</span>' : ''}</div></td>
                <td>
                  <label><input type="radio" name="tm-name" value="local_add_alias" ${nameDefault === 'local_add_alias' ? 'checked' : ''}> Keep + Add stash-box alias</label>
                  <label><input type="radio" name="tm-name" value="local" ${nameDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-name" value="stashdb" ${nameDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Description</td>
                <td><div class="tm-diff-value" id="tm-desc-local">${descDiff.html1 || '<em>empty</em>'}</div></td>
                <td><div class="tm-diff-value" id="tm-desc-stashdb">${descDiff.html2 || '<em>empty</em>'}</div></td>
                <td>
                  <label><input type="radio" name="tm-desc" value="local" ${descDefault === 'local' ? 'checked' : ''}> Keep</label>
                  <label><input type="radio" name="tm-desc" value="stashdb" ${descDefault === 'stashdb' ? 'checked' : ''}> StashDB</label>
                </td>
              </tr>
              <tr>
                <td>Aliases</td>
                <td colspan="3">
                  <div class="tm-alias-columns">
                    <div class="tm-alias-column">
                      <div class="tm-alias-column-header">Your Aliases</div>
                      <div class="tm-alias-checkbox-list" id="tm-local-aliases">
                        ${renderAliasCheckboxes(tag.aliases || [], stashdbTag.aliases || [], 'local')}
                      </div>
                    </div>
                    <div class="tm-alias-column">
                      <div class="tm-alias-column-header">StashDB Aliases</div>
                      <div class="tm-alias-checkbox-list" id="tm-stashdb-aliases">
                        ${renderAliasCheckboxes(stashdbTag.aliases || [], tag.aliases || [], 'stashdb')}
                      </div>
                    </div>
                  </div>
                  <div class="tm-final-aliases-section">
                    <div class="tm-final-aliases-header">Final aliases:</div>
                    <div class="tm-alias-pills" id="tm-alias-pills"></div>
                  </div>
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

    // Helper function to check/uncheck an alias checkbox by alias value
    function setAliasCheckbox(alias, checked) {
      const safeId = alias.replace(/[^a-zA-Z0-9]/g, '_');
      // Try to find the checkbox in either column (it may be primary or new-from-other)
      const selectors = [
        `#alias-local-${safeId}`,
        `#alias-stashdb-${safeId}`,
        `#alias-new-local-${safeId}`,
        `#alias-new-stashdb-${safeId}`
      ];
      for (const selector of selectors) {
        const cb = modal.querySelector(selector);
        if (cb) {
          cb.checked = checked;
          break;
        }
      }
    }

    // Update aliases when checkboxes change
    modal.querySelectorAll('.tm-alias-checkbox-item input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', updateAliasesFromCheckboxes);
    });

    // Initialize alias pills from checkboxes
    updateAliasesFromCheckboxes();

    // When name choice changes, update alias checkboxes as needed
    modal.querySelectorAll('input[name="tm-name"]').forEach(radio => {
      radio.addEventListener('change', (e) => {
        if (e.target.value === 'local_add_alias') {
          // Check the StashDB name in the alias checkboxes if not already checked
          setAliasCheckbox(stashdbTag.name, true);
          updateAliasesFromCheckboxes();
        } else if (e.target.value === 'stashdb') {
          // Check the local name in the alias checkboxes if not already checked (preserve old name when renaming)
          if (tag.name) {
            setAliasCheckbox(tag.name, true);
            updateAliasesFromCheckboxes();
          }
        }
      });
    });

    // If default is local_add_alias, ensure StashDB name checkbox is checked
    if (nameDefault === 'local_add_alias') {
      setAliasCheckbox(stashdbTag.name, true);
      updateAliasesFromCheckboxes();
    }

    // Initialize selection visuals
    updateSelectionVisuals();

    // Update visuals when radio buttons change
    modal.querySelectorAll('input[type="radio"]').forEach(radio => {
      radio.addEventListener('change', updateSelectionVisuals);
    });

    // Event handlers
    modal.querySelector('.tm-close-btn').addEventListener('click', () => modal.remove());
    modal.querySelector('.tm-cancel-btn').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    modal.querySelector('.tm-apply-btn').addEventListener('click', async () => {
      const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;
      const errorEl = modal.querySelector('#tm-diff-error');

      // Hide any previous error
      errorEl.style.display = 'none';
      errorEl.innerHTML = '';

      // Use the selected stash-box endpoint
      const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
      console.debug(`[tagManager] Saving stash_id with endpoint: ${endpoint}`);

      // Determine final name
      const finalName = nameChoice === 'stashdb' ? stashdbTag.name : tag.name;

      // Sanitize aliases - remove final name to prevent self-referential alias
      const sanitizedAliases = sanitizeAliasesForSave(editableAliases, finalName, tag.name);

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

      // Use sanitized aliases
      updateInput.aliases = sanitizedAliases;

      // Pre-validation: check for conflicts before hitting API
      const validationErrors = validateBeforeSave(finalName, sanitizedAliases, tag.id);
      if (validationErrors.length > 0) {
        const err = validationErrors[0]; // Show first error
        const conflictTag = err.conflictsWith;

        if (err.type === 'name_conflict') {
          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot rename to "${escapeHtml(err.value)}" - this name already exists.
            </div>
            <div class="tm-error-actions">
              <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                Edit "${escapeHtml(conflictTag.name)}"
              </a>
              <button type="button" class="btn btn-secondary btn-sm tm-error-keep-local">
                Keep local name instead
              </button>
            </div>
          `;
        } else {
          errorEl.innerHTML = `
            <div class="tm-error-message">
              Alias "${escapeHtml(err.value)}" conflicts with tag "${escapeHtml(conflictTag.name)}".
            </div>
            <div class="tm-error-actions">
              <button type="button" class="btn btn-secondary btn-sm tm-error-remove-alias" data-alias="${escapeHtml(err.value)}">
                Remove from aliases
              </button>
              <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                Edit "${escapeHtml(conflictTag.name)}"
              </a>
            </div>
          `;
        }

        errorEl.style.display = 'block';

        // Attach action handlers
        const keepLocalBtn = errorEl.querySelector('.tm-error-keep-local');
        if (keepLocalBtn) {
          keepLocalBtn.addEventListener('click', () => {
            modal.querySelector('input[name="tm-name"][value="local"]').checked = true;
            errorEl.style.display = 'none';
          });
        }

        const removeAliasBtn = errorEl.querySelector('.tm-error-remove-alias');
        if (removeAliasBtn) {
          removeAliasBtn.addEventListener('click', () => {
            const aliasToRemove = removeAliasBtn.dataset.alias;
            editableAliases.delete(aliasToRemove);
            renderAliasPills();
            errorEl.style.display = 'none';
          });
        }

        return; // Don't proceed with save
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
        console.error('[tagManager] Save error:', e.message);

        // Parse "tag with name 'X' already exists"
        const nameExistsMatch = e.message.match(/tag with name '([^']+)' already exists/i);
        if (nameExistsMatch) {
          const conflictName = nameExistsMatch[1];
          const conflictTag = findConflictingTag(conflictName, tag.id);

          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot save: "${escapeHtml(conflictName)}" conflicts with an existing tag.
            </div>
            <div class="tm-error-actions">
              ${conflictTag ? `
                <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                  Edit "${escapeHtml(conflictTag.name)}"
                </a>
              ` : ''}
              <button type="button" class="btn btn-secondary btn-sm tm-error-remove-alias" data-alias="${escapeHtml(conflictName)}">
                Remove from aliases
              </button>
            </div>
          `;
          errorEl.style.display = 'block';

          const removeBtn = errorEl.querySelector('.tm-error-remove-alias');
          if (removeBtn) {
            removeBtn.addEventListener('click', () => {
              editableAliases.delete(removeBtn.dataset.alias);
              renderAliasPills();
              errorEl.style.display = 'none';
            });
          }
          return;
        }

        // Parse "name 'X' is used as alias for 'Y'"
        const aliasUsedMatch = e.message.match(/name '([^']+)' is used as alias for '([^']+)'/i);
        if (aliasUsedMatch) {
          const [, conflictName, otherTagName] = aliasUsedMatch;
          const otherTag = localTags.find(t => t.name === otherTagName);

          errorEl.innerHTML = `
            <div class="tm-error-message">
              Cannot use "${escapeHtml(conflictName)}" - it's an alias on "${escapeHtml(otherTagName)}".
            </div>
            <div class="tm-error-actions">
              ${otherTag ? `
                <a href="/tags/${otherTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                  Edit "${escapeHtml(otherTagName)}"
                </a>
              ` : ''}
              <button type="button" class="btn btn-secondary btn-sm tm-error-keep-local">
                Keep local name instead
              </button>
            </div>
          `;
          errorEl.style.display = 'block';

          const keepLocalBtn = errorEl.querySelector('.tm-error-keep-local');
          if (keepLocalBtn) {
            keepLocalBtn.addEventListener('click', () => {
              modal.querySelector('input[name="tm-name"][value="local"]').checked = true;
              errorEl.style.display = 'none';
            });
          }
          return;
        }

        // Fallback for unknown errors
        errorEl.innerHTML = `<div class="tm-error-message">${escapeHtml(e.message)}</div>`;
        errorEl.style.display = 'block';
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
        setPageTitle("Tag Matcher | Stash");
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
   * Tag Hierarchy page state
   */
  let hierarchyTags = [];
  let hierarchyTree = [];
  let hierarchyStats = {};
  let showImages = true;
  let expandedNodes = new Set();

  /**
   * Render a single tree node
   */
  function renderTreeNode(node, isRoot = false) {
    const hasChildren = node.childNodes.length > 0;
    const isExpanded = expandedNodes.has(node.id);

    // Build scene/child count text
    const metaParts = [];
    if (node.scene_count > 0) {
      metaParts.push(`${node.scene_count} scene${node.scene_count !== 1 ? 's' : ''}`);
    }
    if (node.child_count > 0) {
      metaParts.push(`${node.child_count} sub-tag${node.child_count !== 1 ? 's' : ''}`);
    }
    const metaText = metaParts.length > 0 ? metaParts.join(', ') : '';

    // Image HTML
    const imageHtml = node.image_path
      ? `<div class="th-image ${showImages ? '' : 'th-hidden'}">
           <img src="${escapeHtml(node.image_path)}" alt="${escapeHtml(node.name)}" loading="lazy">
         </div>`
      : `<div class="th-image-placeholder ${showImages ? '' : 'th-hidden'}">
           <span>?</span>
         </div>`;

    // Children HTML (recursive)
    let childrenHtml = '';
    if (hasChildren) {
      const childNodes = node.childNodes.map(child => renderTreeNode(child, false)).join('');
      childrenHtml = `<div class="th-children ${isExpanded ? 'th-expanded' : ''}" data-parent-id="${node.id}">${childNodes}</div>`;
    }

    // Toggle icon
    const toggleIcon = hasChildren
      ? (isExpanded ? '&#9660;' : '&#9654;')  // Down arrow / Right arrow
      : '';

    return `
      <div class="th-node ${isRoot ? 'th-root' : ''}" data-tag-id="${node.id}">
        <div class="th-node-content">
          <span class="th-toggle ${hasChildren ? '' : 'th-leaf'}" data-tag-id="${node.id}">${toggleIcon}</span>
          ${imageHtml}
          <div class="th-info">
            <a href="/tags/${node.id}" class="th-name">${escapeHtml(node.name)}</a>
            ${metaText ? `<div class="th-meta">${metaText}</div>` : ''}
          </div>
        </div>
        ${childrenHtml}
      </div>
    `;
  }

  /**
   * Render the full hierarchy page
   */
  function renderHierarchyPage(container) {
    const treeHtml = hierarchyTree.map(root => renderTreeNode(root, true)).join('');

    container.innerHTML = `
      <div class="tag-hierarchy">
        <div class="tag-hierarchy-header">
          <h2>Tag Hierarchy</h2>
          <div class="tag-hierarchy-controls">
            <button id="th-expand-all">Expand All</button>
            <button id="th-collapse-all">Collapse All</button>
            <label>
              <input type="checkbox" id="th-show-images" ${showImages ? 'checked' : ''}>
              Show images
            </label>
          </div>
        </div>
        <div class="th-stats">
          <span class="stat"><strong>${hierarchyStats.totalTags}</strong> total tags</span>
          <span class="stat"><strong>${hierarchyStats.rootTags}</strong> root tags</span>
          <span class="stat"><strong>${hierarchyStats.tagsWithChildren}</strong> with sub-tags</span>
          <span class="stat"><strong>${hierarchyStats.tagsWithParents}</strong> with parents</span>
        </div>
        <div class="th-tree">
          ${treeHtml || '<div class="th-empty">No tags found</div>'}
        </div>
      </div>
    `;

    // Attach event handlers
    attachHierarchyEventHandlers(container);
  }

  /**
   * Attach event handlers for hierarchy page
   */
  function attachHierarchyEventHandlers(container) {
    // Toggle expand/collapse on node click
    container.querySelectorAll('.th-toggle').forEach(toggle => {
      toggle.addEventListener('click', (e) => {
        const tagId = e.target.dataset.tagId;
        if (!tagId) return;

        const childrenContainer = container.querySelector(`.th-children[data-parent-id="${tagId}"]`);
        if (!childrenContainer) return;

        if (expandedNodes.has(tagId)) {
          expandedNodes.delete(tagId);
          childrenContainer.classList.remove('th-expanded');
          e.target.innerHTML = '&#9654;';  // Right arrow
        } else {
          expandedNodes.add(tagId);
          childrenContainer.classList.add('th-expanded');
          e.target.innerHTML = '&#9660;';  // Down arrow
        }
      });
    });

    // Expand All button
    const expandAllBtn = container.querySelector('#th-expand-all');
    if (expandAllBtn) {
      expandAllBtn.addEventListener('click', () => {
        container.querySelectorAll('.th-children').forEach(el => {
          el.classList.add('th-expanded');
          const parentId = el.dataset.parentId;
          if (parentId) expandedNodes.add(parentId);
        });
        container.querySelectorAll('.th-toggle:not(.th-leaf)').forEach(el => {
          el.innerHTML = '&#9660;';
        });
      });
    }

    // Collapse All button
    const collapseAllBtn = container.querySelector('#th-collapse-all');
    if (collapseAllBtn) {
      collapseAllBtn.addEventListener('click', () => {
        container.querySelectorAll('.th-children').forEach(el => {
          el.classList.remove('th-expanded');
        });
        container.querySelectorAll('.th-toggle:not(.th-leaf)').forEach(el => {
          el.innerHTML = '&#9654;';
        });
        expandedNodes.clear();
      });
    }

    // Show images toggle
    const showImagesCheckbox = container.querySelector('#th-show-images');
    if (showImagesCheckbox) {
      showImagesCheckbox.addEventListener('change', (e) => {
        showImages = e.target.checked;
        container.querySelectorAll('.th-image, .th-image-placeholder').forEach(el => {
          el.classList.toggle('th-hidden', !showImages);
        });
      });
    }
  }

  /**
   * Tag Hierarchy page component
   */
  function TagHierarchyPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);

    React.useEffect(() => {
      async function init() {
        if (!containerRef.current) return;

        setPageTitle("Tag Hierarchy | Stash");
        containerRef.current.innerHTML = '<div class="tag-hierarchy"><div class="th-loading">Loading tags...</div></div>';

        try {
          // Fetch all tags with hierarchy info
          hierarchyTags = await fetchAllTagsWithHierarchy();
          console.debug(`[tagManager] Loaded ${hierarchyTags.length} tags for hierarchy`);

          // Build tree structure
          hierarchyTree = buildTagTree(hierarchyTags);
          hierarchyStats = getTreeStats(hierarchyTags);
          console.debug(`[tagManager] Built tree with ${hierarchyTree.length} root nodes`);

          // Reset expand state
          expandedNodes.clear();

          // Render the page
          renderHierarchyPage(containerRef.current);
        } catch (e) {
          console.error("[tagManager] Failed to load tag hierarchy:", e);
          containerRef.current.innerHTML = `<div class="tag-hierarchy"><div class="th-loading">Error loading tags: ${escapeHtml(e.message)}</div></div>`;
        }
      }

      init();
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'tag-hierarchy-container'
    });
  }

  /**
   * Register the route
   */
  function registerRoute() {
    PluginApi.register.route(ROUTE_PATH, TagManagerPage);
    PluginApi.register.route(HIERARCHY_ROUTE_PATH, TagHierarchyPage);
    console.log('[tagManager] Routes registered:', ROUTE_PATH, HIERARCHY_ROUTE_PATH);
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
   * Create the Tag Hierarchy nav button SVG icon
   * Uses a sitemap icon to represent hierarchy/tree view
   */
  function createHierarchyIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 576 512');
    svg.setAttribute('class', 'svg-inline--fa fa-icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.style.width = '1em';
    svg.style.height = '1em';

    // FontAwesome "sitemap" icon path
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', 'M208 80c0-26.5 21.5-48 48-48h64c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-8v40H464c30.9 0 56 25.1 56 56v32h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-64c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-32c0-4.4-3.6-8-8-8H312v40h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48h-64c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-40H112c-4.4 0-8 3.6-8 8v32h8c26.5 0 48 21.5 48 48v64c0 26.5-21.5 48-48 48H48c-26.5 0-48-21.5-48-48v-64c0-26.5 21.5-48 48-48h8v-32c0-30.9 25.1-56 56-56h152v-40h-8c-26.5 0-48-21.5-48-48V80z');
    svg.appendChild(path);

    return svg;
  }

  /**
   * Inject Tag Manager and Tag Hierarchy buttons into Tags list page toolbar
   */
  function injectNavButtons() {
    // Only run on Tags list page
    if (!window.location.pathname.endsWith('/tags')) {
      return;
    }

    // Check if we already injected the buttons
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

    // Create Tag Manager button
    const tmBtn = document.createElement('button');
    tmBtn.id = 'tm-nav-button';
    tmBtn.className = 'btn btn-secondary';
    tmBtn.title = 'Tag Matcher';
    tmBtn.style.marginLeft = '0.5rem';
    tmBtn.appendChild(createTagManagerIcon());
    tmBtn.addEventListener('click', () => {
      window.location.href = ROUTE_PATH;
    });

    // Create Tag Hierarchy button
    const thBtn = document.createElement('button');
    thBtn.id = 'th-nav-button';
    thBtn.className = 'btn btn-secondary';
    thBtn.title = 'Tag Hierarchy';
    thBtn.style.marginLeft = '0.25rem';
    thBtn.appendChild(createHierarchyIcon());
    thBtn.addEventListener('click', () => {
      window.location.href = HIERARCHY_ROUTE_PATH;
    });

    // Insert both buttons after the insertion point
    insertionPoint.parentNode.insertBefore(tmBtn, insertionPoint.nextSibling);
    tmBtn.parentNode.insertBefore(thBtn, tmBtn.nextSibling);
    console.debug('[tagManager] Nav buttons injected on Tags page');
  }

  /**
   * Watch for navigation to Tags page and inject button
   */
  function setupNavButtonInjection() {
    // Try to inject immediately
    injectNavButtons();

    // Watch for URL changes (SPA navigation)
    let lastUrl = window.location.href;
    const observer = new MutationObserver(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        // Wait a bit for DOM to update after navigation
        setTimeout(injectNavButtons, 100);
        setTimeout(injectNavButtons, 500);
        setTimeout(injectNavButtons, 1000);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Also try on initial load with delays (for refresh on Tags page)
    setTimeout(injectNavButtons, 100);
    setTimeout(injectNavButtons, 500);
    setTimeout(injectNavButtons, 1000);
    setTimeout(injectNavButtons, 2000);
  }

  // Initialize
  registerRoute();
  setupNavButtonInjection();
  console.log('[tagManager] Plugin loaded');
})();
