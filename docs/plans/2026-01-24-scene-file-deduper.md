# Scene File Deduper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Flask web tool to find multi-file scenes in Stash and delete duplicate files, keeping the best quality version.

**Architecture:** Python Flask app with Stash GraphQL client. Single-page UI displays scenes with 2+ files as cards with side-by-side file comparison. Users select which file(s) to keep/delete. Delete operations use Stash's `deleteFiles` mutation (auto-setting primary first if needed).

**Tech Stack:** Python 3.10+, Flask, Requests, python-dotenv, Vanilla JS

---

## Task 1: Project Scaffolding

**Files:**
- Create: `scripts/scene-file-deduper/requirements.txt`
- Create: `scripts/scene-file-deduper/.env.example`

**Step 1: Create the directory structure**

```bash
mkdir -p scripts/scene-file-deduper/templates scripts/scene-file-deduper/static
```

**Step 2: Create requirements.txt**

Create `scripts/scene-file-deduper/requirements.txt`:

```
flask>=3.0.0
requests>=2.31.0
python-dotenv>=1.0.0
```

**Step 3: Create .env.example**

Create `scripts/scene-file-deduper/.env.example`:

```
STASH_URL=http://localhost:9999
STASH_API_KEY=your-api-key-here
```

**Step 4: Update .gitignore to allow this new script folder**

Edit `.gitignore` to add exception for the new folder (add after the duplicate-performer-finder line):

```
!scripts/scene-file-deduper/
```

**Step 5: Commit**

```bash
git add scripts/scene-file-deduper/requirements.txt scripts/scene-file-deduper/.env.example .gitignore
git commit -m "feat(sceneFileDeduper): add project scaffolding"
```

---

## Task 2: Stash GraphQL Client

**Files:**
- Create: `scripts/scene-file-deduper/stash_client.py`

**Step 1: Create the StashClient class with base methods**

Create `scripts/scene-file-deduper/stash_client.py`:

```python
"""GraphQL client for Stash API - Scene File Deduper."""

import requests


class StashClient:
    """Client for interacting with Stash GraphQL API."""

    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/") + "/graphql"
        self.headers = {
            "Content-Type": "application/json",
            "ApiKey": api_key,
        }

    def _execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query and return the data."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        response = requests.post(self.url, json=payload, headers=self.headers, timeout=30)
        response.raise_for_status()

        result = response.json()
        if "errors" in result:
            raise RuntimeError(f"GraphQL error: {result['errors']}")

        return result["data"]

    def test_connection(self) -> bool:
        """Test connection to Stash. Returns True if successful."""
        query = "query { systemStatus { databaseSchema } }"
        self._execute(query)
        return True

    def get_all_tags(self) -> list[dict]:
        """Fetch all tags for autocomplete."""
        query = """
        query AllTags {
          allTags {
            id
            name
          }
        }
        """
        data = self._execute(query)
        return data["allTags"]

    def get_multi_file_scenes(self, exclude_tag_ids: list[str] | None = None) -> list[dict]:
        """Fetch all scenes with more than one file, optionally excluding scenes with certain tags."""
        # Build the filter
        scene_filter = "file_count: { value: 1, modifier: GREATER_THAN }"
        if exclude_tag_ids:
            tag_filter = f'tags: {{ value: {exclude_tag_ids}, modifier: EXCLUDES }}'
            scene_filter = f"{scene_filter}, {tag_filter}"

        query = f"""
        query MultiFileScenes {{
          findScenes(scene_filter: {{ {scene_filter} }}, filter: {{ per_page: -1 }}) {{
            scenes {{
              id
              title
              files {{
                id
                path
                basename
                size
                duration
                video_codec
                audio_codec
                width
                height
                frame_rate
                bit_rate
              }}
              performers {{
                id
                name
              }}
              studio {{
                id
                name
              }}
              tags {{
                id
                name
              }}
            }}
          }}
        }}
        """
        data = self._execute(query)
        return data["findScenes"]["scenes"]

    def set_scene_primary_file(self, scene_id: str, file_id: str) -> None:
        """Set the primary file for a scene."""
        query = """
        mutation SetPrimaryFile($id: ID!, $primary_file_id: ID!) {
          sceneUpdate(input: { id: $id, primary_file_id: $primary_file_id }) {
            id
          }
        }
        """
        self._execute(query, {"id": scene_id, "primary_file_id": file_id})

    def delete_files(self, file_ids: list[str]) -> bool:
        """Delete files by ID. Returns True if successful."""
        query = """
        mutation DeleteFiles($ids: [ID!]!) {
          deleteFiles(ids: $ids)
        }
        """
        data = self._execute(query, {"ids": file_ids})
        return data["deleteFiles"]

    def delete_scene_files(
        self,
        scene_id: str,
        file_ids_to_delete: list[str],
        keep_file_id: str,
        all_file_ids: list[str],
    ) -> bool:
        """
        Delete specified files from a scene, handling primary file logic.

        If the primary file (first in list) is being deleted, we first set
        the keep_file_id as primary, then delete the others.

        Args:
            scene_id: The scene ID
            file_ids_to_delete: File IDs to delete
            keep_file_id: The file ID to keep (will be set as primary if needed)
            all_file_ids: All file IDs in order (first is primary)
        """
        primary_file_id = all_file_ids[0] if all_file_ids else None

        # If we're deleting the primary file, set the keep file as primary first
        if primary_file_id in file_ids_to_delete:
            self.set_scene_primary_file(scene_id, keep_file_id)

        # Now delete the files
        return self.delete_files(file_ids_to_delete)
```

