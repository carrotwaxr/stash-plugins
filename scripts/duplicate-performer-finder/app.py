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
        if not data or not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid JSON body"}), 400

        destination_id = data.get("destination_id")
        source_ids = data.get("source_ids", [])

        if not destination_id or not source_ids:
            return jsonify({"success": False, "error": "Missing destination_id or source_ids"}), 400

        try:
            result = client.merge_performers(source_ids, destination_id)
            # Invalidate cache so next page load gets fresh data
            app.config["duplicates_cache"] = None
            return jsonify({"success": True, "merged_into": result["name"]})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/refresh", methods=["POST"])
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
