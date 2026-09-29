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

    def test_one_header_line_per_run(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mc.log")
            for marker in ("first-run", "second-run"):
                logger.init_file_logger(path)
                logger._write_to_file("INFO", marker)
                logger.close_file_logger()
            lines = open(path, encoding="utf-8").read().splitlines()
            self.assertNotIn("mcMetadata Log -", "\n".join(lines))
            headers = [i for i, line in enumerate(lines) if line.startswith("===== mcMetadata run ")]
            self.assertEqual(len(headers), 2)
            for i, marker in zip(headers, ("first-run", "second-run")):
                self.assertTrue(lines[i + 1].endswith(f"[INFO] {marker}"), lines[i:i + 3])

    def test_log_over_5mb_is_rotated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mc.log")
            with open(path + ".1", "w") as f:
                f.write("oldest")
            with open(path, "w") as f:
                f.write("x" * (5 * 1024 * 1024 + 1))
            logger.init_file_logger(path)
            logger._write_to_file("INFO", "fresh")
            logger.close_file_logger()
            self.assertEqual(os.path.getsize(path + ".1"), 5 * 1024 * 1024 + 1)
            text = open(path, encoding="utf-8").read()
            self.assertIn("fresh", text)
            self.assertLess(len(text), 1000)
            self.assertEqual(sorted(os.listdir(d)), ["mc.log", "mc.log.1"])

    def test_log_at_5mb_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mc.log")
            with open(path, "w") as f:
                f.write("x" * (5 * 1024 * 1024))
            logger.init_file_logger(path)
            logger.close_file_logger()
            self.assertFalse(os.path.exists(path + ".1"))
            self.assertGreater(os.path.getsize(path), 5 * 1024 * 1024)


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
