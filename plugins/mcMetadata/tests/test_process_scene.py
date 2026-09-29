"""
process_scene end to end against a fake Stash, and the plugin entry point.

FakeStash keeps scenes, performers, studios and files in memory and performs moveFiles
as a real move inside a temp dir. Like Stash, it refuses a destination folder outside
its library roots (configuration.general.stashes[].path) and an existing destination.

Run with: python -m pytest tests/test_process_scene.py -v
"""

import copy
import importlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

import scene as scene_module  # noqa: E402
import performer as performer_module  # noqa: E402
import utils.files as files_module  # noqa: E402
import utils.logger as logger  # noqa: E402
from plugin_settings import map_settings  # noqa: E402

JPEG = b"\xff\xd8\xff" + b"\0" * 2000 + b"\xff\xd9"

DRY_PATTERNS = {
    "video": re.compile(r"^\[DRY RUN\]\s+To: (.+)$"),
    "sidecar": re.compile(r"^\[DRY RUN\] Would move sidecar: .+ -> (.+)$"),
    "folder": re.compile(r"^\[DRY RUN\] Would move folder-level file: .+ -> (.+)$"),
    "nfo": re.compile(r"^\[DRY RUN\] Would (?:create|update) NFO: (.+)$"),
    "image": re.compile(r"^\[DRY RUN\] Would download image to: (.+)$"),
}


def _write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _tree(root):
    """Every file under root."""
    found = set()
    for folder, _, names in os.walk(root):
        for name in names:
            found.add(os.path.join(folder, name))
    return found


def _dry_targets(info_lines):
    """Target paths a dry run logged, by kind."""
    targets = {kind: [] for kind in DRY_PATTERNS}
    for line in info_lines:
        for kind, pattern in DRY_PATTERNS.items():
            match = pattern.match(line)
            if match:
                targets[kind].append(match.group(1))
    return targets


def _in_library(folder, root):
    """Stash's rule (fsutil.IsPathInDir): the folder is the root or below it."""
    return not os.path.relpath(folder, root).startswith("..")


class FakeStash:
    def __init__(self, roots, scenes=(), performers=None, studios=None, plugin_config=None, config_error=None):
        self.roots = list(roots)
        self.scenes = {str(s["id"]): copy.deepcopy(s) for s in scenes}
        self.files = {f["id"]: f for s in self.scenes.values() for f in (s["files"] or [])}
        self.performers = performers or {}
        self.studios = studios or {}
        self.plugin_config = plugin_config or {}
        self.config_error = config_error
        self.calls = []

    def get_configuration(self):
        self.calls.append(("get_configuration",))
        if self.config_error:
            raise self.config_error
        return {
            "general": {
                "apiKey": "KEY",
                "stashes": [{"path": r, "excludeVideo": False, "excludeImage": False} for r in self.roots],
            },
            "plugins": {"mcMetadata": dict(self.plugin_config)},
        }

    def find_scene(self, scene_id):
        self.calls.append(("find_scene", str(scene_id)))
        return copy.deepcopy(self.scenes.get(str(scene_id)))

    def find_scenes(self, f=None, filter=None, get_count=False):
        scenes = sorted(self.scenes.values(), key=lambda s: int(s["id"]))
        crit = (f or {}).get("id")
        if crit:
            scenes = [s for s in scenes if int(s["id"]) > int(crit["value"])]
        if get_count:
            return [len(scenes)]
        return copy.deepcopy(scenes[: filter["per_page"]])

    def find_performer(self, performer_id, create=False, fragment=None):
        return copy.deepcopy(self.performers.get(str(performer_id)))

    def find_studio(self, studio_id, fragment=None):
        return copy.deepcopy(self.studios.get(str(studio_id)))

    def update_scene(self, update_input):
        self.calls.append(("update_scene", update_input))

    def call_GQL(self, query, variables=None):
        if "moveFiles" not in query:
            raise NotImplementedError(query)
        data = variables["input"]
        folder = data["destination_folder"]
        self.calls.append(("moveFiles", folder, data["destination_basename"]))
        if not any(_in_library(folder, r) for r in self.roots):
            raise RuntimeError(f"folder path {folder} must be within a stash library path")
        for file_id in data["ids"]:
            file_info = self.files[file_id]
            dest = os.path.join(folder, data["destination_basename"])
            if os.path.exists(dest):
                raise RuntimeError(f"file {dest} already exists")
            os.makedirs(folder, exist_ok=True)
            os.rename(file_info["path"], dest)
            file_info["path"] = dest
        return {"moveFiles": True}

    def moves(self):
        return [c for c in self.calls if c[0] == "moveFiles"]


