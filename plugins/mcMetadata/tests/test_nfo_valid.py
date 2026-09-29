"""Every generated NFO must be well-formed XML, and empty/excluded elements are omitted.

Run with: python -m pytest tests/test_nfo_valid.py -v
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

import scene as scene_module
from plugin_settings import map_settings
from utils.nfo import build_nfo_xml


def make_scene(**over):
    s = {
        "id": "1",
        "title": "Title",
        "details": "Plot",
        "rating100": 80,
        "date": "2024-05-06",
        "studio": {"name": "Studio"},
        "performers": [{"name": "Jane"}],
        "tags": [{"name": "Tag"}],
        "files": [{"path": "/x/video.mp4"}],
    }
    s.update(over)
    return s


def parse(xml):
    return ET.fromstring(xml.encode("utf-8"))


class TestNfoValid(unittest.TestCase):
    def test_plot_with_cdata_terminator(self):
        root = parse(build_nfo_xml(make_scene(details="a]]>b")))
        self.assertEqual(root.find("plot").text, "a]]>b")

    def test_control_characters_stripped(self):
        root = parse(build_nfo_xml(make_scene(title="T\x0bi\x00tle", details="pl\x0bo\x00t")))
        self.assertEqual(root.find("title").text, "Title")
        self.assertEqual(root.find("plot").text, "plot")

    def test_control_characters_in_other_fields(self):
        s = make_scene(studio={"name": "St\x0c"}, performers=[{"name": "Ja\x01ne"}],
                       tags=[{"name": "t\x02ag"}])
        root = parse(build_nfo_xml(s, video_path="/x/vi\x03deo.mp4"))
        self.assertEqual(root.find("studio").text, "St")
        self.assertEqual(root.find("actor/name").text, "Jane")
        self.assertEqual(root.find("tag").text, "tag")
        self.assertEqual(root.find("thumb").text, "video-poster.jpg")

    def test_empty_fields_omitted(self):
        s = make_scene(rating100=None, date=None, studio=None, details=None)
        root = parse(build_nfo_xml(s))
        for name in ("rating", "userrating", "criticrating", "premiered",
                     "releasedate", "year", "studio", "plot"):
            self.assertIsNone(root.find(name), name)
        self.assertIsNotNone(root.find("title"))

    def test_rating_zero_is_a_value(self):
        root = parse(build_nfo_xml(make_scene(rating100=0)))
        self.assertEqual(root.find("rating").text, "0")

    def test_exclude_thumb_actor_tag(self):
        base = build_nfo_xml(make_scene(), video_path="/x/video.mp4")
        root = parse(base)
        self.assertIsNotNone(root.find("thumb"))
        self.assertIsNotNone(root.find("actor"))
        self.assertIsNotNone(root.find("tag"))
        for key in ("thumb", "poster"):
            r = parse(build_nfo_xml(make_scene(), {"nfo_exclude_fields": [key]}, "/x/video.mp4"))
            self.assertIsNone(r.find("thumb"))
            self.assertIsNotNone(r.find("actor"))
        r = parse(build_nfo_xml(make_scene(), {"nfo_exclude_fields": ["actor", "tag"]}, "/x/video.mp4"))
        self.assertIsNone(r.find("actor"))
        self.assertIsNone(r.find("tag"))
        self.assertIsNotNone(r.find("genre"))

    def test_unknown_exclude_key_warns(self):
        with patch("utils.nfo.log") as log:
            build_nfo_xml(make_scene(), {"nfo_exclude_fields": ["bogus", "rating"]})
        self.assertTrue(any("bogus" in str(c) for c in log.warning.call_args_list))

    def test_null_exclude_fields(self):
        self.assertEqual(map_settings({"nfoExcludeFields": None})["nfo_exclude_fields"], [])
        parse(build_nfo_xml(make_scene(), {"nfo_exclude_fields": None}))


class TestWriteNfoLog(unittest.TestCase):
    def test_write_nfo_logs_created_vs_updated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "v.nfo")
            settings = {"dry_run": False}
            write_nfo = getattr(scene_module, "__write_nfo")
            with patch.object(scene_module, "log") as log:
                write_nfo(make_scene(), path, settings, "/x/v.mp4")
                write_nfo(make_scene(), path, settings, "/x/v.mp4")
            msgs = [str(c.args[0]) for c in log.info.call_args_list]
            self.assertTrue(msgs[0].startswith("Created"), msgs)
            self.assertTrue(msgs[1].startswith("Updated"), msgs)


if __name__ == "__main__":
    unittest.main()
