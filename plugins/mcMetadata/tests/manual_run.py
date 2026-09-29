#!/usr/bin/env python3
"""
Manual test script for mcMetadata plugin.

This script allows you to test various plugin functions against your Stash server
without actually modifying any files. It's useful for development and debugging.

Usage:
    python tests/manual_test.py --url http://your-stash:9999 --api-key your-key

Options:
    --url       Stash server URL (or set STASH_URL env var)
    --api-key   Stash API key (or set STASH_API_KEY env var)
    --scene-id  Test a specific scene ID
    --limit     Max number of scenes to process (default: 5)
"""

import argparse
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.replacer import get_new_path
from utils.nfo import build_nfo_xml


def connect_to_stash(url, api_key):
    """Create a Stash connection.

    NOTE: This connection is used for READ-ONLY operations only.
    The moveFiles mutation is NEVER called in this test script.
    """
    from stashapi.stashapp import StashInterface
    from urllib.parse import urlparse

    # Parse URL properly
    if "://" not in url:
        url = "http://" + url

    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    port = parsed.port or 9999

    # Remove /graphql from path if present - stashapi adds it
    graphql_url = f"{scheme}://{host}:{port}/graphql"

    # StashInterface expects this format for API key auth
    server_connection = {
        "Scheme": scheme,
        "Host": host,
        "Port": port,
        "ApiKey": api_key,  # Pass API key here!
        "SessionCookie": {"Name": "session", "Value": ""},
        "Dir": "",
        "PluginDir": ""
    }

    stash = StashInterface(server_connection)
    # Override the URL in case it got constructed incorrectly
    stash.url = graphql_url

    return stash


def hydrate_scene(scene, stash):
    """Fetch full performer and studio data for a scene."""
    if scene.get("performers"):
        hydrated_performers = []
        for p in scene["performers"]:
            performer = stash.find_performer(p["id"], False, "id name gender image_path")
            if performer:
                hydrated_performers.append(performer)
        scene["performers"] = sorted(
            hydrated_performers,
            key=lambda p: f"{p.get('gender', 'UNKNOWN')}_{p['name']}"
        )

    if scene.get("studio"):
        scene["studio"] = stash.find_studio(
            scene["studio"]["id"],
            "id name parent_studio { id name parent_studio { id name } }"
        )

    return scene


def test_scene(scene, stash, settings):
    """Test processing a single scene."""
    print(f"\n{'='*60}")
    print(f"Scene: {scene.get('title', 'No title')}")
    print(f"ID: {scene['id']}")
    print(f"{'='*60}")

    # Hydrate scene
    scene = hydrate_scene(scene, stash)

    # Show scene info
    print(f"\nScene Details:")
    print(f"  Date: {scene.get('date', 'N/A')}")
    print(f"  Studio: {scene.get('studio', {}).get('name', 'N/A') if scene.get('studio') else 'N/A'}")
    print(f"  Rating: {scene.get('rating100', 'N/A')}")
    print(f"  Files: {len(scene.get('files', []))}")

    if scene.get("performers"):
        print(f"  Performers:")
        for p in scene["performers"]:
            print(f"    - {p['name']} ({p.get('gender', 'Unknown')})")

    if scene.get("stash_ids"):
        print(f"  StashIDs:")
        for sid in scene["stash_ids"]:
            print(f"    - {sid.get('endpoint', 'N/A')}: {sid.get('stash_id', 'N/A')}")

    # Show files
    if scene.get("files"):
        print(f"\nFiles:")
        for i, f in enumerate(scene["files"]):
            print(f"  File {i+1}:")
            print(f"    ID: {f.get('id')}")
            print(f"    Path: {f.get('path')}")
            print(f"    Resolution: {f.get('width')}x{f.get('height')}")

    # Test path generation
    print(f"\nPath Generation:")
    print(f"  Template: {settings['renamer_path_template']}")
    print(f"  Base path: {settings['renamer_path']}")

    for i, file_info in enumerate(scene.get("files", [])):
        # Create scene copy with this file as primary
        scene_for_file = scene.copy()
        scene_for_file["files"] = [file_info]

        new_path = get_new_path(
            scene_for_file,
            settings["renamer_path"],
            settings["renamer_path_template"],
            settings["renamer_filepath_budget"]
        )

        if new_path:
            print(f"\n  File {i+1}:")
            print(f"    Current: {file_info.get('path')}")
            print(f"    Would become: {new_path}")
        else:
            print(f"\n  File {i+1}: Could not generate path (missing required fields)")

    # Test NFO generation
    print(f"\nNFO Generation:")
    try:
        nfo_xml = build_nfo_xml(scene)
        print(f"  [OK] Generated {len(nfo_xml)} characters of XML")

        # Show a preview
        lines = nfo_xml.split("\n")
        print(f"  Preview (first 10 lines):")
        for line in lines[:10]:
            print(f"    {line}")
        if len(lines) > 10:
            print(f"    ... ({len(lines) - 10} more lines)")
    except Exception as e:
        print(f"  [ERROR] Error generating NFO: {e}")

    return scene