class _Response:
    status = 200

    def __init__(self, data):
        self.data = data
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(data))}

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.library = os.path.join(self.tmp, "library")
        self.incoming = os.path.join(self.library, "incoming")
        self.organized = os.path.join(self.library, "organized")
        self.new_folder = os.path.join(self.organized, "Acme")
        os.makedirs(self.incoming)
        self.urls = []

    def settings(self, **config):
        base = {
            "enableRenamer": True,
            "renamerPath": self.organized,
            "renamerPathTemplate": "$Studio/$Title",
            "renamerMarkOrganized": False,
            "dryRun": False,
        }
        base.update(config)
        settings = map_settings(base)
        settings["data_dir"] = os.path.join(self.tmp, "data")
        return settings

    def make_scene(self, *videos, performers=(), scene_id="5"):
        return {
            "id": scene_id, "title": "New Name", "details": "", "date": "2024-01-15", "rating100": None,
            "organized": False, "studio": {"id": "1"}, "performers": [{"id": p} for p in performers],
            "tags": [], "stash_ids": [],
            "paths": {"screenshot": f"http://stash.invalid/scene/{scene_id}/screenshot?t=1"},
            "files": [{"id": f"f{scene_id}-{i}", "path": v, "height": 1080, "width": 1920}
                      for i, v in enumerate(videos)],
        }

    def fake(self, scenes, **kwargs):
        kwargs.setdefault("studios", {"1": {"id": "1", "name": "Acme", "parent_studio": None}})
        return FakeStash([self.library], scenes, **kwargs)

    def _urlopen(self, request, timeout=None):
        self.urls.append(request.full_url)
        return _Response(JPEG)

    def run_scene(self, stash, settings, scene_id="5"):
        """process_scene on the stored scene; returns (result, {level: [messages]})."""
        lines = {"info": [], "warning": [], "debug": [], "error": []}
        with patch.object(logger, "info", side_effect=lines["info"].append), \
             patch.object(logger, "warning", side_effect=lines["warning"].append), \
             patch.object(logger, "debug", side_effect=lines["debug"].append), \
             patch.object(logger, "error", side_effect=lines["error"].append), \
             patch.object(files_module.urllib.request, "urlopen", side_effect=self._urlopen):
            result = scene_module.process_scene(stash.find_scene(scene_id), stash, settings, "KEY")
        return result, lines


class TestNoneGuards(_Base):
    def test_scene_with_no_files_is_skipped(self):
        for files in ([], None):
            with self.subTest(files=files):
                scene = self.make_scene()
                scene["files"] = files
                stash = self.fake([scene])
                result, lines = self.run_scene(stash, self.settings())
                self.assertEqual(result, "no_files")
                self.assertTrue(any("Scene 5" in w and "no files" in w for w in lines["warning"]), lines)
                self.assertEqual(lines["error"], [])
                self.assertEqual(stash.moves(), [])

                with patch.object(logger, "info"), patch.object(logger, "warning"):
                    summary = scene_module.process_all_scenes(stash, self.settings(), "KEY")
                self.assertEqual(summary["processed"], 0)
                self.assertEqual(summary["errors"], 0)
                self.assertEqual(summary["skipped"], {"no_files": 1})
                self.assertEqual(summary["samples"], [("5", "no_files")])

    def test_deleted_performer_ignored(self):
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        performers = {"1": {"id": "1", "name": "Jane", "gender": "FEMALE", "image_path": None}}
        stash = self.fake([self.make_scene(video, performers=("1", "2"))], performers=performers)
        result, lines = self.run_scene(stash, self.settings())
        self.assertIsNone(result)
        self.assertEqual(lines["error"], [])
        self.assertTrue(any("performer 2" in d.lower() for d in lines["debug"]), lines["debug"])
        nfo = os.path.join(self.new_folder, "New Name.nfo")
        with open(nfo, encoding="utf-8-sig") as f:
            text = f.read()
        self.assertEqual(text.count("<actor>"), 1)
        self.assertIn("<name>Jane</name>", text)


