"""
Renamer paths go through the path layer: safe joins under the base, sanitized
components, one-pass template rendering, overflow-only truncation, byte limits
per component, and full studio hierarchies.

No Stash connection and no file operations: every path lives in a temp dir that
is never written to, and stash is a MagicMock.

Run with: python -m pytest tests/test_replacer_paths.py -v
"""

import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock stashapi before importing any plugin modules (not available outside Stash runtime)
sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

import scene as scene_module  # noqa: E402
import utils.replacer as replacer  # noqa: E402
from utils.paths import is_inside  # noqa: E402
from utils.replacer import get_new_path, resolve_conditionals  # noqa: E402

BASE = os.path.normpath("/media/lib")


def _scene(**overrides):
    scene = {
        "id": 7,
        "title": "A Title",
        "date": "2024-01-15",
        "files": [{"id": "f1", "path": "/incoming/source.mp4", "height": 1080, "width": 1920}],
        "performers": [{"name": "Jane Doe", "gender": "FEMALE"}],
        "studio": {"name": "Studio", "parent_studio": None},
        "stash_ids": [{"stash_id": "abc-123"}],
        "tags": [],
    }
    scene.update(overrides)
    return scene


def _expected(*parts):
    return os.path.join(BASE, *parts)


class TestBasePath(unittest.TestCase):
    def test_base_path_joined_safely(self):
        for base in ("/media/lib", "/media/lib/"):
            with self.subTest(base=base):
                result = get_new_path(_scene(), base, "$Studio/$Title", 250)
                self.assertEqual(result, _expected("Studio", "A Title.mp4"))

    def test_empty_base_path_rejected(self):
        for base in ("", None):
            with self.subTest(base=base):
                with patch("utils.replacer.log") as log:
                    result = get_new_path(_scene(), base, "$Studio/$Title", 250)
                self.assertIsNone(result)
                log.error.assert_called_once()
                self.assertIn("renamerPath", log.error.call_args[0][0])


class TestSanitizing(unittest.TestCase):
    def test_metadata_cannot_escape_base(self):
        cases = [
            (_scene(studio={"name": "..", "parent_studio": None}), "$Studio/$Title", _expected("_", "A Title.mp4")),
            (_scene(title="../../x"), "$Studio/$Title", _expected("Studio", "x.mp4")),
            (_scene(studio={"name": "/abs", "parent_studio": None}), "$Studio/$Title", _expected("abs", "A Title.mp4")),
            (
                _scene(studio={"name": "..", "parent_studio": {"name": "..", "parent_studio": None}}),
                "$Studios/$Title",
                _expected("_", "_", "A Title.mp4"),
            ),
        ]
        for scene, template, expected in cases:
            with self.subTest(template=template, studio=scene["studio"], title=scene["title"]):
                result = get_new_path(scene, BASE, template, 250)
                self.assertEqual(result, expected)
                self.assertTrue(is_inside(result, BASE))

    def test_template_traversal_rejected(self):
        with patch("utils.replacer.log") as log:
            result = get_new_path(_scene(), BASE, "../$Title", 250)
        self.assertIsNone(result)
        log.error.assert_called_once()

    def test_windows_reserved_and_trailing_dots(self):
        self.assertEqual(get_new_path(_scene(title="CON"), BASE, "$Title", 250), _expected("CON_.mp4"))
        self.assertEqual(
            get_new_path(_scene(studio={"name": "Ends.", "parent_studio": None}), BASE, "$Studio/$Title", 250),
            _expected("Ends", "A Title.mp4"),
        )

    def test_ampersand_rule_kept(self):
        """'&' -> 'and' stays where it applied before: performers, studio(s), tags, title."""
        scene = _scene(
            title="Tom & Jerry",
            studio={"name": "Black & White", "parent_studio": None},
            performers=[{"name": "A & B", "gender": "FEMALE"}],
            tags=[{"name": "R&B"}],
        )
        result = get_new_path(scene, BASE, "$Studio/$Title - $Performers $Tags", 250)
        self.assertEqual(result, _expected("Black and White", "Tom and Jerry - A and B RandB.mp4"))

    def test_invalid_chars_in_values(self):
        scene = _scene(title='Why? "Because": <yes>|*')
        result = get_new_path(scene, BASE, "$Title", 250)
        self.assertEqual(result, _expected("Why Because - yes.mp4"))


