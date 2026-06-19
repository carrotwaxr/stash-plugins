"""
Unit tests for the bulk path: the server-side prefilter (build_scene_filter), the
superset invariant that guarantees it never drops a scene the gate would accept, the
#127 regression (null-StashID scenes processed), and the dry-run skip histogram.

Run with: python -m pytest tests/test_bulk.py -v
"""

import itertools
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

from conditions import should_process, build_scene_filter, format_bulk_summary


def _settings(**overrides):
    base = {
        "organized_condition": "ignore",
        "require_stash_id": False,
        "required_tags": [],
        "include_paths": [],
        "exclude_paths": [],
        "dry_run": True,
    }
    base.update(overrides)
    return base


class TestBuildSceneFilter(unittest.TestCase):
    def test_default_is_empty(self):
        self.assertEqual(build_scene_filter(_settings()), {})

    def test_require_organized_pushes_true(self):
        self.assertEqual(build_scene_filter(_settings(organized_condition="require")), {"organized": True})

    def test_skip_organized_pushes_false(self):
        self.assertEqual(build_scene_filter(_settings(organized_condition="skip")), {"organized": False})

    def test_require_stash_id_pushes_not_null(self):
        f = build_scene_filter(_settings(require_stash_id=True))
        self.assertEqual(f["stash_id_endpoint"]["modifier"], "NOT_NULL")

    def test_tags_and_paths_are_not_pushed_server_side(self):
        f = build_scene_filter(_settings(required_tags=["curated"], include_paths=["/x/*"]))
        self.assertNotIn("tags", f)
        self.assertNotIn("path", f)
        self.assertEqual(f, {})


def _passes_server_filter(scene, f):
    """Simulate how Stash applies the f-filter, mirroring build_scene_filter's fields."""
    if "organized" in f and bool(scene.get("organized", False)) != f["organized"]:
        return False
    if "stash_id_endpoint" in f and not scene.get("stash_ids"):
        return False
    return True


class TestSupersetInvariant(unittest.TestCase):
    """The server prefilter must accept every scene the gate accepts (superset),
    so the bulk task can never silently drop a scene should_process would process."""

    def test_filter_is_superset_of_gate_over_matrix(self):
        scenes = []
        for organized, has_id, tag, path in itertools.product(
            [True, False], [True, False], ["curated", "other"],
            ["/media/curated/a.mp4", "/media/trash/a.mp4"],
        ):
            scenes.append({
                "id": "x",
                "organized": organized,
                "stash_ids": [{"stash_id": "s"}] if has_id else [],
                "tags": [{"name": tag}],
                "files": [{"path": path}],
            })

        settings_matrix = [
            _settings(),
            _settings(organized_condition="require"),
            _settings(organized_condition="skip"),
            _settings(require_stash_id=True),
            _settings(required_tags=["curated"]),
            _settings(include_paths=["/media/curated/*"]),
            _settings(exclude_paths=["/media/trash/*"]),
            _settings(organized_condition="require", require_stash_id=True,
                      required_tags=["curated"], include_paths=["/media/curated/*"]),
        ]

        for st in settings_matrix:
            f = build_scene_filter(st)
            for sc in scenes:
                if should_process(sc, st)[0]:
                    self.assertTrue(
                        _passes_server_filter(sc, f),
                        msg=f"gate accepted but server filter dropped: settings={st} scene={sc}",
                    )


class FakeStash:
    """Minimal stash double: applies the server filter, paginates, counts."""

    def __init__(self, scenes):
        self.scenes = scenes

    def _filtered(self, f):
        return [s for s in self.scenes if _passes_server_filter(s, f or {})]

    def find_scenes(self, f=None, filter=None, get_count=False):
        matched = self._filtered(f)
        if get_count:
            return [len(matched)]
        page = filter["page"]
        per = filter["per_page"]
        start = (page - 1) * per
        return matched[start:start + per]


