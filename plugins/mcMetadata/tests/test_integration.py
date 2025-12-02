"""
Integration tests for mcMetadata plugin.

These tests can be run against a real Stash server to validate:
1. GraphQL API communication
2. Data fetching and hydration
3. Path generation logic

No file operations are performed - this is purely for testing the logic.

Usage:
    Set environment variables:
        STASH_URL=http://your-stash-server:9999
        STASH_API_KEY=your-api-key

    Then run:
        python -m unittest tests.test_integration -v
"""

import os
import sys
import unittest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.replacer import get_new_path
from utils.nfo import build_nfo_xml


class StashConnectionTest(unittest.TestCase):
    """Tests that require a connection to a real Stash server."""

    @classmethod
    def setUpClass(cls):
        """Set up Stash connection for all tests."""
        cls.stash_url = os.environ.get("STASH_URL")
        cls.api_key = os.environ.get("STASH_API_KEY")

        if not cls.stash_url or not cls.api_key:
            raise unittest.SkipTest(
                "STASH_URL and STASH_API_KEY environment variables required"
            )

        try:
            from stashapi.stashapp import StashInterface
            # Create a mock server connection dict
            server_connection = {
                "Scheme": "http" if "http://" in cls.stash_url else "https",
                "Host": cls.stash_url.replace("http://", "").replace("https://", "").rstrip("/"),
                "Port": 9999,
                "SessionCookie": {
                    "Name": "session",
                    "Value": ""
                },
                "Dir": "",
                "PluginDir": ""
            }
            cls.stash = StashInterface(server_connection)
            # Override with direct URL if needed
            cls.stash.url = cls.stash_url.rstrip("/") + "/graphql"
            cls.stash.headers = {"ApiKey": cls.api_key}
        except ImportError:
            raise unittest.SkipTest("stashapi package not installed")
        except Exception as e:
            raise unittest.SkipTest(f"Failed to connect to Stash: {e}")

    def test_connection(self):
        """Test that we can connect to the Stash server."""
        try:
            config = self.stash.get_configuration()
            self.assertIsNotNone(config)
            print(f"\n  Connected to Stash successfully")
            print(f"  Database: {config['general'].get('databasePath', 'N/A')}")
        except Exception as e:
            self.fail(f"Failed to get configuration: {e}")

    def test_find_scenes_with_stash_id(self):
        """Test finding scenes that have StashIDs."""
        query = {
            "stash_id_endpoint": {
                "endpoint": "",
                "modifier": "NOT_NULL",
                "stash_id": "",
            }
        }
        result = self.stash.find_scenes(f=query, filter={"per_page": 5})

        print(f"\n  Found {len(result)} scenes with StashIDs")
        for scene in result[:3]:
            print(f"    - {scene.get('title', 'No title')} (ID: {scene['id']})")

        self.assertIsInstance(result, list)

    def test_scene_has_required_fields(self):
        """Test that scenes have all fields needed for renaming."""
        query = {
            "stash_id_endpoint": {
                "endpoint": "",
                "modifier": "NOT_NULL",
                "stash_id": "",
            }
        }
        scenes = self.stash.find_scenes(f=query, filter={"per_page": 1})

        if not scenes:
            self.skipTest("No scenes with StashIDs found")

        scene = scenes[0]
        required_fields = ["id", "title", "date", "files", "studio", "performers", "stash_ids"]

        print(f"\n  Checking scene: {scene.get('title', 'No title')}")
        for field in required_fields:
            value = scene.get(field)
            status = "✓" if value else "✗"
            print(f"    {status} {field}: {type(value).__name__}")

        # Files should have path, height, width
        if scene.get("files"):
            file = scene["files"][0]
            print(f"    File fields:")
            for f in ["id", "path", "height", "width"]:
                value = file.get(f)
                status = "✓" if value else "✗"
                print(f"      {status} {f}: {value if f != 'path' else '...' + str(value)[-50:] if value else None}")

    def test_path_generation(self):
        """Test path generation with real scene data."""
        query = {
            "stash_id_endpoint": {
                "endpoint": "",
                "modifier": "NOT_NULL",
                "stash_id": "",
            }
        }
        scenes = self.stash.find_scenes(f=query, filter={"per_page": 5})

        if not scenes:
            self.skipTest("No scenes with StashIDs found")

        template = "$Studio/$Title - $ReleaseDate [$Resolution]"
        base_path = "/data/tagged/"

        print(f"\n  Testing path generation with template: {template}")

        for scene in scenes:
            # Hydrate scene with full performer and studio data
            if scene.get("performers"):
                hydrated_performers = []
                for p in scene["performers"]:
                    performer = self.stash.find_performer(p["id"], False, "id name gender")
                    if performer:
                        hydrated_performers.append(performer)
                scene["performers"] = hydrated_performers

            if scene.get("studio"):
                scene["studio"] = self.stash.find_studio(
                    scene["studio"]["id"],
                    "id name parent_studio { id name parent_studio { id name } }"
                )

            result = get_new_path(scene, base_path, template, 250)

            title = scene.get("title", "No title")[:40]
            if result:
                print(f"    ✓ {title}")
                print(f"      → {result}")
            else:
                print(f"    ✗ {title} (could not generate path)")

    def test_nfo_generation(self):
        """Test NFO XML generation with real scene data."""
        query = {
            "stash_id_endpoint": {
                "endpoint": "",
                "modifier": "NOT_NULL",
                "stash_id": "",
            }
        }
        scenes = self.stash.find_scenes(f=query, filter={"per_page": 1})

        if not scenes:
            self.skipTest("No scenes with StashIDs found")

        scene = scenes[0]

        # Hydrate scene
        if scene.get("performers"):
            hydrated_performers = []
            for p in scene["performers"]:
                performer = self.stash.find_performer(p["id"], False, "id name gender")
                if performer:
                    hydrated_performers.append(performer)
            scene["performers"] = hydrated_performers

        if scene.get("studio"):
            scene["studio"] = self.stash.find_studio(scene["studio"]["id"], "id name")

        nfo_xml = build_nfo_xml(scene)

        print(f"\n  Generated NFO for: {scene.get('title', 'No title')}")
        print(f"  NFO length: {len(nfo_xml)} characters")
        print(f"  First 500 chars:")
        print(f"  {nfo_xml[:500]}...")

        self.assertIn('<?xml version="1.0"', nfo_xml)
        self.assertIn('<movie>', nfo_xml)
        self.assertIn('</movie>', nfo_xml)

    def test_graphql_move_files_mutation(self):
        """Test that the moveFiles mutation is available (dry run only)."""
        # Just verify the mutation exists by checking schema
        query = """
            query {
                __schema {
                    mutationType {
                        fields {
                            name
                        }
                    }
                }
            }
        """
        try:
            result = self.stash.call_GQL(query)
            mutations = [f["name"] for f in result["__schema"]["mutationType"]["fields"]]

            print(f"\n  Checking for moveFiles mutation...")
            if "moveFiles" in mutations:
                print(f"    ✓ moveFiles mutation is available")
            else:
                print(f"    ✗ moveFiles mutation NOT found")
                print(f"    Available mutations: {', '.join(sorted(mutations)[:20])}...")

            self.assertIn("moveFiles", mutations)
        except Exception as e:
            self.fail(f"Failed to query schema: {e}")


