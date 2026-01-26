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
  let categoryMappings = {}; // Cache of category_name -> local_tag_id
  let tagBlacklist = []; // Parsed blacklist patterns [{type: 'literal'|'regex', pattern: string, regex?: RegExp}]
  let activeTab = 'match'; // 'match' or 'browse'
  let browseCategory = null; // Selected category in browse view
  let selectedForImport = new Set(); // Tag IDs selected for import
  let browseSearchQuery = ''; // Search query for browse view
  let isImporting = false; // Guard against double-click on import

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
    // Extract operation name for logging
    const opMatch = query.match(/(?:query|mutation)\s+(\w+)/);
    const opName = opMatch ? opMatch[1] : 'anonymous';

    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });

    if (!response.ok) {
      console.error('[tagManager] GraphQL request failed:', opName, response.status);
      throw new Error(`GraphQL request failed: ${response.status}`);
    }

    const result = await response.json();
    if (result.errors?.length > 0) {
      console.error('[tagManager] GraphQL errors:', opName, result.errors);
      throw new Error(result.errors[0].message);
    }

    console.debug('[tagManager] GraphQL response:', opName, result.data ? 'OK' : 'empty');
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
   * Load category mappings from plugin settings
   */
  async function loadCategoryMappings() {
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

      // Parse JSON string from settings
      if (pluginConfig.categoryMappings) {
        try {
          categoryMappings = JSON.parse(pluginConfig.categoryMappings);
          console.debug("[tagManager] Loaded category mappings:", Object.keys(categoryMappings).length);
        } catch (e) {
          console.warn("[tagManager] Failed to parse category mappings:", e);
          categoryMappings = {};
        }
      }
    } catch (e) {
      console.error("[tagManager] Failed to load category mappings:", e);
    }
  }

  /**
   * Save category mappings to plugin settings
   */
  async function saveCategoryMappings() {
    try {
      const query = `
        mutation ConfigurePlugin($plugin_id: ID!, $input: Map!) {
          configurePlugin(plugin_id: $plugin_id, input: $input)
        }
      `;

      await graphqlRequest(query, {
        plugin_id: PLUGIN_ID,
        input: {
          categoryMappings: JSON.stringify(categoryMappings)
        }
      });
      console.debug("[tagManager] Saved category mappings");
    } catch (e) {
      console.error("[tagManager] Failed to save category mappings:", e);
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
   * Fetch a single tag's current parent IDs
   * Used to preserve existing parents when adding new ones
   */
  async function fetchTagParentIds(tagId) {
    const query = `
      query FindTag($id: ID!) {
        findTag(id: $id) {
          parents {
            id
          }
        }
      }
    `;

    const result = await graphqlRequest(query, { id: tagId });
    return (result?.findTag?.parents || []).map(p => p.id);
  }

  /**
   * Build a tree structure from flat tag list
   * Tags with multiple parents appear under each parent
   * @param {Array} tags - Flat array of tags with parent/children info
   * @returns {Array} - Array of root nodes (tags with no parents)
   */
  function buildTagTree(tags) {
    console.debug('[tagManager] buildTagTree: Processing', tags.length, 'tags');

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
    let childAssignments = 0;

    tags.forEach(tag => {
      const node = tagMap.get(tag.id);

      if (tag.parents.length === 0) {
        // Root tag - create copy with null parent context
        roots.push({ ...node, parentContextId: null });
      } else {
        // Add this tag as a child to each of its parents
        // Create a COPY with parent context for each parent
        tag.parents.forEach(parent => {
          const parentNode = tagMap.get(parent.id);
          if (parentNode) {
            parentNode.childNodes.push({ ...node, parentContextId: parent.id });
            childAssignments++;
          }
        });
      }
    });

    console.debug('[tagManager] buildTagTree: Found', roots.length, 'root tags,', childAssignments, 'child assignments');

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
   * Parse blacklist string into pattern objects
   */
  function parseBlacklist(blacklistStr) {
    if (!blacklistStr) return [];

    return blacklistStr.split('\n')
      .map(line => line.trim())
      .filter(line => line.length > 0)
      .map(pattern => {
        if (pattern.startsWith('/')) {
          // Regex pattern - extract pattern without leading /
          const regexStr = pattern.slice(1);
          try {
            return { type: 'regex', pattern: regexStr, regex: new RegExp(regexStr, 'i') };
          } catch (e) {
            console.warn(`[tagManager] Invalid regex in blacklist: ${pattern}`, e);
            return null;
          }
        } else {
          // Literal pattern - case-insensitive
          return { type: 'literal', pattern: pattern.toLowerCase() };
        }
      })
      .filter(p => p !== null);
  }

  /**
   * Check if a tag name matches any blacklist pattern
   */
  function isBlacklisted(tagName) {
    if (!tagName || tagBlacklist.length === 0) return false;

    const lowerName = tagName.toLowerCase();

    for (const entry of tagBlacklist) {
      if (entry.type === 'literal') {
        if (lowerName === entry.pattern) return true;
      } else if (entry.type === 'regex') {
        if (entry.regex.test(tagName)) return true;
      }
    }

    return false;
  }

  /**
   * Load blacklist from plugin settings
   */
  async function loadBlacklist() {
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

      if (pluginConfig.tagBlacklist) {
        tagBlacklist = parseBlacklist(pluginConfig.tagBlacklist);
        console.debug("[tagManager] Loaded blacklist:", tagBlacklist.length, "patterns");
      }
    } catch (e) {
      console.error("[tagManager] Failed to load blacklist:", e);
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
   * Filter StashDB tags by search query (matches name and aliases)
   */
  function filterTagsBySearch(query) {
    if (!query || !stashdbTags) return [];
    const lowerQuery = query.toLowerCase().trim();
    if (!lowerQuery) return [];

    return stashdbTags.filter(tag => {
      // Check tag name
      if (tag.name.toLowerCase().includes(lowerQuery)) return true;
      // Check aliases
      if (tag.aliases?.some(alias => alias.toLowerCase().includes(lowerQuery))) return true;
      return false;
    });
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
   * Handle merging a source tag into a destination tag, then apply StashDB link.
   * Used by both pre-validation and API error merge handlers.
   *
   * @param {object} params - Merge parameters
   * @param {object} params.sourceTag - The tag being merged (will be deleted)
   * @param {string} params.destinationId - ID of the tag to merge into
   * @param {object} params.stashdbTag - The StashDB tag to link
   * @param {string} params.endpoint - The stash-box endpoint URL
   * @param {string[]} params.sanitizedAliases - Aliases to include in the merge
   * @param {HTMLElement} params.modal - The modal element (for reading description choice)
   * @param {HTMLElement} params.container - The container element (for re-rendering)
   * @returns {Promise<{success: boolean, error?: string}>}
   */
  async function performTagMerge({ sourceTag, destinationId, stashdbTag, endpoint, sanitizedAliases, modal, container }) {
    const destinationTag = localTags.find(t => t.id === destinationId);
    if (!destinationTag) {
      return { success: false, error: 'Could not find destination tag.' };
    }

    try {
      // Merge current tag into the destination (conflicting) tag
      // This will move all entities from current tag to destination, merge aliases, then delete current tag
      const mergedTag = await mergeTags([sourceTag.id], destinationId);

      // Preserve existing stash_ids and add/update the new one for this endpoint
      const existingStashIds = mergedTag.stash_ids || [];
      const filteredStashIds = existingStashIds.filter(sid => sid.endpoint !== endpoint);

      const stashIdUpdate = {
        id: destinationId,
        stash_ids: [...filteredStashIds, {
          endpoint: endpoint,
          stash_id: stashdbTag.id,
        }],
      };

      // Merge the aliases we collected (including the original tag name) into the destination
      const mergedAliases = new Set(mergedTag.aliases || []);
      for (const alias of sanitizedAliases) {
        mergedAliases.add(alias);
      }
      stashIdUpdate.aliases = Array.from(mergedAliases);

      // Apply description if user chose StashDB description
      const descChoice = modal.querySelector('input[name="tm-desc"]:checked')?.value;
      if (descChoice === 'stashdb' && stashdbTag.description) {
        stashIdUpdate.description = stashdbTag.description;
      }

      await updateTag(stashIdUpdate);

      // Update local state - remove the merged (source) tag and update destination
      const sourceIdx = localTags.findIndex(t => t.id === sourceTag.id);
      if (sourceIdx >= 0) {
        localTags.splice(sourceIdx, 1);
      }

      const destIdx = localTags.findIndex(t => t.id === destinationId);
      if (destIdx >= 0) {
        localTags[destIdx].stash_ids = stashIdUpdate.stash_ids;
        localTags[destIdx].aliases = stashIdUpdate.aliases;
        if (stashIdUpdate.description !== undefined) {
          localTags[destIdx].description = stashIdUpdate.description;
        }
      }

      delete matchResults[sourceTag.id];
      modal.remove();

      showStatus(`Merged "${sourceTag.name}" into "${destinationTag.name}" and linked to StashDB`, 'success');
      renderPage(container);

      return { success: true };
    } catch (e) {
      console.error('[tagManager] Merge error:', e.message);
      return { success: false, error: e.message };
    }
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
   * Find local tags that could be parent tags for a given category name.
   * Searches by exact match, alias match, and fuzzy match.
   *
   * @param {string} categoryName - The StashDB category name to match
   * @returns {object[]} - Array of { tag, matchType, score } sorted by relevance
   */
  function findLocalParentMatches(categoryName) {
    if (!categoryName) return [];

    const lowerCategoryName = categoryName.toLowerCase();
    const matches = [];

    for (const tag of localTags) {
      // Skip tags that are children (have parents) - they're less likely to be category tags
      // But don't skip completely, just deprioritize
      const isChild = tag.parent_count > 0;

      // Exact name match
      if (tag.name.toLowerCase() === lowerCategoryName) {
        matches.push({ tag, matchType: 'exact', score: isChild ? 95 : 100 });
        continue;
      }

      // Name contains category (e.g., "CATEGORY: Action" contains "Action")
      if (tag.name.toLowerCase().includes(lowerCategoryName)) {
        matches.push({ tag, matchType: 'contains', score: isChild ? 85 : 90 });
        continue;
      }

      // Alias match
      if (tag.aliases?.some(a => a.toLowerCase() === lowerCategoryName)) {
        matches.push({ tag, matchType: 'alias', score: isChild ? 80 : 85 });
        continue;
      }

      // Fuzzy match on name (simple: starts with same letters)
      if (tag.name.toLowerCase().startsWith(lowerCategoryName.slice(0, 3)) &&
          tag.name.length < categoryName.length + 5) {
        matches.push({ tag, matchType: 'fuzzy', score: isChild ? 60 : 70 });
      }
    }

    // Sort by score descending
    matches.sort((a, b) => b.score - a.score);

    // Limit to top 5
    return matches.slice(0, 5);
  }

  /**
   * Get filtered tags based on current filter setting and selected endpoint
   */
  function getFilteredTags() {
    const endpoint = selectedStashBox?.endpoint;

    const hasEndpointMatch = (tag) => hasStashIdForEndpoint(tag, endpoint);

    const unmatchedTags = localTags.filter(t => !hasEndpointMatch(t));
    const matchedTags = localTags.filter(t => hasEndpointMatch(t));

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
   * Handle importing selected StashDB tags
   */
  async function handleImportSelected(container) {
    if (selectedForImport.size === 0) return;
    if (isImporting) return; // Prevent double-click

    isImporting = true;

    const statusEl = container.querySelector('.tm-selection-info');
    const btnEl = container.querySelector('#tm-import-selected');

    if (statusEl) statusEl.textContent = 'Importing...';
    if (btnEl) btnEl.disabled = true;

    let imported = 0;
    let errors = 0;

    for (const stashdbId of selectedForImport) {
      const stashdbTag = stashdbTags.find(t => t.id === stashdbId);
      if (!stashdbTag) continue;

      try {
        // Create local tag with stash_id
        const input = {
          name: stashdbTag.name,
          description: stashdbTag.description || '',
          aliases: stashdbTag.aliases || [],
          stash_ids: [{
            endpoint: selectedStashBox.endpoint,
            stash_id: stashdbId
          }]
        };

        const query = `
          mutation TagCreate($input: TagCreateInput!) {
            tagCreate(input: $input) {
              id
              name
            }
          }
        `;

        const data = await graphqlRequest(query, { input });
        if (data?.tagCreate) {
          // Add to local tags
          localTags.push({
            id: data.tagCreate.id,
            name: data.tagCreate.name,
            aliases: stashdbTag.aliases || [],
            stash_ids: input.stash_ids
          });
          imported++;
        }
      } catch (e) {
        console.error(`[tagManager] Failed to import "${stashdbTag.name}":`, e);
        errors++;
      }
    }

    // Clear selection and re-render
    selectedForImport.clear();

    const message = errors > 0
      ? `Imported ${imported} tag${imported !== 1 ? 's' : ''}, ${errors} error${errors !== 1 ? 's' : ''}`
      : `Imported ${imported} tag${imported !== 1 ? 's' : ''}`;

    if (statusEl) statusEl.textContent = message;

    // Re-render after short delay to show message, then reset import guard
    setTimeout(() => {
      isImporting = false;
      renderPage(container);
    }, 1500);
  }

  /**
   * Check if a StashDB tag already exists locally
   */
  function findLocalTagByStashId(stashdbId) {
    return localTags.find(t =>
      t.stash_ids?.some(sid => sid.stash_id === stashdbId)
    );
  }

  /**
   * Check if a tag has a stash_id for a specific endpoint
   * @param {object} tag - Tag object with stash_ids array
   * @param {string} endpoint - Endpoint URL to check
   * @returns {boolean} - True if tag has stash_id for this endpoint
   */
  function hasStashIdForEndpoint(tag, endpoint) {
    if (!tag || !endpoint) return false;
    return tag.stash_ids?.some(sid => sid.endpoint === endpoint) ?? false;
  }

  /**
   * Render list of tags for browse/import view
   */
  function renderBrowseTagList(tags) {
    if (!tags || tags.length === 0) {
      return '<div class="tm-browse-empty">No tags in this category</div>';
    }

    const rows = tags.map(tag => {
      const localTag = findLocalTagByStashId(tag.id);
      const existsLocally = !!localTag;
      const isSelected = selectedForImport.has(tag.id);

      return `
        <div class="tm-browse-tag ${existsLocally ? 'tm-exists-locally' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${existsLocally ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${existsLocally ? `<span class="tm-local-exists" title="Linked to: ${escapeHtml(localTag.name)}">✓ Exists</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    return rows;
  }

  /**
   * Render search results as a flat list with category badges
   */
  function renderSearchResults(tags) {
    if (!tags || tags.length === 0) {
      return `<div class="tm-browse-empty">No tags found matching "${escapeHtml(browseSearchQuery)}"</div>`;
    }

    const rows = tags.map(tag => {
      const localTag = findLocalTagByStashId(tag.id);
      const existsLocally = !!localTag;
      const isSelected = selectedForImport.has(tag.id);
      const categoryName = tag.category?.name || 'Uncategorized';

      return `
        <div class="tm-browse-tag ${existsLocally ? 'tm-exists-locally' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${existsLocally ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            <span class="tm-tag-category-badge">${escapeHtml(categoryName)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${existsLocally ? `<span class="tm-local-exists" title="Linked to: ${escapeHtml(localTag.name)}">✓ Exists</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    return `
      <div class="tm-search-results-count">${tags.length} tag${tags.length !== 1 ? 's' : ''} found</div>
      ${rows}
    `;
  }

  /**
   * Render the browse/import view
   */
  function renderBrowseView() {
    if (!stashdbTags || stashdbTags.length === 0) {
      return `
        <div class="tm-browse-empty">
          <p>No StashDB tags cached. Click "Refresh Cache" above to load tags.</p>
        </div>
      `;
    }

    const isSearching = browseSearchQuery.trim().length > 0;

    // Group tags by category (for sidebar)
    const categories = {};
    const uncategorized = [];

    for (const tag of stashdbTags) {
      const catName = tag.category?.name || null;
      if (catName) {
        if (!categories[catName]) {
          categories[catName] = [];
        }
        categories[catName].push(tag);
      } else {
        uncategorized.push(tag);
      }
    }

    // Sort categories alphabetically
    const sortedCategories = Object.keys(categories).sort();

    // Build category list
    const categoryList = sortedCategories.map(cat => {
      const count = categories[cat].length;
      const isSelected = browseCategory === cat;
      return `<div class="tm-category-item ${isSelected ? 'tm-category-active' : ''}" data-category="${escapeHtml(cat)}">
        <span class="tm-category-name">${escapeHtml(cat)}</span>
        <span class="tm-category-count">${count}</span>
      </div>`;
    }).join('');

    // Add uncategorized if any
    const uncategorizedItem = uncategorized.length > 0
      ? `<div class="tm-category-item ${browseCategory === '__uncategorized__' ? 'tm-category-active' : ''}" data-category="__uncategorized__">
          <span class="tm-category-name">Uncategorized</span>
          <span class="tm-category-count">${uncategorized.length}</span>
        </div>`
      : '';

    // Render tag list based on search or category selection
    let tagListHtml = '';
    if (isSearching) {
      const searchResults = filterTagsBySearch(browseSearchQuery);
      tagListHtml = renderSearchResults(searchResults);
    } else if (browseCategory) {
      const tagsToShow = browseCategory === '__uncategorized__'
        ? uncategorized
        : (categories[browseCategory] || []);
      tagListHtml = renderBrowseTagList(tagsToShow);
    } else {
      tagListHtml = `<div class="tm-browse-hint">Select a category to view tags, or search above</div>`;
    }

    const selectedCount = selectedForImport.size;

    return `
      <div class="tm-browse">
        <div class="tm-browse-sidebar ${isSearching ? 'tm-sidebar-hidden' : ''}">
          <div class="tm-browse-sidebar-header">
            <strong>Categories</strong>
            <span class="tm-total-tags">${stashdbTags.length} total</span>
          </div>
          <div class="tm-category-list">
            ${categoryList}
            ${uncategorizedItem}
          </div>
        </div>
        <div class="tm-browse-main">
          <div class="tm-browse-search">
            <input type="text" class="tm-browse-search-input" id="tm-browse-search"
                   placeholder="Search tags by name or alias..."
                   value="${escapeHtml(browseSearchQuery)}">
            ${browseSearchQuery ? '<button type="button" class="tm-browse-search-clear" id="tm-search-clear">&times;</button>' : ''}
          </div>
          <div class="tm-browse-toolbar">
            <div class="tm-selection-controls">
              <button class="btn btn-sm btn-secondary" id="tm-select-all">Select All</button>
              <button class="btn btn-sm btn-secondary" id="tm-deselect-all">Deselect All</button>
              <span class="tm-selection-info">
                ${selectedCount > 0 ? `${selectedCount} tag${selectedCount > 1 ? 's' : ''} selected` : 'No tags selected'}
              </span>
            </div>
            <button class="btn btn-primary" id="tm-import-selected" ${selectedCount === 0 ? 'disabled' : ''}>
              Import Selected
            </button>
          </div>
          <div class="tm-browse-tags">
            ${tagListHtml}
          </div>
        </div>
      </div>
    `;
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

          <div class="tm-tabs">
            <button class="tm-tab ${activeTab === 'match' ? 'tm-tab-active' : ''}" data-tab="match">Match Local Tags</button>
            <button class="tm-tab ${activeTab === 'browse' ? 'tm-tab-active' : ''}" data-tab="browse">Browse StashDB</button>
          </div>

          ${activeTab === 'match' ? `
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
          ` : `
            ${renderBrowseView()}
          `}
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
    // Tab switching
    container.querySelectorAll('.tm-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const newTab = tab.dataset.tab;
        if (newTab !== activeTab) {
          // Warn if there are pending hierarchy changes
          if (isEditMode && pendingChanges.length > 0) {
            if (!confirm('You have unsaved hierarchy changes. Discard them?')) {
              return;
            }
            // Discard changes
            isEditMode = false;
            pendingChanges = [];
            originalParentMap.clear();
          }
          activeTab = newTab;
          renderPage(container);
        }
      });
    });

    // Browse view handlers (only when browse tab active)
    if (activeTab === 'browse') {
      // Search input with debounce
      let searchTimeout = null;
      const searchInput = container.querySelector('#tm-browse-search');
      if (searchInput) {
        searchInput.addEventListener('input', (e) => {
          clearTimeout(searchTimeout);
          searchTimeout = setTimeout(() => {
            browseSearchQuery = e.target.value;
            renderPage(container);
            // Re-focus and restore cursor position
            const newInput = container.querySelector('#tm-browse-search');
            if (newInput) {
              newInput.focus();
              newInput.setSelectionRange(newInput.value.length, newInput.value.length);
            }
          }, 200);
        });
      }

      // Clear search button
      const clearBtn = container.querySelector('#tm-search-clear');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          browseSearchQuery = '';
          renderPage(container);
        });
      }

      // Category selection
      container.querySelectorAll('.tm-category-item').forEach(item => {
        item.addEventListener('click', () => {
          browseCategory = item.dataset.category;
          renderPage(container);
        });
      });

      // Checkbox selection
      container.querySelectorAll('.tm-browse-tag input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const tagEl = e.target.closest('.tm-browse-tag');
          const stashdbId = tagEl.dataset.stashdbId;
          if (e.target.checked) {
            selectedForImport.add(stashdbId);
          } else {
            selectedForImport.delete(stashdbId);
          }
          // Update selection count display
          const infoEl = container.querySelector('.tm-selection-info');
          const btnEl = container.querySelector('#tm-import-selected');
          if (infoEl) {
            const count = selectedForImport.size;
            infoEl.textContent = count > 0 ? `${count} tag${count > 1 ? 's' : ''} selected` : 'No tags selected';
          }
          if (btnEl) {
            btnEl.disabled = selectedForImport.size === 0;
          }
        });
      });

      // Select All / Deselect All
      const selectAllBtn = container.querySelector('#tm-select-all');
      const deselectAllBtn = container.querySelector('#tm-deselect-all');

      if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
          container.querySelectorAll('.tm-browse-tag:not(.tm-exists-locally) input[type="checkbox"]').forEach(cb => {
            cb.checked = true;
            const tagEl = cb.closest('.tm-browse-tag');
            selectedForImport.add(tagEl.dataset.stashdbId);
          });
          renderPage(container);
        });
      }

      if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
          selectedForImport.clear();
          renderPage(container);
        });
      }

      // Import button
      const importBtn = container.querySelector('#tm-import-selected');
      if (importBtn) {
        importBtn.addEventListener('click', () => handleImportSelected(container));
      }
    }

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

    // Category/parent state
    const hasCategory = !!stashdbTag.category?.name;
    let selectedParentId = null;
    let createParentIfMissing = true;
    let parentMatches = [];

    if (hasCategory) {
      // Check for saved mapping first
      const savedMapping = categoryMappings[stashdbTag.category.name];
      if (savedMapping) {
        selectedParentId = savedMapping;
      } else {
        // Find local matches
        parentMatches = findLocalParentMatches(stashdbTag.category.name);
        if (parentMatches.length > 0) {
          selectedParentId = parentMatches[0].tag.id;
        }
      }
    }

    // Helper function to render alias checkboxes for a column
    function renderAliasCheckboxes(primaryAliases, otherAliases, source) {
      const newAliases = otherAliases.filter(a => !primaryAliases.some(pa => pa.toLowerCase() === a.toLowerCase()));

      if (primaryAliases.length === 0 && newAliases.length === 0) {
        return '<em class="tm-alias-empty">none</em>';
      }

      let html = '';
      let aliasIndex = 0; // Unique index to prevent ID collisions

      // Primary aliases (from this source)
      primaryAliases.forEach(alias => {
        const safeId = `${source}-${aliasIndex++}`;
        html += `
          <div class="tm-alias-checkbox-item">
            <input type="checkbox" id="alias-${safeId}" data-alias="${escapeHtml(alias)}" checked>
            <label for="alias-${safeId}">${escapeHtml(alias)}</label>
          </div>
        `;
      });

      // Aliases from other source that aren't in this one (shown in blue)
      newAliases.forEach(alias => {
        const safeId = `new-${source}-${aliasIndex++}`;
        const fromLabel = source === 'local' ? 'from StashDB' : 'from local';
        html += `
          <div class="tm-alias-checkbox-item new-from-other">
            <input type="checkbox" id="alias-${safeId}" data-alias="${escapeHtml(alias)}" checked>
            <label for="alias-${safeId}">${escapeHtml(alias)} (${fromLabel})</label>
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

    // Parent tag search modal
    function showParentSearchModal() {
      const searchModal = document.createElement('div');
      searchModal.className = 'tm-modal-backdrop tm-search-modal';
      searchModal.innerHTML = `
        <div class="tm-modal tm-modal-small">
          <div class="tm-modal-header">
            <h3>Search Parent Tag</h3>
            <button class="tm-close-btn">&times;</button>
          </div>
          <div class="tm-modal-body">
            <input type="text" id="tm-parent-search-input" class="form-control"
                   placeholder="Search tags..." value="${escapeHtml(stashdbTag.category?.name || '')}">
            <div class="tm-search-results" id="tm-parent-search-results">
              <div class="tm-loading">Type to search...</div>
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(searchModal);

      const input = searchModal.querySelector('#tm-parent-search-input');
      const resultsEl = searchModal.querySelector('#tm-parent-search-results');

      function doSearch() {
        const term = input.value.trim().toLowerCase();
        if (!term) {
          resultsEl.innerHTML = '<div class="tm-loading">Type to search...</div>';
          return;
        }

        const matches = localTags.filter(t =>
          t.name.toLowerCase().includes(term) ||
          t.aliases?.some(a => a.toLowerCase().includes(term))
        ).slice(0, 10);

        if (matches.length === 0) {
          resultsEl.innerHTML = '<div class="tm-no-matches">No matching tags found</div>';
          return;
        }

        resultsEl.innerHTML = matches.map(t => `
          <div class="tm-search-result" data-tag-id="${t.id}">
            <span class="tm-result-name">${escapeHtml(t.name)}</span>
            ${t.aliases?.length ? `<span class="tm-result-aliases">${escapeHtml(t.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
        `).join('');

        resultsEl.querySelectorAll('.tm-search-result').forEach(el => {
          el.addEventListener('click', () => {
            const tagId = el.dataset.tagId;
            const tag = localTags.find(t => t.id === tagId);
            if (tag) {
              // Update the parent select
              const select = modal.querySelector('#tm-parent-select');
              // Add option if not present
              if (!select.querySelector(`option[value="${tagId}"]`)) {
                const option = document.createElement('option');
                option.value = tagId;
                option.textContent = tag.name;
                select.appendChild(option);
              }
              select.value = tagId;
              selectedParentId = tagId;
            }
            searchModal.remove();
          });
        });
      }

      input.addEventListener('input', doSearch);
      input.focus();
      doSearch();

      searchModal.querySelector('.tm-close-btn').addEventListener('click', () => searchModal.remove());
      searchModal.addEventListener('click', (e) => {
        if (e.target === searchModal) searchModal.remove();
      });
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
              ${hasCategory ? `
              <tr>
                <td>Parent Tag</td>
                <td colspan="3">
                  <div class="tm-category-section">
                    <div class="tm-category-info">
                      <span class="tm-category-label">StashDB Category:</span>
                      <span class="tm-category-name">${escapeHtml(stashdbTag.category.name)}</span>
                    </div>
                    <div class="tm-parent-select">
                      <select id="tm-parent-select" class="form-control">
                        <option value="">-- No parent --</option>
                        <option value="__create__" ${!selectedParentId ? 'selected' : ''}>Create "${escapeHtml(stashdbTag.category.name)}"</option>
                        ${parentMatches.map(m => `
                          <option value="${m.tag.id}" ${selectedParentId === m.tag.id ? 'selected' : ''}>
                            ${escapeHtml(m.tag.name)} (${m.matchType})
                          </option>
                        `).join('')}
                      </select>
                      <button type="button" class="btn btn-secondary btn-sm" id="tm-parent-search-btn">Search...</button>
                    </div>
                    <div class="tm-parent-remember">
                      <label>
                        <input type="checkbox" id="tm-remember-mapping" checked>
                        Remember this mapping
                      </label>
                    </div>
                  </div>
                </td>
              </tr>
              ` : ''}
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
      // Find checkbox by data-alias attribute (case-insensitive)
      const checkboxes = modal.querySelectorAll('.tm-alias-checkbox-item input[type="checkbox"]');
      for (const cb of checkboxes) {
        if (cb.dataset.alias && cb.dataset.alias.toLowerCase() === alias.toLowerCase()) {
          cb.checked = checked;
          break;
        }
      }
    }

    // Parent selection handlers (if category exists)
    if (hasCategory) {
      const parentSelect = modal.querySelector('#tm-parent-select');
      if (parentSelect) {
        parentSelect.addEventListener('change', (e) => {
          selectedParentId = e.target.value === '' ? null : e.target.value;
        });
      }

      const searchBtn = modal.querySelector('#tm-parent-search-btn');
      if (searchBtn) {
        searchBtn.addEventListener('click', showParentSearchModal);
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
          // Add the local name as an alias (preserve old name when renaming)
          // The local name may not have a checkbox if it's not in either alias list,
          // so we add it directly to editableAliases
          if (tag.name && tag.name.toLowerCase() !== stashdbTag.name.toLowerCase()) {
            editableAliases.add(tag.name);
            renderAliasPills();
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

      console.debug('[tagManager] Applying match:', {
        localTag: tag.name,
        stashdbTag: stashdbTag.name,
        nameChoice,
        descChoice,
        aliasCount: sanitizedAliases.length,
        hasCategory,
        selectedParentId
      });

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

      // Handle parent tag from category
      let parentTagId = null;
      if (hasCategory && selectedParentId) {
        if (selectedParentId === '__create__') {
          // Create the parent tag
          try {
            const newParent = await createTag({ name: stashdbTag.category.name });
            parentTagId = newParent.id;
            // Add to localTags for future reference
            localTags.push({ id: newParent.id, name: newParent.name, aliases: [] });
            console.debug(`[tagManager] Created parent tag: ${newParent.name}`);
          } catch (e) {
            console.error('[tagManager] Failed to create parent tag:', e);
            errorEl.innerHTML = `<div class="tm-error-message">Failed to create parent tag: ${escapeHtml(e.message)}</div>`;
            errorEl.style.display = 'block';
            return;
          }
        } else {
          parentTagId = selectedParentId;
        }

        // Merge new parent with existing parents (don't replace)
        if (parentTagId) {
          const existingParentIds = await fetchTagParentIds(tag.id);
          // Add new parent if not already present
          if (!existingParentIds.includes(parentTagId)) {
            updateInput.parent_ids = [...existingParentIds, parentTagId];
          }
          // If already a parent, no need to update parent_ids
        }

        // Save mapping if checkbox is checked
        const rememberCheckbox = modal.querySelector('#tm-remember-mapping');
        if (rememberCheckbox?.checked && parentTagId) {
          categoryMappings[stashdbTag.category.name] = parentTagId;
          saveCategoryMappings(); // Fire and forget
        }
      }

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
              <button type="button" class="btn btn-primary btn-sm tm-error-merge" data-conflict-id="${conflictTag.id}">
                Merge into "${escapeHtml(conflictTag.name)}"
              </button>
              <button type="button" class="btn btn-secondary btn-sm tm-error-keep-local">
                Keep local name instead
              </button>
              <a href="/tags/${conflictTag.id}" target="_blank" class="btn btn-secondary btn-sm">
                Edit "${escapeHtml(conflictTag.name)}"
              </a>
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

        // Merge button handler - merges current tag into the conflicting tag
        const mergeBtn = errorEl.querySelector('.tm-error-merge');
        if (mergeBtn) {
          mergeBtn.addEventListener('click', async () => {
            const destinationId = mergeBtn.dataset.conflictId;
            const originalText = mergeBtn.textContent;

            mergeBtn.disabled = true;
            mergeBtn.textContent = 'Merging...';

            const result = await performTagMerge({
              sourceTag: tag,
              destinationId,
              stashdbTag,
              endpoint,
              sanitizedAliases,
              modal,
              container
            });

            if (!result.success) {
              errorEl.innerHTML = `<div class="tm-error-message">Merge failed: ${escapeHtml(result.error)}</div>`;
              mergeBtn.disabled = false;
              mergeBtn.textContent = originalText;
            }
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
                <button type="button" class="btn btn-primary btn-sm tm-error-merge-api" data-conflict-id="${conflictTag.id}">
                  Merge into "${escapeHtml(conflictTag.name)}"
                </button>
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

          // Merge button handler for API error case
          const mergeApiBtn = errorEl.querySelector('.tm-error-merge-api');
          if (mergeApiBtn) {
            mergeApiBtn.addEventListener('click', async () => {
              const destinationId = mergeApiBtn.dataset.conflictId;
              const originalText = mergeApiBtn.textContent;

              mergeApiBtn.disabled = true;
              mergeApiBtn.textContent = 'Merging...';

              const result = await performTagMerge({
                sourceTag: tag,
                destinationId,
                stashdbTag,
                endpoint,
                sanitizedAliases,
                modal,
                container
              });

              if (!result.success) {
                errorEl.innerHTML = `<div class="tm-error-message">Merge failed: ${escapeHtml(result.error)}</div>`;
                mergeApiBtn.disabled = false;
                mergeApiBtn.textContent = originalText;
              }
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
   * Create a new tag via GraphQL
   */
  async function createTag(input) {
    const query = `
      mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) {
          id
          name
        }
      }
    `;

    const data = await graphqlRequest(query, { input });
    return data?.tagCreate;
  }

  /**
   * Merge tags via GraphQL - merges source tags into destination tag.
   * This reassigns all entities (scenes, images, galleries, performers, studios, groups, markers)
   * from source tags to destination, merges aliases and stash_ids, then deletes source tags.
   *
   * @param {string[]} sourceIds - Array of tag IDs to merge (will be deleted)
   * @param {string} destinationId - Tag ID to merge into (will be kept)
   * @returns {object} - The updated destination tag
   */
  async function mergeTags(sourceIds, destinationId) {
    const query = `
      mutation TagsMerge($input: TagsMergeInput!) {
        tagsMerge(input: $input) {
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
    `;

    const data = await graphqlRequest(query, {
      input: { source: sourceIds, destination: destinationId }
    });
    return data?.tagsMerge;
  }

  /**
   * Show modal with all matches for manual selection
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    let matches = matchResults[tagId];
    if (!tag) return;

    // Filter out blacklisted matches
    const originalCount = matches?.length || 0;
    const filteredMatches = matches?.filter(m => !isBlacklisted(m.tag.name)) || [];
    const hiddenCount = originalCount - filteredMatches.length;
    matches = filteredMatches;

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal tm-modal-wide">
        <div class="tm-modal-header">
          <h3>Matches for: ${escapeHtml(tag.name)}</h3>
          ${hiddenCount > 0 ? `<div class="tm-blacklist-notice">${hiddenCount} tag${hiddenCount > 1 ? 's' : ''} hidden by blacklist</div>` : ''}
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
        await loadCategoryMappings();
        await loadBlacklist();

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
  let contextMenuTag = null;
  let contextMenuParentId = null;
  let contextMenuEscapeHandler = null;
  let draggedTagId = null;
  let draggedFromParentId = null;
  let selectedTagId = null;
  let copiedTagId = null;

  // Edit mode state
  let isEditMode = false;
  let pendingChanges = [];
  let originalParentMap = new Map(); // tagId -> array of parent ids (snapshot at edit start)

  /**
   * Enter edit mode - snapshot current state and show changes panel
   */
  function enterEditMode() {
    if (isEditMode) return;

    isEditMode = true;
    pendingChanges = [];

    // Snapshot current parent relationships
    originalParentMap.clear();
    for (const tag of hierarchyTags) {
      originalParentMap.set(tag.id, tag.parents?.map(p => p.id) || []);
    }

    // Show the changes panel
    renderChangesPanel();
  }

  /**
   * Add a pending change, handling cancellation of opposite changes.
   * Returns true if change was added, false if it cancelled out an existing change.
   */
  function addPendingChange(type, tagId, tagName, parentId, parentName) {
    console.debug('[tagManager] addPendingChange:', { type, tagId, tagName, parentId, parentName });

    // Check for opposite change that would cancel this out
    const oppositeType = type === 'add-parent' ? 'remove-parent' : 'add-parent';
    const oppositeIdx = pendingChanges.findIndex(c =>
      c.type === oppositeType && c.tagId === tagId && c.parentId === parentId
    );

    if (oppositeIdx !== -1) {
      // Remove the opposite change (they cancel out)
      pendingChanges.splice(oppositeIdx, 1);

      // If no changes left, exit edit mode
      if (pendingChanges.length === 0) {
        exitEditMode(false);
        showToast('Change cancelled - no pending changes');
        return false;
      }

      renderChangesPanel();
      showToast('Change cancelled previous pending change');
      return false;
    }

    // Check if this exact change already exists
    const existingIdx = pendingChanges.findIndex(c =>
      c.type === type && c.tagId === tagId && c.parentId === parentId
    );

    if (existingIdx === -1) {
      pendingChanges.push({
        type,
        tagId,
        tagName,
        parentId,
        parentName,
        timestamp: Date.now()
      });
    }

    // Re-render the changes panel
    renderChangesPanel();
    return true;
  }

  /**
   * Remove a specific pending change by index
   */
  function removePendingChange(index) {
    if (index >= 0 && index < pendingChanges.length) {
      pendingChanges.splice(index, 1);

      // If no changes left, exit edit mode
      if (pendingChanges.length === 0) {
        exitEditMode(false);
      } else {
        renderChangesPanel();
        // Re-render tree to update visual state
        applyPendingChangesToTree();
      }
    }
  }

  /**
   * Exit edit mode - either save or discard changes
   */
  async function exitEditMode(save) {
    if (!isEditMode) return;

    if (save && pendingChanges.length > 0) {
      await savePendingChanges();
    }

    isEditMode = false;
    pendingChanges = [];
    originalParentMap.clear();

    // Remove the changes panel
    const panel = document.getElementById('th-changes-panel');
    if (panel) panel.remove();

    // Refresh from server to ensure consistent state
    await refreshHierarchy();
  }

  /**
   * Render the pending changes panel at the bottom of the hierarchy view
   */
  function renderChangesPanel() {
    // Remove existing panel
    let panel = document.getElementById('th-changes-panel');
    if (panel) panel.remove();

    const container = document.querySelector('.tag-hierarchy');
    if (!container) return;

    panel = document.createElement('div');
    panel.id = 'th-changes-panel';
    panel.className = 'th-changes-panel';

    const changesHtml = pendingChanges.map((change, idx) => {
      const action = change.type === 'add-parent'
        ? `Added "${escapeHtml(change.parentName)}" as parent of "${escapeHtml(change.tagName)}"`
        : `Removed "${escapeHtml(change.tagName)}" from "${escapeHtml(change.parentName)}"`;
      return `
        <div class="th-change-item">
          <span class="th-change-text">${action}</span>
          <button class="th-change-remove" data-index="${idx}" title="Remove this change">&times;</button>
        </div>
      `;
    }).join('');

    panel.innerHTML = `
      <div class="th-changes-header">
        <span>Pending Changes (${pendingChanges.length})</span>
      </div>
      <div class="th-changes-list">
        ${changesHtml || '<div class="th-no-changes">No changes yet</div>'}
      </div>
      <div class="th-changes-actions">
        <button class="btn btn-secondary" id="th-cancel-changes">Cancel</button>
        <button class="btn btn-primary" id="th-save-changes" ${pendingChanges.length === 0 ? 'disabled' : ''}>Save Changes</button>
      </div>
    `;

    container.appendChild(panel);

    // Attach event handlers
    panel.querySelector('#th-cancel-changes')?.addEventListener('click', () => exitEditMode(false));
    panel.querySelector('#th-save-changes')?.addEventListener('click', () => exitEditMode(true));
    panel.querySelectorAll('.th-change-remove').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const idx = parseInt(e.target.dataset.index, 10);
        removePendingChange(idx);
      });
    });
  }

  /**
   * Save all pending changes to the server
   */
  async function savePendingChanges() {
    if (pendingChanges.length === 0) return;

    console.debug('[tagManager] savePendingChanges: Saving', pendingChanges.length, 'changes:', pendingChanges);

    // Compute final parent state for each modified tag
    const tagUpdates = new Map(); // tagId -> Set of final parent ids

    // Start with original parents
    for (const change of pendingChanges) {
      if (!tagUpdates.has(change.tagId)) {
        const original = originalParentMap.get(change.tagId) || [];
        tagUpdates.set(change.tagId, new Set(original));
      }
    }

    // Apply changes
    for (const change of pendingChanges) {
      const parentSet = tagUpdates.get(change.tagId);
      if (change.type === 'add-parent') {
        parentSet.add(change.parentId);
      } else {
        parentSet.delete(change.parentId);
      }
    }

    // Send mutations
    const errors = [];
    for (const [tagId, parentSet] of tagUpdates) {
      try {
        await updateTagParents(tagId, Array.from(parentSet));
      } catch (err) {
        const tag = hierarchyTags.find(t => t.id === tagId);
        errors.push(`Failed to update "${tag?.name || tagId}": ${err.message}`);
      }
    }

    if (errors.length > 0) {
      showToast(`Some changes failed:\n${errors.join('\n')}`, 'error');
    } else {
      showToast(`Saved ${pendingChanges.length} change${pendingChanges.length !== 1 ? 's' : ''}`);
    }
  }

  /**
   * Apply pending changes to local tree state and re-render
   */
  function applyPendingChangesToTree() {
    // Create a working copy of tags with pending changes applied
    const workingTags = hierarchyTags.map(tag => {
      // Get original parents
      const originalParents = originalParentMap.get(tag.id) || tag.parents?.map(p => p.id) || [];
      const parentSet = new Set(originalParents);

      // Apply pending changes for this tag
      for (const change of pendingChanges) {
        if (change.tagId === tag.id) {
          if (change.type === 'add-parent') {
            parentSet.add(change.parentId);
          } else {
            parentSet.delete(change.parentId);
          }
        }
      }

      // Convert back to parent objects
      const newParents = Array.from(parentSet).map(pid => {
        const parentTag = hierarchyTags.find(t => t.id === pid);
        return parentTag ? { id: pid, name: parentTag.name } : { id: pid, name: 'Unknown' };
      });

      return { ...tag, parents: newParents };
    });

    // Rebuild and re-render tree
    hierarchyTree = buildTagTree(workingTags);
    const container = document.querySelector('.tag-hierarchy-container');
    if (container) {
      renderHierarchyPage(container);
      // Re-attach the changes panel after re-render
      if (isEditMode) {
        renderChangesPanel();
      }
    }
  }

  /**
   * Show context menu for a tag node
   */
  function showContextMenu(x, y, tagId, parentId) {
    hideContextMenu();
    contextMenuTag = hierarchyTags.find(t => t.id === tagId);
    contextMenuParentId = parentId;

    console.debug('[tagManager] showContextMenu:', {
      tagId,
      parentId,
      tagFound: !!contextMenuTag,
      tagName: contextMenuTag?.name,
      tagParents: contextMenuTag?.parents
    });

    if (!contextMenuTag) return;

    const menu = document.createElement('div');
    menu.className = 'th-context-menu';
    menu.id = 'th-context-menu';

    const hasParents = contextMenuTag.parents && contextMenuTag.parents.length > 0;
    const isUnderParent = parentId !== null;

    console.debug('[tagManager] showContextMenu menu options:', { hasParents, isUnderParent });

    let menuHtml = `
      <div class="th-context-menu-item" data-action="add-parent">Add parent...</div>
      <div class="th-context-menu-item" data-action="add-child">Add child...</div>
    `;

    if (isUnderParent) {
      const parentTag = hierarchyTags.find(t => t.id === parentId);
      const parentName = parentTag ? parentTag.name : 'parent';
      menuHtml += `<div class="th-context-menu-separator"></div>`;
      menuHtml += `<div class="th-context-menu-item" data-action="remove-parent" data-parent-id="${parentId}">Remove from "${escapeHtml(parentName)}"</div>`;
    }

    if (hasParents) {
      menuHtml += `<div class="th-context-menu-item" data-action="make-root">Make root (remove all parents)</div>`;
    }

    menu.innerHTML = menuHtml;
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    document.body.appendChild(menu);

    // Position adjustment if off-screen
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
      menu.style.left = `${window.innerWidth - rect.width - 10}px`;
    }
    if (rect.bottom > window.innerHeight) {
      menu.style.top = `${window.innerHeight - rect.height - 10}px`;
    }

    // Click handlers
    menu.querySelectorAll('.th-context-menu-item:not(.disabled)').forEach(item => {
      item.addEventListener('click', handleContextMenuAction);
    });

    // Close on click outside
    setTimeout(() => {
      document.addEventListener('click', hideContextMenu, { once: true });
    }, 0);

    // Close on Escape key
    contextMenuEscapeHandler = (e) => {
      if (e.key === 'Escape') {
        hideContextMenu();
      }
    };
    document.addEventListener('keydown', contextMenuEscapeHandler);
  }

  /**
   * Hide context menu
   */
  function hideContextMenu() {
    const menu = document.getElementById('th-context-menu');
    if (menu) menu.remove();
    contextMenuTag = null;
    contextMenuParentId = null;
    if (contextMenuEscapeHandler) {
      document.removeEventListener('keydown', contextMenuEscapeHandler);
      contextMenuEscapeHandler = null;
    }
  }

  /**
   * Show toast notification
   */
  function showToast(message, type = 'success') {
    // Create container if needed
    let container = document.querySelector('.th-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'th-toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `th-toast ${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    // Auto-remove after 3 seconds
    setTimeout(() => {
      toast.style.animation = 'th-toast-out 0.3s ease forwards';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  /**
   * Show tag search dialog for adding parent/child
   */
  function showTagSearchDialog(mode, targetTag) {
    // mode: 'parent' or 'child'
    const backdrop = document.createElement('div');
    backdrop.className = 'th-search-dialog-backdrop';
    backdrop.id = 'th-search-backdrop';

    const dialog = document.createElement('div');
    dialog.className = 'th-search-dialog';
    dialog.id = 'th-search-dialog';

    const title = mode === 'parent'
      ? `Add parent for "${escapeHtml(targetTag.name)}"`
      : `Add child to "${escapeHtml(targetTag.name)}"`;

    dialog.innerHTML = `
      <h3>${title}</h3>
      <input type="text" class="th-search-input" placeholder="Search tags..." autofocus>
      <div class="th-search-results">
        <div class="th-search-empty">Type to search...</div>
      </div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(dialog);

    const input = dialog.querySelector('.th-search-input');
    const results = dialog.querySelector('.th-search-results');

    // Debounced search
    let searchTimeout;
    input.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        performTagSearch(input.value, mode, targetTag, results);
      }, 200);
    });

    // Close on backdrop click or escape
    backdrop.addEventListener('click', closeTagSearchDialog);
    document.addEventListener('keydown', function escHandler(e) {
      if (e.key === 'Escape') {
        closeTagSearchDialog();
        document.removeEventListener('keydown', escHandler);
      }
    });

    input.focus();
  }

  /**
   * Close the tag search dialog
   */
  function closeTagSearchDialog() {
    document.getElementById('th-search-backdrop')?.remove();
    document.getElementById('th-search-dialog')?.remove();
  }

  /**
   * Perform tag search and render results
   */
  function performTagSearch(query, mode, targetTag, resultsContainer) {
    if (!query.trim()) {
      resultsContainer.innerHTML = '<div class="th-search-empty">Type to search...</div>';
      return;
    }

    const lowerQuery = query.toLowerCase();

    // Filter local tags
    let matches = hierarchyTags.filter(t => {
      // Don't show the target tag itself
      if (t.id === targetTag.id) return false;

      // Check name and aliases
      if (t.name.toLowerCase().includes(lowerQuery)) return true;
      if (t.aliases?.some(a => a.toLowerCase().includes(lowerQuery))) return true;
      return false;
    });

    // Sort by relevance (exact match first, then starts with, then contains)
    matches.sort((a, b) => {
      const aLower = a.name.toLowerCase();
      const bLower = b.name.toLowerCase();
      const aExact = aLower === lowerQuery;
      const bExact = bLower === lowerQuery;
      if (aExact && !bExact) return -1;
      if (bExact && !aExact) return 1;
      const aStarts = aLower.startsWith(lowerQuery);
      const bStarts = bLower.startsWith(lowerQuery);
      if (aStarts && !bStarts) return -1;
      if (bStarts && !aStarts) return 1;
      return a.name.localeCompare(b.name);
    });

    // Limit results
    matches = matches.slice(0, 20);

    if (matches.length === 0) {
      resultsContainer.innerHTML = '<div class="th-search-empty">No tags found</div>';
      return;
    }

    resultsContainer.innerHTML = matches.map(tag => {
      // Check for circular reference
      const wouldCreateCircle = mode === 'parent'
        ? wouldCreateCircularRef(tag.id, targetTag.id)
        : wouldCreateCircularRef(targetTag.id, tag.id);

      const isAlreadyRelated = mode === 'parent'
        ? targetTag.parents?.some(p => p.id === tag.id)
        : tag.parents?.some(p => p.id === targetTag.id);

      const disabled = wouldCreateCircle || isAlreadyRelated;
      const badge = wouldCreateCircle ? 'circular' : isAlreadyRelated ? 'already linked' : '';

      return `
        <div class="th-search-result ${disabled ? 'disabled' : ''}"
             data-tag-id="${tag.id}"
             data-mode="${mode}"
             data-target-id="${targetTag.id}">
          <span class="th-search-result-name">${escapeHtml(tag.name)}</span>
          ${badge ? `<span class="th-search-result-badge">${badge}</span>` : ''}
        </div>
      `;
    }).join('');

    // Click handlers
    resultsContainer.querySelectorAll('.th-search-result:not(.disabled)').forEach(item => {
      item.addEventListener('click', handleSearchResultClick);
    });
  }

  /**
   * Handle click on a search result
   */
  async function handleSearchResultClick(e) {
    const tagId = e.currentTarget.dataset.tagId;
    const mode = e.currentTarget.dataset.mode;
    const targetId = e.currentTarget.dataset.targetId;

    closeTagSearchDialog();

    if (mode === 'parent') {
      await addParent(targetId, tagId);
    } else {
      await addChild(targetId, tagId);
    }
  }

  /**
   * Add a parent to a tag
   */
  async function addParent(tagId, newParentId) {
    const tag = hierarchyTags.find(t => t.id === tagId);
    const parent = hierarchyTags.find(t => t.id === newParentId);
    if (!tag || !parent) return;

    // Enter edit mode if not already
    enterEditMode();

    // Queue the change (returns false if it cancelled out an existing change)
    const wasAdded = addPendingChange('add-parent', tagId, tag.name, newParentId, parent.name);

    if (wasAdded) {
      // Update local state for immediate visual feedback
      applyPendingChangesToTree();
      showToast(`Queued: Add "${tag.name}" as child of "${parent.name}"`);
    }
  }

  /**
   * Add a child to a tag (by adding the target as the child's parent)
   */
  async function addChild(parentId, childId) {
    const child = hierarchyTags.find(t => t.id === childId);
    const parent = hierarchyTags.find(t => t.id === parentId);
    if (!child || !parent) return;

    // Enter edit mode if not already
    enterEditMode();

    // Queue the change (returns false if it cancelled out an existing change)
    const wasAdded = addPendingChange('add-parent', childId, child.name, parentId, parent.name);

    if (wasAdded) {
      // Update local state for immediate visual feedback
      applyPendingChangesToTree();
      showToast(`Queued: Add "${child.name}" as child of "${parent.name}"`);
    }
  }

  /**
   * Check if making potentialParentId a parent of tagId would create a circular reference.
   * This happens if tagId is already an ancestor of potentialParentId.
   * Also considers pending changes that haven't been saved yet.
   */
  function wouldCreateCircularRef(potentialParentId, tagId) {
    // Build effective parent map considering pending changes
    const effectiveParents = new Map();

    for (const tag of hierarchyTags) {
      const parents = new Set(tag.parents?.map(p => p.id) || []);
      effectiveParents.set(tag.id, parents);
    }

    // Apply pending changes
    for (const change of pendingChanges) {
      const parents = effectiveParents.get(change.tagId) || new Set();
      if (change.type === 'add-parent') {
        parents.add(change.parentId);
      } else {
        parents.delete(change.parentId);
      }
      effectiveParents.set(change.tagId, parents);
    }

    // Build a set of all ancestors of potentialParentId
    const ancestors = new Set();

    function collectAncestors(id) {
      const parents = effectiveParents.get(id);
      if (!parents) return;

      for (const parentId of parents) {
        if (ancestors.has(parentId)) continue; // Already visited
        ancestors.add(parentId);
        collectAncestors(parentId);
      }
    }

    collectAncestors(potentialParentId);

    // If tagId is an ancestor of potentialParentId, adding potentialParentId as parent of tagId
    // would create: tagId -> potentialParentId -> ... -> tagId (circular)
    return ancestors.has(tagId);
  }

  /**
   * Update a tag's parent relationships via GraphQL
   */
  async function updateTagParents(tagId, parentIds) {
    const tag = hierarchyTags.find(t => t.id === tagId);
    console.debug('[tagManager] updateTagParents:', {
      tagId,
      tagName: tag?.name,
      newParentIds: parentIds,
      currentParents: tag?.parents
    });

    const query = `
      mutation TagUpdate($input: TagUpdateInput!) {
        tagUpdate(input: $input) {
          id
          name
          parents { id name }
        }
      }
    `;

    const result = await graphqlRequest(query, {
      input: {
        id: tagId,
        parent_ids: parentIds
      }
    });

    console.debug('[tagManager] updateTagParents result:', result?.tagUpdate);
    return result?.tagUpdate;
  }

  /**
   * Refresh hierarchy data and re-render the page
   */
  async function refreshHierarchy() {
    const container = document.querySelector('.tag-hierarchy-container');
    if (!container) return;

    try {
      hierarchyTags = await fetchAllTagsWithHierarchy();
      hierarchyTree = buildTagTree(hierarchyTags);
      hierarchyStats = getTreeStats(hierarchyTags);
      renderHierarchyPage(container);
    } catch (err) {
      console.error('[tagManager] Failed to refresh hierarchy:', err);
      showToast('Failed to refresh hierarchy', 'error');
    }
  }

  /**
   * Remove a specific parent from a tag
   */
  async function removeParent(tagId, parentIdToRemove) {
    const tag = hierarchyTags.find(t => t.id === tagId);
    const parent = hierarchyTags.find(t => t.id === parentIdToRemove);
    if (!tag || !parent) return;

    // Enter edit mode if not already
    enterEditMode();

    // Queue the change (returns false if it cancelled out an existing change)
    const wasAdded = addPendingChange('remove-parent', tagId, tag.name, parentIdToRemove, parent.name);

    if (wasAdded) {
      // Update local state for immediate visual feedback
      applyPendingChangesToTree();
      showToast(`Queued: Remove "${tag.name}" from "${parent.name}"`);
    }
  }

  /**
   * Make a tag a root by removing all its parents
   */
  async function makeRoot(tagId) {
    const tag = hierarchyTags.find(t => t.id === tagId);
    if (!tag) return;

    if (!tag.parents || tag.parents.length === 0) {
      showToast('Tag is already a root');
      return;
    }

    // Enter edit mode if not already
    enterEditMode();

    // Queue removal of each parent
    let anyAdded = false;
    for (const parent of tag.parents) {
      const wasAdded = addPendingChange('remove-parent', tagId, tag.name, parent.id, parent.name);
      if (wasAdded) anyAdded = true;
    }

    if (anyAdded) {
      // Update local state for immediate visual feedback
      applyPendingChangesToTree();
      showToast(`Queued: Make "${tag.name}" a root tag`);
    }
  }

  /**
   * Handle context menu action
   */
  async function handleContextMenuAction(e) {
    e.stopPropagation();
    const action = e.target.dataset.action;
    const parentIdToRemove = e.target.dataset.parentId;

    if (!contextMenuTag) return;

    // Save references before hideContextMenu() clears them
    const tag = contextMenuTag;
    const tagId = contextMenuTag.id;

    hideContextMenu();

    switch (action) {
      case 'add-parent':
        showTagSearchDialog('parent', tag);
        break;
      case 'add-child':
        showTagSearchDialog('child', tag);
        break;
      case 'remove-parent':
        await removeParent(tagId, parentIdToRemove);
        break;
      case 'make-root':
        await makeRoot(tagId);
        break;
    }
  }

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

    // Multi-parent badge
    const parentCount = node.parents?.length || 0;
    const multiParentBadge = parentCount > 1
      ? `<span class="th-multi-parent-badge" title="Appears under ${parentCount} parents">${parentCount} parents</span>`
      : '';

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

    // Parent context attribute for correct context menu behavior
    const parentAttr = node.parentContextId ? `data-parent-context="${node.parentContextId}"` : '';

    return `
      <div class="th-node ${isRoot ? 'th-root' : ''}" data-tag-id="${node.id}" ${parentAttr} draggable="true">
        <div class="th-node-content">
          <span class="th-toggle ${hasChildren ? '' : 'th-leaf'}" data-tag-id="${node.id}">${toggleIcon}</span>
          ${imageHtml}
          <div class="th-info">
            <a href="/tags/${node.id}" class="th-name">${escapeHtml(node.name)}</a>${multiParentBadge}
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
    // Log sample of hierarchy data for debugging
    const sampleNodes = hierarchyTree.slice(0, 3).map(root => ({
      id: root.id,
      name: root.name,
      parentContextId: root.parentContextId,
      childCount: root.childNodes.length,
      sampleChildren: root.childNodes.slice(0, 2).map(c => ({
        id: c.id,
        name: c.name,
        parentContextId: c.parentContextId
      }))
    }));
    console.debug('[tagManager] renderHierarchyPage: Sample tree structure:', sampleNodes);

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
        <div class="th-root-drop-zone" id="th-root-drop-zone">
          Drop here to make root tag
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
   * Keyboard shortcuts
   */
  function handleHierarchyKeyboard(e) {
    // Only handle if hierarchy page is active
    if (!document.querySelector('.tag-hierarchy-container')) return;

    // Ctrl+C - copy selected tag
    if (e.ctrlKey && e.key === 'c' && selectedTagId) {
      e.preventDefault();
      copiedTagId = selectedTagId;

      // Visual feedback
      const container = document.querySelector('.tag-hierarchy-container');
      container?.querySelectorAll('.th-node.th-copied').forEach(n => {
        n.classList.remove('th-copied');
      });
      container?.querySelectorAll(`.th-node[data-tag-id="${copiedTagId}"]`).forEach(n => {
        n.classList.add('th-copied');
      });

      showToast('Tag copied - select target and press Ctrl+V to add as child');
    }

    // Ctrl+V - paste (add copied tag as child of selected)
    if (e.ctrlKey && e.key === 'v' && copiedTagId && selectedTagId && copiedTagId !== selectedTagId) {
      e.preventDefault();

      if (wouldCreateCircularRef(selectedTagId, copiedTagId)) {
        showToast('Cannot create circular reference', 'error');
        return;
      }

      addParent(copiedTagId, selectedTagId);
    }

    // Delete/Backspace - remove selected tag from its current parent
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedTagId) {
      // Don't handle if typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      e.preventDefault();
      const selectedNode = document.querySelector(`.th-node.th-selected[data-tag-id="${selectedTagId}"]`);
      // Use parentContext data attribute for correct parent identification
      const parentId = selectedNode?.dataset.parentContext || null;

      if (parentId) {
        removeParent(selectedTagId, parentId);
      } else {
        showToast('Tag is already a root');
      }
    }

    // Escape - clear selection
    if (e.key === 'Escape') {
      selectedTagId = null;
      copiedTagId = null;
      const container = document.querySelector('.tag-hierarchy-container');
      container?.querySelectorAll('.th-node.th-selected, .th-node.th-copied').forEach(n => {
        n.classList.remove('th-selected', 'th-copied');
      });
    }
  }

  // Note: Keyboard handler is registered/unregistered in TagHierarchyPage component

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

    // Context menu on right-click
    container.querySelectorAll('.th-node').forEach(node => {
      node.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation(); // Prevent bubbling to parent .th-node elements
        const tagId = node.dataset.tagId;
        // Use parentContextId from the node's data attribute (set during tree building)
        const parentId = node.dataset.parentContext || null;
        console.debug('[tagManager] Context menu:', { tagId, parentId, datasetKeys: Object.keys(node.dataset), parentContext: node.dataset.parentContext });
        showContextMenu(e.clientX, e.clientY, tagId, parentId);
      });
    });

    // Highlight all instances of a tag on hover
    container.querySelectorAll('.th-node').forEach(node => {
      node.addEventListener('mouseenter', () => {
        const tagId = node.dataset.tagId;
        container.querySelectorAll(`.th-node[data-tag-id="${tagId}"]`).forEach(n => {
          n.classList.add('th-highlighted');
        });
      });

      node.addEventListener('mouseleave', () => {
        container.querySelectorAll('.th-node.th-highlighted').forEach(n => {
          n.classList.remove('th-highlighted');
        });
      });
    });

    // Drag and drop handlers
    container.querySelectorAll('.th-node').forEach(node => {
      node.addEventListener('dragstart', (e) => {
        draggedTagId = node.dataset.tagId;
        // Use parentContext data attribute for correct parent identification
        draggedFromParentId = node.dataset.parentContext || null;
        node.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', draggedTagId);
      });

      node.addEventListener('dragend', () => {
        node.classList.remove('dragging');
        draggedTagId = null;
        draggedFromParentId = null;
        // Clear all drag-over states
        container.querySelectorAll('.drag-over, .drag-invalid').forEach(el => {
          el.classList.remove('drag-over', 'drag-invalid');
        });
      });

      node.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (!draggedTagId || node.dataset.tagId === draggedTagId) return;

        const targetId = node.dataset.tagId;
        const wouldCircle = wouldCreateCircularRef(targetId, draggedTagId);

        node.classList.remove('drag-over', 'drag-invalid');
        node.classList.add(wouldCircle ? 'drag-invalid' : 'drag-over');
      });

      node.addEventListener('dragleave', () => {
        node.classList.remove('drag-over', 'drag-invalid');
      });

      node.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.remove('drag-over', 'drag-invalid');

        if (!draggedTagId || node.dataset.tagId === draggedTagId) return;

        const targetId = node.dataset.tagId;
        if (wouldCreateCircularRef(targetId, draggedTagId)) {
          showToast('Cannot create circular reference', 'error');
          return;
        }

        // Add target as parent of dragged tag
        await addParent(draggedTagId, targetId);
      });
    });

    // Root drop zone handler
    const rootDropZone = container.querySelector('#th-root-drop-zone');
    if (rootDropZone) {
      rootDropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (draggedTagId) {
          rootDropZone.classList.add('drag-over');
        }
      });

      rootDropZone.addEventListener('dragleave', () => {
        rootDropZone.classList.remove('drag-over');
      });

      rootDropZone.addEventListener('drop', async (e) => {
        e.preventDefault();
        rootDropZone.classList.remove('drag-over');

        if (!draggedTagId) return;

        // If dragged from a specific parent, just remove that parent
        if (draggedFromParentId) {
          await removeParent(draggedTagId, draggedFromParentId);
        } else {
          // Make completely root
          await makeRoot(draggedTagId);
        }
      });
    }

    // Click to select (for keyboard operations)
    container.querySelectorAll('.th-node-content').forEach(content => {
      content.addEventListener('click', (e) => {
        // Don't select if clicking on a link or toggle
        if (e.target.closest('a') || e.target.closest('.th-toggle')) return;

        const node = content.closest('.th-node');
        const tagId = node?.dataset.tagId;
        if (!tagId) return;

        // Clear previous selection
        container.querySelectorAll('.th-node.th-selected').forEach(n => {
          n.classList.remove('th-selected');
        });

        // Select this node
        node.classList.add('th-selected');
        selectedTagId = tagId;
      });
    });
  }

  /**
   * Tag Hierarchy page component
   */
  function TagHierarchyPage() {
    const React = PluginApi.React;
    const containerRef = React.useRef(null);

    React.useEffect(() => {
      // Register keyboard handler for this page
      document.addEventListener('keydown', handleHierarchyKeyboard);

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

      // Cleanup: remove keyboard handler when component unmounts
      return () => {
        document.removeEventListener('keydown', handleHierarchyKeyboard);
      };
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