**Step 2: Commit**

```bash
git add scripts/scene-file-deduper/stash_client.py
git commit -m "feat(sceneFileDeduper): add Stash GraphQL client"
```

---

## Task 3: HTML Template with Tag Filter and Scene Cards

**Files:**
- Create: `scripts/scene-file-deduper/templates/report.html`

**Step 1: Create the HTML template**

Create `scripts/scene-file-deduper/templates/report.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scene File Deduper</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <header>
        <h1>Scene File Deduper</h1>
        <p class="summary">
            {% if total_scenes > 0 %}
                Found <strong>{{ total_scenes }}</strong> scene{{ 's' if total_scenes != 1 else '' }} with multiple files
            {% else %}
                No multi-file scenes found.
            {% endif %}
        </p>
    </header>

    <div class="filter-bar">
        <label for="tag-input">Exclude scenes with tags:</label>
        <div class="tag-input-container">
            <input type="text" id="tag-input" placeholder="Type to search tags..." autocomplete="off">
            <div id="tag-suggestions" class="suggestions hidden"></div>
        </div>
        <div id="selected-tags" class="selected-tags"></div>
        <button id="apply-filter" class="apply-btn">Apply Filter</button>
    </div>

    <main id="scenes-container">
        {% for scene in scenes %}
        <div class="scene-card" data-scene-id="{{ scene.id }}">
            <div class="scene-header">
                <h2>{{ scene.title or 'Untitled Scene' }}</h2>
                <div class="scene-meta">
                    {% if scene.studio %}
                    <span class="studio">{{ scene.studio.name }}</span>
                    {% endif %}
                    {% if scene.performers %}
                    <span class="performers">{{ scene.performers | map(attribute='name') | join(', ') }}</span>
                    {% endif %}
                </div>
                {% if scene.tags %}
                <div class="scene-tags">
                    {% for tag in scene.tags %}
                    <span class="tag">{{ tag.name }}</span>
                    {% endfor %}
                </div>
                {% endif %}
            </div>

            <div class="files-container">
                {% for file in scene.files %}
                <div class="file-card" data-file-id="{{ file.id }}">
                    {% if loop.first %}
                    <div class="primary-badge">Primary</div>
                    {% endif %}

                    <div class="file-info">
                        <div class="resolution">{{ file.width }}x{{ file.height }}</div>
                        <div class="codec">{{ file.video_codec or 'Unknown' }}</div>
                    </div>

                    <div class="file-details">
                        <div class="detail">
                            <span class="label">Size:</span>
                            <span class="value">{{ (file.size / 1024 / 1024 / 1024) | round(2) }} GB</span>
                        </div>
                        <div class="detail">
                            <span class="label">Duration:</span>
                            <span class="value">{{ (file.duration / 60) | round(1) }} min</span>
                        </div>
                        <div class="detail">
                            <span class="label">Bitrate:</span>
                            <span class="value">{{ ((file.bit_rate or 0) / 1000000) | round(1) }} Mbps</span>
                        </div>
                        <div class="detail">
                            <span class="label">Frame Rate:</span>
                            <span class="value">{{ file.frame_rate | round(2) }} fps</span>
                        </div>
                        <div class="detail">
                            <span class="label">Audio:</span>
                            <span class="value">{{ file.audio_codec or 'Unknown' }}</span>
                        </div>
                    </div>

                    <div class="file-path" title="{{ file.path }}">{{ file.basename }}</div>

                    <div class="file-actions">
                        <button class="keep-btn" onclick="keepFile('{{ scene.id }}', '{{ file.id }}', this)">
                            Keep This
                        </button>
                        <button class="delete-btn" onclick="deleteFile('{{ scene.id }}', '{{ file.id }}', this)">
                            Delete
                        </button>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </main>

    <div id="toast" class="toast hidden"></div>

    <script>
        // Store all tags for autocomplete
        const allTags = {{ all_tags | tojson }};
        let selectedTagIds = [];

        // Tag autocomplete functionality
        const tagInput = document.getElementById('tag-input');
        const suggestions = document.getElementById('tag-suggestions');
        const selectedTagsDiv = document.getElementById('selected-tags');

        tagInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            if (query.length < 1) {
                suggestions.classList.add('hidden');
                return;
            }

            const matches = allTags
                .filter(tag => tag.name.toLowerCase().includes(query))
                .filter(tag => !selectedTagIds.includes(tag.id))
                .slice(0, 10);

            if (matches.length === 0) {
                suggestions.classList.add('hidden');
                return;
            }

            suggestions.innerHTML = matches
                .map(tag => `<div class="suggestion" data-id="${tag.id}">${tag.name}</div>`)
                .join('');
            suggestions.classList.remove('hidden');
        });

        suggestions.addEventListener('click', (e) => {
            if (e.target.classList.contains('suggestion')) {
                const tagId = e.target.dataset.id;
                const tagName = e.target.textContent;
                addTag(tagId, tagName);
                tagInput.value = '';
                suggestions.classList.add('hidden');
            }
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.tag-input-container')) {
                suggestions.classList.add('hidden');
            }
        });

        function addTag(id, name) {
            if (selectedTagIds.includes(id)) return;
            selectedTagIds.push(id);

            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.dataset.id = id;
            chip.innerHTML = `${name} <span class="remove" onclick="removeTag('${id}')">&times;</span>`;
            selectedTagsDiv.appendChild(chip);
        }

        function removeTag(id) {
            selectedTagIds = selectedTagIds.filter(t => t !== id);
            const chip = selectedTagsDiv.querySelector(`[data-id="${id}"]`);
            if (chip) chip.remove();
        }

        document.getElementById('apply-filter').addEventListener('click', () => {
            const params = new URLSearchParams();
            if (selectedTagIds.length > 0) {
                params.set('exclude_tags', selectedTagIds.join(','));
            }
            window.location.href = '/?' + params.toString();
        });

        // Initialize selected tags from URL
        const urlParams = new URLSearchParams(window.location.search);
        const excludeTags = urlParams.get('exclude_tags');
        if (excludeTags) {
            excludeTags.split(',').forEach(id => {
                const tag = allTags.find(t => t.id === id);
                if (tag) addTag(tag.id, tag.name);
            });
        }

        // File action functions
        async function keepFile(sceneId, fileId, buttonElement) {
            const sceneCard = buttonElement.closest('.scene-card');
            const allFileCards = sceneCard.querySelectorAll('.file-card');
            const allFileIds = Array.from(allFileCards).map(c => c.dataset.fileId);
            const fileIdsToDelete = allFileIds.filter(id => id !== fileId);

            if (fileIdsToDelete.length === 0) {
                showToast('This is the only file', 'error');
                return;
            }

            // Disable all buttons
            sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = true);

            try {
                const response = await fetch('/api/delete-files', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        scene_id: sceneId,
                        file_ids_to_delete: fileIdsToDelete,
                        keep_file_id: fileId,
                        all_file_ids: allFileIds
                    })
                });

                const result = await response.json();

                if (result.success) {
                    showToast('Files deleted, kept selected file', 'success');
                    sceneCard.remove();
                    updateSummary();
                } else {
                    showToast(`Error: ${result.error}`, 'error');
                    sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = false);
                }
            } catch (err) {
                showToast(`Network error: ${err.message}`, 'error');
                sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = false);
            }
        }

        async function deleteFile(sceneId, fileId, buttonElement) {
            const sceneCard = buttonElement.closest('.scene-card');
            const allFileCards = sceneCard.querySelectorAll('.file-card');
            const allFileIds = Array.from(allFileCards).map(c => c.dataset.fileId);

            if (allFileIds.length <= 1) {
                showToast('Cannot delete the only file', 'error');
                return;
            }

            // Find a file to keep (any file that's not being deleted)
            const keepFileId = allFileIds.find(id => id !== fileId);

            // Disable all buttons
            sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = true);

            try {
                const response = await fetch('/api/delete-files', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        scene_id: sceneId,
                        file_ids_to_delete: [fileId],
                        keep_file_id: keepFileId,
                        all_file_ids: allFileIds
                    })
                });

                const result = await response.json();

                if (result.success) {
                    showToast('File deleted', 'success');
                    // If only one file left, remove the scene card
                    if (allFileIds.length === 2) {
                        sceneCard.remove();
                        updateSummary();
                    } else {
                        // Remove just this file card
                        const fileCard = buttonElement.closest('.file-card');
                        fileCard.remove();
                        // Re-enable buttons on remaining cards
                        sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = false);
                    }
                } else {
                    showToast(`Error: ${result.error}`, 'error');
                    sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = false);
                }
            } catch (err) {
                showToast(`Network error: ${err.message}`, 'error');
                sceneCard.querySelectorAll('button').forEach(btn => btn.disabled = false);
            }
        }

        function showToast(message, type) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = `toast ${type}`;
            setTimeout(() => {
                toast.className = 'toast hidden';
            }, 3000);
        }

        function updateSummary() {
            const scenes = document.querySelectorAll('.scene-card');
            const summary = document.querySelector('.summary');

            if (scenes.length === 0) {
                summary.innerHTML = 'No multi-file scenes found.';
            } else {
                summary.innerHTML = `Found <strong>${scenes.length}</strong> scene${scenes.length !== 1 ? 's' : ''} with multiple files`;
            }
        }
    </script>
</body>
</html>
```