class TestOnePassRendering(unittest.TestCase):
    def test_tokens_in_metadata_not_reexpanded(self):
        scene = _scene(title="Pay $Tags now", tags=[{"name": "Rough"}])
        self.assertEqual(
            get_new_path(scene, BASE, "$Title - $Tags", 250), _expected("Pay $Tags now - Rough.mp4")
        )

    def test_tokens_in_metadata_not_reexpanded_in_conditionals(self):
        scene = _scene(title="Pay $Tags now", tags=[{"name": "Rough"}])
        self.assertEqual(
            get_new_path(scene, BASE, "{$Title - }$Tags", 250), _expected("Pay $Tags now - Rough.mp4")
        )

    def test_resolve_conditionals_does_not_reexpand(self):
        scene = _scene(title="Pay $ReleaseDate now")
        self.assertEqual(
            resolve_conditionals("{$Title $ReleaseDate}", scene), "Pay $ReleaseDate now 2024-01-15"
        )

    def test_studio_and_studios_are_distinct_keys(self):
        scene = _scene(studio={"name": "Child", "parent_studio": {"name": "Parent", "parent_studio": None}})
        self.assertEqual(
            get_new_path(scene, BASE, "$Studios/$Studio - $Title", 250),
            _expected("Parent", "Child", "Child - A Title.mp4"),
        )

    def test_unknown_token_left_literal(self):
        self.assertEqual(get_new_path(_scene(), BASE, "$Titles $Title", 250), _expected("$Titles A Title.mp4"))


class TestMissingValues(unittest.TestCase):
    def test_no_studio_required_variable_skips_rename(self):
        """A bare $Studio with no studio is an error (no rename), as before."""
        with patch("utils.replacer.log") as log:
            result = get_new_path(_scene(studio=None), BASE, "$Studio/$Title", 250)
        self.assertIsNone(result)
        log.error.assert_called_once()

    def test_no_studio_conditional_folder_collapses(self):
        self.assertEqual(get_new_path(_scene(studio=None), BASE, "{$Studio/}$Title", 250), _expected("A Title.mp4"))

    def test_empty_folder_value_collapses(self):
        """An empty truncable folder ($Tags with no tags) collapses, like the old '//' did."""
        self.assertEqual(get_new_path(_scene(tags=[]), BASE, "$Tags/$Title", 250), _expected("A Title.mp4"))

    def test_empty_filename_rejected(self):
        with patch("utils.replacer.log") as log:
            result = get_new_path(_scene(tags=[]), BASE, "$Studio/$Tags", 250)
        self.assertIsNone(result)
        log.error.assert_called_once()


