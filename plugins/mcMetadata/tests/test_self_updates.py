"""
The Scene.Update.Post hook ignores mcMetadata's own `{id, organized: true}` update,
and the renamer moves a scene's NFO and poster before it marks the scene organized.

Stash runs post hooks synchronously, so the plugin's update_scene call blocks while a
nested mcMetadata run handles the same scene. A marker file written right before that
call lets the nested run recognize the update as the plugin's own and skip it.

Everything lives in temp dirs; stash is a fake that moves files with os.rename.

Run with: python -m pytest tests/test_self_updates.py -v
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock stashapi before importing any plugin modules (not available outside Stash runtime)
sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

import scene as scene_module  # noqa: E402
from utils.self_updates import consume, mark, plugin_data_dir, should_skip_hook  # noqa: E402


class _TempDirs(unittest.TestCase):
    """A temp data dir, and the temp-dir fallback redirected into the test's temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.data_dir = os.path.join(self.tmp, "config", "plugin_data", "mcMetadata")
        self.fallback_root = os.path.join(self.tmp, "systemp")
        os.makedirs(self.fallback_root)
        gettempdir = patch("utils.self_updates.tempfile.gettempdir", return_value=self.fallback_root)
        gettempdir.start()
        self.addCleanup(gettempdir.stop)


class TestMarkerRoundtrip(_TempDirs):
    def test_marker_roundtrip(self):
        mark(5, self.data_dir)
        self.assertTrue(consume(5, self.data_dir))
        self.assertFalse(consume(5, self.data_dir))

    def test_old_marker_is_rejected_and_removed(self):
        path = mark("5", self.data_dir)
        self.assertTrue(os.path.exists(path))
        with patch("utils.self_updates.time.time", return_value=time.time() + 121):
            self.assertFalse(consume("5", self.data_dir))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(consume("5", self.data_dir))

    def test_string_and_int_ids_share_a_marker(self):
        mark("5", self.data_dir)
        self.assertTrue(consume(5, self.data_dir))

    def test_markers_are_per_scene(self):
        mark(5, self.data_dir)
        self.assertFalse(consume(6, self.data_dir))
        self.assertTrue(consume(5, self.data_dir))

    def test_marker_lives_under_self_updates(self):
        path = mark(5, self.data_dir)
        self.assertEqual(path, os.path.join(self.data_dir, "self_updates", "5"))
        self.assertEqual(os.listdir(os.path.dirname(path)), ["5"])  # no temp file left behind

    def test_id_is_sanitized_to_digits(self):
        path = mark("../5", self.data_dir)
        self.assertEqual(path, os.path.join(self.data_dir, "self_updates", "5"))
        self.assertIsNone(mark("../..", self.data_dir))
        self.assertFalse(consume("../..", self.data_dir))

    def test_unreadable_marker_content_is_not_fresh(self):
        path = mark(5, self.data_dir)
        with open(path, "w") as f:
            f.write("not a timestamp")
        self.assertFalse(consume(5, self.data_dir))
        self.assertFalse(os.path.exists(path))

    def test_falls_back_to_temp_dir_when_data_dir_cannot_be_created(self):
        blocker = os.path.join(self.tmp, "a_file")
        with open(blocker, "w") as f:
            f.write("x")
        bad_data_dir = os.path.join(blocker, "plugin_data", "mcMetadata")
        path = mark(5, bad_data_dir)
        self.assertEqual(path, os.path.join(self.fallback_root, "mcMetadata", "self_updates", "5"))
        self.assertTrue(consume(5, bad_data_dir))

    def test_no_data_dir_uses_temp_dir(self):
        path = mark(5, None)
        self.assertEqual(path, os.path.join(self.fallback_root, "mcMetadata", "self_updates", "5"))
        self.assertTrue(consume(5, None))


class TestPluginDataDir(unittest.TestCase):
    def test_under_stash_config_dir(self):
        self.assertEqual(
            plugin_data_dir({"Dir": "/root/.stash"}),
            os.path.join("/root/.stash", "plugin_data", "mcMetadata"),
        )

    def test_missing_dir_is_none(self):
        self.assertIsNone(plugin_data_dir({}))
        self.assertIsNone(plugin_data_dir({"Dir": ""}))
        self.assertIsNone(plugin_data_dir(None))