class TestBulkProcessing(unittest.TestCase):
    def _run(self, scenes, settings):
        import scene as scene_module
        with patch.object(scene_module, "process_scene") as spy:
            summary = scene_module.process_all_scenes(FakeStash(scenes), settings, api_key="k")
            return summary, spy

    def test_null_stash_id_scene_is_processed_by_default(self):
        # #127: bulk must process scenes without a StashID when the gate is off.
        scenes = [{"id": "1", "organized": False, "stash_ids": [], "tags": [], "files": [{"path": "/m/a.mp4"}]}]
        summary, spy = self._run(scenes, _settings())
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(summary["processed"], 1)

    def test_require_stash_id_skips_null_and_counts_reason(self):
        scenes = [
            {"id": "1", "organized": False, "stash_ids": [], "tags": [], "files": [{"path": "/m/a.mp4"}]},
            {"id": "2", "organized": False, "stash_ids": [{"stash_id": "s"}], "tags": [], "files": [{"path": "/m/b.mp4"}]},
        ]
        summary, spy = self._run(scenes, _settings(require_stash_id=True))
        # Scene 1 is dropped by the server prefilter (not even fetched); scene 2 processed.
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(summary["processed"], 1)

    def test_client_side_gate_skips_are_counted_in_histogram(self):
        scenes = [
            {"id": "1", "organized": False, "stash_ids": [], "tags": [{"name": "curated"}], "files": [{"path": "/m/a.mp4"}]},
            {"id": "2", "organized": False, "stash_ids": [], "tags": [{"name": "other"}], "files": [{"path": "/m/b.mp4"}]},
            {"id": "3", "organized": False, "stash_ids": [], "tags": [], "files": [{"path": "/m/c.mp4"}]},
        ]
        summary, spy = self._run(scenes, _settings(required_tags=["curated"]))
        self.assertEqual(spy.call_count, 1)               # only scene 1 processed
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["skipped"]["missing_required_tag"], 2)

    def test_summary_totals_reconcile(self):
        scenes = [
            {"id": str(i), "organized": False, "stash_ids": [], "tags": [{"name": "curated"}] if i % 2 else [], "files": [{"path": "/m/x.mp4"}]}
            for i in range(5)
        ]
        summary, _ = self._run(scenes, _settings(required_tags=["curated"]))
        self.assertEqual(summary["scanned"], 5)
        self.assertEqual(summary["processed"] + sum(summary["skipped"].values()) + summary["errors"], 5)


class TestFormatBulkSummary(unittest.TestCase):
    def test_includes_totals(self):
        text = "\n".join(format_bulk_summary(
            {"scanned": 100, "processed": 40, "errors": 0,
             "skipped": {"not_organized": 50, "missing_required_tag": 10}}))
        self.assertIn("100", text)
        self.assertIn("processed: 40", text)
        self.assertIn("skipped: 60", text)
        self.assertIn("not_organized", text)
        self.assertIn("missing_required_tag", text)

    def test_reasons_sorted_descending(self):
        text = "\n".join(format_bulk_summary(
            {"scanned": 75, "processed": 0, "errors": 0,
             "skipped": {"no_stash_id": 5, "not_organized": 50, "excluded_path": 20}}))
        self.assertLess(text.index("not_organized"), text.index("excluded_path"))
        self.assertLess(text.index("excluded_path"), text.index("no_stash_id"))

    def test_dry_run_prefix(self):
        lines = format_bulk_summary(
            {"scanned": 1, "processed": 1, "errors": 0, "skipped": {}}, dry_run=True)
        self.assertTrue(any("DRY RUN" in line for line in lines))

    def test_errors_shown_when_present(self):
        text = "\n".join(format_bulk_summary(
            {"scanned": 3, "processed": 1, "errors": 2, "skipped": {}}))
        self.assertIn("errors: 2", text)

    def test_samples_listed_when_present(self):
        text = "\n".join(format_bulk_summary(
            {"scanned": 3, "processed": 1, "errors": 0,
             "skipped": {"not_organized": 2},
             "samples": [("41", "not_organized"), ("88", "not_organized")]}))
        self.assertIn("41", text)
        self.assertIn("88", text)


if __name__ == "__main__":
    unittest.main()
