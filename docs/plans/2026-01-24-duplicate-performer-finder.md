# Duplicate Performer Finder Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python web app that finds duplicate performers in Stash (same stash-box ID) and provides one-click merging.

**Architecture:** Flask serves an HTML report showing duplicate groups as side-by-side cards. JavaScript handles merge button clicks by calling a local API endpoint, which forwards to Stash's `performerMerge` mutation. Page updates dynamically after each merge.

**Tech Stack:** Python 3, Flask, requests, python-dotenv, Jinja2 templates, vanilla JavaScript

---

## Task 1: Project Scaffolding

**Files:**
- Create: `scripts/duplicate-performer-finder/requirements.txt`
- Create: `scripts/duplicate-performer-finder/.env.example`

**Step 1: Create directory structure**

```bash
mkdir -p scripts/duplicate-performer-finder/templates scripts/duplicate-performer-finder/static
```

**Step 2: Create requirements.txt**

Create `scripts/duplicate-performer-finder/requirements.txt`:

```
flask>=3.0.0
requests>=2.31.0
python-dotenv>=1.0.0
```

**Step 3: Create .env.example**

Create `scripts/duplicate-performer-finder/.env.example`:

```
STASH_URL=http://localhost:9999
STASH_API_KEY=your-api-key-here
```

**Step 4: Commit**

```bash
git add scripts/duplicate-performer-finder/
git commit -m "chore: scaffold duplicate-performer-finder project"
```

---

## Task 2: Stash GraphQL Client

**Files:**
- Create: `scripts/duplicate-performer-finder/stash_client.py`

**Step 1: Write the Stash client module**

Create `scripts/duplicate-performer-finder/stash_client.py`:

```python
"""GraphQL client for Stash API."""

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

    def get_all_performers(self) -> list[dict]:
        """Fetch all performers with stash_ids."""
        query = """
        query AllPerformersWithStashIDs {
          findPerformers(filter: { per_page: -1 }) {
            performers {
              id
              name
              alias_list
              gender
              country
              scene_count
              image_count
              gallery_count
              stash_ids {
                endpoint
                stash_id
              }
            }
          }
        }
        """
        data = self._execute(query)
        return data["findPerformers"]["performers"]

    def merge_performers(self, source_ids: list[str], destination_id: str) -> dict:
        """Merge source performers into destination performer."""
        query = """
        mutation MergePerformers($source: [ID!]!, $destination: ID!) {
          performerMerge(input: { source: $source, destination: $destination }) {
            id
            name
          }
        }
        """
        variables = {"source": source_ids, "destination": destination_id}
        data = self._execute(query, variables)
        return data["performerMerge"]
```

**Step 2: Verify syntax**

```bash
python3 -m py_compile scripts/duplicate-performer-finder/stash_client.py
```

Expected: No output (success)

**Step 3: Commit**

```bash
git add scripts/duplicate-performer-finder/stash_client.py
git commit -m "feat: add Stash GraphQL client"
```

---

## Task 3: Duplicate Detection Logic

**Files:**
- Create: `scripts/duplicate-performer-finder/duplicate_finder.py`

**Step 1: Write the duplicate finder module**

Create `scripts/duplicate-performer-finder/duplicate_finder.py`:

```python
"""Duplicate performer detection logic."""


def find_duplicates(performers: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """
    Find performers that share the same stash_id for the same endpoint.

    Args:
        performers: List of performer dicts from Stash API

    Returns:
        Dict mapping (endpoint, stash_id) tuples to lists of duplicate performers.
        Only includes groups with 2+ performers.
    """
    buckets: dict[tuple[str, str], list[dict]] = {}

    for performer in performers:
        stash_ids = performer.get("stash_ids") or []
        for sid in stash_ids:
            key = (sid["endpoint"], sid["stash_id"])
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(performer)

    # Filter to only actual duplicates (2+ performers sharing same stash_id)
    duplicates = {k: v for k, v in buckets.items() if len(v) >= 2}

    return duplicates


def get_total_content_count(performer: dict) -> int:
    """Calculate total content count for a performer."""
    return (
        (performer.get("scene_count") or 0)
        + (performer.get("image_count") or 0)
        + (performer.get("gallery_count") or 0)
    )


def group_by_endpoint(duplicates: dict[tuple[str, str], list[dict]]) -> dict[str, list[dict]]:
    """
    Reorganize duplicates grouped by endpoint for display.

    Returns:
        Dict mapping endpoint URLs to lists of duplicate groups.
        Each group is a dict with 'stash_id' and 'performers' keys.
    """
    by_endpoint: dict[str, list[dict]] = {}

    for (endpoint, stash_id), performers in duplicates.items():
        if endpoint not in by_endpoint:
            by_endpoint[endpoint] = []

        # Sort performers by content count (highest first) for suggested keeper
        sorted_performers = sorted(
            performers,
            key=get_total_content_count,
            reverse=True,
        )

        # Mark the suggested keeper (highest content count)
        for i, p in enumerate(sorted_performers):
            p["is_suggested"] = i == 0
            p["total_content"] = get_total_content_count(p)

        by_endpoint[endpoint].append({
            "stash_id": stash_id,
            "performers": sorted_performers,
        })

    return by_endpoint
```