**Step 2: Commit**

```bash
git add scripts/scene-file-deduper/templates/report.html
git commit -m "feat(sceneFileDeduper): add HTML template with tag filter"
```

---

## Task 4: CSS Styling

**Files:**
- Create: `scripts/scene-file-deduper/static/style.css`

**Step 1: Create the stylesheet**

Create `scripts/scene-file-deduper/static/style.css`:

```css
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f5f5;
    color: #333;
    line-height: 1.5;
    padding: 2rem;
}

header {
    text-align: center;
    margin-bottom: 1.5rem;
}

h1 {
    font-size: 1.8rem;
    margin-bottom: 0.5rem;
}

.summary {
    color: #666;
}

/* Filter Bar */
.filter-bar {
    background: white;
    padding: 1rem;
    border-radius: 8px;
    margin-bottom: 2rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.filter-bar label {
    display: block;
    margin-bottom: 0.5rem;
    font-weight: 500;
}

.tag-input-container {
    position: relative;
    margin-bottom: 0.5rem;
}

#tag-input {
    width: 100%;
    padding: 0.5rem;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 1rem;
}

.suggestions {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    background: white;
    border: 1px solid #ddd;
    border-top: none;
    border-radius: 0 0 4px 4px;
    max-height: 200px;
    overflow-y: auto;
    z-index: 100;
}

.suggestions.hidden {
    display: none;
}

.suggestion {
    padding: 0.5rem;
    cursor: pointer;
}

.suggestion:hover {
    background: #f0f0f0;
}

.selected-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}

.tag-chip {
    background: #e3f2fd;
    color: #1976d2;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    font-size: 0.85rem;
    display: flex;
    align-items: center;
    gap: 0.25rem;
}

.tag-chip .remove {
    cursor: pointer;
    font-weight: bold;
    margin-left: 0.25rem;
}

.apply-btn {
    padding: 0.5rem 1rem;
    background: #2196f3;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9rem;
}

.apply-btn:hover {
    background: #1976d2;
}

/* Scene Cards */
.scene-card {
    background: white;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.scene-header {
    margin-bottom: 1rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid #eee;
}

.scene-header h2 {
    font-size: 1.2rem;
    margin-bottom: 0.25rem;
}

.scene-meta {
    font-size: 0.85rem;
    color: #666;
}

.scene-meta .studio {
    font-weight: 500;
}

.scene-meta .studio::after {
    content: ' - ';
}

.scene-tags {
    margin-top: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
}

.scene-tags .tag {
    background: #f0f0f0;
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
    font-size: 0.75rem;
    color: #666;
}

/* File Cards Container */
.files-container {
    display: flex;
    gap: 1rem;
    overflow-x: auto;
    padding-bottom: 0.5rem;
}

.file-card {
    flex: 0 0 280px;
    background: #fafafa;
    border: 2px solid #e0e0e0;
    border-radius: 6px;
    padding: 1rem;
    position: relative;
}

.primary-badge {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    background: #4caf50;
    color: white;
    font-size: 0.7rem;
    padding: 0.2rem 0.5rem;
    border-radius: 3px;
    font-weight: 500;
}

.file-info {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
}

.file-info .resolution {
    font-size: 1.2rem;
    font-weight: 600;
    color: #333;
}

.file-info .codec {
    font-size: 0.85rem;
    background: #e3e3e3;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
}

.file-details {
    font-size: 0.85rem;
    margin-bottom: 0.75rem;
}

.file-details .detail {
    display: flex;
    justify-content: space-between;
    margin-bottom: 0.2rem;
}

.file-details .label {
    color: #666;
}

.file-details .value {
    font-weight: 500;
}

.file-path {
    font-size: 0.75rem;
    color: #999;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 0.75rem;
    font-family: monospace;
}

.file-actions {
    display: flex;
    gap: 0.5rem;
}

.keep-btn, .delete-btn {
    flex: 1;
    padding: 0.5rem;
    border: none;
    border-radius: 4px;
    font-size: 0.85rem;
    cursor: pointer;
    transition: background 0.2s;
}

.keep-btn {
    background: #4caf50;
    color: white;
}

.keep-btn:hover:not(:disabled) {
    background: #43a047;
}

.delete-btn {
    background: #f44336;
    color: white;
}

.delete-btn:hover:not(:disabled) {
    background: #e53935;
}

.keep-btn:disabled, .delete-btn:disabled {
    background: #bbb;
    cursor: not-allowed;
}

/* Toast */
.toast {
    position: fixed;
    bottom: 2rem;
    left: 50%;
    transform: translateX(-50%);
    padding: 0.75rem 1.5rem;
    border-radius: 4px;
    font-size: 0.9rem;
    transition: opacity 0.3s;
    z-index: 1000;
}

.toast.hidden {
    opacity: 0;
    pointer-events: none;
}

.toast.success {
    background: #4caf50;
    color: white;
}

.toast.error {
    background: #f44336;
    color: white;
}
```