class TestBudget(unittest.TestCase):
    TEMPLATE = "$Studio/$Title - $Performers"

    def _perf_scene(self):
        return _scene(
            performers=[
                {"name": "Jane Doe", "gender": "FEMALE"},
                {"name": "Mary Majorette", "gender": "FEMALE"},
            ]
        )

    def test_truncation_trims_only_overflow(self):
        scene = self._perf_scene()
        full = get_new_path(scene, BASE, self.TEMPLATE, 1000)
        self.assertEqual(full, _expected("Studio", "A Title - Jane Doe Mary Majorette.mp4"))

        # exact fit: nothing is cut
        self.assertEqual(get_new_path(scene, BASE, self.TEMPLATE, len(full)), full)

        # over by 1 or 5: the trailing name is dropped whole, not cut inside
        for over in (1, 5):
            result = get_new_path(scene, BASE, self.TEMPLATE, len(full) - over)
            self.assertEqual(result, _expected("Studio", "A Title - Jane Doe.mp4"))

        # a single remaining name is cut by characters, only by the overflow
        single = _scene(performers=[{"name": "Mary Majorette", "gender": "FEMALE"}])
        full = get_new_path(single, BASE, self.TEMPLATE, 1000)
        over5 = get_new_path(single, BASE, self.TEMPLATE, len(full) - 5)
        self.assertEqual(over5, _expected("Studio", "A Title - Mary Majo.mp4"))
        self.assertEqual(len(over5), len(full) - 5)

    def test_truncation_order_and_stop(self):
        """Truncables are cut in the order Tags, MalePerformers, FemalePerformers, Performers,
        each kept to at least one character, stopping as soon as the path fits."""
        template = "$Title - $FemalePerformers $Tags"
        scene = _scene(
            performers=[{"name": "Jane Doe", "gender": "FEMALE"}],
            tags=[{"name": "Threesome"}, {"name": "Rough"}],
        )
        full = get_new_path(scene, BASE, template, 1000)
        self.assertEqual(full, _expected("A Title - Jane Doe Threesome Rough.mp4"))

        # over by 3: the last tag is dropped whole; the performer is untouched
        self.assertEqual(
            get_new_path(scene, BASE, template, len(full) - 3), _expected("A Title - Jane Doe Threesome.mp4")
        )
        # over by 10: Rough is dropped (6), the lone tag then loses 4 characters (Threesome -> Three)
        self.assertEqual(
            get_new_path(scene, BASE, template, len(full) - 10), _expected("A Title - Jane Doe Three.mp4")
        )
        # over by 20: the lone tag is cut to 1 character (8), then the performer
        # name (a single name) is cut by the remaining 6
        self.assertEqual(
            get_new_path(scene, BASE, template, len(full) - 20), _expected("A Title - Ja T.mp4")
        )

    def test_tags_trimmed_before_any_performer_and_names_dropped_whole(self):
        template = "$Title - $Performers $Tags"
        scene = _scene(
            performers=[{"name": "Jane Doe", "gender": "FEMALE"}, {"name": "John Roe", "gender": "MALE"}],
            tags=[{"name": "Alpha"}, {"name": "Beta"}],
        )
        full = get_new_path(scene, BASE, template, 1000)
        self.assertEqual(full, _expected("A Title - Jane Doe John Roe Alpha Beta.mp4"))

        # tags shrink to one character before any performer name is touched
        tags_gone = get_new_path(scene, BASE, template, len(full) - 9)
        self.assertEqual(tags_gone, _expected("A Title - Jane Doe John Roe A.mp4"))

        # once tags are at their minimum, performers lose whole trailing names, not letters
        one_name = get_new_path(scene, BASE, template, len(full) - 14)
        self.assertEqual(one_name, _expected("A Title - Jane Doe A.mp4"))

        # only a lone name is cut by characters
        cut = get_new_path(scene, BASE, template, len(full) - 20)
        self.assertEqual(cut, _expected("A Title - Jane D A.mp4"))

    def test_repeated_truncable_is_cut_once_per_overflow_share(self):
        template = "$Performers/$Title - $Performers"
        scene = _scene(performers=[{"name": "Jane Doemann", "gender": "FEMALE"}])
        full = get_new_path(scene, BASE, template, 1000)
        self.assertEqual(full, _expected("Jane Doemann", "A Title - Jane Doemann.mp4"))
        # over by 4 with two occurrences: each loses 2, not 4
        result = get_new_path(scene, BASE, template, len(full) - 4)
        self.assertEqual(result, _expected("Jane Doema", "A Title - Jane Doema.mp4"))
        self.assertEqual(len(result), len(full) - 4)

    def test_budget_too_small_names_real_setting(self):
        with patch("utils.replacer.log") as log:
            result = get_new_path(self._perf_scene(), BASE, self.TEMPLATE, 20)
        self.assertIsNone(result)
        log.error.assert_called_once()
        message = log.error.call_args[0][0]
        self.assertIn("renamerFilepathBudget", message)
        self.assertNotIn("renamer_filename_budget", message)

    def test_component_byte_limit(self):
        title = "漢" * 150  # 150 CJK characters, 450 bytes in UTF-8
        result = get_new_path(_scene(title=title), BASE, "$Studio/$Title", 250)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 250)
        name = os.path.basename(result)
        self.assertLessEqual(len(name.encode("utf-8")), 255)
        self.assertTrue(name.endswith(".mp4"))
        self.assertEqual(name, "漢" * 83 + ".mp4")
        self.assertEqual(os.path.dirname(result), _expected("Studio"))

    def test_folder_byte_limit(self):
        studio = "漢" * 100
        result = get_new_path(_scene(studio={"name": studio, "parent_studio": None}), BASE, "$Studio/$Title", 250)
        folder = os.path.basename(os.path.dirname(result))
        self.assertLessEqual(len(folder.encode("utf-8")), 255)
        self.assertEqual(folder, "漢" * 85)

    def test_float_budget(self):
        self.assertEqual(get_new_path(_scene(), BASE, "$Title", 250.0), _expected("A Title.mp4"))

    def test_string_budget(self):
        self.assertEqual(get_new_path(_scene(), BASE, "$Title", "250"), _expected("A Title.mp4"))

    def test_invalid_budget(self):
        with patch("utils.replacer.log") as log:
            result = get_new_path(_scene(), BASE, "$Title", "lots")
        self.assertIsNone(result)
        self.assertIn("renamerFilepathBudget", log.error.call_args[0][0])


def _settings(renamer_path, **overrides):
    settings = {
        "enable_renamer": True,
        "renamer_path": renamer_path,
        "renamer_path_template": "$Title",
        "renamer_filepath_budget": 250,
        "renamer_ignore_files_in_path": True,
        "renamer_enable_mark_organized": False,
        "renamer_multi_file_mode": "all",
        "dry_run": False,
    }
    settings.update(overrides)
    return settings


class TestInTargetDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = os.path.join(self.tmp.name, "lib")
        self.rename = getattr(scene_module, "__rename_videos")

    def _run(self, video_path):
        stash = MagicMock()
        stash.call_GQL.return_value = {"moveFiles": True}
        scene = _scene(files=[{"id": "f1", "path": video_path, "height": 1080, "width": 1920}])
        result = self.rename(scene, stash, _settings(self.lib))
        return result, stash

    def test_in_target_dir_boundary(self):
        """/media/library2/x is not inside renamer_path=/media/lib, so it is moved."""
        video = os.path.join(self.tmp.name, "library2", "x.mp4")
        result, stash = self._run(video)
        stash.call_GQL.assert_called_once()
        self.assertEqual(result, os.path.join(self.lib, "A Title.mp4"))

    def test_inside_target_dir_skipped(self):
        video = os.path.join(self.lib, "Studio", "x.mp4")
        result, stash = self._run(video)
        stash.call_GQL.assert_not_called()
        self.assertEqual(result, video)


STUDIOS = {
    "3": {"id": "3", "name": "Child", "parent_studio": {"id": "2"}},
    "2": {"id": "2", "name": "Parent", "parent_studio": {"id": "1"}},
    "1": {"id": "1", "name": "Grand", "parent_studio": None},
}


def _stash_with_studios(studios):
    stash = MagicMock()
    stash.find_studio.side_effect = lambda studio_id, fragment=None: copy.deepcopy(studios.get(str(studio_id)))
    return stash


class TestStudioHierarchy(unittest.TestCase):
    def setUp(self):
        self.hydrate = getattr(scene_module, "__hydrate_scene")

    def _hydrated(self, studio_id, studios=STUDIOS):
        stash = _stash_with_studios(studios)
        studio = {"id": studio_id} if studio_id else None
        scene = self.hydrate(_scene(performers=[], studio=studio), stash)
        return scene, stash

    def test_deep_studio_hierarchy(self):
        scene, stash = self._hydrated("3")
        self.assertEqual(
            get_new_path(scene, BASE, "$Studios/$Title", 250), _expected("Grand", "Parent", "Child", "A Title.mp4")
        )
        self.assertEqual([c.args[0] for c in stash.find_studio.call_args_list], ["3", "2", "1"])
        for call in stash.find_studio.call_args_list:
            self.assertEqual(call.args[1], "id name parent_studio { id }")

    def test_two_level_hierarchy(self):
        scene, _ = self._hydrated("2")
        self.assertEqual(get_new_path(scene, BASE, "$Studios/$Title", 250), _expected("Grand", "Parent", "A Title.mp4"))

    def test_one_level_hierarchy(self):
        scene, stash = self._hydrated("1")
        self.assertEqual(get_new_path(scene, BASE, "$Studios/$Title", 250), _expected("Grand", "A Title.mp4"))
        self.assertEqual(stash.find_studio.call_count, 1)

    def test_no_studio(self):
        scene, stash = self._hydrated(None)
        self.assertIsNone(scene["studio"])
        stash.find_studio.assert_not_called()
        self.assertEqual(get_new_path(scene, BASE, "{$Studios/}$Title", 250), _expected("A Title.mp4"))

    def test_loop_is_bounded(self):
        looped = {
            "1": {"id": "1", "name": "A", "parent_studio": {"id": "2"}},
            "2": {"id": "2", "name": "B", "parent_studio": {"id": "1"}},
        }
        scene, stash = self._hydrated("1", looped)
        self.assertLessEqual(stash.find_studio.call_count, 10)
        self.assertEqual(get_new_path(scene, BASE, "$Studios/$Title", 250), _expected("B", "A", "A Title.mp4"))

    def test_depth_capped_at_ten(self):
        chain = {
            str(i): {"id": str(i), "name": f"S{i}", "parent_studio": {"id": str(i + 1)}} for i in range(1, 20)
        }
        scene, stash = self._hydrated("1", chain)
        self.assertEqual(stash.find_studio.call_count, 10)
        result = get_new_path(scene, BASE, "$Studios/$Title", 250)
        self.assertEqual(result, _expected(*[f"S{i}" for i in range(10, 0, -1)], "A Title.mp4"))

    def test_windows_studios_separator_unescaped(self):
        studios = getattr(replacer, "__replacer_studios")
        scene = _scene(studio={"name": "Child", "parent_studio": {"name": "Parent", "parent_studio": None}})
        with patch.object(os, "sep", "\\"):
            self.assertEqual(studios(scene), "Parent\\Child")


if __name__ == "__main__":
    unittest.main()
