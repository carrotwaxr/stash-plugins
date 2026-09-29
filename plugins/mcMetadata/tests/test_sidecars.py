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
        self.assertEqual(nfo_module.count_videos(self.incoming), 3)
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


class TestCollisionSuffix(_Base):
    """The " (N)" suffix for a scene's second file keeps within the budget and the 255-byte cap."""

    def _two_files(self, title="New Name", **settings):
        videos = [os.path.join(self.incoming, n) for n in ("one.mp4", "two.mp4")]
        for v in videos:
            _write(v)
        files = [{"id": f"f{i}", "path": p, "height": 1080, "width": 1920} for i, p in enumerate(videos)]
        scene = {"id": "5", "title": title, "date": None, "files": files,
                 "performers": [], "studio": None, "tags": [], "stash_ids": []}
        stash = _FakeStash({f["id"]: f["path"] for f in files})
        with patch.object(scene_module.log, "error") as error:
            self.rename(scene, stash, self._settings(**settings))
        return sorted(os.listdir(self.lib)), [c.args[0] for c in error.call_args_list]

    def test_suffix_trims_the_name_to_stay_within_the_budget(self):
        budget = len(os.path.join(self.lib, "New Name.mp4"))
        names, errors = self._two_files(renamer_filepath_budget=budget)
        self.assertEqual(errors, [])
        self.assertEqual(names, ["New (2).mp4", "New Name.mp4"])
        for name in names:
            self.assertLessEqual(len(os.path.join(self.lib, name)), budget)

    def test_suffix_keeps_the_name_within_255_bytes(self):
        names, errors = self._two_files(title="é" * 200, renamer_filepath_budget=800)
        self.assertEqual(errors, [])
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertLessEqual(len(name.encode("utf-8")), 255)
        self.assertIn("é" * 123 + " (2).mp4", names)

    def test_no_room_for_a_suffix_skips_the_file_with_an_error(self):
        budget = len(os.path.join(self.lib, "N.mp4"))
        names, errors = self._two_files(title="N", renamer_filepath_budget=budget)
        self.assertEqual(names, ["N.mp4"])
        self.assertEqual(os.listdir(self.incoming), ["two.mp4"])
        self.assertEqual(len(errors), 1)
        self.assertIn("renamerFilepathBudget", errors[0])


def _case_insensitive_fs():
    """Patch os.path.exists/samefile to act like Windows or macOS: names match in any case."""
    real_exists, real_samefile = os.path.exists, os.path.samefile

    def actual(path):
        """The existing file path names, found case-insensitively, or None."""
        if real_exists(path):
            return path
        folder, name = os.path.split(path)
        try:
            names = os.listdir(folder)
        except OSError:
            return None
        match = [n for n in names if n.lower() == name.lower()]
        return os.path.join(folder, match[0]) if match else None

    def exists(path):
        return actual(path) is not None

    def samefile(a, b):
        a, b = actual(a), actual(b)
        if a is None or b is None:
            raise FileNotFoundError(a or b)
        return real_samefile(a, b)

    exists_patch = patch.object(os.path, "exists", side_effect=exists)
    samefile_patch = patch.object(os.path, "samefile", side_effect=samefile)
    return exists_patch, samefile_patch


class TestCaseOnlyRename(_Base):
    def setUp(self):
        super().setUp()
        for p in _case_insensitive_fs():
            p.start()
            self.addCleanup(p.stop)

    def _scene_in_place(self, **settings):
        """Rename incoming/movie.mp4 to incoming/Movie.mp4 (a case-only rename)."""
        for name in ("movie.mp4", "movie.srt", "movie-poster.jpg"):
            _write(os.path.join(self.incoming, name), name)
        files = [{"id": "f0", "path": os.path.join(self.incoming, "movie.mp4"), "height": 1080, "width": 1920}]
        scene = {"id": "5", "title": "Movie", "date": None, "files": files,
                 "performers": [], "studio": None, "tags": [], "stash_ids": []}
        stash = _FakeStash({"f0": files[0]["path"]})
        settings = self._settings(renamer_path=self.incoming, renamer_ignore_files_in_path=False, **settings)
        with patch.object(scene_module.log, "warning") as warn, patch.object(scene_module.log, "info") as info:
            result = self.rename(scene, stash, settings)
        return result, [c.args[0] for c in warn.call_args_list], [c.args[0] for c in info.call_args_list]

    def test_rename_file_allows_a_case_only_rename(self):
        src, dest = os.path.join(self.incoming, "movie.srt"), os.path.join(self.incoming, "Movie.srt")
        _write(src, "srt")
        self.assertTrue(os.path.exists(dest))  # the simulation: the source answers for dest
        self.assertEqual(rename_file(src, dest, {"dry_run": False}), dest)
        self.assertEqual(os.listdir(self.incoming), ["Movie.srt"])
        self.assertEqual(rename_file(dest, src, {"dry_run": True}), src)

    def test_rename_file_still_refuses_another_file(self):
        src, other = os.path.join(self.incoming, "a.srt"), os.path.join(self.incoming, "B.srt")
        _write(src, "a")
        _write(other, "b")
        with patch.object(scene_module.log, "warning"):
            self.assertFalse(rename_file(src, os.path.join(self.incoming, "b.srt"), {"dry_run": False}))
        self.assertEqual(sorted(os.listdir(self.incoming)), ["B.srt", "a.srt"])

    def test_video_and_sidecars_get_a_case_only_rename(self):
        result, warnings, _ = self._scene_in_place()
        self.assertEqual(result, os.path.join(self.incoming, "Movie.mp4"))
        self.assertEqual(sorted(os.listdir(self.incoming)), ["Movie-poster.jpg", "Movie.mp4", "Movie.srt"])
        self.assertFalse(any("already exists" in w for w in warnings), warnings)

    def test_dry_run_reports_the_case_only_rename(self):
        result, warnings, info = self._scene_in_place(dry_run=True)
        self.assertEqual(result, os.path.join(self.incoming, "Movie.mp4"))
        self.assertIn(f"[DRY RUN]                    To: {os.path.join(self.incoming, 'Movie.mp4')}", info)
        self.assertIn(
            f"[DRY RUN] Would move sidecar: {os.path.join(self.incoming, 'movie.srt')} -> "
            f"{os.path.join(self.incoming, 'Movie.srt')}", info)
        self.assertFalse(any("already exists" in w for w in warnings), warnings)
        self.assertEqual(sorted(os.listdir(self.incoming)), ["movie-poster.jpg", "movie.mp4", "movie.srt"])

    def test_another_scene_file_at_the_same_name_is_still_a_collision(self):
        # a dry run that already sent another file to Movie.mp4 must not see it as the source
        pending = scene_module._PendingMoves()
        src = os.path.join(self.incoming, "movie.mp4")
        _write(src)
        dest = os.path.join(self.incoming, "Movie.mp4")
        self.assertTrue(pending.same_file(src, dest))
        pending.add(os.path.join(self.tmp, "elsewhere.mp4"), dest, video=True)
        self.assertFalse(pending.same_file(src, dest))


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