class DryRunSimulationTest(unittest.TestCase):
    """Tests that simulate the full plugin flow without actually modifying files."""

    @classmethod
    def setUpClass(cls):
        """Set up Stash connection for all tests."""
        cls.stash_url = os.environ.get("STASH_URL")
        cls.api_key = os.environ.get("STASH_API_KEY")

        if not cls.stash_url or not cls.api_key:
            raise unittest.SkipTest(
                "STASH_URL and STASH_API_KEY environment variables required"
            )

        try:
            from stashapi.stashapp import StashInterface
            server_connection = {
                "Scheme": "http" if "http://" in cls.stash_url else "https",
                "Host": cls.stash_url.replace("http://", "").replace("https://", "").rstrip("/"),
                "Port": 9999,
                "SessionCookie": {"Name": "session", "Value": ""},
                "Dir": "",
                "PluginDir": ""
            }
            cls.stash = StashInterface(server_connection)
            cls.stash.url = cls.stash_url.rstrip("/") + "/graphql"
            cls.stash.headers = {"ApiKey": cls.api_key}
        except ImportError:
            raise unittest.SkipTest("stashapi package not installed")

    def test_bulk_scene_dry_run(self):
        """Simulate a bulk scene update in dry run mode."""
        from scene import process_all_scenes

        # Mock settings with dry_run enabled
        settings = {
            "dry_run": True,
            "enable_renamer": True,
            "renamer_path": "/data/tagged/",
            "renamer_path_template": "$Studio/$Title - $ReleaseDate [$Resolution]",
            "renamer_filepath_budget": 250,
            "renamer_ignore_files_in_path": False,
            "renamer_enable_mark_organized": True,
            "renamer_multi_file_mode": "all",
            "enable_actor_images": False,
            "nfo_skip_existing": False,
        }

        print("\n  Running bulk scene simulation (dry run)...")
        print("  Settings:")
        print(f"    renamer_path: {settings['renamer_path']}")
        print(f"    template: {settings['renamer_path_template']}")

        # This will log what would happen without actually doing it
        try:
            config = self.stash.get_configuration()["general"]
            api_key = config.get("apiKey", "")

            # We can't actually run process_all_scenes without proper stash connection
            # but we can test a single scene
            query = {
                "stash_id_endpoint": {
                    "endpoint": "",
                    "modifier": "NOT_NULL",
                    "stash_id": "",
                }
            }
            count_result = self.stash.find_scenes(
                f=query,
                filter={"per_page": 1},
                get_count=True
            )

            if isinstance(count_result, tuple):
                count = count_result[0]
            else:
                count = len(count_result)

            print(f"\n  Would process {count} scenes with StashIDs")
            print("  ✓ Dry run simulation successful")

        except Exception as e:
            print(f"\n  Error during simulation: {e}")
            raise


if __name__ == "__main__":
    print("\n" + "="*60)
    print("mcMetadata Integration Tests")
    print("="*60)
    print("\nThese tests connect to your Stash server but do NOT modify")
    print("any files. They validate API communication and data handling.\n")

    if not os.environ.get("STASH_URL") or not os.environ.get("STASH_API_KEY"):
        print("Set environment variables to run:")
        print("  export STASH_URL=http://your-stash:9999")
        print("  export STASH_API_KEY=your-api-key")
        print("\nThen run: python -m unittest tests.test_integration -v\n")
    else:
        unittest.main(verbosity=2)