def _hook(scene_id=5, fields=("id", "organized"), organized=True, with_input=True):
    context = {"id": scene_id, "type": "Scene.Update.Post"}
    if fields is not None:
        context["inputFields"] = list(fields)
    if with_input:
        context["input"] = {"id": str(scene_id), "organized": organized}
    return context


class TestShouldSkipHook(_TempDirs):
    def test_hook_skips_self_update(self):
        mark(5, self.data_dir)
        self.assertTrue(should_skip_hook(_hook(fields=["id", "organized"]), self.data_dir))

    def test_skip_consumes_the_marker(self):
        mark(5, self.data_dir)
        self.assertTrue(should_skip_hook(_hook(), self.data_dir))
        # the user's next Organized click is processed
        self.assertFalse(should_skip_hook(_hook(), self.data_dir))

    def test_field_order_does_not_matter(self):
        mark(5, self.data_dir)
        self.assertTrue(should_skip_hook(_hook(fields=["organized", "id"]), self.data_dir))

    def test_no_marker_is_the_users_organized_click(self):
        self.assertFalse(should_skip_hook(_hook(fields=["id", "organized"]), self.data_dir))

    def test_more_fields_than_organized_is_a_user_edit(self):
        path = mark(5, self.data_dir)
        self.assertFalse(should_skip_hook(_hook(fields=["id", "organized", "title"]), self.data_dir))
        # the marker is left to expire, not consumed by the user's edit
        self.assertTrue(os.path.exists(path))

    def test_fewer_fields_is_not_a_self_update(self):
        mark(5, self.data_dir)
        self.assertFalse(should_skip_hook(_hook(fields=["id", "title"]), self.data_dir))

    def test_unorganizing_is_never_skipped(self):
        mark(5, self.data_dir)
        self.assertFalse(should_skip_hook(_hook(organized=False), self.data_dir))

    def test_marker_for_another_scene(self):
        mark(6, self.data_dir)
        self.assertFalse(should_skip_hook(_hook(scene_id=5), self.data_dir))

    def test_stale_marker_is_not_skipped(self):
        mark(5, self.data_dir)
        with patch("utils.self_updates.time.time", return_value=time.time() + 121):
            self.assertFalse(should_skip_hook(_hook(), self.data_dir))

    def test_missing_input_fields_falls_back_to_exact_input(self):
        mark(5, self.data_dir)
        self.assertTrue(should_skip_hook(_hook(fields=None), self.data_dir))

    def test_missing_input_fields_with_other_input_keys(self):
        mark(5, self.data_dir)
        context = _hook(fields=None)
        context["input"]["title"] = "New title"
        self.assertFalse(should_skip_hook(context, self.data_dir))

    def test_missing_input_fields_and_input_is_unknown(self):
        mark(5, self.data_dir)
        self.assertFalse(should_skip_hook(_hook(fields=None, with_input=False), self.data_dir))

    def test_no_id_or_context(self):
        self.assertFalse(should_skip_hook({"inputFields": ["id", "organized"]}, self.data_dir))
        self.assertFalse(should_skip_hook(None, self.data_dir))


class _FakeStash:
    """Moves files with os.rename and records the order of calls."""

    def __init__(self, calls, fail_update=False):
        self.calls = calls
        self.fail_update = fail_update

    def call_GQL(self, query, variables):
        data = variables["input"]
        src = self.paths[data["ids"][0]]
        dest = os.path.join(data["destination_folder"], data["destination_basename"])
        os.makedirs(data["destination_folder"], exist_ok=True)
        os.rename(src, dest)
        self.calls.append(("moveFiles", src, dest))
        return {"moveFiles": True}

    def update_scene(self, update_input):
        self.calls.append(("update_scene", update_input))
        if self.fail_update:
            raise RuntimeError("GraphQL error")


