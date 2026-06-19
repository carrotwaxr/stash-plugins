"""
Unit tests for the unified processing-conditions gate (conditions.should_process).

No Stash connection required. should_process is a pure function over a scene dict
and a snake_case settings dict (lists already parsed from the comma-separated UI strings).

Run with: python -m pytest tests/test_conditions.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock stashapi so importing plugin modules never needs the real package.
sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

from conditions import should_process, describe_active_conditions


def scene(**overrides):
    """A minimal scene dict; override any field."""
    base = {
        "id": "1",
        "organized": False,
        "stash_ids": [],
        "tags": [],
        "files": [{"path": "/media/library/scene.mp4"}],
    }
    base.update(overrides)
    return base


def settings(**overrides):
    """Default settings = every gate a no-op."""
    base = {
        "organized_condition": "ignore",
        "require_stash_id": False,
        "required_tags": [],
        "include_paths": [],
        "exclude_paths": [],
    }
    base.update(overrides)
    return base


class TestNoConditions(unittest.TestCase):
    def test_all_gates_unset_passes(self):
        ok, reason = should_process(scene(), settings())
        self.assertTrue(ok)
        self.assertEqual(reason, "")


class TestOrganizedGate(unittest.TestCase):
    def test_require_passes_when_organized(self):
        ok, _ = should_process(scene(organized=True), settings(organized_condition="require"))
        self.assertTrue(ok)

    def test_require_blocks_when_not_organized(self):
        ok, reason = should_process(scene(organized=False), settings(organized_condition="require"))
        self.assertFalse(ok)
        self.assertEqual(reason, "not_organized")

    def test_skip_blocks_when_organized(self):
        ok, reason = should_process(scene(organized=True), settings(organized_condition="skip"))
        self.assertFalse(ok)
        self.assertEqual(reason, "is_organized")

    def test_skip_passes_when_not_organized(self):
        ok, _ = should_process(scene(organized=False), settings(organized_condition="skip"))
        self.assertTrue(ok)

    def test_ignore_passes_either_way(self):
        self.assertTrue(should_process(scene(organized=True), settings(organized_condition="ignore"))[0])
        self.assertTrue(should_process(scene(organized=False), settings(organized_condition="ignore"))[0])


class TestStashIdGate(unittest.TestCase):
    def test_require_blocks_when_missing(self):
        ok, reason = should_process(scene(stash_ids=[]), settings(require_stash_id=True))
        self.assertFalse(ok)
        self.assertEqual(reason, "no_stash_id")

    def test_require_passes_when_present(self):
        ok, _ = should_process(
            scene(stash_ids=[{"endpoint": "x", "stash_id": "abc"}]),
            settings(require_stash_id=True),
        )
        self.assertTrue(ok)

    def test_no_stash_id_processed_by_default(self):
        # #127: a null-StashID scene must NOT be silently skipped when the gate is off.
        ok, _ = should_process(scene(stash_ids=[]), settings(require_stash_id=False))
        self.assertTrue(ok)


class TestRequiredTagsGate(unittest.TestCase):
    def test_empty_is_noop(self):
        ok, _ = should_process(scene(tags=[]), settings(required_tags=[]))
        self.assertTrue(ok)

    def test_passes_when_scene_has_a_required_tag(self):
        ok, _ = should_process(
            scene(tags=[{"name": "curated"}, {"name": "hd"}]),
            settings(required_tags=["curated"]),
        )
        self.assertTrue(ok)

    def test_any_match_passes_with_one_of_many(self):
        ok, _ = should_process(
            scene(tags=[{"name": "hd"}]),
            settings(required_tags=["curated", "hd"]),
        )
        self.assertTrue(ok)

    def test_blocks_when_no_required_tag_present(self):
        ok, reason = should_process(
            scene(tags=[{"name": "hd"}]),
            settings(required_tags=["curated"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_required_tag")

    def test_blocks_when_scene_has_no_tags(self):
        ok, reason = should_process(scene(tags=[]), settings(required_tags=["curated"]))
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_required_tag")


class TestDirectoryGate(unittest.TestCase):
    def test_include_match_passes(self):
        ok, _ = should_process(
            scene(files=[{"path": "/media/curated/a.mp4"}]),
            settings(include_paths=["/media/curated/*"]),
        )
        self.assertTrue(ok)

    def test_include_recurses_into_subdirs(self):
        ok, _ = should_process(
            scene(files=[{"path": "/media/curated/sub/a.mp4"}]),
            settings(include_paths=["/media/curated/*"]),
        )
        self.assertTrue(ok)

    def test_outside_include_blocks(self):
        ok, reason = should_process(
            scene(files=[{"path": "/media/other/a.mp4"}]),
            settings(include_paths=["/media/curated/*"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "outside_include_paths")

    def test_exclude_blocks(self):
        ok, reason = should_process(
            scene(files=[{"path": "/media/trash/a.mp4"}]),
            settings(exclude_paths=["/media/trash/*"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_path")

    def test_exclude_beats_include(self):
        ok, reason = should_process(
            scene(files=[{"path": "/media/curated/bad/a.mp4"}]),
            settings(include_paths=["/media/curated/*"], exclude_paths=["*/bad/*"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_path")

    def test_case_insensitive(self):
        ok, _ = should_process(
            scene(files=[{"path": "/Media/Curated/A.MP4"}]),
            settings(include_paths=["/media/curated/*"]),
        )
        self.assertTrue(ok)

    def test_multifile_passes_if_any_file_included(self):
        ok, _ = should_process(
            scene(files=[{"path": "/media/other/a.mp4"}, {"path": "/media/curated/a.mp4"}]),
            settings(include_paths=["/media/curated/*"]),
        )
        self.assertTrue(ok)

    def test_multifile_blocked_if_any_file_excluded(self):
        ok, reason = should_process(
            scene(files=[{"path": "/media/curated/a.mp4"}, {"path": "/media/trash/b.mp4"}]),
            settings(include_paths=["/media/curated/*"], exclude_paths=["/media/trash/*"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_path")


class TestAndCombination(unittest.TestCase):
    def test_all_gates_must_pass(self):
        ok, _ = should_process(
            scene(
                organized=True,
                stash_ids=[{"stash_id": "abc"}],
                tags=[{"name": "curated"}],
                files=[{"path": "/media/curated/a.mp4"}],
            ),
            settings(
                organized_condition="require",
                require_stash_id=True,
                required_tags=["curated"],
                include_paths=["/media/curated/*"],
            ),
        )
        self.assertTrue(ok)

    def test_first_failing_gate_reports_organized_first(self):
        # organized is evaluated before tags: an unorganized, untagged scene reports organized.
        ok, reason = should_process(
            scene(organized=False, tags=[]),
            settings(organized_condition="require", required_tags=["curated"]),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "not_organized")


class TestDescribeActiveConditions(unittest.TestCase):
    def test_all_defaults_reports_none(self):
        desc = describe_active_conditions(settings())
        self.assertIn("none", desc.lower())

    def test_organized_require_reported(self):
        self.assertIn("organized=require", describe_active_conditions(settings(organized_condition="require")))

    def test_stash_id_reported(self):
        self.assertIn("stashID", describe_active_conditions(settings(require_stash_id=True)))

    def test_tags_reported(self):
        desc = describe_active_conditions(settings(required_tags=["curated", "hd"]))
        self.assertIn("curated", desc)
        self.assertIn("hd", desc)

    def test_paths_reported(self):
        desc = describe_active_conditions(settings(include_paths=["/m/c/*"], exclude_paths=["/m/t/*"]))
        self.assertIn("/m/c/*", desc)
        self.assertIn("/m/t/*", desc)

    def test_combination_joins_gates(self):
        desc = describe_active_conditions(settings(organized_condition="skip", required_tags=["x"]))
        self.assertIn("organized=skip", desc)
        self.assertIn("x", desc)


if __name__ == "__main__":
    unittest.main()