**Step 2: Commit**

```bash
git add scripts/scene-file-deduper/static/style.css
git commit -m "feat(sceneFileDeduper): add CSS styling"
```

---

## Task 5: Flask Application

**Files:**
- Create: `scripts/scene-file-deduper/app.py`

**Step 1: Create the Flask application**

Create `scripts/scene-file-deduper/app.py`:

```python
#!/usr/bin/env python3
"""Scene File Deduper - Flask web application."""

import os
import sys

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from stash_client import StashClient


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Load environment variables
    load_dotenv()

    stash_url = os.getenv("STASH_URL")
    api_key = os.getenv("STASH_API_KEY")

    if not stash_url or not api_key:
        print("Error: Missing configuration.")
        print("Please copy .env.example to .env and fill in your Stash URL and API key.")
        sys.exit(1)

    # Initialize Stash client
    client = StashClient(stash_url, api_key)

    # Test connection
    try:
        client.test_connection()
        print(f"Connected to Stash at {stash_url}")
    except Exception as e:
        print(f"Error: Cannot connect to Stash at {stash_url}")
        print(f"Details: {e}")
        sys.exit(1)

    # Fetch all tags once at startup for autocomplete
    all_tags = client.get_all_tags()
    print(f"Loaded {len(all_tags)} tags for autocomplete")

    @app.route("/")
    def index():
        """Serve the scene file deduper page."""
        # Get excluded tag IDs from query params
        exclude_tags_param = request.args.get("exclude_tags", "")
        exclude_tag_ids = [t for t in exclude_tags_param.split(",") if t]

        # Fetch multi-file scenes
        scenes = client.get_multi_file_scenes(exclude_tag_ids if exclude_tag_ids else None)

        return render_template(
            "report.html",
            scenes=scenes,
            total_scenes=len(scenes),
            all_tags=all_tags,
        )

    @app.route("/api/delete-files", methods=["POST"])
    def delete_files():
        """Delete files from a scene."""
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid JSON body"}), 400

        scene_id = data.get("scene_id")
        file_ids_to_delete = data.get("file_ids_to_delete", [])
        keep_file_id = data.get("keep_file_id")
        all_file_ids = data.get("all_file_ids", [])

        if not scene_id or not file_ids_to_delete or not keep_file_id or not all_file_ids:
            return jsonify({"success": False, "error": "Missing required fields"}), 400

        try:
            result = client.delete_scene_files(
                scene_id=scene_id,
                file_ids_to_delete=file_ids_to_delete,
                keep_file_id=keep_file_id,
                all_file_ids=all_file_ids,
            )
            return jsonify({"success": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    print("Open http://localhost:5001 in your browser")
    app.run(host="127.0.0.1", port=5001, debug=False)
```

