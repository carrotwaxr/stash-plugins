"""
Unit tests for settings mapping + the hookTriggerMode -> organizedCondition migration.

map_settings is pure: it maps Stash's camelCase plugin config to the internal
snake_case settings dict (with list-parsing and back-compat) without any Stash call.

Run with: python -m pytest tests/test_settings.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

from plugin_settings import map_settings


class TestNewConditionSettings(unittest.TestCase):
    def test_defaults_are_noop_gates(self):
        s = map_settings({})
        self.assertEqual(s["organized_condition"], "ignore")
        self.assertEqual(s["required_tags"], [])
        self.assertEqual(s["include_paths"], [])
        self.assertEqual(s["exclude_paths"], [])

    def test_required_tags_parsed_and_trimmed(self):
        s = map_settings({"requiredTags": " curated , hd ,, four-k "})
        self.assertEqual(s["required_tags"], ["curated", "hd", "four-k"])

    def test_include_and_exclude_paths_parsed(self):
        s = map_settings({
            "includePaths": "/media/curated/*, /media/keep/*",
            "excludePaths": "*/trash/*",
        })
        self.assertEqual(s["include_paths"], ["/media/curated/*", "/media/keep/*"])
        self.assertEqual(s["exclude_paths"], ["*/trash/*"])

    def test_organized_condition_passthrough(self):
        self.assertEqual(map_settings({"organizedCondition": "require"})["organized_condition"], "require")
        self.assertEqual(map_settings({"organizedCondition": "skip"})["organized_condition"], "skip")

    def test_unknown_organized_condition_falls_back_to_ignore(self):
        self.assertEqual(map_settings({"organizedCondition": "bogus"})["organized_condition"], "ignore")


class TestHookTriggerModeMigration(unittest.TestCase):
    def test_legacy_on_organized_maps_to_require(self):
        s = map_settings({"hookTriggerMode": "on_organized"})
        self.assertEqual(s["organized_condition"], "require")

    def test_legacy_always_maps_to_ignore(self):
        s = map_settings({"hookTriggerMode": "always"})
        self.assertEqual(s["organized_condition"], "ignore")

    def test_new_key_overrides_legacy(self):
        s = map_settings({"organizedCondition": "skip", "hookTriggerMode": "on_organized"})
        self.assertEqual(s["organized_condition"], "skip")

    def test_no_legacy_key_defaults_ignore(self):
        self.assertEqual(map_settings({})["organized_condition"], "ignore")


class TestExistingSettingsPreserved(unittest.TestCase):
    def test_safe_defaults_retained(self):
        s = map_settings({})
        self.assertTrue(s["dry_run"])           # default-on safety
        self.assertFalse(s["enable_hook"])       # default-off safety
        self.assertFalse(s["require_stash_id"])  # #127: process all by default
        self.assertEqual(s["renamer_multi_file_mode"], "all")
        self.assertEqual(s["media_server"], "jellyfin")

    def test_nfo_exclude_fields_still_parsed(self):
        s = map_settings({"nfoExcludeFields": "Genre, Rating"})
        self.assertEqual(s["nfo_exclude_fields"], ["genre", "rating"])

    def test_values_passed_through(self):
        s = map_settings({"enableHook": True, "requireStashId": True, "renamerPath": "/x"})
        self.assertTrue(s["enable_hook"])
        self.assertTrue(s["require_stash_id"])
        self.assertEqual(s["renamer_path"], "/x")


if __name__ == "__main__":
    unittest.main()
