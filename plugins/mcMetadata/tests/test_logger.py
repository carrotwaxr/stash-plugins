"""Tests for the file logger and the quiet-hook run-flow decision."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

from utils import logger
from utils.run_flow import is_disabled_hook_run


class TestLogFile(unittest.TestCase):
    def test_log_file_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "mc.log")
            for marker in ("first-run", "second-run"):
                logger.init_file_logger(path)
                logger._write_to_file("INFO", marker)
                logger.close_file_logger()
            text = open(path, encoding="utf-8").read()
            self.assertIn("first-run", text)
            self.assertIn("second-run", text)
            self.assertEqual(text.count("===== mcMetadata run "), 2)


class TestDisabledHook(unittest.TestCase):
    def test_disabled_hook_logs_nothing_and_leaves_log_file(self):
        self.assertTrue(is_disabled_hook_run("Scene.Update.Post", {"enable_hook": False}))
        self.assertTrue(is_disabled_hook_run("Performer.Update.Post", {"enable_actor_images": False}))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mc.log")
            with open(path, "w") as f:
                f.write("keep me")
            settings = {"enable_hook": False, "log_file_path": path, "dry_run": True}
            logger.stash_log.info.reset_mock()
            if not is_disabled_hook_run("Scene.Update.Post", settings):
                logger.init_file_logger(path)
                logger.close_file_logger()
            self.assertEqual(open(path).read(), "keep me")
            logger.stash_log.info.assert_not_called()

    def test_enabled_and_task_modes_run(self):
        self.assertFalse(is_disabled_hook_run("Scene.Update.Post", {"enable_hook": True}))
        self.assertFalse(is_disabled_hook_run("Performer.Update.Post", {"enable_actor_images": True}))
        self.assertFalse(is_disabled_hook_run("bulk", {"enable_hook": False}))


if __name__ == "__main__":
    unittest.main()