class TestDryRunParity(_Base):
    def _compare(self, videos, settings_config=None, stash_kwargs=None):
        """Dry run then live run on the same files; returns (dry targets, live-created files, live lines)."""
        settings_config = settings_config or {}
        stash_kwargs = stash_kwargs or {}
        scene = self.make_scene(*videos)

        before = _tree(self.library)
        stash = self.fake([scene], **stash_kwargs)
        _, dry_lines = self.run_scene(stash, self.settings(dryRun=True, **settings_config))
        self.assertEqual(_tree(self.library), before, "a dry run changed files")
        self.assertEqual(stash.moves(), [])
        self.assertEqual(self.urls, [])
        dry = _dry_targets(dry_lines["info"])

        stash = self.fake([scene], **stash_kwargs)
        _, live_lines = self.run_scene(stash, self.settings(dryRun=False, **settings_config))
        created = _tree(self.library) - before
        return dry, dry_lines, created, live_lines

    def test_dry_run_targets_match_live_run(self):
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old.en.srt", "old.nfo"):
            _write(os.path.join(self.incoming, name), name)
        dry, dry_lines, created, live_lines = self._compare([video])

        new = os.path.join(self.new_folder, "New Name")
        self.assertEqual(dry["video"], [new + ".mp4"])
        self.assertEqual(sorted(dry["sidecar"]), [new + ".en.srt", new + ".nfo"])
        self.assertEqual(dry["nfo"], [new + ".nfo"])
        self.assertEqual(dry["image"], [new + "-poster.jpg"])
        all_dry = {p for paths in dry.values() for p in paths}
        self.assertEqual(all_dry, created)
        # The relocated NFO is at the new path before it is rewritten, in both runs
        self.assertIn(f"[DRY RUN] Would update NFO: {new}.nfo", dry_lines["info"])
        self.assertIn(f"Updated NFO file: {new}.nfo", live_lines["info"])

    def test_dry_run_existing_poster_moves_instead_of_downloading(self):
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old-poster.jpg"):
            _write(os.path.join(self.incoming, name), name)
        dry, _, created, _ = self._compare([video])
        new = os.path.join(self.new_folder, "New Name")
        self.assertEqual(dry["image"], [])
        self.assertEqual(dry["sidecar"], [new + "-poster.jpg"])
        self.assertEqual({p for paths in dry.values() for p in paths}, created)

    def test_dry_run_folder_level_names_match_live_run(self):
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        _write(os.path.join(self.new_folder, "Other.mp4"))
        dry, _, created, _ = self._compare(
            [video], {"nfoFilename": "movie.nfo", "posterFilename": "poster.jpg"})
        # The folder then holds two videos, so folder-level names are skipped in both runs
        self.assertEqual(dry["nfo"], [])
        self.assertEqual(dry["image"], [])
        self.assertEqual(created, {os.path.join(self.new_folder, "New Name.mp4")})
        self.assertEqual({p for paths in dry.values() for p in paths}, created)

    def test_dry_run_reports_outside_library(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        dry, dry_lines, created, live_lines = self._compare([video], {"renamerPath": elsewhere})
        target = os.path.join(elsewhere, "Acme", "New Name.mp4")
        self.assertIn(f"[DRY RUN] Would be refused by Stash (outside every library): {target}", dry_lines["info"])
        self.assertEqual(dry["video"], [])
        # The video stays, so the NFO and poster go next to it, in both runs
        self.assertEqual(dry["nfo"], [os.path.join(self.incoming, "old.nfo")])
        self.assertEqual({p for paths in dry.values() for p in paths}, created)
        self.assertTrue(os.path.exists(video))
        self.assertFalse(os.path.exists(elsewhere))
        self.assertTrue(any(target in w and "library" in w for w in live_lines["warning"]), live_lines)

    def test_live_run_does_not_call_move_files_outside_library(self):
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        stash = self.fake([self.make_scene(video)])
        self.run_scene(stash, self.settings(renamerPath=os.path.join(self.tmp, "elsewhere")))
        self.assertEqual(stash.moves(), [])

    def test_unreadable_library_roots_do_not_block_moves(self):
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        stash = self.fake([self.make_scene(video)], config_error=RuntimeError("no config"))
        _, lines = self.run_scene(stash, self.settings())
        self.assertEqual(len(stash.moves()), 1)
        self.assertTrue(os.path.exists(os.path.join(self.new_folder, "New Name.mp4")))
        self.assertTrue(any("librar" in w for w in lines["warning"]), lines["warning"])

    def test_library_roots_fetched_once_per_run(self):
        scenes = []
        for i in range(1, 4):
            video = os.path.join(self.incoming, f"old{i}.mp4")
            _write(video)
            scene = self.make_scene(video, scene_id=str(i))
            scene["title"] = f"Scene {i}"
            scenes.append(scene)
        stash = self.fake(scenes)
        with patch.object(logger, "info"), patch.object(logger, "debug"), \
             patch.object(files_module.urllib.request, "urlopen", side_effect=self._urlopen):
            summary = scene_module.process_all_scenes(stash, self.settings(), "KEY")
        self.assertEqual(summary["processed"], 3)
        self.assertEqual(len(stash.moves()), 3)
        self.assertEqual(stash.calls.count(("get_configuration",)), 1)


class TestDryRunHelper(_Base):
    def test_dry_run_null_is_dry(self):
        from utils.run_flow import is_dry_run

        for value in (None, True, 0, "false", ""):
            self.assertTrue(is_dry_run({"dry_run": value}), value)
        self.assertTrue(is_dry_run({}))
        self.assertTrue(is_dry_run(None))
        self.assertFalse(is_dry_run({"dry_run": False}))
        self.assertIs(map_settings({"dryRun": None})["dry_run"], True)

        # A whole scene with dry_run None: nothing moves, downloads or gets written
        video = os.path.join(self.incoming, "old.mp4")
        for name in ("old.mp4", "old.srt"):
            _write(os.path.join(self.incoming, name))
        stash = self.fake([self.make_scene(video, performers=("1",))],
                          performers={"1": {"id": "1", "name": "Jane", "gender": "FEMALE",
                                            "image_path": "http://stash.invalid/performer/1/image?t=1"}})
        settings = self.settings(enableActorImages=True, actorMetadataPath=os.path.join(self.library, "people"),
                                 renamerMarkOrganized=True)
        settings["dry_run"] = None
        before = _tree(self.library)
        _, lines = self.run_scene(stash, settings)
        self.assertEqual(_tree(self.library), before)
        self.assertEqual(stash.moves(), [])
        self.assertNotIn("update_scene", [c[0] for c in stash.calls])
        self.assertEqual(self.urls, [])
        self.assertTrue(any(line.startswith("[DRY RUN]") for line in lines["info"]))

        # Each helper on its own
        src, dst = os.path.join(self.incoming, "old.srt"), os.path.join(self.incoming, "new.srt")
        self.assertEqual(files_module.rename_file(src, dst, {"dry_run": None}), dst)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dst))
        with patch.object(files_module.urllib.request, "urlopen", side_effect=self._urlopen):
            files_module.download_image("http://stash.invalid/x", os.path.join(self.tmp, "x.jpg"), {"dry_run": None})
        self.assertEqual(self.urls, [])
        with patch.object(performer_module, "download_image") as dl:
            performer_module.process_performer(
                {"name": "Jane", "image_path": "http://stash.invalid/p"},
                {"dry_run": None, "enable_actor_images": True, "media_server": "jellyfin",
                 "actor_metadata_path": os.path.join(self.tmp, "people")}, "KEY")
            performer_module.process_performer_hook(
                MagicMock(**{"find_performer.return_value": {"id": "1", "name": "Jane", "image_path": "x"}}),
                "1", {"dry_run": None, "enable_actor_images": True}, "KEY")
        dl.assert_not_called()

        # One helper: no module reads the setting itself
        direct = re.compile(r"""\[\s*["']dry_run["']\s*\]|\.get\(\s*["']dry_run["']""")
        for rel in ("mcMetadata.py", "scene.py", "performer.py", os.path.join("utils", "files.py")):
            with open(os.path.join(PLUGIN_DIR, rel), encoding="utf-8") as f:
                self.assertIsNone(direct.search(f.read()), f"{rel} reads dry_run directly")


