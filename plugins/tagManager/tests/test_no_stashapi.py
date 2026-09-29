"""Tag Manager must not need the stashapp-tools package (issue #129)."""
import importlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

PLUGIN_DIR = Path(__file__).parent.parent


class TestNoStashapi(unittest.TestCase):
    def test_no_plugin_module_imports_stashapi(self):
        offenders = [p.name for p in PLUGIN_DIR.glob("*.py")
                     if "stashapi" in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_sync_runs_without_stashapi(self):
        import plugin_data
        import tag_manager
        from test_stashdb_scene_sync import TestHandleSyncSceneTags

        helper = TestHandleSyncSceneTags("test_main_sync_mode")
        helper.setUp()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(setattr, plugin_data, "_current_dir", None)
        payload = json.dumps({"server_connection": {**helper.SERVER, "Dir": tmp},
                              "args": {"mode": "sync_scene_tags"}})
        out = io.StringIO()
        try:
            with patch.dict(sys.modules, {"stashapi": None, "stashapi.stashapp": None}):
                importlib.reload(tag_manager)
                with patch("sys.stdin", io.StringIO(payload)), patch("sys.stdout", out), \
                     patch("tag_manager.LocalStash", side_effect=helper.fake_local_stash), \
                     helper.remote.patched():
                    tag_manager.main()
        finally:
            importlib.reload(tag_manager)

        output = json.loads(out.getvalue())["output"]
        self.assertTrue(output["success"])


if __name__ == "__main__":
    unittest.main()
