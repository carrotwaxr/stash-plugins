(function () {
  "use strict";

  const PLUGIN_ID = "tagManager";
  const ROUTE_PATH = "/plugins/tag-manager";
  const HIERARCHY_ROUTE_PATH = "/plugins/tag-hierarchy";

  const STASHDB_ENDPOINT = "https://stashdb.org/graphql";
  const STASHDB_API_KEY = "";

  // Default settings are loaded from shared default_settings.json.
  let DEFAULTS = {};

  // State
  let settings = {};
  let stashBoxes = []; // Configured stash-box endpoints from Stash
  let selectedStashBox = null; // Currently selected stash-box
  let stashdbTags = null; // Cached tags for selected endpoint
  let cacheStatus = null; // Cache status for selected endpoint
  let localTags = []; // Local Stash tags
  let currentPage = 1;
  let isLoading = false;
  let isCacheLoading = false;
  let matchResults = {}; // Cache of tag_id -> matches
  let matchErrors = {}; // tag_id -> error text of its last failed search (no matchResults entry then)
  let fuzzyHintShown = false; // the "fuzzy matching unavailable" hint shows once per page visit
  let currentFilter = 'unmatched'; // 'unmatched', 'matched', or 'all'
  let categoryMappingsLoaded = false; // true once the stored mappings were read (or found empty)
  let pendingMappingDeletes = new Set(); // JSON [endpoint, category] deleted while not loaded
  let categoryMappings = {}; // { endpoint: { category_name: local_tag_id } } (see getCategoryMapping)
  let tagBlacklistRaw = ''; // Raw blacklist text as saved (for the editor)
  let blacklistPanelOpen = false;
  let blacklistDraft = null; // Unsaved editor text (null when the editor matches the saved text)
  let tagBlacklist = []; // Parsed blacklist patterns [{type: 'literal'|'regex', pattern: string, regex?: RegExp}]
  let activeTab = 'match'; // 'match' or 'browse'
  let browseCategory = null; // Selected category in browse view
  let selectedForImport = new Set(); // Tag IDs selected for import
  let browseSearchQuery = ''; // Search query for browse view
  let isImporting = false; // Guard against double-click on import
  let browseFilter = 'all'; // 'all', 'unlinked', or 'linked' for browse tab

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
   * Get URL for a plugin asset file.
   */
  function getPluginAssetUrl(assetPath) {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    const normalizedAssetPath = assetPath.replace(/^\/+/, "");
    return `${baseURL}plugin/${PLUGIN_ID}/assets/${normalizedAssetPath}`;
  }

  /**
   * F16: an app path under Stash's <base href> (e.g. "/stash/"), so links and
   * navigation work when Stash is served from a sub-path. Accepts paths with or
   * without a leading slash; never doubles slashes.
   *   stashPath("/tags/5") -> "/tags/5" (base "/") or "/stash/tags/5" (base "/stash/")
   */
  function stashPath(path) {
    const baseEl = document.querySelector("base");
    let base = (baseEl && baseEl.getAttribute("href")) || "/";
    if (/^([a-z][a-z\d+.-]*:)?\/\//i.test(base)) { // absolute or protocol-relative
      try { base = new URL(base, window.location.href).pathname; } catch (e) { /* keep the raw href */ }
    }
    let prefix = base.replace(/\/+$/, "");
    if (prefix && !prefix.startsWith("/")) prefix = `/${prefix}`;
    const rel = String(path == null ? "" : path).replace(/^\/+/, "");
    return `${prefix}/${rel}`;
  }

  /**
   * F16: navigate inside Stash's single-page app (react-router's BrowserRouter
   * listens for popstate) instead of a full page load. `path` is un-based
   * ("/tags/5"); stashPath adds the base. Falls back to a normal load only if
   * the History API refuses.
   */
  function navigateTo(path) {
    const url = stashPath(path);
    try {
      window.history.pushState({}, "", url);
      // history v4 ignores a popstate whose state is undefined, so pass one.
      window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
    } catch (e) {
      console.warn("[tagManager] In-app navigation failed; loading the page instead:", e);
      window.location.href = url;
    }
  }

  /**
   * F16: delegated click handler for in-page links that carry
   * data-tm-route="/tags/5" (their href is the based path, so middle-click and
   * open-in-new-tab still work). A plain left click navigates in the SPA; any
   * modifier key, non-left button, target=_blank or an already-handled click is
   * left to the browser.
   */
  function handleInternalLinkClick(e) {
    if (!e || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target && typeof e.target.closest === "function" ? e.target.closest("a[data-tm-route]") : null;
    if (!link) return;
    const target = link.getAttribute("target");
    if (target && target !== "_self") return;
    const route = link.getAttribute("data-tm-route");
    if (!route) return;
    e.preventDefault();
    navigateTo(route);
  }

  /** href + data-tm-route attributes for an in-page link to a Stash path. */
  function internalLinkAttrs(path) {
    return `href="${escapeHtml(stashPath(path))}" data-tm-route="${escapeHtml(path)}"`;
  }

  // Used when default_settings.json could not be loaded (DEFAULTS is {}).
  const FALLBACK_NUMERIC_DEFAULTS = { fuzzyThreshold: 80, pageSize: 25 };

  /**
   * F18: parse an integer setting (radix 10). NaN or below `min` gives `def`;
   * above `max` is capped. 0 is a valid value when `min` allows it.
   */
  function parseIntSetting(value, def, min = -Infinity, max = Infinity) {
    const n = Number.parseInt(String(value), 10);
    if (Number.isNaN(n) || n < min) return def;
    return n > max ? max : n;
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
   * Load plugin config map from stash
   */
  async function getPluginConfig() {
    const query = `
      query Configuration {
        configuration {
          plugins
        }
      }
    `;
    const data = await graphqlRequest(query);
    return data?.configuration?.plugins?.[PLUGIN_ID] || {};
  }

  /**
   * Serialize async config writes so concurrent read-merge-write patches can't
   * clobber each other. Each enqueued task runs after the prior one settles and
   * resolves to its own result; a failure never poisons the chain.
   */
  function createConfigWriteQueue() {
    let chain = Promise.resolve();
    return function enqueue(task) {
      const result = chain.then(task, task);
      chain = result.then(() => {}, () => {});
      return result;
    };
  }
  const _enqueueConfigWrite = createConfigWriteQueue();

  /**
   * Verify that every key in `written` round-tripped into the persisted `readback`.
   */
  function valuesPersisted(written, readback) {
    return Object.keys(written).every(
      (k) => JSON.stringify(readback?.[k]) === JSON.stringify(written[k])
    );
  }

  /**
   * Merge `partialInput` into the current plugin settings and persist.
   *
   * Stash overwrites `plugins.settings.<pluginId>` on each `configurePlugin` call,
   * so we load the existing map, shallow-merge, then send the full object. Writes
   * are serialized through a single queue: each patch merges onto the latest
   * persisted config (not a stale snapshot), so concurrent savers don't clobber.
   *
   * @param {Record<string, unknown>} partialInput
   * @returns {Promise<Record<string, unknown>>} The saved settings map
   */
  async function _writeFullConfig(next) {
    const mutation = `
      mutation ConfigurePlugin($plugin_id: ID!, $input: Map!) {
        configurePlugin(plugin_id: $plugin_id, input: $input)
      }
    `;
    await graphqlRequest(mutation, { plugin_id: PLUGIN_ID, input: next });
    return next;
  }

  function savePluginConfigPatch(partialInput) {
    return _enqueueConfigWrite(async () => {
      const current = await getPluginConfig();
      return _writeFullConfig({ ...current, ...partialInput });
    });
  }

  /**
   * Bootstrap plugin defaults from `default_settings.json`.
   *
   * This runs once during startup, caches the defaults in memory, and backfills
   * only missing keys in Stash plugin configuration. Existing user values are
   * preserved.
   */
  async function initializeDefaultSettings() {
    // Load default settings
    const response = await fetch(getPluginAssetUrl("default_settings.json"), {
      method: "GET",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch default settings file: ${response.status}`);
    }

    DEFAULTS = await response.json();
    if (Object.keys(settings).length === 0) {
      settings = { ...DEFAULTS };
    }

    // Backfill missing defaults atomically inside the config-write queue: read the
    // LATEST persisted config and write in one critical section, so this can never
    // clobber a concurrent user save (e.g. category mappings) with a stale default.
    await _enqueueConfigWrite(async () => {
      const pluginConfig = await getPluginConfig();
      const missingDefaults = {};
      for (const [key, value] of Object.entries(DEFAULTS)) {
        if (pluginConfig[key] === undefined || pluginConfig[key] === null) {
          missingDefaults[key] = value;
        }
      }
      if (Object.keys(missingDefaults).length > 0) {
        await _writeFullConfig({ ...pluginConfig, ...missingDefaults });
        console.info("[tagManager] Persisted missing defaults:", Object.keys(missingDefaults));
      }
    });
  }

  /**
   * Idempotent gate so the backfill runs exactly once and callers can await it
   * (ensures DEFAULTS is populated before settings are read). Never rejects.
   */
  let _initDefaultsPromise = null;
  function ensureDefaultsInitialized() {
    if (!_initDefaultsPromise) {
      _initDefaultsPromise = initializeDefaultSettings().catch((e) => {
        console.error("[tagManager] initializeDefaultSettings failed:", e);
      });
    }
    return _initDefaultsPromise;
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
                name
              }
            }
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID] || {};

      settings = {
        stashdbEndpoint: pluginConfig.stashdbEndpoint || STASHDB_ENDPOINT,
        stashdbApiKey: pluginConfig.stashdbApiKey || STASHDB_API_KEY,
        enableFuzzySearch: pluginConfig.enableFuzzySearch ?? DEFAULTS.enableFuzzySearch,
        enableSynonymSearch: pluginConfig.enableSynonymSearch ?? DEFAULTS.enableSynonymSearch,
        // F18: 0 is a valid threshold; a missing/unusable DEFAULTS falls back to constants.
        fuzzyThreshold: parseIntSetting(pluginConfig.fuzzyThreshold,
          parseIntSetting(DEFAULTS.fuzzyThreshold, FALLBACK_NUMERIC_DEFAULTS.fuzzyThreshold, 0, 100), 0, 100),
        pageSize: parseIntSetting(pluginConfig.pageSize,
          parseIntSetting(DEFAULTS.pageSize, FALLBACK_NUMERIC_DEFAULTS.pageSize, 1), 1),
        preferStashBoxName: pluginConfig.preferStashBoxName ?? DEFAULTS.preferStashBoxName,
        preferStashBoxDescription: pluginConfig.preferStashBoxDescription ?? DEFAULTS.preferStashBoxDescription,
        leaveParentTagsAlone: pluginConfig.leaveParentTagsAlone ?? DEFAULTS.leaveParentTagsAlone,
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

  // Category mappings were one flat map, and the plugin historically targeted StashDB.
  const LEGACY_MAPPINGS_ENDPOINT = STASHDB_ENDPOINT;

  const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const isMappingId = (v) => typeof v === "string" || (typeof v === "number" && Number.isFinite(v));
  const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  /**
   * F20: normalize stored category mappings to the per-endpoint shape
   * { endpoint: { categoryName: localTagId } }. Pure: returns a new object.
   *   - a string/number value is a legacy flat entry { category: id }; it moves
   *     under `legacyEndpoint`, unless that endpoint's nested map already has the
   *     category (the nested entry wins).
   *   - a plain-object value is an endpoint map; its string/number ids are kept.
   *   - ids are stored as strings (local tag ids are strings).
   *   - anything else (null, booleans, arrays) is dropped with a console.warn.
   *   - a non-object input gives {}.
   * An already-nested map, or {}, comes back unchanged.
   */
  function migrateCategoryMappings(raw, legacyEndpoint = LEGACY_MAPPINGS_ENDPOINT) {
    if (!isPlainObject(raw)) {
      if (raw !== null && raw !== undefined) {
        console.warn("[tagManager] Category mappings are not an object; ignoring them:", raw);
      }
      return {};
    }
    const out = {};
    const legacy = {};
    const dropped = [];
    for (const [key, value] of Object.entries(raw)) {
      if (isMappingId(value)) {
        legacy[key] = String(value);
      } else if (isPlainObject(value)) {
        const inner = {};
        for (const [category, id] of Object.entries(value)) {
          if (isMappingId(id)) inner[category] = String(id);
          else dropped.push(`${key} / ${category}`);
        }
        out[key] = inner;
      } else {
        dropped.push(key);
      }
    }
    if (Object.keys(legacy).length > 0) {
      const target = out[legacyEndpoint] || {};
      const overridden = Object.keys(legacy).filter(c => hasOwn(target, c) && target[c] !== legacy[c]);
      if (overridden.length > 0) {
        console.warn(`[tagManager] Legacy category mappings for ${overridden.join(", ")} conflict with ` +
          `newer ${legacyEndpoint} mappings; keeping the newer ones.`);
      }
      out[legacyEndpoint] = { ...legacy, ...target };
    }
    if (dropped.length > 0) {
      console.warn("[tagManager] Dropped unreadable category mapping entries:", dropped);
    }
    return out;
  }

  /** F20: the saved local parent tag id for `category` on `endpoint`, or undefined. */
  function getCategoryMapping(endpoint, category) {
    if (!endpoint || !category || !hasOwn(categoryMappings, endpoint)) return undefined;
    const map = categoryMappings[endpoint];
    return isPlainObject(map) && hasOwn(map, category) ? map[category] : undefined;
  }

  /** F20: remember `category` -> local tag `id` for `endpoint` (in memory; save separately). */
  function setCategoryMapping(endpoint, category, id) {
    if (!endpoint || !category || id === null || id === undefined) return;
    if (!hasOwn(categoryMappings, endpoint) || !isPlainObject(categoryMappings[endpoint])) {
      categoryMappings[endpoint] = {};
    }
    categoryMappings[endpoint][category] = String(id);
    pendingMappingDeletes.delete(JSON.stringify([endpoint, category]));
  }

  /** F20: forget one endpoint's mapping for `category`; drops the endpoint map once empty. */
  function deleteCategoryMapping(endpoint, category) {
    if (!endpoint || !category) return;
    // Not loaded: the stored value may still hold it, so remember to remove it on merge.
    if (!categoryMappingsLoaded) pendingMappingDeletes.add(JSON.stringify([endpoint, category]));
    if (!hasOwn(categoryMappings, endpoint)) return;
    const map = categoryMappings[endpoint];
    if (isPlainObject(map)) delete map[category];
    if (!isPlainObject(map) || Object.keys(map).length === 0) delete categoryMappings[endpoint];
  }

  /**
   * Load category mappings from plugin settings. F20: a legacy flat map is
   * migrated to the per-endpoint shape (under StashDB) and saved once. If that
   * save fails, the migrated map stays in memory: every later save writes the
   * whole map, and the next load migrates the stored flat map again, so nothing
   * is lost.
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
        let parsed;
        try {
          parsed = typeof pluginConfig.categoryMappings === "string"
            ? JSON.parse(pluginConfig.categoryMappings)
            : pluginConfig.categoryMappings;
        } catch (e) {
          console.warn("[tagManager] Stored category mappings are unreadable and will be " +
            "replaced on the next save:", e);
          categoryMappings = {};
          categoryMappingsLoaded = true;
          return;
        }
        categoryMappings = migrateCategoryMappings(parsed, LEGACY_MAPPINGS_ENDPOINT);
        categoryMappingsLoaded = true;
        console.debug("[tagManager] Loaded category mappings for endpoints:", Object.keys(categoryMappings).length);
        if (JSON.stringify(categoryMappings) !== JSON.stringify(parsed)) {
          console.info("[tagManager] Migrating category mappings to the per-endpoint shape");
          const saved = await saveCategoryMappings({ quiet: true });
          if (!saved) {
            console.warn("[tagManager] Could not save the migrated category mappings; " +
              "using them in memory and retrying on the next save.");
          }
        }
      } else {
        categoryMappingsLoaded = true; // nothing stored yet
      }
    } catch (e) {
      console.error("[tagManager] Failed to load category mappings:", e);
    }
  }

  /**
   * Save category mappings to plugin settings. `quiet` skips the error toast
   * (the load-time migration save, where nothing is lost on failure).
   */
  async function saveCategoryMappings({ quiet = false } = {}) {
    if (!categoryMappingsLoaded) {
      // The earlier load failed: merge onto what is stored instead of replacing it.
      try {
        const stored = (await getPluginConfig()).categoryMappings;
        let base = {};
        if (stored) {
          try {
            base = migrateCategoryMappings(
              typeof stored === "string" ? JSON.parse(stored) : stored, LEGACY_MAPPINGS_ENDPOINT);
          } catch (e) {
            console.warn("[tagManager] Stored category mappings are unreadable and will be " +
              "replaced on the next save:", e);
          }
        }
        for (const key of pendingMappingDeletes) {
          const [endpoint, category] = JSON.parse(key);
          if (isPlainObject(base[endpoint])) {
            delete base[endpoint][category];
            if (Object.keys(base[endpoint]).length === 0) delete base[endpoint];
          }
        }
        for (const [endpoint, map] of Object.entries(categoryMappings)) {
          if (!isPlainObject(map)) continue;
          base[endpoint] = { ...(isPlainObject(base[endpoint]) ? base[endpoint] : {}), ...map };
        }
        categoryMappings = base;
        categoryMappingsLoaded = true;
        pendingMappingDeletes = new Set();
      } catch (e) {
        console.error("[tagManager] Could not re-read stored category mappings; not saving:", e);
        if (!quiet && typeof showToast === "function") {
          showToast("Failed to save category mappings — they may not persist.", "error");
        }
        return false;
      }
    }
    const written = { categoryMappings: JSON.stringify(categoryMappings) };
    try {
      await savePluginConfigPatch(written);
      // Verify it actually round-tripped — a silent drop was the root of #122.
      const readback = await getPluginConfig();
      if (!valuesPersisted(written, readback)) {
        throw new Error("category mappings did not round-trip");
      }
      console.debug("[tagManager] Saved category mappings");
      return true;
    } catch (e) {
      console.error("[tagManager] Failed to save category mappings:", e);
      if (!quiet && typeof showToast === "function") {
        showToast("Failed to save category mappings — they may not persist.", "error");
      }
      return false;
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
            parents {
              id
              name
            }
          }
        }
      }
    `;

    const data = await graphqlRequest(query);
    return data?.findTags?.tags || [];
  }

  // --- #124 reactivity: keep localTags in sync with the server ----------------
  let _activeContainer = null;   // set while the Tag Manager page is mounted
  let _refreshTimer = null;

  /**
   * Drop selections for tags that no longer exist (e.g. merged away). Pure.
   */
  function reconcileSelections(prevSelectedIds, freshTags) {
    const ids = new Set(freshTags.map((t) => t.id));
    const next = new Set();
    for (const id of prevSelectedIds) {
      if (ids.has(id)) next.add(id);
    }
    return next;
  }

  /**
   * #125: The browse import selection holds StashDB tag ids (uuids) — a different
   * id space than local Stash tags (integers). Reconcile it against the loaded
   * StashDB set only: reconciling against localTags drops every selection (a uuid
   * never equals a local integer id), silently emptying an in-progress import.
   * A null cache (not loaded yet) leaves the selection untouched. Pure.
   */
  function reconcileImportSelection(selectedForImport, stashdbTags) {
    if (!stashdbTags) return new Set(selectedForImport);
    return reconcileSelections(selectedForImport, stashdbTags);
  }

  /**
   * Re-fetch local tags and re-render so the UI reflects changes made here or in
   * another tab (#124). Modals live on document.body, so re-rendering the page
   * container is safe under an open modal; only an active import loop defers it
   * (the import does its own render, then calls this for server-truth reconcile).
   */
  async function refreshLocalTags() {
    if (!_activeContainer || isImporting) return;
    try {
      localTags = await fetchLocalTags();
      selectedForImport = reconcileImportSelection(selectedForImport, stashdbTags);
      if (_activeContainer) renderPage(_activeContainer);
    } catch (e) {
      console.error("[tagManager] Failed to refresh local tags:", e);
    }
  }

  /** Debounced refresh for focus/visibility events. */
  function scheduleRefresh() {
    if (_refreshTimer) clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(() => {
      _refreshTimer = null;
      refreshLocalTags();
    }, 300);
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
          aliases
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

    // Use selected stash-box or fall back to plugin settings. The backend looks
    // up the API key for this endpoint in Stash's config.
    const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;

    console.debug(`[tagManager] callBackend mode=${mode} endpoint=${endpoint}`);

    const { stashdbApiKey, ...backendSettings } = settings;
    const fullArgs = {
      mode,
      stashdb_url: endpoint,
      settings: backendSettings,
      ...args,
    };

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: fullArgs,
    });

    const output = data?.runPluginOperation;
    if (output?.error) {
      const err = new Error(output.error);
      err.output = output; // keep auth_error etc. for formatBackendError
      throw err;
    }

    return output;
  }

  /**
   * Human-readable text for a backend error output ({error, auth_error}).
   */
  function formatBackendError(output, boxName) {
    if (!output || typeof output !== 'object' || !output.error) return 'Unknown error';
    if (output.auth_error) {
      const name = boxName || selectedStashBox?.name || 'The stash-box';
      return `${name} rejected the API key (${output.error}). Check the API key in Settings → Metadata Providers.`;
    }
    return String(output.error);
  }

  function backendErrorText(e) {
    return formatBackendError(e?.output || { error: e?.message });
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
   * Parse blacklist text into pattern objects. Same rules as blacklist.py
   * (both run tests/blacklist_cases.json): separators are newline, comma and
   * semicolon; /body/flags is a regex; a "/" entry with no valid closing "/"
   * is the legacy form (rest of the line is the body).
   */
  function parseBlacklist(blacklistStr) {
    if (!blacklistStr) return [];
    const text = String(blacklistStr);
    const isSep = (c) => c === '\n' || c === ',' || c === ';';
    const isSpace = (c) => /\s/.test(c);
    const out = [];
    const n = text.length;
    let i = 0;

    const addRegex = (body, flags, raw) => {
      if (!body) return;
      let jsFlags = 'i';
      for (const f of flags) {
        if (f === 'm' || f === 's') {
          if (!jsFlags.includes(f)) jsFlags += f;
        } else if (f !== 'i') {
          console.warn(`[tagManager] Unknown regex flag '${f}' in blacklist: ${raw}`);
        }
      }
      try {
        out.push({ type: 'regex', pattern: body, regex: new RegExp(body, jsFlags) });
      } catch (e) {
        console.warn(`[tagManager] Invalid regex in blacklist: ${raw}`, e);
      }
    };

    while (i < n) {
      const ch = text[i];
      if (isSpace(ch) || isSep(ch)) { i++; continue; }

      let eol = text.indexOf('\n', i);
      if (eol === -1) eol = n;

      if (ch === '/') {
        let j = i + 1;
        let close = -1;
        while (j < eol) {
          if (text[j] === '\\' && j + 1 < eol) { j += 2; continue; }
          if (text[j] === '/') { close = j; break; }
          j++;
        }
        if (close !== -1) {
          let k = close + 1;
          while (k < n && text[k] >= 'a' && text[k] <= 'z') k++;
          if (k === n || isSpace(text[k]) || isSep(text[k])) {
            addRegex(text.slice(i + 1, close), text.slice(close + 1, k), text.slice(i, k));
            i = k;
            continue;
          }
        }
        // Legacy form: rest of the line is the body (right-trimmed)
        const raw = text.slice(i, eol).replace(/\s+$/, '');
        addRegex(raw.slice(1), '', raw);
        i = eol;
        continue;
      }

      let end = i;
      while (end < n && !isSep(text[end])) end++;
      const raw = text.slice(i, end).trim();
      out.push({ type: 'literal', pattern: raw.toLowerCase() });
      i = end;
    }
    return out;
  }

  /**
   * Matches with blacklisted tags dropped; each entry keeps its index into
   * the original array so callers can address matchResults[tagId][index].
   */
  function visibleMatches(matches) {
    const out = [];
    (matches || []).forEach((match, index) => {
      if (!isBlacklisted(match.tag?.name)) out.push({ match, index });
    });
    return out;
  }

  /** First non-blacklisted match and its original index, or null. */
  function bestVisibleMatch(matches) {
    return visibleMatches(matches)[0] || null;
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

      tagBlacklistRaw = pluginConfig.tagBlacklist || '';
      tagBlacklist = parseBlacklist(tagBlacklistRaw);
      console.debug("[tagManager] Loaded blacklist:", tagBlacklist.length, "patterns");
    } catch (e) {
      console.error("[tagManager] Failed to load blacklist:", e);
    }
  }

  /**
   * Save the blacklist text through the config write queue, verify it
   * round-tripped, then reload tagBlacklist.
   */
  async function saveBlacklist(text) {
    const written = { tagBlacklist: text };
    try {
      await savePluginConfigPatch(written);
      const readback = await getPluginConfig();
      if (!valuesPersisted(written, readback)) {
        throw new Error("blacklist did not round-trip");
      }
      await loadBlacklist();
      return true;
    } catch (e) {
      console.error("[tagManager] Failed to save blacklist:", e);
      if (typeof showToast === "function") {
        showToast("Failed to save the blacklist — it may not persist.", "error");
      }
      return false;
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
      showStatus(`Cache refresh failed: ${backendErrorText(e)}`, 'error');
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
      cacheStatus = { exists: false, error: backendErrorText(e) };
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
    return indexFindByName(getLocalTagIndex(), name, excludeTagId) || null;
  }

  /**
   * #125: Detect name/alias collisions for an incoming stash-box tag against
   * local tags. Returns one record per colliding value (name or alias).
   * @param {object} stashdbTag - incoming tag { name, aliases }
   * @returns {Array<{conflictingValue: string, conflictingTag: object}>}
   */
  function detectImportConflicts(stashdbTag) {
    const conflicts = [];
    const values = [stashdbTag.name, ...(stashdbTag.aliases || [])];
    for (const value of values) {
      const conflictingTag = findConflictingTag(value, null);
      if (conflictingTag) {
        conflicts.push({ conflictingValue: value, conflictingTag });
      }
    }
    return conflicts;
  }

  /**
   * #125: Return the incoming tag's aliases with all colliding values removed,
   * plus the dropped values (for the "strip alias & import" action / UI note).
   * @param {object} stashdbTag - incoming tag { aliases }
   * @param {Array<{conflictingValue: string}>} conflicts
   * @returns {{aliases: string[], removed: string[]}}
   */
  function sanitizeAliasesForImport(stashdbTag, conflicts) {
    const dropped = new Set(conflicts.map(c => c.conflictingValue.toLowerCase()));
    const kept = [];
    const removed = [];
    for (const alias of (stashdbTag.aliases || [])) {
      if (dropped.has(alias.toLowerCase())) removed.push(alias);
      else kept.push(alias);
    }
    return { aliases: kept, removed };
  }

  /**
   * #125: Build the tagUpdate input that links an incoming stash-box entity to
   * the existing conflicting tag (the "merge into existing" action): replace the
   * stash_id for this endpoint, add the incoming name + non-conflicting aliases,
   * and add the resolved parent if missing. No deletion.
   * @returns {object} tagUpdate input
   */
  function buildMergeIntoExistingInput(existingTag, stashdbTag, conflicts, endpoint, stashdbId, parentId) {
    const conflictVals = new Set(conflicts.map(c => c.conflictingValue.toLowerCase()));
    const existingLower = new Set([
      existingTag.name.toLowerCase(),
      ...(existingTag.aliases || []).map(a => a.toLowerCase()),
    ]);
    // candidate aliases to add: incoming name + incoming aliases, minus conflicts and dupes
    const candidates = [stashdbTag.name, ...(stashdbTag.aliases || [])];
    const addAliases = [];
    for (const v of candidates) {
      const low = v.toLowerCase();
      if (conflictVals.has(low) || existingLower.has(low) || addAliases.some(a => a.toLowerCase() === low)) continue;
      addAliases.push(v);
    }
    const filteredStashIds = (existingTag.stash_ids || []).filter(s => s.endpoint !== endpoint);
    const input = {
      id: existingTag.id,
      aliases: [...(existingTag.aliases || []), ...addAliases],
      stash_ids: [...filteredStashIds, { endpoint, stash_id: stashdbId }],
    };
    const existingParents = (existingTag.parents || []).map(p => p.id);
    if (parentId && !existingParents.includes(parentId)) {
      input.parent_ids = [...existingParents, parentId];
    }
    return input;
  }

  /**
   * #125: Build the import summary line, including distinct conflict/skipped
   * counts alongside the existing created/linked/parented/errors tallies.
   * @param {object} c - { created, linked, parented, categories, conflicts, skipped, errors }
   * @returns {string}
   */
  function summarizeImportResult(c) {
    const parts = [];
    if (c.created > 0) parts.push(`Created ${c.created} tag${c.created !== 1 ? 's' : ''}`);
    if (c.linked > 0) parts.push(`linked ${c.linked} existing`);
    if (c.parented > 0) parts.push(`set parents for ${c.parented} (${c.categories} ${c.categories === 1 ? 'category' : 'categories'})`);
    if (c.conflicts > 0) parts.push(`${c.conflicts} conflict${c.conflicts !== 1 ? 's' : ''} resolved`);
    if (c.skipped > 0) parts.push(`${c.skipped} skipped`);
    if (c.errors > 0) parts.push(`${c.errors} error${c.errors !== 1 ? 's' : ''}`);
    return parts.length ? parts.join(', ') : 'No changes';
  }

  /**
   * F5: does this Stash accept `TagsMergeInput.values` (Stash v0.31+)? With it,
   * the merge and the destination's update run in ONE transaction. A successful
   * answer is cached; a failed introspection counts as "no" and is retried on the
   * next call.
   * @returns {Promise<boolean>}
   */
  let _mergeValuesSupport = null; // Promise<boolean> once introspection is in flight/succeeded
  async function supportsMergeValues() {
    if (!_mergeValuesSupport) {
      const pending = graphqlRequest(`
        query TagsMergeInputFields {
          __type(name: "TagsMergeInput") { inputFields { name } }
        }
      `).then(data => (data?.__type?.inputFields || []).some(f => f.name === 'values'));
      _mergeValuesSupport = pending;
      pending.catch(() => {
        if (_mergeValuesSupport === pending) _mergeValuesSupport = null;
      });
    }
    try {
      return await _mergeValuesSupport;
    } catch (e) {
      console.warn('[tagManager] could not detect tagsMerge values support:', e);
      return false;
    }
  }

  /**
   * F5: fetch both merge sides fresh (aliases, stash_ids, parents, children) so
   * the destination's final values aren't built from a stale localTags.
   * @returns {Promise<{source: object|null, destination: object|null}>}
   */
  async function fetchTagsForMerge(sourceId, destinationId) {
    const fields = `
      id
      name
      aliases
      stash_ids { endpoint stash_id }
      parents { id }
      children { id }
    `;
    const data = await graphqlRequest(`
      query FindTagsForMerge($source: ID!, $destination: ID!) {
        source: findTag(id: $source) { ${fields} }
        destination: findTag(id: $destination) { ${fields} }
      }
    `, { source: sourceId, destination: destinationId });
    return { source: data?.source || null, destination: data?.destination || null };
  }

  /** Ids of a tag's parents/children, minus the two merge sides. */
  function relationIdsForMerge(list, source, destination) {
    return (list || []).map(r => String(r.id))
      .filter(id => id !== String(source.id) && id !== String(destination.id));
  }

  /**
   * The destination's parent ids after absorbing `source`: dest ∪ source parents
   * ∪ `addParentId` (optional), minus both merge sides. No direct cycles: a tag
   * that is already the destination's child can't also become its parent.
   * Deeper cycles are rejected by Stash's own hierarchy validation. Pure.
   */
  function mergeParentIds(source, destination, addParentId) {
    const destChildIds = new Set(relationIdsForMerge(destination.children, source, destination));
    return [...new Set([
      ...relationIdsForMerge(destination.parents, source, destination),
      ...relationIdsForMerge(source.parents, source, destination),
      ...relationIdsForMerge(addParentId ? [{ id: addParentId }] : [], source, destination),
    ])].filter(id => !destChildIds.has(id));
  }

  /**
   * F5: the destination's values after absorbing `source`. Stash's plain merge
   * moves scenes etc., the source name/aliases and stash_ids, but NOT parents,
   * children or description. With TagsMergeInput.values these override the
   * destination, so every field is spelled out in full:
   *   aliases    = dest ∪ source name ∪ source aliases ∪ sanitizedAliases, minus
   *                the destination name, deduped case-insensitively
   *   stash_ids  = dest ∪ source, with this endpoint replaced by the new link
   *   parent_ids = dest ∪ source parents ∪ `addParentId` (the dialog's parent
   *                choice, optional), minus both sides and dest's children
   *   child_ids  = dest ∪ source children, minus both sides and the new parents
   *   description only when `description` is given (no rename: never `name`).
   * Pure.
   */
  function buildMergeValues({ source, destination, stashdbTag, endpoint, sanitizedAliases, description, addParentId }) {
    const aliases = [];
    const seenAliases = new Set([String(destination.name || '').toLowerCase()]);
    const candidates = [
      ...(destination.aliases || []), source.name, ...(source.aliases || []), ...(sanitizedAliases || []),
    ];
    for (const alias of candidates) {
      if (!alias) continue;
      const key = String(alias).toLowerCase();
      if (seenAliases.has(key)) continue;
      seenAliases.add(key);
      aliases.push(alias);
    }

    const stashIds = [];
    const seenStashIds = new Set();
    for (const sid of [...(destination.stash_ids || []), ...(source.stash_ids || [])]) {
      if (!sid || sid.endpoint === endpoint) continue;
      const key = `${sid.endpoint}\u0000${sid.stash_id}`;
      if (seenStashIds.has(key)) continue;
      seenStashIds.add(key);
      stashIds.push({ endpoint: sid.endpoint, stash_id: sid.stash_id });
    }
    stashIds.push({ endpoint, stash_id: stashdbTag.id });

    // No direct cycles: a new parent can't also stay a child.
    const parentIds = mergeParentIds(source, destination, addParentId);
    const parentSet = new Set(parentIds);
    const childIds = [...new Set([
      ...relationIdsForMerge(destination.children, source, destination),
      ...relationIdsForMerge(source.children, source, destination),
    ])].filter(id => !parentSet.has(id));

    const values = {
      id: String(destination.id), // TagUpdateInput.id is required (ignored by tagsMerge)
      aliases,
      stash_ids: stashIds,
      parent_ids: parentIds,
      child_ids: childIds,
    };
    if (description !== undefined) values.description = description;
    return values;
  }

  /**
   * F5: ask before an irreversible merge, with what moves. Expects the source's
   * `parents`/`children` (fresh from fetchTagsForMerge); relations to either
   * merge side aren't counted. `addedParent` ({ name, isNew }) is a parent the
   * merge adds to the destination (the dialog's parent choice), if any.
   * @returns {Promise<boolean>}
   */
  async function confirmTagMerge(sourceTag, destinationTag, { addedParent = null } = {}) {
    const sceneCount = await getTagSceneCount(sourceTag.id);
    const childCount = relationIdsForMerge(sourceTag.children, sourceTag, destinationTag).length;
    const parentCount = relationIdsForMerge(sourceTag.parents, sourceTag, destinationTag).length;
    const count = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const parentNote = addedParent
      ? `, and "${addedParent.name}" is added as a parent${addedParent.isNew ? ' (a new tag)' : ''}`
      : '';
    return confirm(
      `Merge "${sourceTag.name}" into "${destinationTag.name}"?\n\n` +
      `${count(sceneCount, 'scene')}, ${count(childCount, 'child tag')} and ${count(parentCount, 'parent tag')} ` +
      `move to "${destinationTag.name}"${parentNote}. "${sourceTag.name}" will be deleted. This can't be undone.`
    );
  }

  /** Local state after the source tag is gone from the server. */
  function dropMergedSourceLocally(sourceId) {
    const sourceIdx = localTags.findIndex(t => t.id === sourceId);
    if (sourceIdx >= 0) {
      localTags.splice(sourceIdx, 1);
      localTagsChanged();
    }
    delete matchResults[sourceId];
  }

  /**
   * Create the category tag for a '__create__' parent choice and add it to
   * localTags. Throws if the create fails.
   * @returns {Promise<object>} the new local tag
   */
  async function createCategoryParentTag(categoryName) {
    const newParent = await createTag({ name: categoryName });
    if (!newParent?.id) throw new Error('the server returned no tag');
    const created = localTagFromCreated(newParent, { name: categoryName });
    localTags.push(created);
    localTagsChanged();
    console.debug(`[tagManager] Created parent tag: ${created.name}`);
    return created;
  }

  /**
   * Handle merging a source tag into a destination tag, then apply StashDB link.
   * Used by both pre-validation and API error merge handlers.
   *
   * F5: confirms first, then on Stash v0.31+ sends ONE tagsMerge whose `values`
   * set the destination's aliases, stash_ids, parents, children and optional
   * description in the same transaction. On v0.30 it merges, then updates; if
   * that update fails the merge has already happened, so the source is dropped
   * locally and the error says what to fix.
   *
   * The dialog's parent choice is applied the way Apply does it: unless
   * parents are left alone, a chosen parent for the stash-box tag's category is
   * added to the destination's parents. A '__create__' parent is created only
   * after the user confirms; if that fails, nothing is merged. A remembered
   * mapping is saved only once the merge (and on v0.30 the update) succeeded.
   *
   * @param {object} params - Merge parameters
   * @param {object} params.sourceTag - The tag being merged (will be deleted)
   * @param {string} params.destinationId - ID of the tag to merge into
   * @param {object} params.stashdbTag - The StashDB tag to link
   * @param {string} params.endpoint - The stash-box endpoint URL
   * @param {string[]} params.sanitizedAliases - Aliases to include in the merge
   * @param {HTMLElement} params.modal - The modal element (for reading description choice)
   * @param {HTMLElement} params.container - The container element (for re-rendering)
   * @param {?string} [params.parentId] - the dialog's parent: a tag id, '__create__', or null
   * @param {boolean} [params.rememberMapping] - save category -> parent for `endpoint`
   * @returns {Promise<{success: boolean, error?: string, cancelled?: boolean, merged?: boolean,
   *   createdParent?: object}>}
   *   `cancelled`: the user declined, nothing was sent. `merged`: the source is
   *   gone on the server even though the result is a failure. `createdParent`:
   *   the '__create__' parent, set whenever it was created (even on failure).
   */
  async function performTagMerge({
    sourceTag, destinationId, stashdbTag, endpoint, sanitizedAliases, modal, container, parentId = null, rememberMapping = false,
  }) {
    const destinationTag = localTags.find(t => t.id === destinationId);
    if (!destinationTag) {
      return { success: false, error: 'Could not find destination tag.' };
    }

    let fresh;
    try {
      fresh = await fetchTagsForMerge(sourceTag.id, destinationId);
    } catch (e) {
      console.error('[tagManager] Merge pre-check error:', e.message);
      return { success: false, error: `Could not load the tags to merge: ${e.message}` };
    }
    if (!fresh.source || !fresh.destination) {
      const missing = !fresh.source ? sourceTag.name : destinationTag.name;
      return { success: false, error: `"${missing}" no longer exists in Stash (it may have been merged or deleted). Refresh and try again.` };
    }
    const source = fresh.source;
    const destination = fresh.destination;

    // The dialog's parent choice, as Apply resolves it (#126: none when leaving
    // parents alone). Only a parent the merge really adds is announced.
    const categoryName = stashdbTag.category?.name || null;
    const parentChoice = shouldResolveParents(settings) && categoryName && parentId ? String(parentId) : null;
    let addedParent = null;
    if (parentChoice === '__create__') {
      addedParent = { name: categoryName, isNew: true };
    } else if (parentChoice &&
        mergeParentIds(source, destination, parentChoice).length > mergeParentIds(source, destination).length) {
      addedParent = { name: localTags.find(t => t.id === parentChoice)?.name || parentChoice, isNew: false };
    }

    if (!(await confirmTagMerge(source, destination, { addedParent }))) {
      return { success: false, cancelled: true };
    }

    // Create a '__create__' parent only now that the user has confirmed.
    let parentTagId = parentChoice === '__create__' ? null : parentChoice;
    let createdParent = null;
    if (parentChoice === '__create__') {
      try {
        createdParent = await createCategoryParentTag(categoryName);
      } catch (e) {
        console.error('[tagManager] Failed to create parent tag:', e);
        return { success: false, error: `Failed to create parent tag "${categoryName}": ${e.message} (nothing was merged)` };
      }
      parentTagId = createdParent.id;
    }

    // Apply description if user chose StashDB description
    const descChoice = modal.querySelector('input[name="tm-desc"]:checked')?.value;
    const values = buildMergeValues({
      source,
      destination,
      stashdbTag,
      endpoint,
      sanitizedAliases,
      description: descChoice === 'stashdb' && stashdbTag.description ? stashdbTag.description : undefined,
      addParentId: parentTagId,
    });

    try {
      if (await supportsMergeValues()) {
        // Stash v0.31+: merge + destination update in one transaction.
        try {
          await mergeTags([sourceTag.id], destinationId, values);
        } catch (e) {
          console.error('[tagManager] Merge error:', e.message);
          const unchanged = createdParent
            ? `nothing was merged; the new parent tag "${createdParent.name}" was kept`
            : 'nothing was changed';
          return { success: false, error: `${e.message} (${unchanged})`, createdParent };
        }
      } else {
        // Stash v0.30: two steps. The merge moves scenes, aliases and stash_ids
        // and deletes the source; the update adds the rest.
        await mergeTags([sourceTag.id], destinationId);
        try {
          await updateTag(values);
        } catch (e) {
          console.error('[tagManager] Post-merge update error:', e.message);
          // The source is gone on the server: don't keep offering it.
          dropMergedSourceLocally(sourceTag.id);
          try {
            renderPage(container);
          } catch (renderErr) {
            console.error('[tagManager] re-render after merge failed:', renderErr);
          }
          await refreshLocalTags();
          return {
            success: false,
            merged: true,
            createdParent,
            error: `Merged '${source.name}' into '${destination.name}', but updating '${destination.name}' failed: ${e.message}. ` +
              `The merge can't be undone; fix the conflict (e.g. rename the clashing alias) and edit '${destination.name}' in Stash.`,
          };
        }
      }

      // Update local state - remove the merged (source) tag and update destination
      dropMergedSourceLocally(sourceTag.id);

      const destIdx = localTags.findIndex(t => t.id === destinationId);
      if (destIdx >= 0) {
        localTags[destIdx].stash_ids = values.stash_ids;
        localTags[destIdx].aliases = values.aliases;
        localTagsChanged();
        if (values.description !== undefined) {
          localTags[destIdx].description = values.description;
        }
      }

      // Save the mapping only once the merge (and on v0.30 the update) succeeded
      if (rememberMapping && parentTagId) {
        setCategoryMapping(endpoint, categoryName, parentTagId);
        await saveCategoryMappings();
      }

      modal.remove();

      showStatus(`Merged "${sourceTag.name}" into "${destinationTag.name}" and linked to StashDB`, 'success');
      renderPage(container);
      await refreshLocalTags(); // #124: pull server truth after the merge

      return { success: true, createdParent };
    } catch (e) {
      console.error('[tagManager] Merge error:', e.message);
      return { success: false, error: e.message, createdParent };
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
   * #126: whether to create/assign category parent tags during sync/import.
   * False when the user has opted to leave their own tag hierarchy untouched.
   */
  function shouldResolveParents(settings) {
    return !settings.leaveParentTagsAlone;
  }

  /**
   * F19: whether the Parent row (select, Search, Remember) is rendered in the diff dialog.
   */
  function shouldShowParentControls(settings, hasCategory) {
    return !!hasCategory && !settings.leaveParentTagsAlone;
  }

  /**
   * F4: options for the diff dialog's parent dropdown. Exactly one is selected.
   * A saved mapping is offered (once) only if its tag still exists.
   * Values: '' = no parent, '__create__' = create the category tag, otherwise a tag id.
   *
   * @returns {{value: string, label: string, selected: boolean}[]}
   */
  function buildParentOptions({ existingParents = [], parentMatches = [], savedMappingId, categoryName, localTags: tags = [] }) {
    const saved = savedMappingId ? tags.find(t => t.id === savedMappingId) : null;
    const savedId = saved ? saved.id : null;
    const createLabel = `Create "${categoryName}"`;

    let selectedValue;
    if (savedId) selectedValue = savedId;
    else if (existingParents.length) selectedValue = existingParents[0].id;
    else if (parentMatches.length) selectedValue = parentMatches[0].tag.id;
    else selectedValue = '__create__';

    const opts = [];
    const add = (value, label) => opts.push({ value, label, selected: value === selectedValue });
    add('', '-- No parent --');
    if (saved) add(saved.id, `${saved.name} (saved mapping)`);
    existingParents.filter(p => p.id !== savedId).forEach(p => add(p.id, `${p.name} (current parent)`));
    if (!existingParents.length && !parentMatches.length && !savedId) add('__create__', createLabel);
    parentMatches
      .filter(m => m.tag.id !== savedId && !existingParents.some(p => p.id === m.tag.id))
      .forEach(m => add(m.tag.id, `${m.tag.name} (${m.matchType})`));
    if (existingParents.length || parentMatches.length || savedId) add('__create__', createLabel);
    return opts;
  }

  /**
   * Resolve parent tags for categories found among selected StashDB tags.
   * Returns: { categoryName: { parentTagId, parentTagName, resolution, description } }
   * resolution is one of: 'saved', 'exact', 'create'
   * F20: saved mappings are read for `endpoint` (default: the selected stash-box).
   */
  function resolveCategoryParents(selectedIds, endpoint = selectedStashBox?.endpoint) {
    const result = {};
    const stashdbById = new Map((stashdbTags || []).map(t => [t.id, t]));

    for (const stashdbId of selectedIds) {
      const tag = stashdbById.get(stashdbId);
      if (!tag?.category) continue;

      const catName = tag.category.name;
      if (result[catName]) continue;

      // 1. Check saved mapping
      const savedId = getCategoryMapping(endpoint, catName);
      if (savedId) {
        const savedTag = localTags.find(t => t.id === savedId);
        if (savedTag) {
          result[catName] = {
            parentTagId: savedTag.id,
            parentTagName: savedTag.name,
            resolution: 'saved',
            description: tag.category.description || '',
          };
          continue;
        }
      }

      // 2. Exact name match
      const matches = findLocalParentMatches(catName);
      const exactMatch = matches.find(m => m.matchType === 'exact');
      if (exactMatch) {
        result[catName] = {
          parentTagId: exactMatch.tag.id,
          parentTagName: exactMatch.tag.name,
          resolution: 'exact',
          description: tag.category.description || '',
        };
        continue;
      }

      // 3. Will create
      result[catName] = {
        parentTagId: null,
        parentTagName: catName,
        resolution: 'create',
        description: tag.category.description || '',
      };
    }

    return result;
  }

  /**
   * Show a modal previewing category-to-parent-tag resolutions before import.
   * @param {object} categoryResolutions - Output from resolveCategoryParents()
   * @returns {Promise<{parentMap, remember, resolutions}|null|'cancel'>}
   */
  function showCategoryPreviewModal(categoryResolutions) {
    return new Promise((resolve) => {
      const currentResolutions = JSON.parse(JSON.stringify(categoryResolutions));
      const catNames = Object.keys(currentResolutions);
      const backdrop = document.createElement('div');
      backdrop.className = 'tm-modal-backdrop';

      const statusLabels = {
        saved: 'Saved mapping',
        exact: 'Matched',
        create: 'Will create',
        manual: 'Manual',
      };
      const statusClasses = {
        saved: 'tm-cat-saved',
        exact: 'tm-cat-exact',
        create: 'tm-cat-create',
        manual: 'tm-cat-manual',
      };

      function buildRow(catName) {
        const info = currentResolutions[catName];
        const badge = `<span class="tm-cat-resolution ${statusClasses[info.resolution] || ''}">${escapeHtml(statusLabels[info.resolution] || info.resolution)}</span>`;
        return `<tr data-cat="${escapeHtml(catName)}">
          <td>${escapeHtml(catName)}</td>
          <td class="tm-cat-parent-name">${escapeHtml(info.parentTagName)}</td>
          <td class="tm-cat-status">${badge}</td>
          <td><button class="btn btn-secondary btn-sm tm-cat-change" data-cat="${escapeHtml(catName)}">Change</button></td>
        </tr>`;
      }

      backdrop.innerHTML = `
        <div class="tm-modal tm-modal-wide">
          <div class="tm-modal-header">
            <h3>Category Parents</h3>
            <button class="tm-close-btn">&times;</button>
          </div>
          <div class="tm-modal-body">
            <p class="tm-preview-intro">The selected tags belong to ${catNames.length} ${catNames.length === 1 ? 'category' : 'categories'}. Review the parent tag assignments below.</p>
            <table class="tm-category-preview-table">
              <thead>
                <tr>
                  <th>StashDB Category</th>
                  <th>Parent Tag</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${catNames.map(c => buildRow(c)).join('')}
              </tbody>
            </table>
            <label class="tm-remember-label">
              <input type="checkbox" id="tm-remember-mappings" checked>
              Remember these mappings
            </label>
          </div>
          <div class="tm-modal-footer">
            <button class="btn btn-secondary" id="tm-import-no-parents">Import without Parents</button>
            <button class="btn btn-primary" id="tm-import-with-parents">Import with Parents</button>
          </div>
        </div>
      `;

      document.body.appendChild(backdrop);

      function cleanup(result) {
        backdrop.remove();
        resolve(result);
      }

      // Close / cancel
      backdrop.querySelector('.tm-close-btn').addEventListener('click', () => cleanup('cancel'));
      backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) cleanup('cancel');
      });

      // Import without parents
      backdrop.querySelector('#tm-import-no-parents').addEventListener('click', () => cleanup(null));

      // Import with parents
      backdrop.querySelector('#tm-import-with-parents').addEventListener('click', () => {
        const remember = backdrop.querySelector('#tm-remember-mappings').checked;
        const parentMap = {};
        for (const [catName, info] of Object.entries(currentResolutions)) {
          parentMap[catName] = info.parentTagId;
        }
        cleanup({ parentMap, remember, resolutions: currentResolutions });
      });

      // Change buttons
      backdrop.querySelectorAll('.tm-cat-change').forEach(btn => {
        btn.addEventListener('click', () => {
          const catName = btn.dataset.cat;
          const row = backdrop.querySelector(`tr[data-cat="${CSS.escape(catName)}"]`);
          showCategoryParentSearch(catName, currentResolutions, row);
        });
      });
    });
  }

  /**
   * Show a tag search modal for changing a category's parent tag assignment.
   * Opens above the preview modal (z-index 1060).
   * @param {string} catName - Category name
   * @param {object} currentResolutions - Mutable resolutions object
   * @param {HTMLElement} row - Table row to update visually
   */
  function showCategoryParentSearch(catName, currentResolutions, row) {
    const searchBackdrop = document.createElement('div');
    searchBackdrop.className = 'tm-modal-backdrop tm-cat-search-backdrop';
    searchBackdrop.style.zIndex = '1060';

    searchBackdrop.innerHTML = `
      <div class="tm-modal tm-modal-small">
        <div class="tm-modal-header">
          <h3>Search Parent Tag for "${escapeHtml(catName)}"</h3>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <input type="text" class="form-control tm-cat-search-input"
                 placeholder="Search tags..." value="${escapeHtml(catName)}">
          <div class="tm-search-results tm-cat-search-results">
            <div class="tm-loading">Type to search...</div>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(searchBackdrop);

    const input = searchBackdrop.querySelector('.tm-cat-search-input');
    const resultsEl = searchBackdrop.querySelector('.tm-cat-search-results');

    function closeSearch() {
      searchBackdrop.remove();
    }

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
            // Update resolution
            currentResolutions[catName] = {
              parentTagId: tag.id,
              parentTagName: tag.name,
              resolution: 'manual',
              description: currentResolutions[catName]?.description || '',
            };

            // Update row display
            if (row) {
              const nameCell = row.querySelector('.tm-cat-parent-name');
              const statusCell = row.querySelector('.tm-cat-status');
              if (nameCell) nameCell.textContent = tag.name;
              if (statusCell) statusCell.innerHTML = '<span class="tm-cat-resolution tm-cat-manual">Manual</span>';
            }
          }
          closeSearch();
        });
      });
    }

    input.addEventListener('input', doSearch);
    input.focus();
    doSearch();

    searchBackdrop.querySelector('.tm-close-btn').addEventListener('click', closeSearch);
    searchBackdrop.addEventListener('click', (e) => {
      if (e.target === searchBackdrop) closeSearch();
    });
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
   * Handle importing selected StashDB tags, with optional category parent assignment.
   * Resolves false if the user cancelled in the category preview, true otherwise.
   * F11: isImporting and the import button are reset in `finally` on every exit
   * path (done, cancelled or thrown); a throw is shown to the user.
   */
  async function handleImportSelected(container) {
    if (selectedForImport.size === 0) return true;
    if (isImporting) return true;
    isImporting = true;
    importCancelRequested = false;

    const statusEl = container.querySelector('.tm-selection-info');
    const btnEl = container.querySelector('#tm-import-selected');
    let message;
    let failed = false;
    try {
      message = await importSelectedTags(statusEl, btnEl);
    } catch (e) {
      console.error('[tagManager] Import failed:', e);
      message = `Import failed: ${e.message || e}`;
      failed = true;
    } finally {
      isImporting = false;
      if (btnEl) btnEl.disabled = selectedForImport.size === 0;
    }
    if (message === null) return false; // cancelled in the category preview

    if (statusEl) statusEl.textContent = message;
    if (failed) showToast(message, 'error');

    setTimeout(() => {
      renderPage(container);
      refreshLocalTags(); // #124: reconcile with server truth after import
    }, 1500);
    return true;
  }

  /**
   * "Import All Unlinked": select every non-blacklisted, not-yet-linked StashDB
   * tag and import. Ignored while an import runs (it owns selectedForImport).
   */
  async function handleImportAll(container) {
    if (isImporting) return; // a running import owns selectedForImport
    const endpoint = selectedStashBox?.endpoint;
    if (!stashdbTags || !endpoint) return;

    const unlinked = importAllCandidates({ stashdbTags, localTags, endpoint, isBlacklisted });

    if (unlinked.length === 0) {
      showStatus('All tags are already linked', 'info');
      return;
    }

    if (!confirm(`Import ${unlinked.length} unlinked tag${unlinked.length !== 1 ? 's' : ''} from all categories?`)) {
      return;
    }

    const previousSelection = selectedForImport;
    selectedForImport = new Set(unlinked);
    const started = await handleImportSelected(container);
    // Cancelled in the category preview: give the user their own selection back.
    if (started === false) {
      selectedForImport = previousSelection;
      renderPage(container);
    }
  }

  /**
   * The import itself; handleImportSelected owns the isImporting flag.
   * @returns {Promise<?string>} the summary line, or null if the user cancelled
   */
  async function importSelectedTags(statusEl, btnEl) {
    const endpoint = selectedStashBox?.endpoint;
    if (!endpoint) throw new Error('no stash-box endpoint is selected');
    if (!stashdbTags) throw new Error('the stash-box tags are not loaded; reload them and try again');

    let parentMap = null;
    let remember = false;
    let resolutions = null;

    // #126: when "leave parent tags alone" is on, import flat — skip the category
    // preview modal and all parent creation/assignment (parentMap stays null).
    if (shouldResolveParents(settings)) {
      const categoryResolutions = resolveCategoryParents(selectedForImport, endpoint);
      const hasCategories = Object.keys(categoryResolutions).length > 0;

      if (hasCategories) {
        const result = await showCategoryPreviewModal(categoryResolutions);
        if (result === 'cancel') return null;
        if (result !== null) {
          parentMap = result.parentMap;
          remember = result.remember;
          resolutions = result.resolutions;
        }
      }
    }

    const total = selectedForImport.size;
    if (statusEl) statusEl.textContent = 'Importing...';
    if (btnEl) btnEl.disabled = true;
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-sm btn-secondary';
    cancelBtn.id = 'tm-import-cancel';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => {
      requestImportCancel();
      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling...';
    });
    if (btnEl?.parentNode) btnEl.parentNode.insertBefore(cancelBtn, btnEl.nextSibling);

    let created = 0;
    let linked = 0;
    let parented = 0;
    let errors = 0;
    // #125: { stashdbTag, parentId, conflicts } collected for end-of-batch resolution
    const importConflicts = [];

    // Pre-create parent tags that need creating
    const createdParents = {};
    if (parentMap) {
      for (const [catName, parentTagId] of Object.entries(parentMap)) {
        if (parentTagId === null) {
          try {
            const desc = resolutions[catName]?.description || '';
            const newTag = await createTag({ name: catName, description: desc });
            if (newTag) {
              createdParents[catName] = newTag.id;
              localTags.push({ id: newTag.id, name: newTag.name, aliases: [], stash_ids: [], parents: [] });
              localTagsChanged();
            }
          } catch (e) {
            console.error(`[tagManager] Failed to create parent tag "${catName}":`, e);
          }
        }
      }

      // Backfill description on existing parent tags if empty
      if (resolutions) {
        for (const [catName, parentTagId] of Object.entries(parentMap)) {
          if (parentTagId !== null && resolutions[catName]?.description) {
            const parentTag = localTags.find(t => t.id === parentTagId);
            if (parentTag && !parentTag.description) {
              try {
                await updateTag({ id: parentTagId, description: resolutions[catName].description });
                parentTag.description = resolutions[catName].description;
              } catch (e) {
                console.warn(`[tagManager] Failed to backfill description for "${catName}":`, e);
              }
            }
          }
        }
      }
    }

    const stashdbById = new Map(stashdbTags.map(t => [t.id, t]));
    const processed = new Set();
    let cancelledAt = -1;
    let position = 0;
    for (const stashdbId of selectedForImport) {
      if (importCancelRequested) { cancelledAt = position; break; }
      position++;
      processed.add(stashdbId);
      if (statusEl) statusEl.textContent = `Importing ${position} / ${total}`;
      const stashdbTag = stashdbById.get(stashdbId);
      if (!stashdbTag) continue;

      // Resolve parent ID for this tag's category
      let parentId = null;
      if (parentMap && stashdbTag.category) {
        const catName = stashdbTag.category.name;
        parentId = parentMap[catName] ?? createdParents[catName] ?? null;
      }

      try {
        const existingTag = findLocalTagByName(stashdbTag.name);

        if (existingTag) {
          const existingStashIds = existingTag.stash_ids || [];
          const filteredStashIds = existingStashIds.filter(
            sid => sid.endpoint !== endpoint
          );

          await updateTag({
            id: existingTag.id,
            stash_ids: [...filteredStashIds, {
              endpoint: endpoint,
              stash_id: stashdbId
            }]
          });

          const idx = localTags.findIndex(t => t.id === existingTag.id);
          if (idx >= 0) {
            localTags[idx].stash_ids = [...filteredStashIds, {
              endpoint: endpoint,
              stash_id: stashdbId
            }];
            localTagsChanged();
          }

          linked++;

          // Add parent if missing
          if (parentId) {
            const existingParentIds = (existingTag.parents || []).map(p => p.id);
            if (!existingParentIds.includes(parentId)) {
              await updateTag({
                id: existingTag.id,
                parent_ids: [...existingParentIds, parentId]
              });
              if (idx >= 0) {
                localTags[idx].parents = [...(localTags[idx].parents || []), { id: parentId }];
              }
              parented++;
            }
          }
        } else {
          // #125: pre-flight — if the incoming name/aliases collide with a local
          // tag, defer to the end-of-batch resolution modal instead of a failing
          // tagCreate that would only surface in the console.
          const conflicts = detectImportConflicts(stashdbTag);
          if (conflicts.length > 0) {
            importConflicts.push({ stashdbTag, parentId, conflicts });
            continue;
          }

          const input = {
            name: stashdbTag.name,
            description: stashdbTag.description || '',
            aliases: stashdbTag.aliases || [],
            stash_ids: [{
              endpoint: endpoint,
              stash_id: stashdbId
            }]
          };

          if (parentId) {
            input.parent_ids = [parentId];
          }

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
            localTags.push({
              id: data.tagCreate.id,
              name: data.tagCreate.name,
              aliases: stashdbTag.aliases || [],
              stash_ids: input.stash_ids,
              parents: parentId ? [{ id: parentId }] : []
            });
            localTagsChanged();
            created++;
            if (parentId) parented++;
          }
        }
      } catch (e) {
        // #125: a create/link rejection may be an alias/name conflict our in-memory
        // pre-flight missed (out-of-band tag, server-side normalization). Route it to
        // the same resolution queue instead of a logs-only error.
        const conflicts = detectImportConflicts(stashdbTag);
        if (conflicts.length > 0) {
          importConflicts.push({ stashdbTag, parentId, conflicts });
        } else {
          console.error(`[tagManager] Failed to import/link "${stashdbTag.name}":`, e);
          errors++;
        }
      }
    }

    cancelBtn.remove();

    // Save category mappings if requested
    if (remember && resolutions) {
      for (const [catName, info] of Object.entries(resolutions)) {
        const finalId = info.parentTagId || createdParents[catName];
        if (finalId) {
          setCategoryMapping(endpoint, catName, finalId);
        }
      }
      await saveCategoryMappings();
    }

    if (cancelledAt >= 0) {
      for (const id of processed) selectedForImport.delete(id); // keep the untouched rest selected
    } else {
      selectedForImport.clear();
    }

    // #125: resolve any collected alias/name conflicts via the modal. Counts from
    // resolution actions fold into the created/linked totals; resolved/skipped are
    // reported separately in the summary.
    let conflictsResolved = 0;
    let skipped = 0;
    if (importConflicts.length > 0) {
      const outcome = await renderConflictResolutionModal(importConflicts, endpoint);
      created += outcome.created;
      linked += outcome.linked;
      conflictsResolved = outcome.resolved;
      skipped = outcome.skipped;
    }

    const summary = summarizeImportResult({
      created,
      linked,
      parented,
      categories: parentMap ? Object.keys(parentMap).length : 0,
      conflicts: conflictsResolved,
      skipped,
      errors,
    });
    return cancelledAt >= 0 ? `Cancelled after ${cancelledAt} of ${total}. ${summary}` : summary;
  }

  /**
   * F6: the conflict modal's model, without the DOM. One row per deferred import:
   * { i, stashdbTag, parentId, conflicts, done, result, error }. `outcome` holds
   * the counts that fold into the import summary. `busy` lets one row action run
   * at a time (two merge-intos on one tag would overwrite each other's aliases).
   * @param {Array<{stashdbTag: object, parentId: ?string, conflicts: Array}>} importConflicts
   * @param {string} endpoint - the stash-box endpoint the import links to
   */
  function createConflictSession(importConflicts, endpoint) {
    const rows = new Map();
    importConflicts.forEach((entry, i) => rows.set(i, {
      i,
      stashdbTag: entry.stashdbTag,
      parentId: entry.parentId || null,
      conflicts: entry.conflicts || [],
      done: false,
      result: '',
      error: '',
    }));
    const session = { endpoint, rows, outcome: { created: 0, linked: 0, skipped: 0, resolved: 0 }, busy: false };
    recheckConflictRows(session);
    return session;
  }

  /**
   * F6: recompute every pending row's conflicts from the current localTags, so no
   * row shows or acts on a clash that another row's action has changed. A row
   * whose conflicts are all gone is offered a plain Import.
   */
  function recheckConflictRows(session) {
    localTagsChanged(); // tags may have been edited in place since the last lookup (e.g. via an "Open" link)
    for (const row of session.rows.values()) {
      if (!row.done) row.conflicts = detectImportConflicts(row.stashdbTag);
    }
  }

  function pendingConflictRows(session) {
    return [...session.rows.values()].filter(r => !r.done);
  }

  /** Distinct local tags among a row's conflicts, in first-seen order. */
  function distinctConflictTags(conflicts) {
    const seen = new Map();
    for (const c of conflicts) {
      if (!seen.has(c.conflictingTag.id)) seen.set(c.conflictingTag.id, c.conflictingTag);
    }
    return [...seen.values()];
  }

  /** The incoming name itself is taken, so no new tag can be created with it. */
  function rowHasNameConflict(row) {
    const nameLower = row.stashdbTag.name.toLowerCase();
    return row.conflicts.some(c => c.conflictingValue.toLowerCase() === nameLower);
  }

  function quoteList(values) {
    return values.map(v => `"${v}"`).join(', ');
  }

  /** 'aliases "Bar", "Baz" (already used by "Barbara", "Bazza")' */
  function droppedAliasesText(conflicts, removed) {
    const lower = new Set(removed.map(a => a.toLowerCase()));
    const owners = [...new Set(conflicts
      .filter(c => lower.has(c.conflictingValue.toLowerCase()))
      .map(c => c.conflictingTag.name))];
    return `${removed.length === 1 ? 'alias' : 'aliases'} ${quoteList(removed)} (already used by ${quoteList(owners)})`;
  }

  /**
   * A localTags entry for a tag just created: what the server returned (createTag
   * selects the full tag), falling back to what was sent.
   */
  function localTagFromCreated(created, input = {}) {
    return {
      id: created.id,
      name: created.name ?? input.name,
      description: created.description ?? input.description ?? '',
      aliases: created.aliases ?? input.aliases ?? [],
      stash_ids: created.stash_ids ?? input.stash_ids ?? [],
      parents: created.parents ?? (input.parent_ids || []).map(id => ({ id })),
    };
  }

  function conflictCreateInput(session, row, aliases) {
    const input = {
      name: row.stashdbTag.name,
      description: row.stashdbTag.description || '',
      aliases,
      stash_ids: [{ endpoint: session.endpoint, stash_id: row.stashdbTag.id }],
    };
    if (row.parentId) input.parent_ids = [row.parentId];
    return input;
  }

  function conflictStaleTarget(row) {
    return { ok: false, message: `"${row.stashdbTag.name}" no longer clashes with that tag; choose again.` };
  }

  function conflictNameTaken(row) {
    return {
      ok: false,
      message: `The name "${row.stashdbTag.name}" is already taken, so it can't be a new tag. Merge it into the existing tag or skip it.`,
    };
  }

  /** "Merge into existing": link the incoming stash-box tag to a clashing local tag. */
  async function conflictMergeInto(session, row, tagId) {
    const existing = localTags.find(t => t.id === tagId);
    if (!existing || !row.conflicts.some(c => c.conflictingTag.id === tagId)) return conflictStaleTarget(row);
    const { endpoint } = session;
    const stashdbId = row.stashdbTag.id;
    const current = (existing.stash_ids || []).find(s => s.endpoint === endpoint);
    if (current && current.stash_id !== stashdbId) {
      const box = getEndpointDisplayName(stashBoxes.find(sb => sb.endpoint === endpoint) || { endpoint });
      if (!confirm(
        `"${existing.name}" is already linked to ${box} ID ${current.stash_id}.\n\n` +
        `Replace that link with ${stashdbId} ("${row.stashdbTag.name}")?`
      )) {
        return { ok: false, cancelled: true, message: '' };
      }
    }
    const input = buildMergeIntoExistingInput(existing, row.stashdbTag, row.conflicts, endpoint, stashdbId, row.parentId);
    const added = input.aliases.slice((existing.aliases || []).length);
    let updated;
    try {
      updated = await updateTag(input);
    } catch (e) {
      console.error('[tagManager] merge-into-existing failed:', e);
      return { ok: false, message: `Merge into "${existing.name}" failed: ${e.message || e}` };
    }
    const idx = localTags.findIndex(t => t.id === tagId);
    if (idx >= 0) {
      localTags[idx].aliases = input.aliases;
      localTags[idx].stash_ids = (updated && updated.stash_ids) || input.stash_ids;
      localTagsChanged();
      if (input.parent_ids) localTags[idx].parents = input.parent_ids.map(id => ({ id }));
    }
    session.outcome.linked++; session.outcome.resolved++;
    const addedText = added.length ? ` (added ${added.length === 1 ? 'alias' : 'aliases'} ${quoteList(added)})` : '';
    return { ok: true, localTagsChanged: true, message: `Merged into "${existing.name}"${addedText}` };
  }

  /**
   * "Strip alias & import" (strip) or, once a row has no conflicts left, a plain
   * Import (all original aliases).
   */
  async function conflictImport(session, row, { strip }) {
    if (strip && rowHasNameConflict(row)) return conflictNameTaken(row);
    if (!strip && row.conflicts.length) {
      return { ok: false, message: `"${row.stashdbTag.name}" clashes with a local tag again; choose again.` };
    }
    const { aliases, removed } = sanitizeAliasesForImport(row.stashdbTag, row.conflicts);
    const input = conflictCreateInput(session, row, aliases);
    let created;
    try {
      created = await createTag(input);
    } catch (e) {
      console.error('[tagManager] conflict import failed:', e);
      return { ok: false, message: `Import failed: ${e.message || e}` };
    }
    if (!created?.id) return { ok: false, message: 'Import failed: the server returned no tag.' };
    localTags.push(localTagFromCreated(created, input));
    localTagsChanged();
    session.outcome.created++; session.outcome.resolved++;
    let message = 'Imported';
    if (removed.length) message = `Imported without the ${droppedAliasesText(row.conflicts, removed)}`;
    else if (!strip) message = 'Imported (no conflicts left)';
    return { ok: true, localTagsChanged: true, message };
  }

  /**
   * "Merge existing into this": create the incoming tag, then merge the clashing
   * local tag into it. F6: if the merge fails, the new tag is destroyed again so
   * nothing is left behind and a retry doesn't collide on the name.
   */
  async function conflictReverse(session, row, tagId) {
    const existing = localTags.find(t => t.id === tagId);
    if (!existing || !row.conflicts.some(c => c.conflictingTag.id === tagId)) return conflictStaleTarget(row);
    if (rowHasNameConflict(row)) return conflictNameTaken(row);
    const name = row.stashdbTag.name;
    const sceneCount = await getTagSceneCount(tagId);
    if (!confirm(`Merge "${existing.name}" into "${name}"?\n\nThis deletes "${existing.name}" and reassigns its ${sceneCount} scene${sceneCount === 1 ? '' : 's'} to the imported tag. This cannot be undone.`)) {
      return { ok: false, cancelled: true, message: '' };
    }
    // Create the incoming tag clean (strip the conflicting aliases so the create
    // succeeds while the existing tag still owns them), then absorb the existing
    // tag — tagsMerge carries its aliases/scenes onto the new tag.
    const { aliases, removed } = sanitizeAliasesForImport(row.stashdbTag, row.conflicts);
    const input = conflictCreateInput(session, row, aliases);
    let created;
    try {
      created = await createTag(input);
    } catch (e) {
      console.error('[tagManager] reverse-merge create failed:', e);
      return { ok: false, message: `Could not create "${name}": ${e.message || e}. Nothing was changed.` };
    }
    if (!created?.id) return { ok: false, message: `Could not create "${name}": the server returned no tag. Nothing was changed.` };

    let merged;
    try {
      merged = await mergeTags([tagId], created.id);
    } catch (e) {
      console.error('[tagManager] reverse-merge failed; removing the new tag:', e);
      const why = e.message || e;
      try {
        await destroyTag(created.id);
      } catch (destroyErr) {
        console.error('[tagManager] could not remove the new tag after a failed merge:', destroyErr);
        localTags.push(localTagFromCreated(created, input)); // it exists on the server
        localTagsChanged();
        return {
          ok: false,
          localTagsChanged: true,
          message: `Merging "${existing.name}" into "${name}" failed: ${why}. Removing the new tag "${name}" (id ${created.id}) also failed: ${destroyErr.message || destroyErr}. Delete it in Stash, or merge into it here.`,
        };
      }
      return { ok: false, message: `Merging "${existing.name}" into "${name}" failed: ${why}. The new tag was removed again; nothing changed.` };
    }

    dropMergedSourceLocally(tagId);
    const createdLocal = localTagFromCreated(created, input);
    localTags.push({
      ...createdLocal,
      name: (merged && merged.name) || createdLocal.name,
      aliases: (merged && merged.aliases) || createdLocal.aliases,
      stash_ids: (merged && merged.stash_ids) || createdLocal.stash_ids,
    });
    localTagsChanged();
    session.outcome.created++; session.outcome.resolved++;
    // Aliases that clashed with OTHER tags were stripped and are not brought back by the merge.
    const otherConflicts = row.conflicts.filter(c => c.conflictingTag.id !== tagId);
    const lost = removed.filter(a => otherConflicts.some(c => c.conflictingValue.toLowerCase() === a.toLowerCase()));
    const lostText = lost.length ? `, without the ${droppedAliasesText(otherConflicts, lost)}` : '';
    return { ok: true, localTagsChanged: true, message: `Created "${name}" and merged "${existing.name}" into it${lostText}` };
  }

  /**
   * F6: run one conflict-row action and re-check the remaining rows afterwards.
   * @param {object} session - from createConflictSession
   * @param {number} i - row index
   * @param {'merge-into'|'strip'|'reverse'|'import'|'skip'} action
   * @param {string} [tagId] - the local tag for merge-into / reverse
   * @returns {Promise<{ok: boolean, message: string, localTagsChanged?: boolean, cancelled?: boolean, busy?: boolean}>}
   *   ok: the row is resolved and shows `message` as its result. Otherwise the row
   *   stays actionable and shows `message` as its error (not when cancelled).
   */
  async function resolveConflictRow(session, i, action, tagId) {
    const row = session.rows.get(i);
    if (!row || row.done) return { ok: false, message: 'This row is already resolved.' };
    if (session.busy) return { ok: false, busy: true, message: 'Another action is still running.' };
    session.busy = true;
    row.error = '';
    let result;
    try {
      row.conflicts = detectImportConflicts(row.stashdbTag); // act on the current clashes
      if (action === 'merge-into') result = await conflictMergeInto(session, row, tagId);
      else if (action === 'strip') result = await conflictImport(session, row, { strip: true });
      else if (action === 'import') result = await conflictImport(session, row, { strip: false });
      else if (action === 'reverse') result = await conflictReverse(session, row, tagId);
      else if (action === 'skip') {
        session.outcome.skipped++;
        result = { ok: true, message: 'Skipped' };
      } else result = { ok: false, message: `Unknown action "${action}".` };
    } catch (e) {
      console.error(`[tagManager] conflict action "${action}" failed:`, e);
      result = { ok: false, message: `Failed: ${e.message || e}` };
    } finally {
      session.busy = false;
    }
    if (result.ok) {
      row.done = true;
      row.result = result.message;
    } else if (!result.cancelled) {
      row.error = result.message;
    }
    recheckConflictRows(session);
    return result;
  }

  /** One conflict row: its result once resolved, else its clashes and actions. */
  function conflictRowHtml(row) {
    const { i, stashdbTag, conflicts } = row;
    const nameHtml = `<strong>${escapeHtml(stashdbTag.name)}</strong>`;
    if (row.done) {
      return `
          <div class="tm-conflict-row tm-conflict-done" data-i="${i}">
            <div class="tm-conflict-desc">
              ${nameHtml}
              <span class="tm-conflict-result">${escapeHtml(row.result)}</span>
            </div>
          </div>
        `;
    }
    const errorHtml = row.error ? `<div class="tm-conflict-error">${escapeHtml(row.error)}</div>` : '';
    const skipBtn = `<button class="btn btn-secondary btn-sm tm-conflict-skip" data-i="${i}">Skip</button>`;
    if (conflicts.length === 0) {
      return `
          <div class="tm-conflict-row" data-i="${i}">
            <div class="tm-conflict-desc">
              ${nameHtml}
              <span class="tm-conflict-detail">no conflicts left (an earlier action resolved them)</span>
            </div>
            ${errorHtml}
            <div class="tm-conflict-actions">
              <button class="btn btn-primary btn-sm tm-conflict-import" data-i="${i}">Import</button>
              ${skipBtn}
            </div>
          </div>
        `;
    }
    const hasNameConflict = rowHasNameConflict(row);
    const values = [...new Set(conflicts.map(c => c.conflictingValue))];
    const tags = distinctConflictTags(conflicts);
    const mergeBtns = tags.map(t =>
      `<button class="btn btn-primary btn-sm tm-conflict-merge-into" data-i="${i}" data-tag="${escapeHtml(t.id)}">Merge into "${escapeHtml(t.name)}"</button>`
    ).join('');
    // A name collision means the incoming name is taken, so we can't create a
    // new tag with it — strip-alias and reverse-merge are unavailable.
    const stripBtn = hasNameConflict ? '' :
      `<button class="btn btn-secondary btn-sm tm-conflict-strip" data-i="${i}">Strip alias &amp; import</button>`;
    const reverseBtns = hasNameConflict ? '' : tags.map(t =>
      `<button class="btn btn-danger btn-sm tm-conflict-reverse" data-i="${i}" data-tag="${escapeHtml(t.id)}">Merge "${escapeHtml(t.name)}" into this</button>`
    ).join('');
    const openBtns = tags.map(t =>
      `<a href="${escapeHtml(stashPath(`/tags/${t.id}`))}" target="_blank" class="btn btn-secondary btn-sm">Open "${escapeHtml(t.name)}"</a>`
    ).join('');
    return `
          <div class="tm-conflict-row" data-i="${i}">
            <div class="tm-conflict-desc">
              ${nameHtml}
              <span class="tm-conflict-detail">${hasNameConflict ? 'name' : 'alias'} ${values.map(v => `&ldquo;${escapeHtml(v)}&rdquo;`).join(', ')} already used by ${tags.map(t => `&ldquo;${escapeHtml(t.name)}&rdquo;`).join(', ')}</span>
            </div>
            ${errorHtml}
            <div class="tm-conflict-actions">
              ${mergeBtns}
              ${stripBtn}
              ${reverseBtns}
              ${openBtns}
              ${skipBtn}
            </div>
          </div>
        `;
  }

  /**
   * #125: End-of-import resolution modal for alias/name conflicts. Renders one
   * row per deferred import with per-row actions (see resolveConflictRow).
   * Resolved rows stay listed with their result; the footer button reads "Done"
   * once nothing is pending. Resolves to aggregate counts
   * { created, linked, skipped, resolved } that fold into the import summary.
   * Unresolved rows at close count as skipped.
   * @param {Array<{stashdbTag: object, parentId: ?string, conflicts: Array}>} importConflicts
   * @param {string} [endpoint] - defaults to the selected stash-box's endpoint
   * @returns {Promise<{created: number, linked: number, skipped: number, resolved: number}>}
   */
  function renderConflictResolutionModal(importConflicts, endpoint = selectedStashBox?.endpoint) {
    return new Promise((resolve) => {
      if (!endpoint) {
        // Nothing can be linked without an endpoint: leave every row unresolved.
        console.error('[tagManager] No stash-box endpoint selected; skipping conflict resolution.');
        resolve({ created: 0, linked: 0, skipped: importConflicts.length, resolved: 0 });
        return;
      }
      const session = createConflictSession(importConflicts, endpoint);

      const backdrop = document.createElement('div');
      backdrop.className = 'tm-modal-backdrop';
      backdrop.innerHTML = `
        <div class="tm-modal tm-modal-wide">
          <div class="tm-modal-header">
            <h3>Resolve Tag Conflicts</h3>
            <button class="tm-close-btn">&times;</button>
          </div>
          <div class="tm-modal-body">
            <p class="tm-preview-intro">${importConflicts.length} imported tag${importConflicts.length === 1 ? ' has a' : 's have'} name or alias already used by a local tag. Choose how to resolve each. Anything left unresolved is skipped.</p>
            <div class="tm-conflict-list"></div>
          </div>
          <div class="tm-modal-footer">
            <button class="btn btn-secondary" id="tm-conflict-skip-all">Skip all remaining</button>
          </div>
        </div>
      `;
      document.body.appendChild(backdrop);
      const listEl = backdrop.querySelector('.tm-conflict-list');
      const footerBtn = backdrop.querySelector('#tm-conflict-skip-all');

      let settled = false;
      let closeRequested = false;
      function finish() {
        if (settled) return;
        if (session.busy) { closeRequested = true; return; } // close once the running action ends
        settled = true;
        session.outcome.skipped += pendingConflictRows(session).length; // unresolved → skipped
        backdrop.remove();
        resolve(session.outcome);
      }

      function render() {
        listEl.innerHTML = [...session.rows.values()].map(conflictRowHtml).join('');
        if (footerBtn) footerBtn.textContent = pendingConflictRows(session).length ? 'Skip all remaining' : 'Done';
        attachHandlers();
      }

      async function act(i, action, tagId) {
        if (settled || session.busy) return;
        const rowEl = listEl.querySelector(`.tm-conflict-row[data-i="${i}"]`);
        if (rowEl) rowEl.classList.add('tm-conflict-busy');
        listEl.classList.add('tm-conflict-busy');
        try {
          await resolveConflictRow(session, i, action, tagId);
        } finally {
          listEl.classList.remove('tm-conflict-busy');
        }
        if (closeRequested) finish();
        else if (!settled) render();
      }

      function attachHandlers() {
        const on = (selector, action) => listEl.querySelectorAll(selector).forEach(b =>
          b.addEventListener('click', () => act(Number(b.dataset.i), action, b.dataset.tag)));
        on('.tm-conflict-merge-into', 'merge-into');
        on('.tm-conflict-strip', 'strip');
        on('.tm-conflict-reverse', 'reverse');
        on('.tm-conflict-import', 'import');
        on('.tm-conflict-skip', 'skip');
        // "Open" links intentionally do not resolve the row — the user may return
        // to it; if they close the modal with it unresolved it counts as skipped.
      }

      backdrop.querySelector('.tm-close-btn').addEventListener('click', finish);
      backdrop.addEventListener('click', (e) => { if (e.target === backdrop) finish(); });
      if (footerBtn) footerBtn.addEventListener('click', finish);

      render();
    });
  }

  /**
   * #125: Fetch a tag's scene count for the reverse-merge confirmation prompt.
   * @param {string} tagId
   * @returns {Promise<number>}
   */
  async function getTagSceneCount(tagId) {
    try {
      const data = await graphqlRequest(`
        query FindTag($id: ID!) {
          findTag(id: $id) { scene_count }
        }
      `, { id: tagId });
      return data?.findTag?.scene_count ?? 0;
    } catch (e) {
      console.warn('[tagManager] could not fetch scene count:', e);
      return 0;
    }
  }

  /**
   * Update linked tags that are missing description or aliases from stash-box data.
   * Finds all local tags linked to the current endpoint and backfills empty fields.
   */
  async function handleUpdateLinkedTags(container) {
    if (isImporting) return;
    const endpoint = selectedStashBox?.endpoint;
    if (!stashdbTags || !endpoint) return;

    // Find linked tags that need updating
    const tagsToUpdate = [];
    for (const stashdbTag of stashdbTags) {
      const localTag = localTags.find(t =>
        t.stash_ids?.some(sid => sid.stash_id === stashdbTag.id && sid.endpoint === endpoint)
      );
      if (!localTag) continue;

      const needsDescription = !localTag.description && stashdbTag.description;
      const stashdbAliases = stashdbTag.aliases || [];
      const localAliases = localTag.aliases || [];
      const localAliasesLower = new Set(localAliases.map(a => a.toLowerCase()));
      const newAliases = stashdbAliases.filter(a => !localAliasesLower.has(a.toLowerCase()));
      const needsAliases = newAliases.length > 0;

      if (needsDescription || needsAliases) {
        tagsToUpdate.push({ localTag, stashdbTag, needsDescription, newAliases });
      }
    }

    if (tagsToUpdate.length === 0) {
      showStatus('All linked tags are already up to date', 'info');
      return;
    }

    if (!confirm(`Update ${tagsToUpdate.length} linked tag${tagsToUpdate.length !== 1 ? 's' : ''} with missing description/aliases from ${getEndpointDisplayName(selectedStashBox)}?`)) {
      return;
    }

    // F11: the flag and the button are reset in `finally`, whatever happens.
    isImporting = true;
    const btnEl = container.querySelector('#tm-update-linked');
    if (btnEl) btnEl.disabled = true;
    try {
      const statusEl = container.querySelector('.tm-selection-info');
      if (statusEl) statusEl.textContent = 'Updating linked tags...';

      let updated = 0;
      let errors = 0;

      for (const { localTag, stashdbTag, needsDescription, newAliases } of tagsToUpdate) {
        try {
          const input = { id: localTag.id };
          if (needsDescription) {
            input.description = stashdbTag.description;
          }
          if (newAliases.length > 0) {
            input.aliases = [...(localTag.aliases || []), ...newAliases];
          }

          await updateTag(input);

          // Update local cache
          const idx = localTags.findIndex(t => t.id === localTag.id);
          if (idx >= 0) {
            if (needsDescription) localTags[idx].description = stashdbTag.description;
            if (newAliases.length > 0) localTags[idx].aliases = input.aliases;
            localTagsChanged();
          }
          updated++;
        } catch (e) {
          console.error(`[tagManager] Failed to update "${localTag.name}":`, e);
          errors++;
        }
      }

      const parts = [];
      if (updated > 0) parts.push(`Updated ${updated} tag${updated !== 1 ? 's' : ''}`);
      if (errors > 0) parts.push(`${errors} error${errors !== 1 ? 's' : ''}`);
      const message = parts.join(', ') || 'No changes';

      showStatus(message, errors > 0 ? 'warning' : 'success');
    } catch (e) {
      console.error('[tagManager] Updating linked tags failed:', e);
      showStatus(`Updating linked tags failed: ${e.message || e}`, 'error');
    } finally {
      isImporting = false;
      if (btnEl) btnEl.disabled = false;
    }

    setTimeout(() => {
      renderPage(container);
      refreshLocalTags(); // #124: reconcile with server truth after import
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
   * Find a local tag by name or alias match (case-insensitive)
   * @param {string} name - Name to search for
   * @returns {object|undefined} - Matching local tag or undefined
   */
  function findLocalTagByName(name) {
    if (!name) return undefined;
    return indexFindByName(getLocalTagIndex(), name);
  }

  // ---- F10: local tag index -------------------------------------------------
  // Lookups by name/alias/stash_id were linear scans of localTags (10k+ tags),
  // repeated per StashDB tag (3k+) on every render and import. The index turns
  // them into Map lookups with the same semantics as the scans they replace.

  /**
   * Build lookup maps over local tags.
   *  byName:  lowercase name or alias -> tags carrying it, in localTags order
   *  byStash: `endpoint|stash_id` -> first tag having that stash_id
   */
  function buildLocalTagIndex(tags) {
    const byName = new Map();
    const byStash = new Map();
    const addName = (value, tag) => {
      if (typeof value !== 'string' || !value) return;
      const key = value.toLowerCase();
      const list = byName.get(key);
      if (!list) byName.set(key, [tag]);
      else if (list[list.length - 1] !== tag) list.push(tag);
    };
    for (const tag of tags || []) {
      addName(tag.name, tag);
      for (const a of tag.aliases || []) addName(a, tag);
      for (const sid of tag.stash_ids || []) {
        const key = `${sid.endpoint}|${sid.stash_id}`;
        if (!byStash.has(key)) byStash.set(key, tag);
      }
    }
    return { byName, byStash };
  }

  /** First tag whose name or alias equals `name` (case-insensitive), skipping excludeId. */
  function indexFindByName(index, name, excludeId) {
    const list = index.byName.get(String(name).toLowerCase());
    if (!list) return undefined;
    return excludeId === undefined || excludeId === null
      ? list[0]
      : list.find(t => t.id !== excludeId);
  }

  /** The tag linked to `stashId` for `endpoint`, if any. */
  function indexFindByStashId(index, endpoint, stashId) {
    return index.byStash.get(`${endpoint}|${stashId}`);
  }

  // Invalidation rule: the memoized index is rebuilt when (a) the localTags array
  // reference changed (reassignment after a fetch), (b) its length changed (a push
  // or splice, even one that forgot to notify), or (c) localTagsChanged() was
  // called. In-place edits of a tag's name/aliases/stash_ids keep the array and
  // its length, so those sites must call localTagsChanged().
  let _localIndex = null;
  let _localIndexRef = null;
  let _localIndexLen = -1;
  let _localIndexVersion = 0;
  let _localIndexBuiltVersion = -1;

  function localTagsChanged() {
    _localIndexVersion++;
  }

  function getLocalTagIndex() {
    if (!_localIndex || _localIndexRef !== localTags || _localIndexLen !== localTags.length ||
        _localIndexBuiltVersion !== _localIndexVersion) {
      _localIndex = buildLocalTagIndex(localTags);
      _localIndexRef = localTags;
      _localIndexLen = localTags.length;
      _localIndexBuiltVersion = _localIndexVersion;
    }
    return _localIndex;
  }

  /** True if some local tag has this stash_id for this endpoint. */
  function isLinkedForEndpoint(stashId, endpoint) {
    return !!endpoint && !!indexFindByStashId(getLocalTagIndex(), endpoint, stashId);
  }

  /**
   * StashDB tags to import with "Import All Unlinked": everything except
   * blacklisted tags and tags already linked (for this endpoint) to a local tag.
   * Tags that only match a local tag by name stay in: the import links them.
   * @returns {string[]} StashDB tag ids
   */
  function importAllCandidates({ stashdbTags: tags, localTags: locals, endpoint, isBlacklisted: blacklisted }) {
    const index = buildLocalTagIndex(locals);
    const skip = blacklisted || (() => false);
    const ids = [];
    for (const tag of tags || []) {
      if (skip(tag.name)) continue;
      if (indexFindByStashId(index, endpoint, tag.id)) continue;
      ids.push(tag.id);
    }
    return ids;
  }

  /** Tags on a page that still need a match for `endpoint` (ignores links to other stash-boxes). */
  function tagsToSearchOnPage(tags, endpoint) {
    return tags.filter(t => !hasStashIdForEndpoint(t, endpoint));
  }

  // F10: cancel flag for a running import; checked before each tag.
  let importCancelRequested = false;
  function requestImportCancel() {
    importCancelRequested = true;
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
   * Get a readable display name for a stash-box endpoint
   * @param {object} stashBox - Stash-box object with endpoint and optionally name
   * @returns {string} - Display name like "StashDB" or "pmvstash.org"
   */
  function getEndpointDisplayName(stashBox) {
    if (!stashBox) return 'Stash-Box';
    if (stashBox.name) return stashBox.name;
    // Extract domain from endpoint URL
    try {
      const url = new URL(stashBox.endpoint);
      return url.hostname.replace(/^www\./, '');
    } catch {
      return 'Stash-Box';
    }
  }

  /**
   * Render list of tags for browse/import view
   */
  function renderBrowseTagList(tags) {
    if (!tags || tags.length === 0) {
      return '<div class="tm-browse-empty">No tags in this category</div>';
    }

    const endpoint = selectedStashBox?.endpoint;

    // Apply browse filter
    let filteredTags = tags;
    if (browseFilter === 'linked') {
      filteredTags = tags.filter(tag => {
        return isLinkedForEndpoint(tag.id, endpoint);
      });
    } else if (browseFilter === 'unlinked') {
      filteredTags = tags.filter(tag => {
        return !isLinkedForEndpoint(tag.id, endpoint);
      });
    }

    if (filteredTags.length === 0) {
      return '<div class="tm-browse-empty">No tags match the current filter</div>';
    }

    tags = filteredTags;

    const rows = tags.map(tag => {
      // Check if linked to THIS endpoint specifically
      const isLinkedToEndpoint = isLinkedForEndpoint(tag.id, endpoint);
      // Check if tag exists locally by name (for smart import)
      const existsByName = findLocalTagByName(tag.name);
      const canLink = existsByName && !isLinkedToEndpoint;

      const isSelected = selectedForImport.has(tag.id);

      let statusHtml = '';
      if (isLinkedToEndpoint) {
        statusHtml = `<span class="tm-local-exists" title="Already linked to ${escapeHtml(getEndpointDisplayName(selectedStashBox))}">✓ Linked</span>`;
      } else if (canLink) {
        statusHtml = `<span class="tm-can-link" title="Will link to existing tag: ${escapeHtml(existsByName.name)}">→ Link to "${escapeHtml(existsByName.name)}"</span>`;
      }

      return `
        <div class="tm-browse-tag ${isLinkedToEndpoint ? 'tm-exists-locally' : ''} ${canLink ? 'tm-can-link-row' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${isLinkedToEndpoint ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${statusHtml}
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

    const endpoint = selectedStashBox?.endpoint;

    // Apply browse filter
    let filteredTags = tags;
    if (browseFilter === 'linked') {
      filteredTags = tags.filter(tag => {
        return isLinkedForEndpoint(tag.id, endpoint);
      });
    } else if (browseFilter === 'unlinked') {
      filteredTags = tags.filter(tag => {
        return !isLinkedForEndpoint(tag.id, endpoint);
      });
    }

    if (filteredTags.length === 0) {
      return '<div class="tm-browse-empty">No tags match the current filter</div>';
    }

    tags = filteredTags;

    const rows = tags.map(tag => {
      // Check if linked to THIS endpoint specifically
      const isLinkedToEndpoint = isLinkedForEndpoint(tag.id, endpoint);
      // Check if tag exists locally by name (for smart import)
      const existsByName = findLocalTagByName(tag.name);
      const canLink = existsByName && !isLinkedToEndpoint;

      const isSelected = selectedForImport.has(tag.id);
      const categoryName = tag.category?.name || 'Uncategorized';

      let statusHtml = '';
      if (isLinkedToEndpoint) {
        statusHtml = `<span class="tm-local-exists" title="Already linked to ${escapeHtml(getEndpointDisplayName(selectedStashBox))}">✓ Linked</span>`;
      } else if (canLink) {
        statusHtml = `<span class="tm-can-link" title="Will link to existing tag: ${escapeHtml(existsByName.name)}">→ Link</span>`;
      }

      return `
        <div class="tm-browse-tag ${isLinkedToEndpoint ? 'tm-exists-locally' : ''} ${canLink ? 'tm-can-link-row' : ''}" data-stashdb-id="${escapeHtml(tag.id)}">
          <label class="tm-browse-checkbox">
            <input type="checkbox" ${isSelected ? 'checked' : ''} ${isLinkedToEndpoint ? 'disabled' : ''}>
          </label>
          <div class="tm-browse-tag-info">
            <span class="tm-browse-tag-name">${escapeHtml(tag.name)}</span>
            <span class="tm-tag-category-badge">${escapeHtml(categoryName)}</span>
            ${tag.aliases?.length ? `<span class="tm-browse-tag-aliases">${escapeHtml(tag.aliases.slice(0, 3).join(', '))}</span>` : ''}
          </div>
          <div class="tm-browse-tag-status">
            ${statusHtml}
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
            <div class="tm-browse-filters">
              <select id="tm-browse-filter" class="form-control">
                <option value="all" ${browseFilter === 'all' ? 'selected' : ''}>Show All</option>
                <option value="unlinked" ${browseFilter === 'unlinked' ? 'selected' : ''}>Show Unlinked</option>
                <option value="linked" ${browseFilter === 'linked' ? 'selected' : ''}>Show Linked</option>
              </select>
            </div>
            <button class="btn btn-primary" id="tm-import-selected" ${selectedCount === 0 ? 'disabled' : ''}>
              Import Selected
            </button>
            <button class="btn btn-sm btn-secondary" id="tm-import-all" title="Import all unlinked tags from all categories">
              Import All Unlinked
            </button>
            <button class="btn btn-sm btn-secondary" id="tm-update-linked" title="Update description and aliases for linked tags from stash-box data">
              Update Linked Tags
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

    // A re-render (e.g. the focus refresh) must not drop focus from the blacklist editor.
    const active = document.activeElement;
    const editorFocus = active && active.id === 'tm-blacklist-text'
      ? { start: active.selectionStart, end: active.selectionEnd }
      : null;

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
            <button class="tm-tab ${activeTab === 'browse' ? 'tm-tab-active' : ''}" data-tab="browse">Browse Stash-Box</button>
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
              <button class="btn btn-secondary" id="tm-blacklist-toggle">
                Blacklist${tagBlacklist.length ? ` (${tagBlacklist.length})` : ''} ${blacklistPanelOpen ? '\u25B2' : '\u25BC'}
              </button>
            </div>
            ${blacklistPanelOpen ? `
              <div class="tm-blacklist-editor">
                <textarea id="tm-blacklist-text" class="form-control" rows="6"
                  placeholder="One pattern per line. Plain text matches the whole tag name; /regex/i is a regular expression.">${escapeHtml(blacklistDraft ?? tagBlacklistRaw)}</textarea>
                <div class="tm-blacklist-actions">
                  <button class="btn btn-primary btn-sm" id="tm-blacklist-save">Save</button>
                  <span class="tm-blacklist-hint">Blacklisted StashDB tags are hidden from matches and search results.</span>
                </div>
              </div>
            ` : ''}

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

    if (editorFocus) {
      const editor = container.querySelector('#tm-blacklist-text');
      if (editor) {
        editor.focus();
        editor.setSelectionRange(editorFocus.start, editorFocus.end);
      }
    }
  }

  /**
   * Render a single tag row
   */
  function renderTagRow(tag) {
    const matches = matchResults[tag.id];
    const best = bestVisibleMatch(matches);
    const hasMatches = !!best;
    const bestMatch = best ? best.match : null;

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
    } else if (matchErrors[tag.id]) {
      // The Find Match handler (.tm-search) retries the search
      matchContent = `
        <span class="tm-no-match tm-search-failed">Search failed: ${escapeHtml(matchErrors[tag.id])}</span>
        <button class="btn btn-primary btn-sm tm-search" data-tag-id="${tag.id}">Retry</button>
      `;
    } else if (matches !== undefined) {
      const hidden = (matches?.length || 0);
      matchContent = `
        <span class="tm-no-match">${hidden > 0 ? 'No matches (hidden by blacklist)' : 'No matches found'}</span>
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
          <a ${internalLinkAttrs(`/tags/${tag.id}`)} class="tm-tag-name">${escapeHtml(tag.name)}</a>
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

      // Import All Unlinked button
      const importAllBtn = container.querySelector('#tm-import-all');
      if (importAllBtn) {
        importAllBtn.addEventListener('click', () => handleImportAll(container));
      }

      // Update Linked Tags button
      const updateLinkedBtn = container.querySelector('#tm-update-linked');
      if (updateLinkedBtn) {
        updateLinkedBtn.addEventListener('click', () => handleUpdateLinkedTags(container));
      }

      // Browse filter dropdown
      container.querySelector('#tm-browse-filter')?.addEventListener('change', (e) => {
        browseFilter = e.target.value;
        renderPage(container);
      });
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
        matchErrors = {};
        cacheStatus = null;
        selectedForImport = new Set();
        // Load cache status for new endpoint
        await loadCacheStatus();

        // If on browse tab and cache exists, load it automatically
        if (activeTab === 'browse' && cacheStatus?.exists && !cacheStatus?.expired) {
          await loadTagsFromCache(container);
        }

        renderPage(container);
        if (cacheStatus?.error) showStatus(cacheStatus.error, 'error');
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

    // Blacklist editor
    container.querySelector('#tm-blacklist-toggle')?.addEventListener('click', () => {
      blacklistPanelOpen = !blacklistPanelOpen;
      renderPage(container);
    });
    // Unsaved text lives in blacklistDraft so a re-render keeps it
    container.querySelector('#tm-blacklist-text')?.addEventListener('input', (e) => {
      blacklistDraft = e.target.value;
    });
    container.querySelector('#tm-blacklist-save')?.addEventListener('click', async (e) => {
      const btn = e.target;
      const text = container.querySelector('#tm-blacklist-text')?.value || '';
      btn.disabled = true;
      btn.textContent = 'Saving...';
      const ok = await saveBlacklist(text);
      if (ok) {
        if (blacklistDraft === text) blacklistDraft = null; // keep anything typed during the save
        renderPage(container);
        showStatus('Blacklist saved', 'success');
      } else {
        btn.disabled = false;
        btn.textContent = 'Save';
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
        const best = bestVisibleMatch(matchResults[tagId]);
        if (best) showDiffDialog(tagId, container, best.index);
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

    // Only search tags that have no ID for the selected stash-box (a link to another one doesn't count)
    const tagsToSearch = tagsToSearchOnPage(pageTags, selectedStashBox?.endpoint);

    if (tagsToSearch.length === 0) {
      showStatus('All tags on this page are already matched', 'info');
      return;
    }

    isLoading = true;
    renderPage(container);

    let searchError = null;
    let fuzzyUnavailable = false;
    for (const tag of tagsToSearch) {
      try {
        const result = await callBackend('search', {
          tag_name: tag.name,
        });
        matchResults[tag.id] = result.matches || [];
        delete matchErrors[tag.id];
        if (result.fuzzy_unavailable) fuzzyUnavailable = true;
      } catch (e) {
        console.error(`[tagManager] Error searching for ${tag.name}:`, e);
        const text = backendErrorText(e);
        delete matchResults[tag.id];
        matchErrors[tag.id] = text;
        if (!searchError) {
          searchError = text;
        }
      }
    }

    isLoading = false;
    renderPage(container);
    if (searchError) showStatus(`Error: ${searchError}`, 'error');
    else if (fuzzyUnavailable) showFuzzyUnavailableHint();
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
      });
      matchResults[tagId] = result.matches || [];
      delete matchErrors[tagId];
      renderPage(container);
      if (result.fuzzy_unavailable) showFuzzyUnavailableHint();
    } catch (e) {
      console.error(`[tagManager] Error searching for ${tag.name}:`, e);
      const text = backendErrorText(e);
      delete matchResults[tagId];
      matchErrors[tagId] = text;
      renderPage(container);
      showStatus(`Error: ${text}`, 'error');
    }
  }

  /**
   * F12: the diff dialog's Apply, without the DOM. validateBeforeSave runs FIRST:
   * on a conflict nothing is created, saved or fetched. Only then does it create
   * a '__create__' parent, update the tag and, once the update succeeded, save
   * the category mapping. The caller passes the dialog's current selections.
   * @param {object} p
   * @param {object} p.tag - the local tag being matched
   * @param {object} p.stashdbTag - the chosen stash-box tag
   * @param {string} p.endpoint
   * @param {'local'|'local_add_alias'|'stashdb'} p.nameChoice
   * @param {'local'|'stashdb'} p.descChoice
   * @param {Iterable<string>} p.aliases - the dialog's final aliases
   * @param {?string} p.parentId - a tag id, '__create__', or null
   * @param {boolean} p.rememberMapping
   * @returns {Promise<object>} { ok, sanitizedAliases, updateInput?, createdParent?,
   *   validationErrors?, error?, stage?: 'parent'|'update' }. `createdParent` is
   *   set whenever a parent was created, even if a later step failed.
   */
  async function applyDiff({ tag, stashdbTag, endpoint, nameChoice, descChoice, aliases, parentId, rememberMapping }) {
    // Determine final name
    const finalName = nameChoice === 'stashdb' ? stashdbTag.name : tag.name;

    // Sanitize aliases - remove final name to prevent self-referential alias
    const sanitizedAliases = sanitizeAliasesForSave(aliases, finalName, tag.name);

    // Pre-validation: check for conflicts before hitting the API at all
    const validationErrors = validateBeforeSave(finalName, sanitizedAliases, tag.id);
    if (validationErrors.length > 0) {
      return { ok: false, validationErrors, sanitizedAliases };
    }

    const categoryName = stashdbTag.category?.name || null;
    console.debug('[tagManager] Applying match:', {
      localTag: tag.name,
      stashdbTag: stashdbTag.name,
      endpoint,
      nameChoice,
      descChoice,
      aliasCount: sanitizedAliases.length,
      categoryName,
      parentId,
    });

    // Build update input - preserve existing stash_ids from other endpoints
    const filteredStashIds = (tag.stash_ids || []).filter(sid => sid.endpoint !== endpoint);
    const updateInput = {
      id: tag.id,
      stash_ids: [...filteredStashIds, {
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
    updateInput.aliases = sanitizedAliases;

    // Handle parent tag from category (#126: skipped when leaving parents alone)
    let parentTagId = null;
    let createdParent = null;
    if (shouldResolveParents(settings) && categoryName && parentId) {
      if (parentId === '__create__') {
        try {
          createdParent = await createCategoryParentTag(categoryName);
        } catch (e) {
          console.error('[tagManager] Failed to create parent tag:', e);
          return { ok: false, stage: 'parent', error: `Failed to create parent tag: ${e.message}`, sanitizedAliases };
        }
        parentTagId = createdParent.id;
      } else {
        parentTagId = parentId;
      }

      // Merge new parent with existing parents (don't replace)
      try {
        const existingParentIds = await fetchTagParentIds(tag.id);
        if (!existingParentIds.includes(parentTagId)) {
          updateInput.parent_ids = [...existingParentIds, parentTagId];
        }
      } catch (e) {
        console.error('[tagManager] Failed to read current parents:', e);
        return {
          ok: false, stage: 'parent', createdParent, sanitizedAliases,
          error: `Could not read the current parents of "${tag.name}": ${e.message}`,
        };
      }
    }

    try {
      await updateTag(updateInput);
    } catch (e) {
      console.error('[tagManager] Save error:', e.message);
      return { ok: false, stage: 'update', error: e.message, createdParent, sanitizedAliases };
    }

    // Update local state
    const idx = localTags.findIndex(t => t.id === tag.id);
    if (idx >= 0) {
      localTags[idx].stash_ids = updateInput.stash_ids;
      if (updateInput.name) localTags[idx].name = updateInput.name;
      if (updateInput.description !== undefined) localTags[idx].description = updateInput.description;
      if (updateInput.aliases) localTags[idx].aliases = updateInput.aliases;
      localTagsChanged();
    }
    delete matchResults[tag.id];

    // Save the mapping only once the tag itself is saved
    if (rememberMapping && parentTagId) {
      setCategoryMapping(endpoint, categoryName, parentTagId);
      await saveCategoryMappings();
    }

    return { ok: true, updateInput, createdParent, sanitizedAliases };
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

    // Determine defaults for name and description selections.
    // Settings can override to always prefer stash-box values.
    const namesMatch = tag.name.toLowerCase() === stashdbTag.name.toLowerCase();
    const nameDefault = settings.preferStashBoxName
      ? 'stashdb'
      : (!tag.name ? 'stashdb' : (namesMatch ? 'local' : 'local_add_alias'));
    const descDefault = settings.preferStashBoxDescription
      ? 'stashdb'
      : (tag.description ? 'local' : 'stashdb');

    // Alias editing state - start with merged aliases
    let editableAliases = new Set([...(tag.aliases || []), ...(stashdbTag.aliases || [])]);

    // Category/parent state
    const hasCategory = !!stashdbTag.category?.name;
    let selectedParentId = null;
    let createParentIfMissing = true;
    let parentMatches = [];
    const existingParents = tag.parents || [];

    let parentOptions = [];
    let savedMappingId = null;
    const showParentControls = shouldShowParentControls(settings, hasCategory);
    if (hasCategory) {
      const categoryName = stashdbTag.category.name;
      // F20: mappings are per endpoint; the same endpoint Apply links to.
      const mappingEndpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;
      savedMappingId = getCategoryMapping(mappingEndpoint, categoryName) || null;
      if (savedMappingId && !localTags.some(t => t.id === savedMappingId)) {
        // Stale mapping (tag deleted): drop it so it is not offered or re-saved
        deleteCategoryMapping(mappingEndpoint, categoryName);
        savedMappingId = null;
        Promise.resolve(saveCategoryMappings()).catch(e =>
          console.warn('[tagManager] Failed to drop stale category mapping:', e));
      }
      parentMatches = findLocalParentMatches(categoryName);
      parentOptions = buildParentOptions({
        existingParents, parentMatches, savedMappingId, categoryName, localTags,
      });
      const sel = parentOptions.find(o => o.selected);
      selectedParentId = sel && sel.value !== '' ? sel.value : null;
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
              const remember = modal.querySelector('#tm-remember-mapping');
              if (remember) remember.checked = tagId !== savedMappingId;
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
              ${showParentControls ? `
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
                        ${parentOptions.map(o => `
                          <option value="${escapeHtml(o.value)}" ${o.selected ? 'selected' : ''}>${escapeHtml(o.label)}</option>
                        `).join('')}
                      </select>
                      <button type="button" class="btn btn-secondary btn-sm" id="tm-parent-search-btn">Search...</button>
                    </div>
                    <div class="tm-parent-remember">
                      <label>
                        <input type="checkbox" id="tm-remember-mapping" ${selectedParentId && selectedParentId === savedMappingId ? '' : 'checked'}>
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
            <strong>${escapeHtml(getEndpointDisplayName(selectedStashBox))} ID will be added:</strong> ${escapeHtml(stashdbTag.id)}
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
          const remember = modal.querySelector('#tm-remember-mapping');
          if (remember) remember.checked = selectedParentId !== savedMappingId;
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

    // Pre-validation failed: explain the first conflict and offer fixes.
    function showValidationError(errorEl, validationErrors, endpoint, sanitizedAliases) {
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
            <a href="${escapeHtml(stashPath(`/tags/${conflictTag.id}`))}" target="_blank" class="btn btn-secondary btn-sm">
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
            <a href="${escapeHtml(stashPath(`/tags/${conflictTag.id}`))}" target="_blank" class="btn btn-secondary btn-sm">
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
            container,
            ...currentParentChoice(),
          });
          if (result.createdParent) createdParentId = result.createdParent.id;

          if (result.cancelled) {
            mergeBtn.disabled = false;
            mergeBtn.textContent = originalText;
          } else if (!result.success) {
            errorEl.innerHTML = `<div class="tm-error-message">Merge failed: ${escapeHtml(result.error)}</div>`;
            mergeBtn.disabled = false;
            mergeBtn.textContent = originalText;
          }
        });
      }
    }

    // The save itself failed: parse known server conflicts into actions.
    function showSaveError(errorEl, message, endpoint, sanitizedAliases) {
      // Parse "tag with name 'X' already exists"
      const nameExistsMatch = message.match(/tag with name '([^']+)' already exists/i);
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
              <a href="${escapeHtml(stashPath(`/tags/${conflictTag.id}`))}" target="_blank" class="btn btn-secondary btn-sm">
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
              container,
              ...currentParentChoice(),
            });
            if (result.createdParent) createdParentId = result.createdParent.id;

            if (result.cancelled) {
              mergeApiBtn.disabled = false;
              mergeApiBtn.textContent = originalText;
            } else if (!result.success) {
              errorEl.innerHTML = `<div class="tm-error-message">Merge failed: ${escapeHtml(result.error)}</div>`;
              mergeApiBtn.disabled = false;
              mergeApiBtn.textContent = originalText;
            }
          });
        }
        return;
      }

      // Parse "name 'X' is used as alias for 'Y'"
      const aliasUsedMatch = message.match(/name '([^']+)' is used as alias for '([^']+)'/i);
      if (aliasUsedMatch) {
        const [, conflictName, otherTagName] = aliasUsedMatch;
        const otherTag = localTags.find(t => t.name === otherTagName);

        errorEl.innerHTML = `
          <div class="tm-error-message">
            Cannot use "${escapeHtml(conflictName)}" - it's an alias on "${escapeHtml(otherTagName)}".
          </div>
          <div class="tm-error-actions">
            ${otherTag ? `
              <a href="${escapeHtml(stashPath(`/tags/${otherTag.id}`))}" target="_blank" class="btn btn-secondary btn-sm">
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
      errorEl.innerHTML = `<div class="tm-error-message">${escapeHtml(message)}</div>`;
      errorEl.style.display = 'block';
    }

    // F12: Apply validates first (applyDiff), stays disabled and ignores re-clicks
    // until it finishes, and reuses a parent that a failed attempt already created.
    const applyBtn = modal.querySelector('.tm-apply-btn');
    let applying = false;
    let createdParentId = null;

    // The Parent row's current choice, as Apply and both Merge buttons send it.
    // A '__create__' parent an earlier attempt already created is reused.
    function currentParentChoice() {
      return {
        parentId: selectedParentId === '__create__' && createdParentId ? createdParentId : selectedParentId,
        rememberMapping: !!modal.querySelector('#tm-remember-mapping')?.checked,
      };
    }

    applyBtn.addEventListener('click', async () => {
      if (applying) return;
      applying = true;
      applyBtn.disabled = true;
      let closed = false;
      try {
        const nameChoice = modal.querySelector('input[name="tm-name"]:checked').value;
        const descChoice = modal.querySelector('input[name="tm-desc"]:checked').value;
        const errorEl = modal.querySelector('#tm-diff-error');

        // Hide any previous error
        errorEl.style.display = 'none';
        errorEl.innerHTML = '';

        // Use the selected stash-box endpoint
        const endpoint = selectedStashBox?.endpoint || settings.stashdbEndpoint;

        const result = await applyDiff({
          tag,
          stashdbTag,
          endpoint,
          nameChoice,
          descChoice,
          aliases: editableAliases,
          ...currentParentChoice(),
        });
        if (result.createdParent) createdParentId = result.createdParent.id;

        if (result.ok) {
          closed = true;
          modal.remove();
          showStatus(`Matched "${tag.name}" to "${stashdbTag.name}"`, 'success');
          try {
            renderPage(container);
          } catch (renderErr) {
            console.error('[tagManager] re-render after Apply failed:', renderErr);
          }
        } else if (result.validationErrors) {
          showValidationError(errorEl, result.validationErrors, endpoint, result.sanitizedAliases);
        } else if (result.stage === 'update') {
          showSaveError(errorEl, result.error, endpoint, result.sanitizedAliases);
        } else {
          errorEl.innerHTML = `<div class="tm-error-message">${escapeHtml(result.error)}</div>`;
          errorEl.style.display = 'block';
        }
      } finally {
        applying = false;
        if (!closed) applyBtn.disabled = false;
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
   * Create a new tag via GraphQL. Returns the full tag (the fields localTags
   * entries carry), so callers can track it without a refetch.
   */
  async function createTag(input) {
    const query = `
      mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) {
          id
          name
          description
          aliases
          stash_ids {
            endpoint
            stash_id
          }
          parents {
            id
            name
          }
        }
      }
    `;

    const data = await graphqlRequest(query, { input });
    return data?.tagCreate;
  }

  /**
   * Delete a tag via GraphQL (used to undo a create whose follow-up failed).
   * @param {string} id
   */
  async function destroyTag(id) {
    const query = `
      mutation TagDestroy($input: TagDestroyInput!) {
        tagDestroy(input: $input)
      }
    `;

    const data = await graphqlRequest(query, { input: { id } });
    return data?.tagDestroy;
  }

  /**
   * Merge tags via GraphQL - merges source tags into destination tag.
   * This reassigns all entities (scenes, images, galleries, performers, studios, groups, markers)
   * from source tags to destination, merges aliases and stash_ids, then deletes source tags.
   *
   * @param {string[]} sourceIds - Array of tag IDs to merge (will be deleted)
   * @param {string} destinationId - Tag ID to merge into (will be kept)
   * @param {object} [values] - TagUpdateInput applied to the destination in the
   *   same transaction (Stash v0.31+ only; check supportsMergeValues() first)
   * @returns {object} - The updated destination tag
   */
  async function mergeTags(sourceIds, destinationId, values) {
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

    const input = { source: sourceIds, destination: destinationId };
    if (values) input.values = values;
    const data = await graphqlRequest(query, { input });
    return data?.tagsMerge;
  }

  /**
   * Show modal with all matches for manual selection
   */
  function showMatchesModal(tagId, container) {
    const tag = localTags.find(t => t.id === tagId);
    if (!tag) return;

    // Blacklisted matches are dropped; data-index keeps the ORIGINAL index
    // into matchResults[tagId] (showDiffDialog reads the unfiltered array).
    const visible = visibleMatches(matchResults[tagId]);
    const hiddenCount = (matchResults[tagId]?.length || 0) - visible.length;
    const renderMatchItems = (list) => list.map(({ match: m, index: i }) => `
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
              `).join('');
    const hiddenNotice = (n) => n > 0 ? `${n} tag${n > 1 ? 's' : ''} hidden by blacklist` : '';

    const modal = document.createElement('div');
    modal.className = 'tm-modal-backdrop';
    modal.innerHTML = `
      <div class="tm-modal tm-modal-wide">
        <div class="tm-modal-header">
          <h3>Matches for: ${escapeHtml(tag.name)}</h3>
          <div class="tm-blacklist-notice" id="tm-blacklist-notice" ${hiddenCount > 0 ? '' : 'style="display:none"'}>${hiddenNotice(hiddenCount)}</div>
          <button class="tm-close-btn">&times;</button>
        </div>
        <div class="tm-modal-body">
          <div class="tm-search-row">
            <input type="text" id="tm-manual-search" class="form-control" placeholder="Search StashDB..." value="${escapeHtml(tag.name)}">
            <button class="btn btn-primary" id="tm-manual-search-btn">Search</button>
          </div>
          <div class="tm-matches-list" id="tm-matches-list">
            ${visible.length
              ? renderMatchItems(visible)
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
        });
        matchResults[tagId] = result.matches || [];
        delete matchErrors[tagId];
        if (result.fuzzy_unavailable) showFuzzyUnavailableHint();

        // Re-render matches list
        const listEl = modal.querySelector('#tm-matches-list');
        const newVisible = visibleMatches(matchResults[tagId]);
        listEl.innerHTML = newVisible.length
          ? renderMatchItems(newVisible)
          : '<div class="tm-no-matches">No matches found.</div>';

        const noticeEl = modal.querySelector('#tm-blacklist-notice');
        if (noticeEl) {
          const hidden = (matchResults[tagId]?.length || 0) - newVisible.length;
          noticeEl.textContent = hiddenNotice(hidden);
          noticeEl.style.display = hidden > 0 ? '' : 'none';
        }

        // Re-attach select handlers
        attachSelectHandlers();
      } catch (e) {
        showStatus(`Search error: ${backendErrorText(e)}`, 'error');
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
   * Once per page visit, say that searches run without fuzzy matching: the
   * backend has no stash-box tag cache yet (search result `fuzzy_unavailable`).
   * Nothing when fuzzy search is turned off.
   */
  function showFuzzyUnavailableHint() {
    if (fuzzyHintShown || !settings.enableFuzzySearch) return;
    fuzzyHintShown = true;
    showStatus('Fuzzy matching is unavailable until the stash-box tags are cached. Use Refresh Cache.', 'info');
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
        fuzzyHintShown = false;
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading configuration...</div></div>';

        // Ensure defaults are loaded/backfilled before reading settings, so
        // loadSettings sees a populated DEFAULTS and the backfill can't race.
        await ensureDefaultsInitialized();
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

        // Load tags from cache (or fetch if no cache); Browse needs them even without fuzzy search
        containerRef.current.innerHTML = '<div class="tag-manager"><div class="tm-loading">Loading tag cache...</div></div>';
        await loadTagsFromCache(containerRef.current);

        setInitialized(true);
        console.debug("[tagManager] Initialization complete");
        _activeContainer = containerRef.current;
        renderPage(containerRef.current);
        if (cacheStatus?.error) showStatus(cacheStatus.error, 'error');
      }

      init();

      // F16: tag links navigate inside the SPA on a plain left click.
      const linkRoot = containerRef.current;
      if (linkRoot) linkRoot.addEventListener('click', handleInternalLinkClick);

      // #124: refresh tag data when returning to the tab (e.g. after fixing a
      // conflicting tag elsewhere). Listeners are cleaned up on unmount.
      const onFocus = () => scheduleRefresh();
      const onVisibility = () => {
        if (document.visibilityState === "visible") scheduleRefresh();
      };
      window.addEventListener("focus", onFocus);
      document.addEventListener("visibilitychange", onVisibility);

      return () => {
        if (linkRoot) linkRoot.removeEventListener('click', handleInternalLinkClick);
        window.removeEventListener("focus", onFocus);
        document.removeEventListener("visibilitychange", onVisibility);
        if (_refreshTimer) {
          clearTimeout(_refreshTimer);
          _refreshTimer = null;
        }
        _activeContainer = null;
      };
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
  let tagSearchEscHandler = null;
  let draggedTagId = null;
  let draggedFromParentId = null;
  let selectedTagId = null;
  let copiedTagId = null;

  // Edit mode state
  let isEditMode = false;
  let pendingChanges = [];
  let originalParentMap = new Map(); // tagId -> array of parent ids (snapshot at edit start)

  /**
   * Drop all hierarchy edit/selection state. Runs when the hierarchy page
   * mounts and unmounts so nothing stale leaks between visits.
   */
  function resetHierarchyEditState() {
    isEditMode = false;
    pendingChanges = [];
    originalParentMap.clear();
    selectedTagId = null;
    copiedTagId = null;
    draggedTagId = null;
    draggedFromParentId = null;
    hideContextMenu();
    closeTagSearchDialog();
    document.getElementById('th-changes-panel')?.remove();
  }

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
      const allSaved = await savePendingChanges();
      if (!allSaved) {
        // Keep failed changes pending and stay in edit mode so they can be retried
        applyPendingChangesToTree();
        renderChangesPanel();
        return;
      }
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
    if (pendingChanges.length === 0) return true;

    console.debug('[tagManager] savePendingChanges: Saving', pendingChanges.length, 'changes:', pendingChanges);

    // Group pending changes by tag
    const changesByTag = new Map(); // tagId -> changes
    for (const change of pendingChanges) {
      if (!changesByTag.has(change.tagId)) changesByTag.set(change.tagId, []);
      changesByTag.get(change.tagId).push(change);
    }

    const failedTagIds = new Set();
    const errors = [];
    let savedCount = 0;

    for (const [tagId, changes] of changesByTag) {
      const tag = hierarchyTags.find(t => t.id === tagId);
      try {
        // Read the tag's CURRENT parents so edits made elsewhere are not overwritten
        const parentSet = new Set(await fetchTagParentIds(tagId));
        for (const change of changes) {
          if (change.type === 'add-parent') {
            parentSet.add(change.parentId);
          } else {
            parentSet.delete(change.parentId);
          }
        }
        const result = Array.from(parentSet);
        await updateTagParents(tagId, result);

        savedCount += changes.length;
        // Keep local state in step with the server for the saved tag
        originalParentMap.set(tagId, result);
        if (tag) {
          tag.parents = result.map(pid => ({ id: pid }));
        }
      } catch (err) {
        failedTagIds.add(tagId);
        errors.push(`Failed to update "${tag?.name || tagId}": ${err.message}`);
      }
    }

    // Successful changes are done; failed ones stay pending
    pendingChanges = pendingChanges.filter(c => failedTagIds.has(c.tagId));

    if (errors.length > 0) {
      showToast(`Some changes failed (still pending):\n${errors.join('\n')}`, 'error');
      return false;
    }
    showToast(`Saved ${savedCount} change${savedCount !== 1 ? 's' : ''}`);
    return true;
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
    document.removeEventListener('click', hideContextMenu);
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
    closeTagSearchDialog(); // never stack a second dialog/listener
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
    tagSearchEscHandler = (e) => {
      if (e.key === 'Escape') closeTagSearchDialog();
    };
    document.addEventListener('keydown', tagSearchEscHandler);

    input.focus();
  }

  /**
   * Close the tag search dialog
   */
  function closeTagSearchDialog() {
    if (tagSearchEscHandler) {
      document.removeEventListener('keydown', tagSearchEscHandler);
      tagSearchEscHandler = null;
    }
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
      // Only build the DOM for expanded branches; expanding renders on demand
      const childNodes = isExpanded
        ? node.childNodes.map(child => renderTreeNode(child, false)).join('')
        : '';
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
            <a ${internalLinkAttrs(`/tags/${node.id}`)} class="th-name">${escapeHtml(node.name)}</a>${multiParentBadge}
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
  /**
   * Is this element somewhere a user types (input, select, textarea, contentEditable)?
   */
  function isEditableElement(el) {
    if (!el) return false;
    const tag = String(el.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return true;
    return el.isContentEditable === true || el.contentEditable === 'true';
  }

  /**
   * Should a tree shortcut (copy/paste/delete) act for this key event?
   * Rule: never while an editable element has focus; otherwise only when focus
   * is inside the tree container, or nothing is focused (body) and the event
   * target is inside the tree.
   */
  function shouldHandleHierarchyKey(e, activeElement) {
    const inTree = (el) => !!(el && typeof el.closest === 'function' && el.closest('.tag-hierarchy-container'));
    if (isEditableElement(activeElement) || isEditableElement(e && e.target)) return false;
    if (inTree(activeElement)) return true;
    const nothingFocused = !activeElement || activeElement === document.body;
    return nothingFocused && inTree(e && e.target);
  }

  function handleHierarchyKeyboard(e) {
    // Only handle if hierarchy page is active
    if (!document.querySelector('.tag-hierarchy-container')) return;

    const key = String(e.key || '').toLowerCase();
    const mod = e.ctrlKey || e.metaKey;
    const treeKey = shouldHandleHierarchyKey(e, document.activeElement);

    // Ctrl/Cmd+C - copy selected tag (leave native copy alone if text is selected)
    const hasTextSelection = typeof window.getSelection === 'function' &&
      String(window.getSelection() || '') !== '';
    if (treeKey && mod && key === 'c' && selectedTagId && !hasTextSelection) {
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

    // Ctrl/Cmd+V - paste (add copied tag as child of selected)
    if (treeKey && mod && key === 'v' && copiedTagId && selectedTagId && copiedTagId !== selectedTagId) {
      e.preventDefault();

      if (wouldCreateCircularRef(selectedTagId, copiedTagId)) {
        showToast('Cannot create circular reference', 'error');
        return;
      }

      addParent(copiedTagId, selectedTagId);
    }

    // Delete/Backspace - remove selected tag from its current parent
    if (treeKey && (e.key === 'Delete' || e.key === 'Backspace') && selectedTagId) {
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
  /**
   * Find a tag's child nodes in the tree (every instance of a tag shares them).
   */
  function findTreeChildNodes(tagId) {
    const seen = new Set();
    function walk(nodes) {
      for (const node of nodes) {
        if (node.id === tagId) return node.childNodes;
        if (seen.has(node.id)) continue;
        seen.add(node.id);
        const found = walk(node.childNodes);
        if (found) return found;
      }
      return null;
    }
    return walk(hierarchyTree) || [];
  }

  /**
   * Add every tag that has children to expandedNodes (for Expand All).
   */
  function expandAllNodes() {
    const seen = new Set();
    function walk(nodes) {
      for (const node of nodes) {
        if (node.childNodes.length === 0 || seen.has(node.id)) continue;
        seen.add(node.id);
        expandedNodes.add(node.id);
        walk(node.childNodes);
      }
    }
    walk(hierarchyTree);
  }

  function attachHierarchyEventHandlers(container) {
    attachNodeHandlers(container, container);

    // Expand All button
    const expandAllBtn = container.querySelector('#th-expand-all');
    if (expandAllBtn) {
      expandAllBtn.addEventListener('click', () => {
        // Collapsed branches have no DOM yet, so mark everything expanded and re-render
        expandAllNodes();
        renderHierarchyPage(container);
        if (isEditMode) renderChangesPanel();
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
  }

  /**
   * Attach per-node handlers (toggle, context menu, hover, drag/drop, select)
   * to every node under `scope`. Used for the whole page and for lazily
   * rendered subtrees.
   */
  function attachNodeHandlers(container, scope) {
    // Toggle expand/collapse
    scope.querySelectorAll('.th-toggle').forEach(toggle => {
      toggle.addEventListener('click', () => {
        const tagId = toggle.dataset.tagId;
        if (!tagId) return;

        const nodeEl = toggle.closest('.th-node');
        const childrenContainer = nodeEl
          ? Array.from(nodeEl.children).find(c => c.classList.contains('th-children'))
          : null;
        if (!childrenContainer) return;

        if (expandedNodes.has(tagId)) {
          expandedNodes.delete(tagId);
          childrenContainer.classList.remove('th-expanded');
          toggle.innerHTML = '&#9654;';  // Right arrow
        } else {
          expandedNodes.add(tagId);
          // Collapsed branches are not rendered up front; build them now
          if (!childrenContainer.firstElementChild) {
            childrenContainer.innerHTML = findTreeChildNodes(tagId)
              .map(child => renderTreeNode(child, false)).join('');
            attachNodeHandlers(container, childrenContainer);
          }
          childrenContainer.classList.add('th-expanded');
          toggle.innerHTML = '&#9660;';  // Down arrow
        }
      });
    });

    // Context menu on right-click
    scope.querySelectorAll('.th-node').forEach(node => {
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
    scope.querySelectorAll('.th-node').forEach(node => {
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
    scope.querySelectorAll('.th-node').forEach(node => {
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

    // Click to select (for keyboard operations)
    scope.querySelectorAll('.th-node-content').forEach(content => {
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
      // Start every visit from a clean edit/selection state
      resetHierarchyEditState();

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

      // F16: tag links navigate inside the SPA on a plain left click.
      const linkRoot = containerRef.current;
      if (linkRoot) linkRoot.addEventListener('click', handleInternalLinkClick);

      // Cleanup: remove keyboard handler when component unmounts
      return () => {
        if (linkRoot) linkRoot.removeEventListener('click', handleInternalLinkClick);
        document.removeEventListener('keydown', handleHierarchyKeyboard);
        resetHierarchyEditState();
      };
    }, []);

    return React.createElement('div', {
      ref: containerRef,
      className: 'tag-hierarchy-container',
      tabIndex: 0  // focusable so clicks inside the tree scope keyboard shortcuts
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

  /** F16: on the Tags list page, under Stash's base path (e.g. /stash/tags). */
  function isTagsListPage() {
    const here = String(window.location.pathname || '').replace(/\/+$/, '');
    return here === stashPath('/tags');
  }

  /**
   * Inject Tag Manager and Tag Hierarchy buttons into Tags list page toolbar
   */
  function injectNavButtons() {
    // Only run on Tags list page
    if (!isTagsListPage()) {
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
    tmBtn.addEventListener('click', () => navigateTo(ROUTE_PATH));

    // Create Tag Hierarchy button
    const thBtn = document.createElement('button');
    thBtn.id = 'th-nav-button';
    thBtn.className = 'btn btn-secondary';
    thBtn.title = 'Tag Hierarchy';
    thBtn.style.marginLeft = '0.25rem';
    thBtn.appendChild(createHierarchyIcon());
    thBtn.addEventListener('click', () => navigateTo(HIERARCHY_ROUTE_PATH));

    // Insert both buttons after the insertion point
    insertionPoint.parentNode.insertBefore(tmBtn, insertionPoint.nextSibling);
    tmBtn.parentNode.insertBefore(thBtn, tmBtn.nextSibling);
    console.debug('[tagManager] Nav buttons injected on Tags page');
  }

  /**
   * F16: at most one pending injection check per animation frame (setTimeout 0
   * where requestAnimationFrame is missing). The check is cheap and only calls
   * injectNavButtons when on the Tags page with the button missing.
   */
  let _navInjectScheduled = false;
  function scheduleNavButtonInjection() {
    if (_navInjectScheduled) return;
    _navInjectScheduled = true;
    const run = () => {
      _navInjectScheduled = false;
      if (isTagsListPage() && !document.querySelector('#tm-nav-button')) injectNavButtons();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    else setTimeout(run, 0);
  }

  /**
   * Watch for the Tags page toolbar (SPA navigation, late renders) and inject the
   * buttons: one call now, then one body observer for the page's lifetime whose
   * callback is debounced to a frame.
   */
  function setupNavButtonInjection() {
    injectNavButtons();
    const observer = new MutationObserver(scheduleNavButtonInjection);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // Initialize
  ensureDefaultsInitialized();
  registerRoute();
  setupNavButtonInjection();
  console.log('[tagManager] Plugin loaded');
  // Test hook: only active when a test harness sets window.__TAG_MANAGER_TEST__.
  // Add functions/state here as tests need them.
  if (window.__TAG_MANAGER_TEST__) {
    window.__TAG_MANAGER_TEST__.exports = {
      parseBlacklist,
      isBlacklisted,
      visibleMatches,
      bestVisibleMatch,
      saveBlacklist,
      callBackend,
      formatBackendError,
      loadTagsFromCache,
      buildParentOptions,
      shouldShowParentControls,
      supportsMergeValues,
      confirmTagMerge,
      performTagMerge,
      detectImportConflicts,
      sanitizeAliasesForImport,
      buildMergeIntoExistingInput,
      summarizeImportResult,
      renderConflictResolutionModal,
      createConflictSession,
      resolveConflictRow,
      recheckConflictRows,
      conflictRowHtml,
      handleImportSelected,
      buildLocalTagIndex,
      importAllCandidates,
      tagsToSearchOnPage,
      requestImportCancel,
      findLocalTagByName,
      findConflictingTag,
      handleImportAll,
      hasStashIdForEndpoint,
      importSelectedTags,
      handleUpdateLinkedTags,
      showDiffDialog,
      applyDiff,
      wouldCreateCircularRef,
      buildTagTree,
      getTreeStats,
      resetHierarchyEditState,
      savePendingChanges,
      exitEditMode,
      fetchAllTagsWithHierarchy,
      showTagSearchDialog,
      closeTagSearchDialog,
      renderTreeNode,
      handleHierarchyKeyboard,
      shouldHandleHierarchyKey,
      stashPath,
      navigateTo,
      handleInternalLinkClick,
      injectNavButtons,
      isTagsListPage,
      renderTagRow,
      parseIntSetting,
      loadSettings,
      migrateCategoryMappings,
      getCategoryMapping,
      setCategoryMapping,
      deleteCategoryMapping,
      loadCategoryMappings,
      saveCategoryMappings,
      resolveCategoryParents,
      renderPage,
      searchAllOnPage,
      searchSingleTag,
    };
    window.__TAG_MANAGER_TEST__.getState = () => ({
      localTags, settings, stashBoxes, selectedStashBox, stashdbTags, matchResults, matchErrors,
      categoryMappings, tagBlacklist, isImporting, pendingChanges, isEditMode, cacheStatus,
      selectedForImport, hierarchyTags, hierarchyTree, expandedNodes, selectedTagId, copiedTagId,
      originalParentMap, blacklistDraft, blacklistPanelOpen, tagBlacklistRaw,
    });
    window.__TAG_MANAGER_TEST__.setState = (patch) => {
      if ("localTags" in patch) localTags = patch.localTags;
      if ("settings" in patch) settings = patch.settings;
      if ("stashBoxes" in patch) stashBoxes = patch.stashBoxes;
      if ("selectedStashBox" in patch) selectedStashBox = patch.selectedStashBox;
      if ("stashdbTags" in patch) stashdbTags = patch.stashdbTags;
      if ("matchResults" in patch) matchResults = patch.matchResults;
      if ("matchErrors" in patch) matchErrors = patch.matchErrors;
      if ("categoryMappings" in patch) categoryMappings = patch.categoryMappings;
      if ("tagBlacklist" in patch) tagBlacklist = patch.tagBlacklist;
      if ("isImporting" in patch) isImporting = patch.isImporting;
      if ("pendingChanges" in patch) pendingChanges = patch.pendingChanges;
      if ("isEditMode" in patch) isEditMode = patch.isEditMode;
      if ("selectedForImport" in patch) selectedForImport = patch.selectedForImport;
      if ("hierarchyTags" in patch) hierarchyTags = patch.hierarchyTags;
      if ("hierarchyTree" in patch) hierarchyTree = patch.hierarchyTree;
      if ("expandedNodes" in patch) expandedNodes = patch.expandedNodes;
      if ("selectedTagId" in patch) selectedTagId = patch.selectedTagId;
      if ("copiedTagId" in patch) copiedTagId = patch.copiedTagId;
      if ("originalParentMap" in patch) originalParentMap = patch.originalParentMap;
      if ("blacklistDraft" in patch) blacklistDraft = patch.blacklistDraft;
      if ("blacklistPanelOpen" in patch) blacklistPanelOpen = patch.blacklistPanelOpen;
      if ("tagBlacklistRaw" in patch) tagBlacklistRaw = patch.tagBlacklistRaw;
    };
  }
})();