**Step 2: Verify syntax**

```bash
python3 -m py_compile scripts/duplicate-performer-finder/duplicate_finder.py
```

Expected: No output (success)

**Step 3: Commit**

```bash
git add scripts/duplicate-performer-finder/duplicate_finder.py
git commit -m "feat: add duplicate detection logic"
```

---

## Task 4: HTML Template

**Files:**
- Create: `scripts/duplicate-performer-finder/templates/report.html`

**Step 1: Write the Jinja2 template**

Create `scripts/duplicate-performer-finder/templates/report.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Duplicate Performer Finder</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <header>
        <h1>Duplicate Performer Finder</h1>
        <p class="summary">
            {% if total_groups > 0 %}
                Found <strong>{{ total_groups }}</strong> duplicate group{{ 's' if total_groups != 1 else '' }}
                across <strong>{{ endpoint_count }}</strong> endpoint{{ 's' if endpoint_count != 1 else '' }}
            {% else %}
                No duplicate performers found. Your library is clean!
            {% endif %}
        </p>
    </header>

    <main>
        {% for endpoint, groups in duplicates_by_endpoint.items() %}
        <section class="endpoint-section">
            <h2>{{ endpoint }}</h2>

            {% for group in groups %}
            <div class="duplicate-group" data-stash-id="{{ group.stash_id }}" data-endpoint="{{ endpoint }}">
                <div class="group-header">
                    <span class="stash-id">Stash ID: {{ group.stash_id[:8] }}...</span>
                    <span class="performer-count">{{ group.performers | length }} performers</span>
                </div>

                <div class="performer-cards">
                    {% for performer in group.performers %}
                    <div class="performer-card {% if performer.is_suggested %}suggested{% endif %}"
                         data-performer-id="{{ performer.id }}">
                        <h3>{{ performer.name }}</h3>

                        {% if performer.alias_list %}
                        <div class="field">
                            <span class="label">Aliases:</span>
                            <span class="value">{{ performer.alias_list | join(', ') }}</span>
                        </div>
                        {% endif %}

                        {% if performer.gender %}
                        <div class="field">
                            <span class="label">Gender:</span>
                            <span class="value">{{ performer.gender }}</span>
                        </div>
                        {% endif %}

                        {% if performer.country %}
                        <div class="field">
                            <span class="label">Country:</span>
                            <span class="value">{{ performer.country }}</span>
                        </div>
                        {% endif %}

                        <div class="counts">
                            <span class="count" title="Scenes">{{ performer.scene_count or 0 }} scenes</span>
                            <span class="count" title="Images">{{ performer.image_count or 0 }} images</span>
                            <span class="count" title="Galleries">{{ performer.gallery_count or 0 }} galleries</span>
                        </div>

                        <button class="merge-btn" onclick="mergeInto('{{ performer.id }}', this)">
                            Keep This One
                        </button>

                        {% if performer.is_suggested %}
                        <div class="suggested-badge">Suggested</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </section>
        {% endfor %}
    </main>

    <div id="toast" class="toast hidden"></div>

    <script>
        async function mergeInto(destinationId, buttonElement) {
            const card = buttonElement.closest('.performer-card');
            const group = buttonElement.closest('.duplicate-group');
            const allCards = group.querySelectorAll('.performer-card');

            // Collect source IDs (all performers except the destination)
            const sourceIds = [];
            allCards.forEach(c => {
                const id = c.dataset.performerId;
                if (id !== destinationId) {
                    sourceIds.push(id);
                }
            });

            // Disable all buttons in this group
            group.querySelectorAll('.merge-btn').forEach(btn => {
                btn.disabled = true;
                btn.textContent = 'Merging...';
            });

            try {
                const response = await fetch('/api/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        destination_id: destinationId,
                        source_ids: sourceIds
                    })
                });

                const result = await response.json();

                if (result.success) {
                    showToast(`Merged into ${result.merged_into}`, 'success');
                    group.remove();
                    updateSummary();
                } else {
                    showToast(`Error: ${result.error}`, 'error');
                    // Re-enable buttons
                    group.querySelectorAll('.merge-btn').forEach(btn => {
                        btn.disabled = false;
                        btn.textContent = 'Keep This One';
                    });
                }
            } catch (err) {
                showToast(`Network error: ${err.message}`, 'error');
                group.querySelectorAll('.merge-btn').forEach(btn => {
                    btn.disabled = false;
                    btn.textContent = 'Keep This One';
                });
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
            const groups = document.querySelectorAll('.duplicate-group');
            const summary = document.querySelector('.summary');
            const endpoints = document.querySelectorAll('.endpoint-section');

            if (groups.length === 0) {
                summary.innerHTML = 'No duplicate performers found. Your library is clean!';
                // Remove empty endpoint sections
                endpoints.forEach(section => {
                    if (section.querySelectorAll('.duplicate-group').length === 0) {
                        section.remove();
                    }
                });
            } else {
                const endpointCount = document.querySelectorAll('.endpoint-section').length;
                summary.innerHTML = `Found <strong>${groups.length}</strong> duplicate group${groups.length !== 1 ? 's' : ''} across <strong>${endpointCount}</strong> endpoint${endpointCount !== 1 ? 's' : ''}`;
            }
        }
    </script>
</body>
</html>
```

