"""Jellyfin interop options (#152): filename templates, rating field, director,
stash-box uniqueids, theporndbid, folder-level names, Performer.Update.Post hook.

Run with: python -m pytest tests/test_jellyfin_options.py -v
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

import performer as performer_module  # noqa: E402
import scene as scene_module  # noqa: E402
import utils.nfo as nfo_module  # noqa: E402
from plugin_settings import map_settings  # noqa: E402
from utils.nfo import build_nfo_xml  # noqa: E402

STASHDB = "https://stashdb.org/graphql"
PORNDB = "https://theporndb.net/graphql"


def make_scene(**kw):
    s = {
        "id": "7", "title": "T", "details": "", "rating100": 80, "date": None,
        "studio": None, "performers": [], "tags": [], "director": None, "stash_ids": [],
        "files": [{"path": "/x/video.mp4"}],
        "paths": {"screenshot": "http://stash/s?t=1"},
    }
    s.update(kw)
    return s


def nfo(scene=None, video="/x/video.mp4", **settings):
    xml = build_nfo_xml(scene or make_scene(), settings=settings, video_path=video)
    return ET.fromstring(xml.encode("utf-8"))


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


class TestSettingsMapping(unittest.TestCase):
    def test_defaults_keep_todays_output(self):
        s = map_settings({})
        self.assertEqual(s["nfo_filename"], "{basename}.nfo")
        self.assertEqual(s["poster_filename"], "{basename}-poster.jpg")
        self.assertEqual(s["backdrop_filename"], "")
        self.assertEqual(s["nfo_rating_field"], "both")

    def test_camel_case_keys(self):
        s = map_settings({"nfoFilename": "movie.nfo", "posterFilename": "folder.jpg",
                          "backdropFilename": "backdrop.jpg", "nfoRatingField": "Rating"})
        self.assertEqual(
            (s["nfo_filename"], s["poster_filename"], s["backdrop_filename"], s["nfo_rating_field"]),
            ("movie.nfo", "folder.jpg", "backdrop.jpg", "rating"),
        )


class TestFilenameTemplates(unittest.TestCase):
    def names(self, video="/x/My Video.mp4", **settings):
        return nfo_module.artwork_filenames(settings, video)

    def test_defaults(self):
        self.assertEqual(self.names(), {"nfo": "My Video.nfo", "poster": "My Video-poster.jpg", "backdrop": None})

    def test_basename_placeholder_rendering(self):
        n = self.names(nfo_filename="{basename}.nfo", poster_filename="{basename}-cover.png",
                       backdrop_filename="{basename}-fanart.jpg")
        self.assertEqual(n, {"nfo": "My Video.nfo", "poster": "My Video-cover.png",
                             "backdrop": "My Video-fanart.jpg"})

    def test_folder_level_names_render_literally(self):
        for nfo_name, poster, backdrop in (("movie.nfo", "poster.jpg", "backdrop.jpg"),
                                          ("movie.nfo", "folder.jpg", "")):
            n = self.names(nfo_filename=nfo_name, poster_filename=poster, backdrop_filename=backdrop)
            self.assertEqual(n["nfo"], "movie.nfo")
            self.assertEqual(n["poster"], poster)
            self.assertEqual(n["backdrop"], backdrop or None)

    def test_other_braces_are_literal(self):
        n = self.names(poster_filename="{title}-{basename}.jpg")
        self.assertEqual(n["poster"], "{title}-My Video.jpg")

    def test_invalid_names_fall_back_with_warning(self):
        nfo_module._warned.clear()
        with patch.object(nfo_module.log, "warning") as warn:
            n = self.names(nfo_filename="sub/movie.nfo", poster_filename="poster.txt",
                           backdrop_filename="..\\x.jpg")
        self.assertEqual(n, {"nfo": "My Video.nfo", "poster": "My Video-poster.jpg", "backdrop": None})
        self.assertEqual(warn.call_count, 3)

    def test_plex_backdrop_uses_setting_else_fanart(self):
        self.assertEqual(self.names(media_server="plex")["backdrop"], "My Video-fanart.jpg")
        self.assertEqual(
            self.names(media_server="plex", backdrop_filename="backdrop.jpg")["backdrop"], "backdrop.jpg"
        )

    def test_poster_thumb_uses_configured_name(self):
        root = nfo(poster_filename="folder.jpg")
        self.assertEqual(root.find("thumb").text, "folder.jpg")


class TestFolderLevelNamesRequireOneVideoPerFolder(unittest.TestCase):
    def test_folder_level_names_require_one_video_per_folder(self):
        for name in ("movie.nfo", "poster.jpg", "folder.jpg"):
            kind = "nfo_filename" if name.endswith(".nfo") else "poster_filename"
            with tempfile.TemporaryDirectory() as d:
                video = os.path.join(d, "a.mp4")
                touch(video)
                self.assertEqual(
                    nfo_module.artwork_filenames({kind: name}, video)["nfo" if kind == "nfo_filename" else "poster"],
                    name,
                )
                touch(os.path.join(d, "b.mkv"))
                nfo_module._warned.clear()
                with patch.object(nfo_module.log, "warning") as warn:
                    out = nfo_module.artwork_filenames({kind: name}, video)
                self.assertIsNone(out["nfo" if kind == "nfo_filename" else "poster"])
                self.assertEqual(warn.call_count, 1)

    def test_stem_names_unaffected_by_other_videos(self):
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "a.mp4")
            touch(video)
            touch(os.path.join(d, "b.mkv"))
            self.assertEqual(nfo_module.artwork_filenames({}, video)["nfo"], "a.nfo")

    def test_process_scene_skips_folder_level_files_for_shared_folder(self):
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "a.mp4")
            touch(video)
            touch(os.path.join(d, "b.mp4"))
            s = scene_module
            settings = {"nfo_filename": "movie.nfo", "poster_filename": "poster.jpg", "dry_run": False}
            with patch.object(s, "download_image") as dl, patch.object(s, "process_performer"), \
                 patch.object(s, "_" + "_hydrate_scene", side_effect=lambda sc, st: sc, create=True), \
                 patch.object(s, "_" + "_rename_videos", return_value=video, create=True), \
                 patch.object(s, "_" + "_write_nfo", create=True) as write:
                s.process_scene(make_scene(), MagicMock(), settings, "K")
            dl.assert_not_called()
            write.assert_not_called()

    def test_process_scene_writes_configured_names(self):
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "a.mp4")
            touch(video)
            s = scene_module
            settings = {"nfo_filename": "movie.nfo", "poster_filename": "folder.jpg",
                        "backdrop_filename": "backdrop.jpg", "dry_run": False}
            with patch.object(s, "download_image") as dl, patch.object(s, "process_performer"), \
                 patch.object(s, "_" + "_hydrate_scene", side_effect=lambda sc, st: sc, create=True), \
                 patch.object(s, "_" + "_rename_videos", return_value=video, create=True), \
                 patch.object(s, "_" + "_write_nfo", create=True) as write:
                s.process_scene(make_scene(), MagicMock(), settings, "K")
            self.assertEqual([os.path.basename(c.args[1]) for c in dl.call_args_list],
                             ["folder.jpg", "backdrop.jpg"])
            self.assertEqual(os.path.basename(write.call_args.args[1]), "movie.nfo")


class TestRatingField(unittest.TestCase):
    def tags(self, field):
        root = nfo(nfo_rating_field=field)
        return [t for t in ("rating", "userrating", "criticrating") if root.find(t) is not None]

    def test_both_is_default(self):
        self.assertEqual(self.tags("both"), ["rating", "userrating", "criticrating"])
        self.assertEqual(nfo().findtext("rating"), "8")

    def test_rating_only(self):
        self.assertEqual(self.tags("rating"), ["rating", "criticrating"])

    def test_userrating_only(self):
        self.assertEqual(self.tags("userrating"), ["userrating", "criticrating"])

    def test_unknown_value_warns_and_uses_both(self):
        nfo_module._warned.clear()
        with patch.object(nfo_module.log, "warning") as warn:
            self.assertEqual(self.tags("bogus"), ["rating", "userrating", "criticrating"])
        warn.assert_called_once()


class TestIdsAndDirector(unittest.TestCase):
    def test_director(self):
        self.assertEqual(nfo(make_scene(director="Jane & Co")).findtext("director"), "Jane & Co")
        self.assertIsNone(nfo(make_scene(director="")).find("director"))
        self.assertIsNone(nfo(make_scene(director=None)).find("director"))

    def test_local_uniqueid_is_default(self):
        u = nfo().findall("uniqueid")
        self.assertEqual([(x.get("type"), x.get("default"), x.text) for x in u],
                         [("stash", "true", "7")])

    def test_uniqueid_per_endpoint(self):
        scene = make_scene(stash_ids=[
            {"endpoint": STASHDB, "stash_id": "aaa"},
            {"endpoint": PORNDB, "stash_id": "bbb"},
            {"endpoint": "https://fansdb.cc/graphql", "stash_id": "ccc"},
            {"endpoint": "https://box.example.com:9999/graphql", "stash_id": "ddd"},
        ])
        u = [(x.get("type"), x.text) for x in nfo(scene).findall("uniqueid")]
        self.assertEqual(u, [("stash", "7"), ("stashdb", "aaa"), ("theporndb", "bbb"),
                             ("fansdb", "ccc"), ("box.example.com", "ddd")])

    def test_theporndbid(self):
        scene = make_scene(stash_ids=[{"endpoint": PORNDB, "stash_id": "bbb"}])
        self.assertEqual(nfo(scene).findtext("theporndbid"), "bbb")
        scene = make_scene(stash_ids=[{"endpoint": STASHDB, "stash_id": "aaa"}])
        self.assertIsNone(nfo(scene).find("theporndbid"))

    def test_uniqueid_exclusion_drops_all_ids(self):
        scene = make_scene(stash_ids=[{"endpoint": PORNDB, "stash_id": "bbb"}])
        root = nfo(scene, nfo_exclude_fields=["uniqueid"])
        self.assertIsNone(root.find("uniqueid"))
        self.assertIsNone(root.find("theporndbid"))

    def test_scene_without_new_keys_still_builds(self):
        scene = make_scene()
        del scene["director"], scene["stash_ids"]
        self.assertEqual(len(nfo(scene).findall("uniqueid")), 1)


class TestFolderLevelRelocation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.old = os.path.join(self.tmp, "old")
        self.lib = os.path.join(self.tmp, "lib")

    def run_move(self, extra_videos=(), **settings):
        video = os.path.join(self.old, "a.mp4")
        touch(video)
        for name in extra_videos:
            touch(os.path.join(self.old, name))
        for name in ("movie.nfo", "poster.jpg", "backdrop.jpg", "a.nfo"):
            with open(os.path.join(self.old, name), "w") as f:
                f.write("old " + name)
        stash = MagicMock()

        def move(query, variables):
            data = variables["input"]
            os.makedirs(data["destination_folder"], exist_ok=True)
            os.rename(video, os.path.join(data["destination_folder"], data["destination_basename"]))
            return {"moveFiles": True}

        stash.call_GQL.side_effect = move
        cfg = {
            "enable_renamer": True, "renamer_path": self.lib, "renamer_path_template": "$Title",
            "renamer_filepath_budget": 250, "renamer_ignore_files_in_path": True,
            "renamer_enable_mark_organized": False, "renamer_multi_file_mode": "all",
            "renamer_move_sidecars": True, "dry_run": False,
            "nfo_filename": "movie.nfo", "poster_filename": "poster.jpg",
            "backdrop_filename": "backdrop.jpg",
        }
        cfg.update(settings)
        scene = {"id": "5", "title": "New", "date": None, "performers": [], "studio": None,
                 "tags": [], "stash_ids": [],
                 "files": [{"id": "f0", "path": video, "height": 1080, "width": 1920}]}
        getattr(scene_module, "__rename_videos")(scene, stash, cfg)

    def test_folder_level_files_move_when_video_was_alone(self):
        self.run_move()
        self.assertEqual(sorted(os.listdir(self.lib)),
                         ["New.mp4", "New.nfo", "backdrop.jpg", "movie.nfo", "poster.jpg"])
        self.assertEqual(os.listdir(self.old), [])

    def test_folder_level_files_stay_when_folder_has_other_videos(self):
        self.run_move(extra_videos=("b.mkv",))
        self.assertEqual(sorted(os.listdir(self.lib)), ["New.mp4", "New.nfo"])
        self.assertIn("movie.nfo", os.listdir(self.old))

    def test_folder_level_move_never_overwrites(self):
        touch(os.path.join(self.lib, "movie.nfo"))
        with open(os.path.join(self.lib, "movie.nfo"), "w") as f:
            f.write("existing")
        self.run_move()
        with open(os.path.join(self.lib, "movie.nfo")) as f:
            self.assertEqual(f.read(), "existing")
        self.assertIn("movie.nfo", os.listdir(self.old))

    def test_nothing_folder_level_moves_with_default_names(self):
        self.run_move(nfo_filename="{basename}.nfo", poster_filename="{basename}-poster.jpg",
                      backdrop_filename="")
        self.assertEqual(sorted(os.listdir(self.lib)), ["New.mp4", "New.nfo"])
        self.assertIn("movie.nfo", os.listdir(self.old))

    def test_sidecar_toggle_off_still_moves_configured_stem_names(self):
        self.run_move(renamer_move_sidecars=False, nfo_filename="{basename}.nfo")
        self.assertIn("New.nfo", os.listdir(self.lib))


class TestPerformerHook(unittest.TestCase):
    def stash(self):
        stash = MagicMock()
        stash.find_performer.return_value = {"id": "3", "name": "Jane", "image_path": "http://s/p?t=1"}
        return stash

    def test_reexports_image_when_enabled(self):
        with patch.object(performer_module, "process_performer") as pp:
            performer_module.process_performer_hook(
                self.stash(), "3", {"enable_actor_images": True, "dry_run": False}, "KEY")
        self.assertEqual(pp.call_args.args[0]["name"], "Jane")
        self.assertTrue(pp.call_args.kwargs["overwrite"])

    def test_does_nothing_when_disabled(self):
        stash = self.stash()
        with patch.object(performer_module, "process_performer") as pp:
            performer_module.process_performer_hook(stash, "3", {"enable_actor_images": False}, "K")
        pp.assert_not_called()
        stash.find_performer.assert_not_called()

    def test_dry_run_only_logs(self):
        with patch.object(performer_module, "process_performer") as pp, \
             patch.object(performer_module.log, "info") as info:
            performer_module.process_performer_hook(
                self.stash(), "3", {"enable_actor_images": True, "dry_run": True}, "K")
        pp.assert_not_called()
        self.assertIn("DRY RUN", info.call_args.args[0])

    def test_hook_registered_in_manifest(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcMetadata.yml")
        text = open(path, encoding="utf-8").read()
        self.assertIn("- Performer.Update.Post", text)
        self.assertIn("- Scene.Update.Post", text)


if __name__ == "__main__":
    unittest.main()