def main():
    parser = argparse.ArgumentParser(description="Manual test for mcMetadata plugin")
    parser.add_argument("--url", help="Stash server URL", default=os.environ.get("STASH_URL"))
    parser.add_argument("--api-key", help="Stash API key", default=os.environ.get("STASH_API_KEY"))
    parser.add_argument("--scene-id", help="Test a specific scene ID", type=int)
    parser.add_argument("--limit", help="Max scenes to process", type=int, default=5)
    parser.add_argument("--template", help="Renamer template to test",
                        default="$Studio/$Title - $FemalePerformers $ReleaseDate [$Resolution]")
    parser.add_argument("--base-path", help="Base path for renamer", default="/data/tagged/")

    args = parser.parse_args()

    if not args.url or not args.api_key:
        print("Error: Stash URL and API key required")
        print("Set STASH_URL and STASH_API_KEY environment variables, or use --url and --api-key")
        sys.exit(1)

    # Settings for testing
    settings = {
        "dry_run": True,
        "enable_renamer": True,
        "renamer_path": args.base_path,
        "renamer_path_template": args.template,
        "renamer_filepath_budget": 250,
        "renamer_ignore_files_in_path": False,
        "renamer_enable_mark_organized": True,
        "renamer_multi_file_mode": "all",
        "enable_actor_images": False,
        "nfo_skip_existing": False,
    }

    print("\n" + "="*60)
    print("mcMetadata Plugin - Manual Test")
    print("="*60)
    print(f"\nConnecting to: {args.url}")

    try:
        stash = connect_to_stash(args.url, args.api_key)

        # Test connection
        config = stash.get_configuration()
        print(f"[OK] Connected successfully")
        print(f"  Database: {config['general'].get('databasePath', 'N/A')}")

        if args.scene_id:
            # Test specific scene
            print(f"\nFetching scene ID: {args.scene_id}")
            scene = stash.find_scene(args.scene_id)
            if scene:
                test_scene(scene, stash, settings)
            else:
                print(f"Scene {args.scene_id} not found")
        else:
            # Find scenes with StashIDs
            query = {
                "stash_id_endpoint": {
                    "endpoint": "",
                    "modifier": "NOT_NULL",
                    "stash_id": "",
                }
            }

            count_result = stash.find_scenes(
                f=query,
                filter={"per_page": 1},
                get_count=True
            )
            if isinstance(count_result, tuple):
                total_count = count_result[0]
            else:
                total_count = 0

            print(f"\nFound {total_count} scenes with StashIDs")
            print(f"Testing first {args.limit} scenes...\n")

            scenes = stash.find_scenes(f=query, filter={"per_page": args.limit})

            for scene in scenes:
                test_scene(scene, stash, settings)

        print("\n" + "="*60)
        print("Test complete!")
        print("="*60 + "\n")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
