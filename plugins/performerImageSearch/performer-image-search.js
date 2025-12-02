(function () {
  "use strict";

  const PLUGIN_ID = "performerImageSearch";

  // Default settings
  const DEFAULTS = {
    searchSuffix: "pornstar",
    layout: "All",
    // Source toggles - all enabled by default
    enableBabepedia: true,
    enablePornPics: true,
    enableFreeOnes: true,
    enableEliteBabes: true,
    enableBoobpedia: true,
    enableJavDatabase: true,
    enableBing: true,
  };

  // All available sources (will be filtered by settings)
  const ALL_SOURCES = [
    { id: "babepedia", settingKey: "enableBabepedia" },
    { id: "pornpics", settingKey: "enablePornPics" },
    { id: "freeones", settingKey: "enableFreeOnes" },
    { id: "elitebabes", settingKey: "enableEliteBabes" },
    { id: "boobpedia", settingKey: "enableBoobpedia" },
    { id: "javdatabase", settingKey: "enableJavDatabase" },
    { id: "bing", settingKey: "enableBing" },
  ];

  // Aspect ratio thresholds
  const ASPECT_THRESHOLDS = {
    Portrait: [0, 0.9],     // width/height < 0.9
    Square: [0.9, 1.1],     // 0.9 <= ratio <= 1.1
    Landscape: [1.1, Infinity],  // ratio > 1.1
  };

  // Active sources (filtered by settings, populated at runtime)
  let SOURCES = [];

  // State
  let modalRoot = null;
  let currentPerformerId = null;
  let currentPerformerName = null;
  let allResults = []; // All fetched results
  let filteredResults = []; // Results after applying filters
  let imageDimensions = {}; // Map of index -> {width, height}
  let loadedCount = 0; // Number of images that have loaded
  let isLoading = false;
  let previewImage = null;
  let seenImageUrls = new Set(); // For deduplication across sources
  let completedSources = []; // Track which sources have completed
  let pendingSources = []; // Track which sources are still loading

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
  async function graphqlRequest(query, variables = {}) {
    const response = await fetch(getGraphQLUrl(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
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
        layout: pluginConfig?.defaultLayout || DEFAULTS.layout,
      };

      // Process source toggles
      // Stash BOOLEAN settings: if not set, returns undefined (use default true)
      // If explicitly set to false, returns false
      for (const source of ALL_SOURCES) {
        const configValue = pluginConfig?.[source.settingKey];
        // Treat undefined/null as true (enabled by default)
        // Only disable if explicitly set to false
        settings[source.settingKey] = configValue !== false;
      }

      // Build active SOURCES array based on settings
      SOURCES = ALL_SOURCES
        .filter(source => settings[source.settingKey])
        .map(source => source.id);

      console.debug("[PerformerImageSearch] Active sources:", SOURCES);

      return settings;
    } catch (e) {
      console.error("[PerformerImageSearch] Failed to get settings:", e);
      // On error, enable all sources
      SOURCES = ALL_SOURCES.map(source => source.id);
      return DEFAULTS;
    }
  }

  /**
   * Search images using the Python backend via runPluginOperation
   * @param {string} query - Search query
   * @param {string} performerName - Performer name
   * @param {string|null} source - Specific source to search (null for all)
   */
  async function searchImages(query, performerName, source = null) {
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
      });

      const output = data?.runPluginOperation;

      if (!output) {
        throw new Error("No response from search plugin");
      }

      if (output.error) {
        throw new Error(output.error);
      }

      return output;
    } catch (e) {
      console.error("[PerformerImageSearch] Search failed:", e);
      throw e;
    }
  }

  /**
   * Apply aspect ratio filter to results based on loaded dimensions
   */
  function applyFilters() {
    const layoutFilter = document.getElementById("pis-layout")?.value || "All";

    if (layoutFilter === "All") {
      filteredResults = [...allResults];
      return;
    }

    filteredResults = allResults.filter((result, index) => {
      const dims = imageDimensions[index];

      // If dimensions not loaded yet, include by default
      if (!dims || dims.width === 0 || dims.height === 0) {
        return true;
      }

      const { width, height } = dims;
      const ratio = width / height;

      // Layout filter
      const [minRatio, maxRatio] = ASPECT_THRESHOLDS[layoutFilter] || [0, Infinity];
      if (!(ratio >= minRatio && ratio < maxRatio)) {
        return false;
      }

      return true;
    });
  }

  /**
   * Update status with filter and source progress
   */
  function updateFilterStatus() {
    const loaded = Object.keys(imageDimensions).length;
    const total = allResults.length;

    // Build source status
    let sourceStatus = "";
    if (pendingSources.length > 0) {
      sourceStatus = ` | Searching: ${pendingSources.join(", ")}`;
    } else if (completedSources.length > 0) {
      sourceStatus = ` | All sources complete`;
    }

    if (pendingSources.length > 0) {
      showStatus(`Found ${filteredResults.length} images (${loaded}/${total} loaded)${sourceStatus}`, "loading");
    } else if (loaded < total) {
      showStatus(`Found ${filteredResults.length} images (loading ${loaded}/${total}...)`, "loading");
    } else {
      showStatus(`${filteredResults.length} of ${total} images match filters`, "success");
    }
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
    currentPerformerId = performerId;
    currentPerformerName = performerName;
    allResults = [];
    filteredResults = [];
    previewImage = null;

    // Create modal if it doesn't exist
    if (!modalRoot) {
      modalRoot = document.createElement("div");
      modalRoot.id = "performer-image-search-modal-root";
      document.body.appendChild(modalRoot);
    }

    renderModal();
  }

  /**
   * Hide and cleanup the modal
   */
  function hideModal() {
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
    seenImageUrls = new Set();
    completedSources = [];
    pendingSources = [];
  }

  /**
   * Render the modal content
   */
  async function renderModal() {
    if (!modalRoot) return;

    const settings = await getPluginSettings();
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
            <div id="pis-status" class="pis-status"></div>
          </div>
        </div>
      </div>

      <!-- Preview overlay -->
      <div id="pis-preview-overlay" class="pis-preview-overlay" style="display: none;" onclick="window.pisClosePreview()">
        <div class="pis-preview-content" onclick="event.stopPropagation()">
          <img id="pis-preview-image" src="" alt="Preview" />
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
      if (allResults.length > 0) {
        applyFilters();
        renderResults();
        updateFilterStatus();
      }
    });
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
  async function searchSource(query, performerName, source) {
    console.debug(`[PerformerImageSearch] Starting search for source: ${source}`);
    try {
      const data = await searchImages(query, performerName, source);
      const results = data.results || [];
      console.debug(`[PerformerImageSearch] ${source}: Received ${results.length} results`);

      // Add results and update UI
      const added = addResultsFromSource(results, source);

      // Move from pending to completed
      pendingSources = pendingSources.filter(s => s !== source);
      completedSources.push(source);

      // Re-apply filters and render
      if (added > 0) {
        applyFilters();
        renderResults();
      }
      updateFilterStatus();

      console.debug(`[PerformerImageSearch] ${source}: Complete. Total results now: ${allResults.length}`);
      return results.length;
    } catch (e) {
      console.error(`[PerformerImageSearch] ${source}: Search failed:`, e);
      // Move from pending to completed even on error
      pendingSources = pendingSources.filter(s => s !== source);
      completedSources.push(source);
      updateFilterStatus();
      return 0;
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

    // Reset state
    allResults = [];
    filteredResults = [];
    imageDimensions = {};
    loadedCount = 0;
    seenImageUrls = new Set();
    completedSources = [];
    pendingSources = [...SOURCES];
    isLoading = true;

    console.debug(`[PerformerImageSearch] Starting search for: "${query}" (performer: ${currentPerformerName})`);
    console.debug(`[PerformerImageSearch] Sources to search: ${SOURCES.join(", ")}`);

    showStatus(`Searching ${SOURCES.length} sources...`, "loading");
    renderResults(); // Show empty state initially

    // Launch all source searches in parallel
    const searchPromises = SOURCES.map(source =>
      searchSource(query, currentPerformerName, source)
    );

    // Wait for all to complete
    try {
      const results = await Promise.all(searchPromises);
      const totalFound = results.reduce((a, b) => a + b, 0);
      console.debug(`[PerformerImageSearch] All sources complete. Total results: ${allResults.length} (${totalFound} before dedup)`);
    } catch (e) {
      console.error("[PerformerImageSearch] Search error:", e);
    } finally {
      isLoading = false;
      updateFilterStatus();
    }
  };

  /**
   * Handle image load - capture dimensions and re-filter
   */
  window.pisImageLoaded = function (img, originalIndex) {
    if (!imageDimensions[originalIndex]) {
      imageDimensions[originalIndex] = {
        width: img.naturalWidth,
        height: img.naturalHeight,
      };
      loadedCount++;

      // Re-apply filters periodically as images load
      // (every 10 images or when all loaded)
      if (loadedCount % 10 === 0 || loadedCount === allResults.length) {
        applyFilters();
        renderResults();
        updateFilterStatus();
      }
    }
  };

  /**
   * Render search results grid
   */
  function renderResults() {
    const resultsContainer = document.getElementById("pis-results");

    if (!resultsContainer) return;

    if (filteredResults.length === 0) {
      if (allResults.length === 0) {
        resultsContainer.innerHTML = '<div class="pis-placeholder">No images found</div>';
      } else {
        resultsContainer.innerHTML = '<div class="pis-placeholder">No images match current filters</div>';
      }
      return;
    }

    // Build a map of filtered result to original index for click handling
    const filteredWithIndex = filteredResults.map((result) => {
      const originalIndex = allResults.indexOf(result);
      return { result, originalIndex };
    });

    resultsContainer.innerHTML = filteredWithIndex
      .map(
        ({ result, originalIndex }) => {
          return `
        <div class="pis-result-item" onclick="window.pisShowPreview(${originalIndex})">
          <img
            src="${escapeHtml(result.thumbnail)}"
            alt="${escapeHtml(result.title)}"
            loading="lazy"
            onload="this.classList.add('pis-loaded'); window.pisImageLoaded(this, ${originalIndex})"
            onerror="this.parentElement.classList.add('pis-error')"
          />
          <div class="pis-result-info">
            ${escapeHtml(result.source)}
          </div>
        </div>
      `;
        }
      )
      .join("");
  }

  /**
   * Show full-size image preview
   */
  window.pisShowPreview = function (originalIndex) {
    const result = allResults[originalIndex];
    if (!result) return;

    previewImage = result.image;

    const overlay = document.getElementById("pis-preview-overlay");
    const img = document.getElementById("pis-preview-image");
    const dimInfo = document.getElementById("pis-preview-dims");

    if (overlay && img) {
      // Clear previous dimensions
      if (dimInfo) dimInfo.textContent = "Loading...";

      // Show dimensions once image loads
      img.onload = function () {
        if (dimInfo) {
          dimInfo.textContent = `${img.naturalWidth} x ${img.naturalHeight} - ${result.source}`;
        }
      };

      // Try full-size first, fall back to thumbnail if it fails
      img.onerror = function () {
        if (img.src !== result.thumbnail) {
          img.src = result.thumbnail;
          previewImage = result.thumbnail; // Update so we save the working URL
        }
      };
      img.src = result.image;
      overlay.style.display = "flex";
    }
  };

  /**
   * Close preview overlay
   */
  window.pisClosePreview = function () {
    const overlay = document.getElementById("pis-preview-overlay");
    if (overlay) {
      overlay.style.display = "none";
    }
    previewImage = null;
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
})();
