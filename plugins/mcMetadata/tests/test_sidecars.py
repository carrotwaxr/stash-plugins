"""
Sidecar files (subtitles, funscripts, fanart, NFO, poster) move with their video,
and a move never overwrites an existing file.

Everything lives in temp dirs; stash is a fake that moves the video with os.rename.

Run with: python -m pytest tests/test_sidecars.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

import scene as scene_module  # noqa: E402
from utils.files import find_sidecars, rename_file  # noqa: E402


def _write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read(path):
    with open(path) as f:
        return f.read()


class _FakeStash:
    def __init__(self, paths):
        self.paths = paths

    def call_GQL(self, query, variables):
        data = variables["input"]
        src = self.paths[data["ids"][0]]
        dest = os.path.join(data["destination_folder"], data["destination_basename"])
        os.makedirs(data["destination_folder"], exist_ok=True)
        os.rename(src, dest)
        return {"moveFiles": True}

    def update_scene(self, update_input):
        pass


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.incoming = os.path.join(self.tmp, "incoming")
        self.lib = os.path.join(self.tmp, "lib")
        os.makedirs(self.incoming)
        self.rename = getattr(scene_module, "__rename_videos")

    def _settings(self, **overrides):
        settings = {
            "enable_renamer": True,
            "renamer_path": self.lib,
            "renamer_path_template": "$Title",
            "renamer_filepath_budget": 250,
            "renamer_ignore_files_in_path": True,
            "renamer_enable_mark_organized": False,
            "renamer_multi_file_mode": "all",
            "renamer_move_sidecars": True,
            "dry_run": False,
            "data_dir": os.path.join(self.tmp, "data"),
        }
        settings.update(overrides)
        return settings

    def _run(self, videos, **settings):
        files = [{"id": f"f{i}", "path": p, "height": 1080, "width": 1920} for i, p in enumerate(videos)]
        scene = {
            "id": "5", "title": "New Name", "date": "2024-01-15", "files": files,
            "performers": [], "studio": None, "tags": [], "stash_ids": [],
        }
        stash = _FakeStash({f["id"]: f["path"] for f in files})
        return self.rename(scene, stash, self._settings(**settings))


class TestFindSidecars(_Base):
    def test_find_sidecars_boundaries(self):
        video = os.path.join(self.incoming, "Scene 1.mp4")
        wanted = ["Scene 1.srt", "Scene 1.en.srt", "Scene 1.funscript", "Scene 1-fanart.jpg",
                  "Scene 1.nfo", "Scene 1-poster.jpg"]
        unwanted = ["Scene 10.srt", "Scene 1x.srt", "Scene 1.mkv", "Other.srt"]
        for name in wanted + unwanted + ["Scene 1.mp4"]:
            _write(os.path.join(self.incoming, name))
        found = find_sidecars(video)
        self.assertEqual(sorted(os.path.basename(p) for p in found), sorted(wanted))
        self.assertTrue(all(os.path.dirname(p) == self.incoming for p in found))

    def test_missing_folder_gives_empty_list(self):
        self.assertEqual(find_sidecars(os.path.join(self.tmp, "nope", "a.mp4")), [])

    def test_another_videos_files_are_not_sidecars(self):
        show = ["Show.nfo", "Show-poster.jpg", "Show.srt"]
        part2 = ["Show-Part2.nfo", "Show-Part2-poster.jpg", "Show-Part2.srt"]
        for name in show + part2 + ["Show.mp4", "Show-Part2.mp4"]:
            _write(os.path.join(self.incoming, name))
        found = find_sidecars(os.path.join(self.incoming, "Show.mp4"))
        self.assertEqual(sorted(os.path.basename(p) for p in found), sorted(show))
        found = find_sidecars(os.path.join(self.incoming, "Show-Part2.mp4"))
        self.assertEqual(sorted(os.path.basename(p) for p in found), sorted(part2))

    def test_videos_of_any_common_type_are_not_sidecars(self):
        videos = ["Show.mpg", "Show.mpeg", "Show.vob", "Show.3gp", "Show.3g2", "Show.rm", "Show.rmvb",
                  "Show.ogv", "Show.divx", "Show.xvid", "Show.asf", "Show.iso", "Show.f4v", "Show.mts",
                  "Show.MPG"]
        for name in videos + ["Show.mp4", "Show.srt"]:
            _write(os.path.join(self.incoming, name))
        found = find_sidecars(os.path.join(self.incoming, "Show.mp4"))
        self.assertEqual([os.path.basename(p) for p in found], ["Show.srt"])

    def test_scene_videos_that_already_moved_keep_their_files(self):
        # Show-Part2.mp4 was moved earlier in the run, but a sidecar of it stayed behind
        for name in ("Show.mp4", "Show.srt", "Show-Part2.srt"):
            _write(os.path.join(self.incoming, name))
        moved = os.path.join(self.incoming, "Show-Part2.mp4")
        found = find_sidecars(os.path.join(self.incoming, "Show.mp4"), other_videos=[moved])
        self.assertEqual([os.path.basename(p) for p in found], ["Show.srt"])

    def test_shared_video_extensions(self):
        from utils import nfo as nfo_module
        from utils import videos

        for name in ("a.mp4", "b.mpg", "c.iso", "d.srt"):
            _write(os.path.join(self.incoming, name))
        self.assertEqual(nfo_module._count_videos(self.incoming), 3)
        for ext in ("mp4 m4v mkv avi mov wmv webm flv ts m2ts mts mpg mpeg vob 3gp 3g2 rm rmvb ogv "
                    "divx xvid asf iso f4v").split():
            self.assertTrue(videos.is_video(f"x.{ext.upper()}"), ext)
        self.assertFalse(videos.is_video("x.srt"))
        self.assertFalse(hasattr(nfo_module, "_VIDEO_EXTENSIONS"))
        import utils.files as files_module
        self.assertFalse(hasattr(files_module, "VIDEO_EXTENSIONS"))


class TestRenameFile(_Base):
    def test_refuses_to_overwrite(self):
        src, dst = os.path.join(self.incoming, "a.nfo"), os.path.join(self.lib, "a.nfo")
        _write(src, "src")
        _write(dst, "dst")
        self.assertFalse(rename_file(src, dst, {"dry_run": False}))
        self.assertEqual(_read(src), "src")
        self.assertEqual(_read(dst), "dst")


class TestSidecarMoves(_Base):
    def test_sidecars_follow_every_moved_file(self):
        v1 = os.path.join(self.incoming, "one.mp4")
        v2 = os.path.join(self.incoming, "two.mp4")
        for name in ("one.mp4", "two.mp4", "one.srt", "one.en.srt", "one-fanart.jpg",
                     "two.funscript", "two.nfo"):
            _write(os.path.join(self.incoming, name), name)
        self._run([v1, v2])
        moved = sorted(os.listdir(self.lib))
        self.assertEqual(moved, sorted([
            "New Name.mp4", "New Name.srt", "New Name.en.srt", "New Name-fanart.jpg",
            "New Name (2).mp4", "New Name (2).funscript", "New Name (2).nfo",
        ]))
        self.assertEqual(os.listdir(self.incoming), [])
        self.assertEqual(_read(os.path.join(self.lib, "New Name (2).funscript")), "two.funscript")
        self.assertEqual(_read(os.path.join(self.lib, "New Name.en.srt")), "one.en.srt")

    def test_rename_leaves_another_videos_files(self):
        for name in ("Show.mp4", "Show.nfo", "Show-poster.jpg", "Show.srt", "Show.mpg",
                     "Show-Part2.mp4", "Show-Part2.nfo", "Show-Part2-poster.jpg", "Show-Part2.srt"):
            _write(os.path.join(self.incoming, name), name)
        self._run([os.path.join(self.incoming, "Show.mp4")])
        self.assertEqual(sorted(os.listdir(self.lib)), sorted([
            "New Name.mp4", "New Name.nfo", "New Name-poster.jpg", "New Name.srt",
        ]))
        self.assertEqual(sorted(os.listdir(self.incoming)), sorted([
            "Show.mpg", "Show-Part2.mp4", "Show-Part2.nfo", "Show-Part2-poster.jpg", "Show-Part2.srt",
        ]))

    def test_left_behind_file_of_a_moved_scene_video_stays(self):
        # Show-Part2.srt can't follow its video (the destination is taken), and Show.mp4,
        # moved next, must not take it either
        for name in ("Show-Part2.mp4", "Show-Part2.srt", "Show.mp4", "Show.srt"):
            _write(os.path.join(self.incoming, name), name)
        _write(os.path.join(self.lib, "New Name.srt"), "existing")
        with patch.object(scene_module.log, "warning"):
            self._run([os.path.join(self.incoming, "Show-Part2.mp4"), os.path.join(self.incoming, "Show.mp4")])
        self.assertEqual(os.listdir(self.incoming), ["Show-Part2.srt"])
        self.assertEqual(_read(os.path.join(self.lib, "New Name (2).srt")), "Show.srt")

    def test_existing_destination_not_overwritten(self):
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old.srt", "old.nfo", "old-poster.jpg"):
            _write(os.path.join(self.incoming, name), "incoming " + name)
        _write(os.path.join(self.lib, "New Name.srt"), "existing srt")
        _write(os.path.join(self.lib, "New Name.nfo"), "existing nfo")
        with patch.object(scene_module.log, "warning") as warn:
            self._run([video])
        self.assertEqual(_read(os.path.join(self.lib, "New Name.srt")), "existing srt")
        self.assertEqual(_read(os.path.join(self.incoming, "old.srt")), "incoming old.srt")
        self.assertEqual(_read(os.path.join(self.lib, "New Name.nfo")), "existing nfo")
        self.assertEqual(_read(os.path.join(self.incoming, "old.nfo")), "incoming old.nfo")
        self.assertEqual(_read(os.path.join(self.lib, "New Name-poster.jpg")), "incoming old-poster.jpg")
        self.assertTrue(warn.called)

    def test_setting_off_moves_only_nfo_and_poster(self):
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old.srt", "old.nfo", "old-poster.jpg", "old-fanart.jpg"):
            _write(os.path.join(self.incoming, name))
        self._run([video], renamer_move_sidecars=False)
        self.assertEqual(sorted(os.listdir(self.lib)), ["New Name-poster.jpg", "New Name.mp4", "New Name.nfo"])
        self.assertEqual(sorted(os.listdir(self.incoming)), ["old-fanart.jpg", "old.srt"])

    def test_dry_run_lists_sidecars(self):
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old.en.srt", "old.nfo"):
            _write(os.path.join(self.incoming, name))
        with patch.object(scene_module.log, "info") as info:
            self._run([video], dry_run=True)
        lines = [c.args[0] for c in info.call_args_list]
        for name in ("old.en.srt", "old.nfo"):
            dst = os.path.join(self.lib, "New Name" + name[len("old"):])
            self.assertIn(f"[DRY RUN] Would move sidecar: {os.path.join(self.incoming, name)} -> {dst}", lines)
        self.assertEqual(sorted(os.listdir(self.incoming)), ["old.en.srt", "old.mp4", "old.nfo"])
        self.assertFalse(os.path.exists(self.lib))


if __name__ == "__main__":
    unittest.main()