**Step 2: Commit**

```bash
git add scripts/duplicate-performer-finder/templates/report.html
git commit -m "feat: add HTML report template"
```

---

## Task 5: CSS Styling

**Files:**
- Create: `scripts/duplicate-performer-finder/static/style.css`

**Step 1: Write the stylesheet**

Create `scripts/duplicate-performer-finder/static/style.css`:

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
    margin-bottom: 2rem;
}

h1 {
    font-size: 1.8rem;
    margin-bottom: 0.5rem;
}

.summary {
    color: #666;
}

.endpoint-section {
    margin-bottom: 2rem;
}

.endpoint-section h2 {
    font-size: 1rem;
    color: #666;
    padding: 0.5rem 0;
    border-bottom: 1px solid #ddd;
    margin-bottom: 1rem;
    word-break: break-all;
}

.duplicate-group {
    background: white;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.group-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #eee;
}

.stash-id {
    font-family: monospace;
    font-size: 0.85rem;
    color: #666;
}

.performer-count {
    font-size: 0.85rem;
    color: #999;
}

.performer-cards {
    display: flex;
    gap: 1rem;
    overflow-x: auto;
    padding-bottom: 0.5rem;
}

.performer-card {
    flex: 0 0 280px;
    background: #fafafa;
    border: 2px solid #e0e0e0;
    border-radius: 6px;
    padding: 1rem;
    position: relative;
}

.performer-card.suggested {
    border-color: #4caf50;
    background: #f1f8e9;
}

.performer-card h3 {
    font-size: 1.1rem;
    margin-bottom: 0.75rem;
    padding-right: 70px;
}

.field {
    font-size: 0.85rem;
    margin-bottom: 0.25rem;
}

.field .label {
    color: #666;
}

.field .value {
    color: #333;
}

.counts {
    display: flex;
    gap: 0.5rem;
    margin: 0.75rem 0;
    flex-wrap: wrap;
}

.count {
    font-size: 0.8rem;
    background: #e3e3e3;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
}

.merge-btn {
    width: 100%;
    padding: 0.6rem;
    background: #2196f3;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 0.9rem;
    cursor: pointer;
    transition: background 0.2s;
}

