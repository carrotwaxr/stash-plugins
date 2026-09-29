"""Plex NFO mode (Plex Media Server 1.43.1+ built-in NFO provider).

Run with: python -m pytest tests/test_plex_mode.py -v
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
from utils.nfo import build_nfo_xml

IMG = "http://stash:9999/performer/7/image?t=123"


def make_scene():
    return {
        "id": "1",
        "title": "Title",
        "details": "Plot",
        "rating100": 80,
        "date": "2024-05-06",
        "studio": {"name": "Studio"},
        "performers": [
            {"name": "Jane", "image_path": IMG},
            {"name": "Nopic", "image_path": None},
        ],
        "tags": [{"name": "Tag"}],
        "files": [{"path": "/x/video.mp4"}],
        "paths": {"screenshot": "http://stash:9999/scene/1/screenshot?t=1"},
    }


def actors(xml):
    return ET.fromstring(xml.encode("utf-8")).findall("actor")


def settings(server="plex", **kw):
    s = {"media_server": server, "enable_actor_images": True, "dry_run": False}
    s.update(kw)
    return s


class TestPlexNfo(unittest.TestCase):
    def test_actor_blocks_have_name_role_order(self):
        xml = build_nfo_xml(make_scene(), settings(), "/x/video.mp4", api_key="")
        a = actors(xml)
        self.assertEqual([x.findtext("name") for x in a], ["Jane", "Nopic"])
        self.assertEqual([x.findtext("role") for x in a], ["Jane", "Nopic"])
        self.assertEqual([x.findtext("order") for x in a], ["0", "1"])

    def test_actor_thumb_is_stash_url_without_key(self):
        xml = build_nfo_xml(make_scene(), settings(), "/x/video.mp4", api_key="")
        a = actors(xml)
        self.assertEqual(a[0].findtext("thumb"), IMG)
        self.assertIsNone(a[1].find("thumb"))
        self.assertNotIn("apikey", xml.lower())

    def test_actor_thumb_omitted_when_stash_needs_auth(self):
        xml = build_nfo_xml(make_scene(), settings(), "/x/video.mp4", api_key="SECRET")
        self.assertIsNone(actors(xml)[0].find("thumb"))
        self.assertNotIn("SECRET", xml)

    def test_actor_thumb_omitted_when_actor_images_off(self):
        xml = build_nfo_xml(
            make_scene(), settings(enable_actor_images=False), "/x/video.mp4", api_key=""
        )
        self.assertIsNone(actors(xml)[0].find("thumb"))

    def test_poster_thumb_keeps_stem_poster_name(self):
        xml = build_nfo_xml(make_scene(), settings(), "/x/video.mp4", api_key="")
        thumb = ET.fromstring(xml.encode()).find("thumb")
        self.assertEqual(thumb.text, "video-poster.jpg")

    def test_media_server_case_insensitive(self):
        xml = build_nfo_xml(make_scene(), settings("Plex"), "/x/video.mp4", api_key="")
        self.assertEqual(actors(xml)[0].findtext("thumb"), IMG)

    def test_jellyfin_and_emby_unchanged(self):
        for server in ("jellyfin", "emby"):
            with tempfile.TemporaryDirectory() as d:
                s = settings(server, actor_metadata_path=d)
                with_key = build_nfo_xml(make_scene(), s, "/x/video.mp4", api_key="")
                default = build_nfo_xml(make_scene(), s, "/x/video.mp4")
                self.assertEqual(with_key, default)
                self.assertNotIn(IMG, with_key)


class TestPlexArtwork(unittest.TestCase):
    def run_scene(self, server):
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "video.mp4")
            open(video, "w").close()
            s = scene_module
            with patch.object(s, "download_image") as dl, \
                 patch.object(s, "process_performer"), \
                 patch.object(s, "_" + "_hydrate_scene", side_effect=lambda sc, st: sc, create=True), \
                 patch.object(s, "_" + "_rename_videos", return_value=video, create=True), \
                 patch.object(s, "_" + "_write_nfo", create=True):
                s.process_scene(make_scene(), MagicMock(), settings(server), "KEY")
            return [os.path.basename(c.args[1]) for c in dl.call_args_list]

    def test_plex_downloads_poster_and_fanart(self):
        self.assertEqual(self.run_scene("plex"), ["video-poster.jpg", "video-fanart.jpg"])

    def test_other_servers_poster_only(self):
        for server in ("jellyfin", "emby"):
            self.assertEqual(self.run_scene(server), ["video-poster.jpg"])


if __name__ == "__main__":
    unittest.main()
