/**
 * Missing Scenes Core - Shared utilities and components
 * Loaded first, exposes API on window.MissingScenesCore
 */
(function () {
  "use strict";

  const PLUGIN_ID = "missingScenes";

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

    if (output.error) {
      throw new Error(output.error);
    }

    return output;
  }

  /**
   * Escape HTML to prevent XSS
   */
  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
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
   * Add a scene to Whisparr
   */
  async function addToWhisparr(stashId, title) {
    return runPluginOperation({
      operation: "add_to_whisparr",
      stash_id: stashId,
      title: title,
    });
  }

  /**
   * Handle adding a single scene to Whisparr (manages button state)
   * @param {Object} scene - Scene object with stash_id and title
   * @param {HTMLElement} button - The button element to update
   * @param {Object} config - Configuration object
   * @param {Function} config.onSuccess - Optional callback on success
   * @param {Function} config.onError - Optional callback on error
   */
  async function handleAddToWhisparr(scene, button, config = {}) {
    const originalText = button.textContent;
    button.textContent = "Adding...";
    button.disabled = true;
    button.classList.add("ms-btn-loading");

    try {
      await addToWhisparr(scene.stash_id, scene.title);

      // Update button state
      button.textContent = "Added!";
      button.classList.remove("ms-btn-loading");
      button.classList.add("ms-btn-success");

      // Mark scene as in Whisparr
      scene.in_whisparr = true;

      if (config.onSuccess) {
        config.onSuccess(scene);
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
   * @param {Function} config.onWhisparrAdd - Callback when Whisparr add completes (success or error)
   * @returns {HTMLElement} The scene card element
   */
  function createSceneCard(scene, config) {
    const { stashdbUrl = "https://stashdb.org", whisparrConfigured = false, onWhisparrAdd } = config;

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

    // Whisparr button (if configured)
    if (whisparrConfigured) {
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
            onSuccess: onWhisparrAdd ? () => onWhisparrAdd(scene, true) : undefined,
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
    escapeHtml,
    formatDate,
    formatDuration,

    // Whisparr
    addToWhisparr,
    handleAddToWhisparr,

    // Components
    createSceneCard,
  };

  console.log("[MissingScenes] Core module loaded");
})();