class TestRenameOrder(_TempDirs):
    def setUp(self):
        super().setUp()
        self.rename = getattr(scene_module, "__rename_videos")
        self.incoming = os.path.join(self.tmp, "incoming")
        self.lib = os.path.join(self.tmp, "lib")
        os.makedirs(self.incoming)
        self.video = os.path.join(self.incoming, "source.mp4")
        for name, content in (("source.mp4", "video"), ("source.nfo", "old nfo"), ("source-poster.jpg", "poster")):
            with open(os.path.join(self.incoming, name), "w") as f:
                f.write(content)
        self.calls = []
        real_rename_file = scene_module.rename_file

        def recording_rename_file(src, dest, settings):
            self.calls.append(("rename_file", src, dest))
            return real_rename_file(src, dest, settings)

        patcher = patch.object(scene_module, "rename_file", side_effect=recording_rename_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _settings(self, **overrides):
        settings = {
            "enable_renamer": True,
            "renamer_path": self.lib,
            "renamer_path_template": "$Title",
            "renamer_filepath_budget": 250,
            "renamer_ignore_files_in_path": True,
            "renamer_enable_mark_organized": True,
            "renamer_multi_file_mode": "all",
            "enable_hook": True,
            "dry_run": False,
            "data_dir": self.data_dir,
        }
        settings.update(overrides)
        return settings

    def _scene(self):
        return {
            "id": "5",
            "title": "A Title",
            "date": "2024-01-15",
            "files": [{"id": "f1", "path": self.video, "height": 1080, "width": 1920}],
            "performers": [],
            "studio": None,
            "tags": [],
            "stash_ids": [],
        }

    def _run(self, fail_update=False, **settings):
        stash = _FakeStash(self.calls, fail_update=fail_update)
        stash.paths = {"f1": self.video}
        result = self.rename(self._scene(), stash, self._settings(**settings))
        return result, stash

    def _record_state_at_update(self, stash):
        """Capture the files and the marker as they are when update_scene is called."""
        state = {}
        original = stash.update_scene

        def update_scene(update_input):
            state["new_nfo"] = os.path.exists(os.path.join(self.lib, "A Title.nfo"))
            state["new_poster"] = os.path.exists(os.path.join(self.lib, "A Title-poster.jpg"))
            state["old_nfo"] = os.path.exists(os.path.join(self.incoming, "source.nfo"))
            state["marker"] = os.path.exists(os.path.join(self.data_dir, "self_updates", "5"))
            return original(update_input)

        stash.update_scene = update_scene
        return state

    def test_sidecars_moved_before_organized_update(self):
        stash = _FakeStash(self.calls)
        stash.paths = {"f1": self.video}
        state = self._record_state_at_update(stash)
        result = self.rename(self._scene(), stash, self._settings())

        self.assertEqual(result, os.path.join(self.lib, "A Title.mp4"))
        kinds = [c[0] for c in self.calls]
        self.assertEqual(kinds, ["moveFiles", "rename_file", "rename_file", "update_scene"])
        self.assertEqual(self.calls[-1], ("update_scene", {"id": "5", "organized": True}))
        self.assertEqual(state, {"new_nfo": True, "new_poster": True, "old_nfo": False, "marker": True})
        with open(os.path.join(self.lib, "A Title.nfo")) as f:
            self.assertEqual(f.read(), "old nfo")

    def test_marker_left_for_the_nested_hook(self):
        self._run()
        self.assertTrue(consume("5", self.data_dir))

    def test_failed_update_consumes_the_marker(self):
        self._run(fail_update=True)
        self.assertEqual(self.calls[-1][0], "update_scene")
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "self_updates", "5")))
        self.assertFalse(consume("5", self.data_dir))

    def test_no_marker_when_hook_disabled(self):
        self._run(enable_hook=False)
        self.assertEqual(self.calls[-1], ("update_scene", {"id": "5", "organized": True}))
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "self_updates", "5")))

    def test_no_marker_or_update_without_mark_organized(self):
        self._run(renamer_enable_mark_organized=False)
        self.assertNotIn("update_scene", [c[0] for c in self.calls])
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "self_updates")))

    def test_dry_run_writes_no_marker_and_no_update(self):
        result, _ = self._run(dry_run=True)
        # Where the video would be, so the dry run reports the NFO and poster there
        self.assertEqual(result, os.path.join(self.lib, "A Title.mp4"))
        self.assertTrue(os.path.exists(self.video))
        self.assertNotIn("update_scene", [c[0] for c in self.calls])
        self.assertNotIn("moveFiles", [c[0] for c in self.calls])
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "self_updates")))
        self.assertTrue(os.path.exists(os.path.join(self.incoming, "source.nfo")))


if __name__ == "__main__":
    unittest.main()