.merge-btn:hover:not(:disabled) {
    background: #1976d2;
}

.merge-btn:disabled {
    background: #bbb;
    cursor: not-allowed;
}

.suggested-badge {
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
git add scripts/duplicate-performer-finder/static/style.css
git commit -m "feat: add CSS styling for report"
```

---

## Task 6: Flask Application

**Files:**
- Create: `scripts/duplicate-performer-finder/app.py`

**Step 1: Write the Flask application**

Create `scripts/duplicate-performer-finder/app.py`:

```python
#!/usr/bin/env python3
"""Duplicate Performer Finder - Flask web application."""

import os
import sys

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from duplicate_finder import find_duplicates, group_by_endpoint
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

    # Store client and cache in app config
    app.config["stash_client"] = client
    app.config["duplicates_cache"] = None

    def refresh_duplicates() -> dict:
        """Fetch performers and detect duplicates."""
        performers = client.get_all_performers()
        duplicates = find_duplicates(performers)
        grouped = group_by_endpoint(duplicates)
        app.config["duplicates_cache"] = {
            "by_endpoint": grouped,
            "total_groups": sum(len(groups) for groups in grouped.values()),
        }
        return app.config["duplicates_cache"]

    @app.route("/")
    def index():
        """Serve the duplicate report page."""
        cache = app.config["duplicates_cache"]
        if cache is None:
            cache = refresh_duplicates()

        return render_template(
            "report.html",
            duplicates_by_endpoint=cache["by_endpoint"],
            total_groups=cache["total_groups"],
            endpoint_count=len(cache["by_endpoint"]),
        )

    @app.route("/api/merge", methods=["POST"])
    def merge():
        """Execute a performer merge."""
        data = request.get_json()
        destination_id = data.get("destination_id")
        source_ids = data.get("source_ids", [])

        if not destination_id or not source_ids:
            return jsonify({"success": False, "error": "Missing destination_id or source_ids"})

        try:
            result = client.merge_performers(source_ids, destination_id)
            # Invalidate cache so next page load gets fresh data
            app.config["duplicates_cache"] = None
            return jsonify({"success": True, "merged_into": result["name"]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/refresh")
    def refresh():
        """Re-fetch performers and return updated duplicate data."""
        cache = refresh_duplicates()
        return jsonify({
            "total_groups": cache["total_groups"],
            "endpoint_count": len(cache["by_endpoint"]),
        })

    # Initial data fetch
    cache = refresh_duplicates()
    print(f"Found {cache['total_groups']} duplicate groups across {len(cache['by_endpoint'])} endpoints")

    return app


if __name__ == "__main__":
    app = create_app()
    print("Open http://localhost:5000 in your browser")
    app.run(host="127.0.0.1", port=5000, debug=False)
```

**Step 2: Make executable**

```bash
chmod +x scripts/duplicate-performer-finder/app.py
```

**Step 3: Verify syntax**

```bash
python3 -m py_compile scripts/duplicate-performer-finder/app.py
```

Expected: No output (success)

**Step 4: Commit**

```bash
git add scripts/duplicate-performer-finder/app.py
git commit -m "feat: add Flask application with merge endpoint"
```

---

## Task 7: Manual Integration Test

**Files:**
- None (testing only)

**Step 1: Set up environment**

```bash
cd scripts/duplicate-performer-finder
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Step 2: Configure .env**

```bash
cp .env.example .env
# Edit .env with your actual Stash URL and API key
```

**Step 3: Run the application**

```bash
python app.py
```

Expected output:
```
Connected to Stash at http://localhost:9999
Found N duplicate groups across M endpoints
Open http://localhost:5000 in your browser
```

**Step 4: Test in browser**

1. Open http://localhost:5000
2. Verify duplicate groups display correctly
3. Test merging one duplicate (click "Keep This One")
4. Verify the group disappears and toast shows success

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: complete duplicate-performer-finder implementation"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Project scaffolding | requirements.txt, .env.example |
| 2 | Stash GraphQL client | stash_client.py |
| 3 | Duplicate detection | duplicate_finder.py |
| 4 | HTML template | templates/report.html |
| 5 | CSS styling | static/style.css |
| 6 | Flask application | app.py |
| 7 | Manual integration test | (none) |