class TestEntryPoint(_Base):
    def _entry(self):
        """Import mcMetadata.py fresh with a stdin that must not be read."""
        stdin = MagicMock()
        stdin.read.side_effect = AssertionError("mcMetadata read stdin at import")
        sys.modules.pop("mcMetadata", None)
        with patch.object(sys, "stdin", stdin):
            return importlib.import_module("mcMetadata")

    def _stdin(self, hook_type="Scene.Update.Post", scene_id="5"):
        return json.dumps({
            "server_connection": {"Scheme": "http", "Host": "stash.invalid", "Port": 9999,
                                  "Dir": os.path.join(self.tmp, "config")},
            "args": {"hookContext": {"type": hook_type, "id": scene_id,
                                     "input": {"id": scene_id, "title": "x"}, "inputFields": ["id", "title"]}},
        })

    def _run(self, entry, stash):
        out = io.StringIO()
        with patch.object(entry, "stashapi_problem", return_value=None), redirect_stdout(out), \
             patch.object(logger, "info"), patch.object(logger, "debug"), patch.object(logger, "warning"), \
             patch.object(files_module.urllib.request, "urlopen", side_effect=self._urlopen):
            entry.run(self._stdin(), stash_factory=lambda connection: stash)
        return out.getvalue()

    def test_hook_main_smoke(self):
        entry = self._entry()
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        stash = self.fake([self.make_scene(video)], plugin_config={"enableHook": False, "enableRenamer": True,
                                                                   "renamerPath": self.organized, "dryRun": False})
        out = self._run(entry, stash)
        self.assertEqual(json.loads(out), {"output": "ok"})
        self.assertNotIn(("find_scene", "5"), stash.calls)
        self.assertEqual(stash.moves(), [])
        self.assertTrue(os.path.exists(video))

    def test_hook_main_processes_scene(self):
        entry = self._entry()
        video = os.path.join(self.incoming, "old.mp4")
        _write(video)
        stash = self.fake([self.make_scene(video)], plugin_config={
            "enableHook": True, "enableRenamer": True, "renamerPath": self.organized,
            "renamerPathTemplate": "$Studio/$Title", "renamerMarkOrganized": False, "dryRun": False})
        out = self._run(entry, stash)
        self.assertEqual(json.loads(out), {"output": "ok"})
        self.assertTrue(os.path.exists(os.path.join(self.new_folder, "New Name.mp4")))
        self.assertTrue(os.path.exists(os.path.join(self.new_folder, "New Name.nfo")))

    def test_missing_stashapi_reports_error_json(self):
        entry = self._entry()
        out, err = io.StringIO(), io.StringIO()
        factory = MagicMock()
        with patch.object(entry, "stashapi_problem", return_value="stashapp-tools missing"), \
             redirect_stdout(out), patch.object(sys, "stderr", err):
            entry.run(self._stdin(), stash_factory=factory)
        self.assertEqual(json.loads(out.getvalue()), {"error": "stashapp-tools missing"})
        self.assertIn("\x01e\x02stashapp-tools missing", err.getvalue())
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
