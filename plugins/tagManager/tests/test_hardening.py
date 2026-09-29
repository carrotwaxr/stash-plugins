"""
Tests for defensive config reads (folded #128-review hardening follow-ups).

- safe_int tolerates empty/None/non-numeric stored values (Stash stores "" for a
  cleared numeric field; backfill only fills *missing* keys, not "").
- get_settings_from_config no longer crashes on an empty-string fuzzyThreshold/pageSize.
- resolve_sync_dry_run falls back to the safe default on a missing/malformed config.
- resolve_stashbox takes the stash-box URL and key from Stash's config, never the client.

Run with: python -m pytest tests/test_hardening.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tag_manager import safe_int, get_settings_from_config, resolve_sync_dry_run, resolve_stashbox
from tag_manager import DEFAULT_PLUGIN_SETTINGS


class TestSafeInt(unittest.TestCase):
    def test_parses_numeric_string(self):
        self.assertEqual(safe_int("75", 80), 75)

    def test_passes_through_int(self):
        self.assertEqual(safe_int(50, 80), 50)

    def test_empty_string_falls_back(self):
        self.assertEqual(safe_int("", 80), 80)

    def test_none_falls_back(self):
        self.assertEqual(safe_int(None, 80), 80)

    def test_non_numeric_falls_back(self):
        self.assertEqual(safe_int("abc", 80), 80)

    def test_whitespace_tolerated(self):
        self.assertEqual(safe_int("  90  ", 80), 90)


class TestSettingsRobustToEmptyStrings(unittest.TestCase):
    def test_empty_fuzzy_threshold_uses_default(self):
        s = get_settings_from_config({"fuzzyThreshold": ""})
        self.assertEqual(s["fuzzy_threshold"], DEFAULT_PLUGIN_SETTINGS["fuzzyThreshold"])

    def test_empty_page_size_uses_default(self):
        s = get_settings_from_config({"pageSize": ""})
        self.assertEqual(s["page_size"], DEFAULT_PLUGIN_SETTINGS["pageSize"])

    def test_valid_value_still_parsed(self):
        s = get_settings_from_config({"fuzzyThreshold": "65"})
        self.assertEqual(s["fuzzy_threshold"], 65)


class TestResolveSyncDryRun(unittest.TestCase):
    def test_reads_explicit_value(self):
        cfg = {"plugins": {"tagManager": {"syncDryRun": False}}}
        self.assertFalse(resolve_sync_dry_run(cfg))

    def test_missing_defaults_to_safe(self):
        self.assertEqual(resolve_sync_dry_run({}), DEFAULT_PLUGIN_SETTINGS["syncDryRun"])

    def test_malformed_plugins_none_falls_back(self):
        self.assertEqual(resolve_sync_dry_run({"plugins": None}), DEFAULT_PLUGIN_SETTINGS["syncDryRun"])

    def test_malformed_plugin_entry_none_falls_back(self):
        self.assertEqual(
            resolve_sync_dry_run({"plugins": {"tagManager": None}}),
            DEFAULT_PLUGIN_SETTINGS["syncDryRun"],
        )

    def test_none_config_falls_back(self):
        self.assertEqual(resolve_sync_dry_run(None), DEFAULT_PLUGIN_SETTINGS["syncDryRun"])


class TestResolveStashbox(unittest.TestCase):
    CONFIG = {
        "general": {"stashBoxes": [
            {"endpoint": "https://stashdb.org/graphql", "api_key": "stashdb-key", "name": "StashDB"},
            {"endpoint": "https://fansdb.cc/graphql", "api_key": "fansdb-key", "name": "FansDB"},
        ]},
        "plugins": {},
    }

    def test_matches_configured_endpoint(self):
        self.assertEqual(
            resolve_stashbox("https://fansdb.cc/graphql", self.CONFIG),
            ("https://fansdb.cc/graphql", "fansdb-key"),
        )

    def test_match_ignores_case_and_trailing_slash_but_returns_configured_url(self):
        self.assertEqual(
            resolve_stashbox("HTTPS://StashDB.org/graphql/", self.CONFIG),
            ("https://stashdb.org/graphql", "stashdb-key"),
        )

    def test_unconfigured_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            resolve_stashbox("http://10.0.0.4:8080/graphql", self.CONFIG)

    def test_empty_request_uses_first_box(self):
        self.assertEqual(resolve_stashbox("", self.CONFIG), ("https://stashdb.org/graphql", "stashdb-key"))

    def test_no_boxes_raises(self):
        with self.assertRaises(ValueError):
            resolve_stashbox("https://stashdb.org/graphql", {"general": {"stashBoxes": []}})

    def test_legacy_plugin_credentials(self):
        cfg = {"general": {"stashBoxes": []}, "plugins": {"tagManager": {"stashdbApiKey": "legacy-key"}}}
        self.assertEqual(
            resolve_stashbox("https://stashdb.org/graphql", cfg),
            ("https://stashdb.org/graphql", "legacy-key"),
        )

    def test_malformed_config_raises_value_error(self):
        for cfg in (None, {}, {"general": None, "plugins": None}, {"plugins": {"tagManager": None}}):
            with self.assertRaises(ValueError):
                resolve_stashbox("https://stashdb.org/graphql", cfg)


if __name__ == "__main__":
    unittest.main()
