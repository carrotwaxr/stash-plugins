"""
Unit tests for settings mapping + the hookTriggerMode -> organizedCondition migration.

map_settings is pure: it maps Stash's camelCase plugin config to the internal
snake_case settings dict (with list-parsing and back-compat) without any Stash call.

Run with: python -m pytest tests/test_settings.py -v
"""

import os
import sys
import unittest
import unittest.mock
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

    def test_unknown_organized_condition_is_invalid(self):
        self.assertEqual(map_settings({"organizedCondition": "bogus"})["organized_condition"], "invalid")


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


ALL_KEYS = [
    "dryRun", "logFilePath", "enableHook", "organizedCondition", "hookTriggerMode",
    "requireStashId", "requiredTags", "includePaths", "excludePaths", "enableRenamer",
    "renamerPath", "renamerPathTemplate", "renamerFilepathBudget",
    "renamerIgnoreFilesInPath", "renamerMarkOrganized", "renamerMultiFileMode",
    "renamerMoveSidecars", "nfoSkipExisting", "nfoExcludeFields", "nfoFilename",
    "posterFilename", "backdropFilename", "nfoRatingField", "enableActorImages",
    "mediaServer", "actorMetadataPath",
]


class TestSettingsRobustness(unittest.TestCase):
    def setUp(self):
        import plugin_settings
        self.log = MagicMock()
        patcher = unittest.mock.patch.object(plugin_settings, "log", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_none_gives_defaults(self):
        s = map_settings({k: None for k in ALL_KEYS})
        d = map_settings({})
        self.assertEqual(s, d)
        self.assertIs(s["dry_run"], True)
        self.assertEqual(s["organized_condition"], "ignore")
        self.assertEqual(s["renamer_filepath_budget"], 250)

    def test_nfo_exclude_fields_none(self):
        self.assertEqual(map_settings({"nfoExcludeFields": None})["nfo_exclude_fields"], [])

    def test_bool_strings_and_ints(self):
        s = map_settings({"dryRun": "false", "enableHook": "TRUE", "requireStashId": 1,
                          "renamerMarkOrganized": 0})
        self.assertIs(s["dry_run"], False)
        self.assertIs(s["enable_hook"], True)
        self.assertIs(s["require_stash_id"], True)
        self.assertIs(s["renamer_enable_mark_organized"], False)
        self.log.warning.assert_not_called()

    def test_bad_bool_uses_default_and_warns(self):
        s = map_settings({"dryRun": "maybe", "enableHook": [1]})
        self.assertIs(s["dry_run"], True)
        self.assertIs(s["enable_hook"], False)
        self.assertEqual(self.log.warning.call_count, 2)

    def test_budget_string_coerced(self):
        self.assertEqual(map_settings({"renamerFilepathBudget": "300"})["renamer_filepath_budget"], 300)

    def test_budget_garbage_defaults_and_warns(self):
        s = map_settings({"renamerFilepathBudget": "lots"})
        self.assertEqual(s["renamer_filepath_budget"], 250)
        self.log.warning.assert_called()

    def test_budget_clamped(self):
        self.assertEqual(map_settings({"renamerFilepathBudget": 39})["renamer_filepath_budget"], 40)
        self.assertEqual(map_settings({"renamerFilepathBudget": 801})["renamer_filepath_budget"], 800)
        self.assertEqual(map_settings({"renamerFilepathBudget": 40})["renamer_filepath_budget"], 40)
        self.assertEqual(self.log.warning.call_count, 2)

    def test_organized_condition_true_is_invalid(self):
        s = map_settings({"organizedCondition": True})
        self.assertEqual(s["organized_condition"], "invalid")
        self.log.warning.assert_called()

    def test_organized_condition_typo_fails_closed(self):
        from conditions import should_process
        s = map_settings({"organizedCondition": "requre", "hookTriggerMode": "on_organized"})
        self.assertEqual(s["organized_condition"], "invalid")
        msg = self.log.warning.call_args[0][0]
        for word in ("require", "skip", "ignore"):
            self.assertIn(word, msg)
        for organized in (True, False):
            ok, _ = should_process({"id": "1", "organized": organized}, s)
            self.assertFalse(ok)

    def test_empty_organized_condition_still_migrates(self):
        self.assertEqual(map_settings({"organizedCondition": "", "hookTriggerMode": "on_organized"})["organized_condition"], "require")
        self.assertEqual(map_settings({"hookTriggerMode": "always"})["organized_condition"], "ignore")

    def test_template_uniqueness_rule(self):
        ok = ["$StashID", "$Studio/$Title $ReleaseDate", "$Studios/$Title-$ReleaseDate"]
        bad = ["$Title $Performers", "$Studio $Title", "$Title $ReleaseDate", ""]
        for t in ok:
            self.log.reset_mock()
            s = map_settings({"enableRenamer": True, "renamerPathTemplate": t})
            self.assertIs(s["enable_renamer"], True, t)
            self.log.error.assert_not_called()
        for t in bad:
            self.log.reset_mock()
            s = map_settings({"enableRenamer": True, "renamerPathTemplate": t})
            self.assertIs(s["enable_renamer"], False, t)
            self.assertEqual(self.log.error.call_count, 1)

    def test_bad_template_ignored_when_renamer_off_and_keeps_other_settings(self):
        s = map_settings({"enableRenamer": False, "renamerPathTemplate": "$Title"})
        self.assertIs(s["enable_renamer"], False)
        self.log.error.assert_not_called()
        s = map_settings({"enableRenamer": True, "renamerPathTemplate": "$Title", "dryRun": False})
        self.assertIs(s["dry_run"], False)


class TestStashapiCheck(unittest.TestCase):
    def test_missing(self):
        import stashapi_check
        with unittest.mock.patch.object(stashapi_check.importlib.util, "find_spec", return_value=None):
            msg = stashapi_check.stashapi_problem()
        self.assertIn("pip install stashapp-tools", msg)
        self.assertIn("README", msg)

    def test_old_version(self):
        import stashapi_check
        with unittest.mock.patch.object(stashapi_check.importlib.util, "find_spec", return_value=object()), \
                unittest.mock.patch.object(stashapi_check.importlib.metadata, "version", return_value="0.2.58"):
            msg = stashapi_check.stashapi_problem()
        self.assertIn("0.2.58", msg)
        self.assertIn("pip install --upgrade stashapp-tools", msg)

    def test_ok(self):
        import stashapi_check
        with unittest.mock.patch.object(stashapi_check.importlib.util, "find_spec", return_value=object()), \
                unittest.mock.patch.object(stashapi_check.importlib.metadata, "version", return_value="0.2.59"):
            self.assertIsNone(stashapi_check.stashapi_problem())