**Step 2: Commit**

```bash
git add scripts/scene-file-deduper/app.py
git commit -m "feat(sceneFileDeduper): add Flask application"
```

---

## Task 6: Manual Integration Test

**Files:** None (manual testing)

**Step 1: Set up the environment**

```bash
cd scripts/scene-file-deduper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Step 2: Configure credentials**

```bash
cp .env.example .env
# Edit .env with your actual STASH_URL and STASH_API_KEY
```

**Step 3: Run the application**

```bash
python app.py
```

Expected output:
```
Connected to Stash at http://localhost:9999
Loaded X tags for autocomplete
Open http://localhost:5001 in your browser
```

**Step 4: Test in browser**

1. Open http://localhost:5001
2. Verify scenes with multiple files are displayed
3. Verify tag exclusion filter works (type a tag name, select it, click Apply)
4. Test "Delete" button on a file (should delete just that file)
5. Test "Keep This" button on a file (should delete all other files)
6. Verify toast messages appear
7. Verify scene cards are removed after successful actions

**Step 5: Stop the server and deactivate venv**

```bash
# Ctrl+C to stop server
deactivate
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Project scaffolding | requirements.txt, .env.example, .gitignore |
| 2 | Stash GraphQL client | stash_client.py |
| 3 | HTML template | templates/report.html |
| 4 | CSS styling | static/style.css |
| 5 | Flask application | app.py |
| 6 | Manual integration test | (none) |
