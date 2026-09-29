(function () {
  "use strict";

  const PLUGIN_ID = "performerImageSearch";

  // Default settings
  const DEFAULTS = {
    searchSuffix: "pornstar",
    layout: "All",
    // Source toggles: on by default, except DuckDuckGo (rate-limited)
    enableBabepedia: true,
    enablePornPics: true,
    enableFreeOnes: true,
    enableEliteBabes: true,
    enableBoobpedia: true,
    enableJavDatabase: true,
    enableDuckDuckGo: false,
  };

  // All available sources (will be filtered by settings)
  const ALL_SOURCES = [
    { id: "babepedia", settingKey: "enableBabepedia" },
    { id: "pornpics", settingKey: "enablePornPics" },
    { id: "freeones", settingKey: "enableFreeOnes" },
    { id: "elitebabes", settingKey: "enableEliteBabes" },
    { id: "boobpedia", settingKey: "enableBoobpedia" },
    { id: "javdatabase", settingKey: "enableJavDatabase" },
    { id: "duckduckgo", settingKey: "enableDuckDuckGo" },
  ];

  // Aspect ratio classes: Portrait < 0.9, Square 0.9..1.1 inclusive, Landscape > 1.1
  const ASPECT_TESTS = {
    Portrait: (r) => r < 0.9,
    Square: (r) => r >= 0.9 && r <= 1.1,
    Landscape: (r) => r > 1.1,
  };

  // "all"/"any" in any case mean no filter; unknown values fall back to it too
  function normalizeLayout(value) {
    const v = String(value == null ? "" : value).trim().toLowerCase();
    for (const name of Object.keys(ASPECT_TESTS)) {
      if (name.toLowerCase() === v) return name;
    }
    return "All";
  }

  // Active sources (filtered by settings, populated at runtime)
  let SOURCES = [];

  // State
  let modalRoot = null;
  let currentPerformerId = null;
  let currentPerformerName = null;
  let allResults = []; // All fetched results
  let filteredResults = []; // Results after applying filters
  let imageDimensions = {}; // Map of image URL -> {width, height, rank}; 0x0 means unknown
  let previewResult = null; // The result shown in the preview
  let gridContainer = null; // The #pis-results element the grid nodes live in
  let gridNodes = new Map(); // image URL -> result item element (kept across filter changes)
  let emptyNote = null; // "No images match" note inside the grid
  let loadedCount = 0; // Number of images that have loaded
  let isLoading = false;
  let previewImage = null;
  let currentPreviewIndex = -1;
  let seenImageUrls = new Set(); // For deduplication across sources
  let completedSources = []; // Track which sources have completed
  let pendingSources = []; // Track which sources are still loading
  let sourceErrors = []; // Track per-source failures: { source, message }
  let sourceStatus = {}; // source -> { status, count, detail }; "pending" until it answers
  let searchGeneration = 0; // bumped whenever results/modal are superseded
  let activeControllers = []; // AbortControllers of in-flight plugin calls
  const SOURCE_TIMEOUT_MS = 45000; // client-side cap per plugin call
  const STATUS_REFRESH_MS = 250; // thumbnail loads refresh the status line at most this often
  let statusRefreshTimer = null;

  /**
   * Get the GraphQL endpoint URL
   */
  function getGraphQLUrl() {
    const baseEl = document.querySelector("base");
    const baseURL = baseEl ? baseEl.getAttribute("href") : "/";
    return `${baseURL}graphql`;
  }

  /**
   * Make a GraphQL request using fetch
   */
  async function graphqlRequest(query, variables = {}, signal = undefined) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables }),
      signal,
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
   * Get plugin settings from Stash configuration
   */
  async function getPluginSettings() {
    try {
      const query = `
        query Configuration {
          configuration {
            plugins
          }
        }
      `;
      const data = await graphqlRequest(query);
      const pluginConfig = data?.configuration?.plugins?.[PLUGIN_ID];

      // Build settings object with defaults
      const settings = {
        searchSuffix: pluginConfig?.defaultSearchSuffix || DEFAULTS.searchSuffix,
        layout: normalizeLayout(pluginConfig?.defaultLayout),
      };

      // Process source toggles
      // Stash BOOLEAN settings are undefined when never set: use that source's default
      for (const source of ALL_SOURCES) {
        const configValue = pluginConfig?.[source.settingKey];
        settings[source.settingKey] = typeof configValue === "boolean"
          ? configValue
          : DEFAULTS[source.settingKey];
      }

      // Build active SOURCES array based on settings
      SOURCES = ALL_SOURCES
        .filter(source => settings[source.settingKey])
        .map(source => source.id);

      console.debug("[PerformerImageSearch] Active sources:", SOURCES);

      return settings;
    } catch (e) {
      console.error("[PerformerImageSearch] Failed to get settings:", e);
      // On error, use each source's default
      SOURCES = ALL_SOURCES.filter(source => DEFAULTS[source.settingKey]).map(source => source.id);
      return { ...DEFAULTS };
    }
  }

  /**
   * Search images using the Python backend via runPluginOperation
   * @param {string} query - Search query
   * @param {string} performerName - Performer name
   * @param {string|null} source - Specific source to search (null for all)
   * @param {AbortSignal} [signal] - Aborts the request
   * @returns the plugin output: { results, status, error?, warnings? }. An
   *   `error` does not throw: results that arrive with it are kept.
   */
  async function searchImages(query, performerName, source = null, signal = undefined) {
    try {
      const gqlQuery = `
        mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
          runPluginOperation(plugin_id: $plugin_id, args: $args)
        }
      `;

      const args = {
        mode: "search",
        query: query,
        performerName: performerName,
      };

      if (source) {
        args.source = source;
      }

      const data = await graphqlRequest(gqlQuery, {
        plugin_id: PLUGIN_ID,
        args: args,
      }, signal);

      const output = data?.runPluginOperation;

      if (!output) {
        throw new Error("No response from search plugin");
      }

      return output;
    } catch (e) {
      console.error("[PerformerImageSearch] Search failed:", e);
      throw e;
    }
  }

  const positive = (n) => typeof n === "number" && Number.isFinite(n) && n > 0;

  /**
   * Known size of a result: the backend's, else what loaded in the browser; null if unknown
   */
  function getDimensions(result) {
    if (positive(result.width) && positive(result.height)) {
      return { width: result.width, height: result.height };
    }
    const d = imageDimensions[result.image];
    return d && positive(d.width) && positive(d.height) ? d : null;
  }

  /**
   * Apply the aspect ratio filter. Results of unknown size pass every filter.
   */
  function applyFilters() {
    const layoutFilter = normalizeLayout(document.getElementById("pis-layout")?.value);
    const test = ASPECT_TESTS[layoutFilter];
    if (!test) {
      filteredResults = [...allResults];
      return;
    }
    filteredResults = allResults.filter((result) => {
      const dims = getDimensions(result);
      return !dims || test(dims.width / dims.height);
    });
  }

  /**
   * Record a size for an image URL. A higher rank (2 full image, 1 thumbnail,
   * 0 unknown) replaces a lower one. Returns true when the stored entry changed.
   */
  function recordDimensions(url, width, height, rank) {
    if (!url) return false;
    const known = positive(width) && positive(height);
    const cur = imageDimensions[url];
    if (!cur) loadedCount++;
    if (cur && (!known || cur.rank >= rank)) return false;
    imageDimensions[url] = known ? { width, height, rank } : { width: 0, height: 0, rank: 0 };
    return !cur || known;
  }

  function refreshGrid() {
    applyFilters();
    renderResults();
    updateFilterStatus();
  }

  /**
   * Update status with filter and source progress
   */
  function updateFilterStatus() {
    // Only the current results count: imageDimensions can hold other URLs
    const loaded = allResults.filter((r) => imageDimensions[r.image]).length;
    const total = allResults.length;

    // Build source status
    let sourceNote = "";
    if (pendingSources.length > 0) {
      sourceNote = ` | Searching: ${pendingSources.join(", ")}`;
    } else if (completedSources.length > 0) {
      sourceNote = ` | All sources finished`;
    }

    if (pendingSources.length > 0) {
      showStatus(`Found ${filteredResults.length} images (${loaded}/${total} loaded)${sourceNote}`, "loading");
    } else if (total === 0 && sourceErrors.length >= SOURCES.length && sourceErrors.length > 0) {
      // Every source failed - almost always the server cannot reach the image
      // sites (no internet egress, DNS, or the sites blocking the server IP).
      showStatus(`Couldn't reach any image source (${sourceErrors.length} failed). Check the Stash server's internet access. First error: ${sourceErrors[0].message}`, "error");
    } else if (total === 0 && sourceErrors.length > 0) {
      showStatus(`No images found - ${sourceErrors.length} of ${SOURCES.length} sources failed (e.g. ${sourceErrors[0].message})`, "error");
    } else if (total === 0) {
      showStatus(`No images found for "${currentPerformerName}"`, "success");
    } else {
      const failed = sourceErrors.length > 0
        ? ` (${sourceErrors.length} source${sourceErrors.length > 1 ? "s" : ""} failed)`
        : "";
      const loadNote = loaded < total ? `, loading ${loaded}/${total}` : "";
      showStatus(`${filteredResults.length} of ${total} images match filters${failed}${loadNote} | All sources finished`, "success");
    }
    renderSourceChips();
  }

  /**
   * Refresh the status line soon. Thumbnails load in bursts, so each burst gives
   * one refresh, at most every STATUS_REFRESH_MS.
   */
  function scheduleStatusRefresh() {
    if (statusRefreshTimer !== null) return;
    statusRefreshTimer = setTimeout(() => {
      statusRefreshTimer = null;
      updateFilterStatus();
    }, STATUS_REFRESH_MS);
  }

  /**
   * Render one status chip per enabled source into #pis-source-chips
   */
  function renderSourceChips() {
    const el = document.getElementById("pis-source-chips");
    if (!el) return;
    el.innerHTML = SOURCES.map((source) => {
      const st = sourceStatus[source] || { status: "pending" };
      let label = st.status;
      if (st.status === "ok" || st.status === "partial") label += ` (${st.count || 0})`;
      const title = st.detail ? ` title="${escapeHtml(st.detail)}"` : "";
      return `<span class="pis-chip pis-chip-${escapeHtml(st.status)}"${title}>${escapeHtml(source)}: ${escapeHtml(label)}</span>`;
    }).join("");
  }

  /**
   * Abort every in-flight plugin call and invalidate their responses
   */
  function supersedeSearches() {
    searchGeneration++;
    for (const c of activeControllers) c.abort();
    activeControllers = [];
  }

  /**
   * Set performer image using Stash GraphQL API
   */
  async function setPerformerImage(performerId, imageUrl) {
    try {
      const query = `
        mutation PerformerUpdate($input: PerformerUpdateInput!) {
          performerUpdate(input: $input) {
            id
            image_path
          }
        }
      `;

      const data = await graphqlRequest(query, {
        input: {
          id: performerId,
          image: imageUrl,
        },
      });

      return data?.performerUpdate;
    } catch (e) {
      console.error("[PerformerImageSearch] Failed to set image:", e);
      throw e;
    }
  }

  /**
   * Create and show the search modal
   */
  function showModal(performerId, performerName) {
    supersedeSearches();
    isLoading = false;
    pendingSources = [];
    sourceStatus = {};
    currentPerformerId = performerId;
    currentPerformerName = performerName;
    allResults = [];
    filteredResults = [];
    imageDimensions = {};
    loadedCount = 0;
    previewImage = null;
    previewResult = null;
    currentPreviewIndex = -1;

    // Create modal if it doesn't exist
    if (!modalRoot) {
      modalRoot = document.createElement("div");
      modalRoot.id = "performer-image-search-modal-root";
      document.body.appendChild(modalRoot);
    }

    // Capture phase on window: runs before Stash's Mousetrap (bubble, on document)
    window.removeEventListener("keydown", handleModalKeydown, true);
    window.addEventListener("keydown", handleModalKeydown, true);

    renderModal();
  }

  /**
   * Hide and cleanup the modal
   */
  function hideModal() {
    supersedeSearches();
    window.removeEventListener("keydown", handleModalKeydown, true);
    if (modalRoot) {
      modalRoot.innerHTML = "";
    }
    currentPerformerId = null;
    currentPerformerName = null;
    allResults = [];
    filteredResults = [];
    imageDimensions = {};
    loadedCount = 0;
    previewImage = null;
    previewResult = null;
    currentPreviewIndex = -1;
    seenImageUrls = new Set();
    completedSources = [];
    pendingSources = [];
    sourceErrors = [];
    sourceStatus = {};
    isLoading = false;
  }

  /**
   * Render the modal content
   */
  async function renderModal() {
    if (!modalRoot) return;

    const generation = searchGeneration;
    const settings = await getPluginSettings();
    // Closed, or another performer opened, while settings were loading
    if (generation !== searchGeneration || !currentPerformerName) return;
    const defaultQuery = `${currentPerformerName} ${settings.searchSuffix}`.trim();

    modalRoot.innerHTML = `
      <div class="pis-modal-backdrop" onclick="window.pisHideModal()">
        <div class="pis-modal" onclick="event.stopPropagation()">
          <div class="pis-modal-header">
            <h3>Search Images for ${escapeHtml(currentPerformerName)}</h3>
            <button class="pis-close-btn" onclick="window.pisHideModal()">&times;</button>
          </div>

          <div class="pis-modal-controls">
            <div class="pis-search-row">
              <input
                type="text"
                id="pis-search-query"
                class="pis-input"
                value="${escapeHtml(defaultQuery)}"
                placeholder="Search query..."
              />
              <button class="pis-btn pis-btn-primary" onclick="window.pisSearch()">Search</button>
            </div>

            <div class="pis-filter-row">
              <label>
                Aspect:
                <select id="pis-layout" class="pis-select">
                  <option value="All" ${settings.layout === "All" ? "selected" : ""}>Any</option>
                  <option value="Portrait" ${settings.layout === "Portrait" ? "selected" : ""}>Portrait</option>
                  <option value="Landscape" ${settings.layout === "Landscape" ? "selected" : ""}>Landscape</option>
                  <option value="Square" ${settings.layout === "Square" ? "selected" : ""}>Square</option>
                </select>
              </label>
            </div>
          </div>

          <div class="pis-modal-body">
            <div id="pis-results" class="pis-results">
              <div class="pis-placeholder">Enter a search query and click Search</div>
            </div>
          </div>

          <div class="pis-modal-footer">
            <div id="pis-source-chips" class="pis-source-chips"></div>
            <div id="pis-status" class="pis-status"></div>
          </div>
        </div>
      </div>

      <!-- Preview overlay -->
      <div id="pis-preview-overlay" class="pis-preview-overlay" style="display: none;" onclick="window.pisClosePreview()">
        <div class="pis-preview-content" onclick="event.stopPropagation()">
          <img id="pis-preview-image" src="" alt="Preview" onclick="window.pisClickPreview(event)" />
          <div id="pis-preview-dims" class="pis-preview-dims"></div>
          <div class="pis-preview-actions">
            <button id="pis-confirm-btn" class="pis-btn pis-btn-primary" onclick="window.pisConfirmImage()">Set as Performer Image</button>
            <button class="pis-btn" onclick="window.pisClosePreview()">Cancel</button>
          </div>
        </div>
      </div>
    `;

    // Add enter key handler for search input
    const searchInput = document.getElementById("pis-search-query");
    if (searchInput) {
      searchInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
          window.pisSearch();
        }
      });
      searchInput.focus();
    }

    // Add filter change handler - apply filters client-side (instant!)
    const layoutSelect = document.getElementById("pis-layout");
    if (layoutSelect) layoutSelect.addEventListener("change", () => {
      if (allResults.length > 0) refreshGrid();
    });
  }

  function previewIsOpen() {
    const overlay = document.getElementById("pis-preview-overlay");
    return !!overlay && overlay.style.display !== "none";
  }

  function swallow(e) {
    if (e.preventDefault) e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();
  }

  /**
   * Arrow keys and Escape while a preview is open. Returns true when handled.
   * Arrow keys are left alone while focus is in a form field.
   */
  function handlePreviewKeydown(e) {
    if (!previewIsOpen()) return false;
    const tag = String((e.target && e.target.tagName) || "").toUpperCase();
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

    if (e.key === "Escape") {
      window.pisClosePreview();
    } else if (!typing && e.key === "ArrowLeft") {
      window.pisPrevPreview();
    } else if (!typing && e.key === "ArrowRight") {
      window.pisNextPreview();
    } else {
      return false;
    }
    swallow(e);
    return true;
  }

  /**
   * Window capture-phase key handler while the modal is open: Escape closes the
   * preview if open, else the modal. Handled keys never reach Stash's hotkeys.
   */
  function handleModalKeydown(e) {
    if (handlePreviewKeydown(e)) return;
    if (e.key === "Escape" && !previewIsOpen()) {
      swallow(e);
      hideModal();
    }
  }

  /**
   * Add results from a source, deduplicating by URL
   */
  function addResultsFromSource(results, source) {
    let added = 0;
    for (const result of results) {
      const imgUrl = result.image;
      if (imgUrl && !seenImageUrls.has(imgUrl)) {
        seenImageUrls.add(imgUrl);
        allResults.push(result);
        added++;
      }
    }
    console.debug(`[PerformerImageSearch] ${source}: Added ${added} unique images (${results.length - added} duplicates skipped)`);
    return added;
  }

  /**
   * Search a single source and update results
   */
  async function searchSource(query, performerName, source, generation = searchGeneration) {
    console.debug(`[PerformerImageSearch] Starting search for source: ${source}`);
    const controller = new AbortController();
    activeControllers.push(controller);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, SOURCE_TIMEOUT_MS);
    // Marks the source finished; every state change is guarded by the generation
    const finish = (status, count, detail) => {
      sourceStatus[source] = { status, count, detail };
      pendingSources = pendingSources.filter(s => s !== source);
      completedSources.push(source);
    };
    try {
      const data = await searchImages(query, performerName, source, controller.signal);
      if (generation !== searchGeneration) return 0;
      const results = data.results || [];
      console.debug(`[PerformerImageSearch] ${source}: Received ${results.length} results`);

      const added = addResultsFromSource(results, source);
      let status = data.status || (data.error ? "error" : results.length ? "ok" : "empty");
      const detail = [data.error, ...(data.warnings || [])].filter(Boolean).join("; ");
      if (data.error && results.length && status === "error") status = "partial";
      if (data.error && status !== "ok" && status !== "empty") {
        sourceErrors.push({ source, message: data.error });
      }
      finish(status, results.length, detail);

      if (added > 0) {
        applyFilters();
        renderResults();
      }
      updateFilterStatus();
      return results.length;
    } catch (e) {
      if (generation !== searchGeneration) return 0;
      const message = timedOut || e?.name === "AbortError"
        ? `Timed out after ${SOURCE_TIMEOUT_MS / 1000}s`
        : e?.message || String(e);
      console.error(`[PerformerImageSearch] ${source}: Search failed:`, e);
      sourceErrors.push({ source, message });
      finish(timedOut || e?.name === "AbortError" ? "timeout" : "error", 0, message);
      updateFilterStatus();
      return 0;
    } finally {
      clearTimeout(timer);
      activeControllers = activeControllers.filter(c => c !== controller);
    }
  }

  /**
   * Perform search - fetches from all sources in parallel, streams results
   */
  window.pisSearch = async function () {
    const query = document.getElementById("pis-search-query")?.value?.trim();

    if (!query) {
      showStatus("Please enter a search query", "error");
      return;
    }

    // Reset state; responses of any earlier search are now stale
    supersedeSearches();
    const generation = searchGeneration;
    allResults = [];
    filteredResults = [];
    imageDimensions = {};
    loadedCount = 0;
    seenImageUrls = new Set();
    completedSources = [];
    pendingSources = [...SOURCES];
    sourceErrors = [];
    sourceStatus = {};
    isLoading = true;

    console.debug(`[PerformerImageSearch] Starting search for: "${query}" (performer: ${currentPerformerName})`);
    console.debug(`[PerformerImageSearch] Sources to search: ${SOURCES.join(", ")}`);

    showStatus(`Searching ${SOURCES.length} sources...`, "loading");
    renderResults(); // Show empty state initially
    renderSourceChips();

    // Launch all source searches in parallel
    const searchPromises = SOURCES.map(source =>
      searchSource(query, currentPerformerName, source, generation)
    );

    // Wait for all to complete
    try {
      const results = await Promise.all(searchPromises);
      const totalFound = results.reduce((a, b) => a + b, 0);
      console.debug(`[PerformerImageSearch] All sources complete. Total results: ${allResults.length} (${totalFound} before dedup)`);
    } catch (e) {
      console.error("[PerformerImageSearch] Search error:", e);
    } finally {
      if (generation === searchGeneration) {
        isLoading = false;
        renderResults();
        updateFilterStatus();
      }
    }
  };

  /**
   * A thumbnail loaded: record its size (keyed by image URL), re-filter, and
   * refresh the loading count
   */
  window.pisImageLoaded = function (img, url) {
    if (recordDimensions(url, img.naturalWidth, img.naturalHeight, 1) && getFilterName() !== "All") {
      applyFilters();
      renderResults();
    }
    scheduleStatusRefresh();
  };

  /**
   * A thumbnail failed: its size is unknown, which counts as loaded
   */
  window.pisImageErrored = function (url) {
    recordDimensions(url, 0, 0, 0);
    scheduleStatusRefresh();
  };

  function getFilterName() {
    return normalizeLayout(document.getElementById("pis-layout")?.value);
  }

  function createResultNode(result) {
    const item = document.createElement("div");
    item.className = "pis-result-item";
    item.addEventListener("click", () => showPreviewFor(result));

    const img = document.createElement("img");
    img.src = result.thumbnail;
    img.alt = result.title || "";
    img.setAttribute("loading", "lazy");
    // A node of an earlier search, or one no longer in the grid, may still finish
    // loading: that load is not this search's
    const generation = searchGeneration;
    const current = () => generation === searchGeneration && gridNodes.get(result.image) === item;
    img.onload = () => {
      img.classList.add("pis-loaded");
      if (current()) window.pisImageLoaded(img, result.image);
    };
    img.onerror = () => {
      item.classList.add("pis-error");
      if (current()) window.pisImageErrored(result.image);
    };
    item.appendChild(img);

    const info = document.createElement("div");
    info.className = "pis-result-info";
    info.textContent = result.source || "";
    item.appendChild(info);
    return item;
  }

  /**
   * Render the results grid. Nodes are created once per result and kept: new
   * results are appended, and filters only toggle visibility, so images are
   * not reloaded and scroll position holds.
   */
  function renderResults() {
    const container = document.getElementById("pis-results");
    if (!container) return;
    if (container !== gridContainer) {
      gridContainer = container;
      gridNodes = new Map();
      emptyNote = null;
    }

    if (allResults.length === 0) {
      let msg;
      if (isLoading) {
        msg = "Searching...";
      } else if (sourceErrors.length >= SOURCES.length && sourceErrors.length > 0) {
        msg = "Couldn't reach any image source. Check that the Stash server has internet access, then try again.";
      } else {
        msg = "No images found for this performer.";
      }
      container.innerHTML = `<div class="pis-placeholder">${msg}</div>`;
      gridNodes = new Map();
      emptyNote = null;
      return;
    }

    if (gridNodes.size === 0) container.innerHTML = ""; // drop the placeholder
    for (const result of allResults) {
      if (!gridNodes.has(result.image)) {
        const node = createResultNode(result);
        gridNodes.set(result.image, node);
        container.appendChild(node);
      }
    }
    const visible = new Set(filteredResults.map((r) => r.image));
    for (const [url, node] of gridNodes) {
      node.style.display = visible.has(url) ? "" : "none";
    }
    if (!emptyNote) {
      emptyNote = document.createElement("div");
      emptyNote.className = "pis-placeholder";
      emptyNote.textContent = "No images match current filters";
      container.appendChild(emptyNote);
    }
    emptyNote.style.display = filteredResults.length === 0 ? "" : "none";
  }

  const THUMB_NOTICE = "Full-size image unavailable; showing the thumbnail";

  /**
   * Show full-size image preview
   */
  window.pisShowPreview = function (originalIndex) {
    const result = allResults[originalIndex];
    if (result) showPreviewFor(result);
  };

  function showPreviewFor(result) {
    previewResult = result;
    previewImage = result.image;
    // Position within the filtered grid; -1 when the result is filtered out
    currentPreviewIndex = filteredResults.indexOf(result);

    const overlay = document.getElementById("pis-preview-overlay");
    const img = document.getElementById("pis-preview-image");
    const dimInfo = document.getElementById("pis-preview-dims");
    const confirmBtn = document.getElementById("pis-confirm-btn");
    if (!overlay || !img) return;

    if (dimInfo) dimInfo.textContent = "Loading...";
    if (confirmBtn) confirmBtn.disabled = false;
    let fellBack = false; // one fall back to the thumbnail; stops error loops

    img.onload = function () {
      if (previewResult !== result || overlay.style.display === "none") return;
      if (fellBack) return; // keep the notice
      if (dimInfo) dimInfo.textContent = `${img.naturalWidth} x ${img.naturalHeight} - ${result.source}`;
      if (recordDimensions(result.image, img.naturalWidth, img.naturalHeight, 2) && getFilterName() !== "All") {
        refreshGrid();
      }
    };

    img.onerror = function () {
      if (previewResult !== result || overlay.style.display === "none") return;
      if (!fellBack && result.thumbnail && result.thumbnail !== result.image) {
        fellBack = true;
        previewImage = result.thumbnail;
        if (dimInfo) dimInfo.textContent = THUMB_NOTICE;
        img.src = result.thumbnail;
        return;
      }
      previewImage = null;
      if (dimInfo) dimInfo.textContent = "Image unavailable";
      if (confirmBtn) confirmBtn.disabled = true;
    };
    img.src = result.image;
    overlay.style.display = "flex";
  }

  /**
   * Handle click on preview image - navigate based on click position
   */
  window.pisClickPreview = function (e) {
    const rect = e.target.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (x < rect.width / 2) {
      window.pisPrevPreview();
    } else {
      window.pisNextPreview();
    }
  };

  /**
   * Step through the visible results from the previewed one. When the
   * previewed result has been filtered out, go to its nearest visible
   * neighbour in that direction.
   */
  function stepPreview(delta) {
    if (!previewResult) return;
    let target;
    const idx = filteredResults.indexOf(previewResult);
    if (idx >= 0) {
      target = filteredResults[idx + delta];
    } else {
      const visible = new Set(filteredResults);
      for (let i = allResults.indexOf(previewResult) + delta; i >= 0 && i < allResults.length; i += delta) {
        if (visible.has(allResults[i])) { target = allResults[i]; break; }
      }
    }
    if (target) showPreviewFor(target);
  }

  window.pisPrevPreview = function () { stepPreview(-1); };

  window.pisNextPreview = function () { stepPreview(1); };

  /**
   * Close preview overlay
   */
  window.pisClosePreview = function () {
    const overlay = document.getElementById("pis-preview-overlay");
    if (overlay) {
      overlay.style.display = "none";
    }
    previewImage = null;
    previewResult = null;
    currentPreviewIndex = -1;
  };

  /**
   * Confirm and set the previewed image as performer image
   */
  window.pisConfirmImage = async function () {
    if (!previewImage || !currentPerformerId) return;

    const confirmBtn = document.getElementById("pis-confirm-btn");
    const originalText = confirmBtn?.textContent;

    // Show loading state on button
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Saving...";
      confirmBtn.classList.add("pis-btn-loading");
    }

    showStatus("Setting performer image...", "loading");

    try {
      await setPerformerImage(currentPerformerId, previewImage);
      showStatus("Image set successfully!", "success");

      if (confirmBtn) {
        confirmBtn.textContent = "Saved!";
      }

      // Close modal after brief delay
      setTimeout(() => {
        hideModal();
        // Refresh the page to show new image
        window.location.reload();
      }, 1000);
    } catch (e) {
      showStatus(`Failed to set image: ${e.message}`, "error");
      // Restore button state on error
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
        confirmBtn.classList.remove("pis-btn-loading");
      }
    }
  };

  /**
   * Hide modal (exposed globally for onclick handlers)
   */
  window.pisHideModal = hideModal;

  /**
   * Show status message
   */
  function showStatus(message, type) {
    const statusEl = document.getElementById("pis-status");
    if (statusEl) {
      statusEl.textContent = message;
      statusEl.className = `pis-status pis-status-${type}`;
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
   * Extract performer ID from current URL
   */
  function getPerformerIdFromUrl() {
    const match = window.location.pathname.match(/\/performers\/(\d+)/);
    return match ? match[1] : null;
  }

  /**
   * Get performer name from the page
   */
  function getPerformerNameFromPage() {
    // Stash renders the performer name in: <h2><span class="performer-name">Name</span>...</h2>
    // The span has class "performer-name" which is the most reliable selector

    // Method 1: Look for the specific performer-name span (most reliable)
    const nameSpan = document.querySelector(".performer-name");
    if (nameSpan) {
      return nameSpan.textContent?.trim() || "Unknown Performer";
    }

    // Method 2: Look for span inside .performer-head h2
    const h2Span = document.querySelector(".performer-head h2 > span:first-child");
    if (h2Span) {
      return h2Span.textContent?.trim() || "Unknown Performer";
    }

    // Method 3: Fallback - try the page title (Helmet sets it to performer name)
    const pageTitle = document.title;
    if (pageTitle && !pageTitle.includes("Stash")) {
      return pageTitle.trim();
    }

    return "Unknown Performer";
  }

  /**
   * Add the "Search Images" button to the performer page
   */
  function addSearchButton() {
    // Check if we're on a performer page
    const performerId = getPerformerIdFromUrl();
    if (!performerId) return;

    // Check if button already exists
    if (document.getElementById("pis-search-button")) return;

    // Find a good place to insert the button
    // Look for the operations/edit buttons area
    const buttonContainer =
      document.querySelector(".detail-header-buttons") ||
      document.querySelector('[class*="detail"] [class*="button"]')?.parentElement ||
      document.querySelector(".performer-head");

    if (!buttonContainer) {
      // Try again later - page might not be fully loaded
      setTimeout(addSearchButton, 500);
      return;
    }

    // Create the button
    const button = document.createElement("button");
    button.id = "pis-search-button";
    button.className = "btn btn-secondary pis-search-button";
    button.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="16" height="16" fill="currentColor" style="margin-right: 6px;">
        <path d="M416 208c0 45.9-14.9 88.3-40 122.7L502.6 457.4c12.5 12.5 12.5 32.8 0 45.3s-32.8 12.5-45.3 0L330.7 376c-34.4 25.2-76.8 40-122.7 40C93.1 416 0 322.9 0 208S93.1 0 208 0S416 93.1 416 208zM208 352a144 144 0 1 0 0-288 144 144 0 1 0 0 288z"/>
      </svg>
      Search Images
    `;

    button.addEventListener("click", () => {
      const performerName = getPerformerNameFromPage();
      showModal(performerId, performerName);
    });

    // Insert the button
    buttonContainer.appendChild(button);
  }

  /**
   * Initialize plugin when on performer page
   */
  function init() {
    // Listen for page changes (Stash is a SPA)
    PluginApi.Event.addEventListener("stash:location", () => {
      // Small delay to let page render
      setTimeout(addSearchButton, 100);
    });

    // Also try to add button immediately if already on performer page
    setTimeout(addSearchButton, 100);
  }

  // Start the plugin
  init();

  console.log("[PerformerImageSearch] Plugin loaded");

  // Test hook: only active when a test sets window.__PERFORMER_IMAGE_SEARCH_TEST__
  if (window.__PERFORMER_IMAGE_SEARCH_TEST__) {
    const hook = window.__PERFORMER_IMAGE_SEARCH_TEST__;
    hook.exports = {
      getPluginSettings,
      graphqlRequest,
      searchImages,
      applyFilters,
      updateFilterStatus,
      renderModal,
      showModal,
      hideModal,
      handlePreviewKeydown,
      handleModalKeydown,
      renderSourceChips,
      addResultsFromSource,
      searchSource,
      renderResults,
      setPerformerImage,
      showStatus,
      escapeHtml,
      addSearchButton,
    };
    hook.getState = () => ({
      SOURCES,
      modalRoot,
      currentPerformerId,
      currentPerformerName,
      allResults,
      filteredResults,
      imageDimensions,
      loadedCount,
      isLoading,
      previewImage,
      previewResult,
      currentPreviewIndex,
      seenImageUrls,
      completedSources,
      pendingSources,
      sourceErrors,
      sourceStatus,
      searchGeneration,
    });
    hook.setState = (patch) => {
      for (const [k, v] of Object.entries(patch || {})) {
        switch (k) {
          case "SOURCES": SOURCES = v; break;
          case "modalRoot": modalRoot = v; break;
          case "currentPerformerId": currentPerformerId = v; break;
          case "currentPerformerName": currentPerformerName = v; break;
          case "allResults": allResults = v; break;
          case "filteredResults": filteredResults = v; break;
          case "imageDimensions": imageDimensions = v; break;
          case "loadedCount": loadedCount = v; break;
          case "isLoading": isLoading = v; break;
          case "previewImage": previewImage = v; break;
          case "previewResult": previewResult = v; break;
          case "currentPreviewIndex": currentPreviewIndex = v; break;
          case "seenImageUrls": seenImageUrls = v; break;
          case "completedSources": completedSources = v; break;
          case "pendingSources": pendingSources = v; break;
          case "sourceErrors": sourceErrors = v; break;
          case "sourceStatus": sourceStatus = v; break;
          default: throw new Error("setState: unknown key " + k);
        }
      }
    };
  }
})();
