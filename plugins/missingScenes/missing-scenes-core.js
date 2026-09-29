/**
 * Missing Scenes Core - Shared utilities and components
 * Loaded first, exposes API on window.MissingScenesCore
 */
(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";

  // A response with one of these keys carries results to render even when it has an
  // error: scenes (find_missing, browse_stashdb) or a fingerprint index build's counts
  const RESULT_KEYS = ["missing_scenes", "scanned"];

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
   * Run a plugin operation via GraphQL
   */
  async function runPluginOperation(args) {
    const query = `
      mutation RunPluginOperation($plugin_id: ID!, $args: Map) {
        runPluginOperation(plugin_id: $plugin_id, args: $args)
      }
    `;

    const data = await graphqlRequest(query, {
      plugin_id: PLUGIN_ID,
      args: args,
    });

    const rawOutput = data?.runPluginOperation;

    if (!rawOutput) {
      throw new Error("No response from plugin");
    }

    let output;
    try {
      output = typeof rawOutput === "string" ? JSON.parse(rawOutput) : rawOutput;
    } catch (e) {
      console.error("[MissingScenes] Failed to parse plugin response:", rawOutput);
      throw new Error("Invalid response from plugin");
    }

    // A response that carries results plus an error is a (partial) result the UI
    // must render; only a plain error (no results key) is thrown.
    if (output.error && !RESULT_KEYS.some((key) => key in output)) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Human-readable text for a failed find_missing / browse_stashdb response.
   * Returns "" when the response carries no error. Plain text (escape before HTML).
   */
  function describeFailure(output) {
    if (!output || !output.error) return "";
    let msg = String(output.error);
    if (output.auth_error && !/api key/i.test(msg)) {
      msg += " Check the stash-box API key in Settings > Metadata Providers.";
    }
    if (output.rate_limited) {
      const wait = output.retry_after;
      msg += wait !== undefined && wait !== null
        ? ` The stash-box is rate-limited, try again in ${wait} s.`
        : " The stash-box is rate-limited, try again shortly.";
    }
    return msg;
  }

  /**
   * Build or update the fingerprint index for a stash-box (the backend's default box when
   * endpoint is empty). Resolves with the counts, which carry `error` when the build
   * stopped early; throws when nothing could be done (no such box, Stash unreachable).
   */
  async function buildFingerprintIndex(endpoint) {
    const args = { operation: "build_fingerprint_index" };
    if (endpoint) args.endpoint = endpoint;
    return runPluginOperation(args);
  }

  const FINGERPRINT_TASK_HINT = "Build it from Settings > Tasks (Build Fingerprint Index), or here.";

  /**
   * The fingerprint note under a view's stats. Plain text (escape before HTML).
   * info: the last response's fingerprint fields; owned: scenes left out as owned by
   * fingerprint so far; build: {running, message} of a build started from the view.
   * Returns {text, button, running}; button is true when "Build fingerprint index" shows.
   */
  function describeFingerprintIndex(info, owned, build, boxName) {
    const parts = [];
    let button = false;
    const box = boxName || "this stash-box";
    if (info && info.fingerprint_matching !== false && typeof info.fingerprint_index === "boolean") {
      if (!info.fingerprint_index) {
        parts.push(`No fingerprint index for ${box} yet, so scenes you have without a ${box} ID show as missing. ${FINGERPRINT_TASK_HINT}`);
        button = true;
      } else {
        if (owned > 0) parts.push(`${owned} counted as owned by fingerprint.`);
        if (info.fingerprint_index_complete === false) {
          parts.push(`The fingerprint index is incomplete: its last build stopped early. ${FINGERPRINT_TASK_HINT}`);
          button = true;
        }
      }
    }
    const running = !!(build && build.running);
    if (running) {
      parts.push("Building the fingerprint index. On a large library this takes minutes; the task in Settings > Tasks runs it in the background.");
    }
    if (build && build.message) parts.push(build.message);
    return { text: parts.join(" "), button, running };
  }

  /**
   * Text for a finished build_fingerprint_index result. Plain text.
   */
  function describeFingerprintBuild(result) {
    if (!result) return "";
    // The backend's message already names the box and says what to do
    if (result.error) return `Fingerprint index: ${result.error}`;
    const box = result.stashdb_name || "the stash-box";
    let msg = `Fingerprint index built: ${result.matched} of ${result.scanned} scenes without a ${box} ID match a ${box} scene.`;
    if (result.no_fingerprints > 0) {
      msg += ` ${result.no_fingerprints} have no fingerprints to look up yet.`;
    }
    return msg;
  }

  /**
   * The fingerprint fields of a find_missing / browse_stashdb response.
   */
  function fingerprintFields(output) {
    return {
      fingerprint_matching: output.fingerprint_matching,
      fingerprint_index: output.fingerprint_index,
      fingerprint_index_complete: output.fingerprint_index_complete,
    };
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Format date for display
   */
  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      const [year, month, day] = dateStr.split("-").map(Number);
      const date = new Date(year, month - 1, day);
      return date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return dateStr;
    }
  }

  /**
   * Format duration from seconds to HH:MM:SS or MM:SS
   */
  function formatDuration(seconds) {
    if (!seconds) return "";
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    }
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }

  /**
   * True when the endpoint URL is StashDB (host stashdb.org, case-insensitive).
   * Mirrors is_stashdb_endpoint() in the backend: Whisparr matches StashDB IDs only.
   */
  function isStashdbEndpoint(url) {
    if (!url) return false;
    try {
      return new URL(String(url).trim()).hostname.toLowerCase() === "stashdb.org";
    } catch (e) {
      return false;
    }
  }

  /**
   * Status-line text for a successful add_to_whisparr result. Plain text.
   */
  function describeWhisparrAdd(scene, result) {
    const title = scene && scene.title ? scene.title : "scene";
    if (result && result.already_exists) return `"${title}" is already in Whisparr`;
    if (result && result.search_triggered === false) {
      const why = result.search_error ? ` (${result.search_error})` : "";
      return `Added "${title}" to Whisparr without starting a search${why}`;
    }
    return `Added "${title}" to Whisparr`;
  }

  /**
   * Banner text when the Whisparr status map could not be fetched. Plain text.
   */
  function describeWhisparrStatusError(error) {
    return `Whisparr status unavailable: ${error}. Run Test Whisparr Connection.`;
  }

  const WHISPARR_STASHDB_HINT = "Whisparr needs StashDB";

  /**
   * Add a scene to Whisparr. Resolves with the result; throws when the add fails.
   */
  async function addToWhisparr(stashId, title, endpoint) {
    const args = {
      operation: "add_to_whisparr",
      stash_id: stashId,
      title: title,
    };
    if (endpoint) args.endpoint = endpoint;
    const result = await runPluginOperation(args);
    if (result && result.success === false) {
      throw new Error(result.error || "Whisparr add failed");
    }
    return result;
  }

  /**
   * Handle adding a single scene to Whisparr (manages button state)
   * @param {Object} scene - Scene object with stash_id and title
   * @param {HTMLElement} button - The button element to update
   * @param {Object} config - Configuration object
   * @param {string} config.endpoint - The view's stash-box endpoint URL
   * @param {Function} config.onSuccess - Optional callback on success (scene, result)
   * @param {Function} config.onError - Optional callback on error
   */
  async function handleAddToWhisparr(scene, button, config = {}) {
    const originalText = button.textContent;
    button.textContent = "Adding...";
    button.disabled = true;
    button.classList.add("ms-btn-loading");

    try {
      const result = await addToWhisparr(scene.stash_id, scene.title, config.endpoint);

      // Update button state
      button.textContent = "Added!";
      button.classList.remove("ms-btn-loading");
      button.classList.add("ms-btn-success");

      // Mark scene as in Whisparr
      scene.in_whisparr = true;

      if (config.onSuccess) {
        config.onSuccess(scene, result);
      }

      // After a delay, update button to show final state
      setTimeout(() => {
        button.textContent = "In Whisparr";
        button.classList.remove("ms-btn-success");
        button.classList.add("ms-btn-disabled");
      }, 2000);
    } catch (error) {
      console.error("[MissingScenes] Failed to add to Whisparr:", error);
      button.textContent = "Failed";
      button.classList.remove("ms-btn-loading");
      button.classList.add("ms-btn-error");
      button.disabled = false;

      if (config.onError) {
        config.onError(error, scene);
      }

      setTimeout(() => {
        button.textContent = originalText;
        button.classList.remove("ms-btn-error");
      }, 3000);
    }
  }

  /**
   * Create a scene card element
   * @param {Object} scene - Scene data from API
   * @param {Object} config - Configuration
   * @param {string} config.stashdbUrl - Base URL for StashDB links
   * @param {boolean} config.whisparrConfigured - Whether Whisparr is configured
   * @param {string} config.endpoint - Stash-box endpoint of the view (Whisparr is StashDB only)
   * @param {Function} config.onWhisparrAdd - Callback when Whisparr add completes: (scene, true, result) or (scene, false, error)
   * @returns {HTMLElement} The scene card element
   */
  function createSceneCard(scene, config) {
    const { stashdbUrl = "https://stashdb.org", whisparrConfigured = false, onWhisparrAdd, endpoint } = config;

    const card = document.createElement("div");
    card.className = "ms-scene-card";
    card.dataset.stashId = scene.stash_id;

    // Thumbnail
    const thumbContainer = document.createElement("div");
    thumbContainer.className = "ms-scene-thumb";

    if (scene.thumbnail) {
      const img = document.createElement("img");
      img.src = scene.thumbnail;
      img.alt = scene.title || "Scene thumbnail";
      img.loading = "lazy";
      img.onload = () => img.classList.add("ms-loaded");
      img.onerror = () => {
        thumbContainer.classList.add("ms-no-image");
        thumbContainer.innerHTML = '<span class="ms-no-image-icon">&#128247;</span>';
      };
      thumbContainer.appendChild(img);
    } else {
      thumbContainer.classList.add("ms-no-image");
      thumbContainer.innerHTML = '<span class="ms-no-image-icon">&#128247;</span>';
    }

    // Info section
    const info = document.createElement("div");
    info.className = "ms-scene-info";

    // Title
    const title = document.createElement("div");
    title.className = "ms-scene-title";
    title.textContent = scene.title || "Unknown";
    title.title = scene.title || "Unknown";

    // Meta (studio, date, duration)
    const meta = document.createElement("div");
    meta.className = "ms-scene-meta";

    const metaParts = [];
    if (scene.studio?.name) {
      metaParts.push(scene.studio.name);
    }
    if (scene.release_date) {
      metaParts.push(formatDate(scene.release_date));
    }
    if (scene.duration) {
      metaParts.push(formatDuration(scene.duration));
    }
    meta.textContent = metaParts.join(" - ");

    // Performers
    const performers = document.createElement("div");
    performers.className = "ms-scene-performers";
    if (scene.performers && scene.performers.length > 0) {
      const names = scene.performers.map((p) => p.name).slice(0, 3);
      performers.textContent = names.join(", ");
      if (scene.performers.length > 3) {
        performers.textContent += ` +${scene.performers.length - 3}`;
      }
    }

    // Matching tags (shown when tag filter is active)
    const matchingTags = document.createElement("div");
    matchingTags.className = "ms-scene-tags";
    if (config.activeFilterTagIds && config.activeFilterTagIds.length > 0 && scene.tags) {
      const filterSet = new Set(config.activeFilterTagIds);
      const matched = scene.tags.filter((t) => filterSet.has(t.id));
      for (const tag of matched) {
        const pill = document.createElement("span");
        pill.className = "ms-tag-pill";
        pill.textContent = tag.name;
        matchingTags.appendChild(pill);
      }
    }

    info.appendChild(title);
    info.appendChild(meta);
    info.appendChild(performers);
    if (matchingTags.childElementCount > 0) {
      info.appendChild(matchingTags);
    }

    // Actions
    const actions = document.createElement("div");
    actions.className = "ms-scene-actions";

    // StashDB link
    const stashdbLink = document.createElement("a");
    stashdbLink.className = "ms-btn ms-btn-small";
    stashdbLink.href = `${stashdbUrl}/scenes/${scene.stash_id}`;
    stashdbLink.target = "_blank";
    stashdbLink.rel = "noopener noreferrer";
    stashdbLink.textContent = "View";
    stashdbLink.onclick = (e) => e.stopPropagation();
    actions.appendChild(stashdbLink);

    // Site links: one per distinct stash-box site
    const seenSites = new Set();
    for (const u of Array.isArray(scene.urls) ? scene.urls : []) {
      if (!u || typeof u.url !== "string" || !/^https?:\/\//i.test(u.url)) continue;
      const site = String(u.site || "").trim() || u.url;
      if (seenSites.has(site)) continue;
      seenSites.add(site);
      const link = document.createElement("a");
      link.className = "ms-btn ms-btn-small ms-site-link";
      link.href = u.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = site;
      link.title = u.url;
      link.onclick = (e) => e.stopPropagation();
      actions.appendChild(link);
    }

    // Whisparr button (if configured)
    if (whisparrConfigured && !isStashdbEndpoint(endpoint || stashdbUrl)) {
      const hint = document.createElement("span");
      hint.className = "ms-whisparr-hint";
      hint.textContent = WHISPARR_STASHDB_HINT;
      hint.title = "Whisparr matches StashDB scene IDs only";
      actions.appendChild(hint);
    } else if (whisparrConfigured) {
      const whisparrBtn = document.createElement("button");
      whisparrBtn.className = "ms-btn ms-btn-small ms-btn-whisparr";

      if (scene.in_whisparr && scene.whisparr_status) {
        // Show detailed status based on whisparr_status object
        const status = scene.whisparr_status.status;
        const progress = scene.whisparr_status.progress;

        switch (status) {
          case "downloaded":
            whisparrBtn.textContent = "Downloaded";
            whisparrBtn.classList.add("ms-btn-success");
            break;
          case "downloading":
            whisparrBtn.textContent = progress ? `Downloading ${progress}%` : "Downloading...";
            whisparrBtn.classList.add("ms-btn-downloading");
            break;
          case "queued":
            whisparrBtn.textContent = "Queued";
            whisparrBtn.classList.add("ms-btn-queued");
            break;
          case "stalled":
            whisparrBtn.textContent = progress ? `Stalled ${progress}%` : "Stalled";
            whisparrBtn.classList.add("ms-btn-stalled");
            whisparrBtn.title = scene.whisparr_status.error || "Download stalled";
            break;
          case "waiting":
            whisparrBtn.textContent = "Waiting";
            whisparrBtn.classList.add("ms-btn-waiting");
            break;
          default:
            whisparrBtn.textContent = "In Whisparr";
        }
        whisparrBtn.disabled = true;
        whisparrBtn.classList.add("ms-btn-disabled");
      } else if (scene.in_whisparr) {
        // Fallback for backwards compatibility
        whisparrBtn.textContent = "In Whisparr";
        whisparrBtn.disabled = true;
        whisparrBtn.classList.add("ms-btn-disabled");
      } else {
        whisparrBtn.textContent = "Add to Whisparr";
        whisparrBtn.onclick = (e) => {
          e.stopPropagation();
          handleAddToWhisparr(scene, whisparrBtn, {
            endpoint: endpoint || stashdbUrl,
            onSuccess: onWhisparrAdd ? (sc, result) => onWhisparrAdd(scene, true, result) : undefined,
            onError: onWhisparrAdd ? (err) => onWhisparrAdd(scene, false, err) : undefined,
          });
        };
      }
      actions.appendChild(whisparrBtn);
    }

    card.appendChild(thumbContainer);
    card.appendChild(info);
    card.appendChild(actions);

    // Click card to open on StashDB
    card.onclick = () => {
      window.open(`${stashdbUrl}/scenes/${scene.stash_id}`, "_blank");
    };

    return card;
  }

  // Expose API on window
  window.MissingScenesCore = {
    // Utilities
    getGraphQLUrl,
    graphqlRequest,
    runPluginOperation,
    describeFailure,
    buildFingerprintIndex,
    describeFingerprintIndex,
    describeFingerprintBuild,
    fingerprintFields,
    escapeHtml,
    formatDate,
    formatDuration,

    // Whisparr
    addToWhisparr,
    handleAddToWhisparr,
    isStashdbEndpoint,
    describeWhisparrAdd,
    describeWhisparrStatusError,

    // Components
    createSceneCard,
  };

  // Test hook: active only when a test sets window.__MISSING_SCENES_TEST__
  if (window.__MISSING_SCENES_TEST__) {
    window.__MISSING_SCENES_TEST__.core = {
      getGraphQLUrl,
      graphqlRequest,
      runPluginOperation,
      describeFailure,
      buildFingerprintIndex,
      describeFingerprintIndex,
      describeFingerprintBuild,
      fingerprintFields,
      escapeHtml,
      formatDate,
      formatDuration,
      addToWhisparr,
      handleAddToWhisparr,
      isStashdbEndpoint,
      describeWhisparrAdd,
      describeWhisparrStatusError,
      createSceneCard,
    };
  }

  console.log("[MissingScenes] Core module loaded");
})();
