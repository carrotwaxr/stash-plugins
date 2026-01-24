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
