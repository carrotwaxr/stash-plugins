import os
import unittest
from utils.files import rename_file, replace_file_ext
from utils.nfo import build_nfo_xml
from utils.replacer import get_new_path
from utils.settings import validate_media_server, validate_settings

SEP = os.path.sep

MOCK_BASE_PATH = f"{SEP}data{SEP}tagged{SEP}"
MOCK_SCENE = {
    "id": 1337,
    "date": "2022-03-14",
    "details": "Hot spicy kitchen sex",
    "files": [
        {
            "height": 1080,
            "path": f"{MOCK_BASE_PATH}Studio{SEP}someFile.mp4",
            "width": 1920,
        }
    ],
    "performers": [
        {
            "gender": "FEMALE",
            "name": "Jayden Jaymes",
        },
        {
            "gender": "MALE",
            "name": "Alec Knight",
        },
        {
            "gender": None,
            "name": "Untagged Performer",
        },
    ],
    "rating100": 80,
    "stash_ids": [{"stash_id": "4562"}],
    "studio": {
        "name": "Brazzers",
        "parent_studio": {"name": "MindGeek", "parent_studio": None},
    },
    "tags": [{"name": "Threesome"}, {"name": "Rough"}],
    "title": "Episode Title",
}
MOCK_TEMPLATE = f"$Studio{SEP}$Title - $FemalePerformers $MalePerformers $ReleaseDate [WEBDL-$Resolution]"
MOCK_SETTINGS = {
    "actor_metadata_path": f"{SEP}mc{SEP}metadata{SEP}people{SEP}",
    "dry_run": False,
    "enable_actor_images": False,
    "enable_hook": True,
    "enable_renamer": True,
    "media_server": "jellyfin",
    "renamer_enable_mark_organized": True,
    "renamer_filename_budget": 500,
    "renamer_ignore_files_in_path": False,
    "renamer_path": MOCK_BASE_PATH,
    "renamer_path_template": MOCK_TEMPLATE,
}


class TestFiles(unittest.TestCase):
    def test_rename(self):
        result = rename_file(
            MOCK_SCENE["files"][0]["path"],
            MOCK_SCENE["files"][0]["path"],
            {"dry_run": True},
        )
        self.assertEqual(
            result,
            MOCK_SCENE["files"][0]["path"],
            "Expected filepath returned when rename succeeds",
        )

    def test_rename_fail(self):
        result = rename_file(
            MOCK_SCENE["files"][0]["path"],
            MOCK_SCENE["files"][0]["path"],
            {},
        )
        self.assertEqual(result, False, "Rename should return False when it fails")

    def test_replace_file_ext(self):
        self.assertEqual(
            replace_file_ext(MOCK_SCENE["files"][0]["path"], "jpg"),
            f"{SEP}data{SEP}tagged{SEP}Studio{SEP}someFile.jpg",
            "File extension not replaced correctly",
        )
        self.assertEqual(
            replace_file_ext(MOCK_SCENE["files"][0]["path"], "jpg", "-poster"),
            f"{SEP}data{SEP}tagged{SEP}Studio{SEP}someFile-poster.jpg",
            "File extension not replaced correctly",
        )


class TestNFO(unittest.TestCase):
    def test_required(self):
        mock_scene = MOCK_SCENE.copy()
        mock_scene["date"] = None
        mock_scene["details"] = None
        mock_scene["performers"] = []
        mock_scene["rating100"] = None
        mock_scene["studio"] = None
        mock_scene["tags"] = []
        mock_scene["title"] = None
        result = build_nfo_xml(mock_scene)

        self.assertEqual(
            result,
            """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
    <name>someFile.mp4</name>
    <title>someFile.mp4</title>
    <originaltitle>someFile.mp4</originaltitle>
    <sorttitle>someFile.mp4</sorttitle>
    <criticrating></criticrating>
    <rating></rating>
    <userrating></userrating>
    <plot><![CDATA[]]></plot>
    <premiered></premiered>
    <releasedate></releasedate>
    <year></year>
    <studio></studio>
    <genre>Adult</genre>
    <uniqueid type="stash">1337</uniqueid>
</movie>""",
            "The generated XML is wrong",
        )

    def test_all(self):
        result = build_nfo_xml(MOCK_SCENE)

        self.assertEqual(
            result,
            """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
    <name>Episode Title</name>
    <title>Episode Title</title>
    <originaltitle>Episode Title</originaltitle>
    <sorttitle>Episode Title</sorttitle>
    <criticrating>80</criticrating>
    <rating>8</rating>
    <userrating>8</userrating>
    <plot><![CDATA[Hot spicy kitchen sex]]></plot>
    <premiered>2022-03-14</premiered>
    <releasedate>2022-03-14</releasedate>
    <year>2022</year>
    <studio>Brazzers</studio>
    <actor>
        <name>Jayden Jaymes</name>
        <role>Jayden Jaymes</role>
        <order>0</order>
        <type>Actor</type>
    </actor>
    <actor>
        <name>Alec Knight</name>
        <role>Alec Knight</role>
        <order>1</order>
        <type>Actor</type>
    </actor>
    <actor>
        <name>Untagged Performer</name>
        <role>Untagged Performer</role>
        <order>2</order>
        <type>Actor</type>
    </actor>
    <genre>Adult</genre>
    <tag>Threesome</tag>
    <tag>Rough</tag>
    <uniqueid type="stash">1337</uniqueid>
</movie>""",
            "The generated XML is wrong",
        )

    def test_missing_field_in_scene(self):
        mock_scene = MOCK_SCENE.copy()
        mock_scene.pop("id")

        with self.assertRaises(KeyError):
            build_nfo_xml(mock_scene)


class TestReplacers(unittest.TestCase):
    def test_all(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        result = get_new_path(MOCK_SCENE, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [FHD-1080p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_all_truncated(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        result = get_new_path(MOCK_SCENE, MOCK_BASE_PATH, template, 160)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec 2022-03-14 [FHD-1080p].mp4",
            "The path is wrong",
        )

    def test_cannot_truncate(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["title"] = (
            "Some Incredibly, Like Really Long Title, Too Long To Be Truncated With This Budget"
        )
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 160)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_settings(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        result = get_new_path(MOCK_SCENE, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_file(self):
        template = "$ReleaseDate"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_release_date(self):
        template = "$ReleaseDate"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["date"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_release_year(self):
        template = "$ReleaseYear"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["date"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_stash_id(self):
        template = "$StashID"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["stash_ids"] = []
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_studio(self):
        template = "$Studio"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["studio"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_studios(self):
        template = "$Studios"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["studio"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_invalid_scene_title(self):
        template = "$Title"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["title"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 100)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_height_none(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_height_quality_none(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = None
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            False,
            "Replacer should have returned False when not renaming",
        )

    def test_height_low(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 360
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [LOW-360p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_480(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 480
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [SD-480p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_720(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 720
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [HD-720p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_1080(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 1080
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [FHD-1080p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_1080_wide(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 1080
        mock_scene["files"][0]["width"] = 2048
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [2K-1080p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_1440(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 1440
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [QHD-1440p] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_4K(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 2160
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [UHD-4K] Threesome Rough.mp4",
            "The path is wrong",
        )

    def test_height_8K(self):
        template = f"$Studios{SEP}$Studio - $StashID - $Title ($ReleaseYear) - $FemalePerformers $MalePerformers $Performers $ReleaseDate [$Quality-$Resolution] $Tags"
        mock_scene = MOCK_SCENE.copy()
        mock_scene["files"][0]["height"] = 4320
        result = get_new_path(mock_scene, MOCK_BASE_PATH, template, 500)
        self.assertEqual(
            result,
            f"{SEP}data{SEP}tagged{SEP}MindGeek{SEP}Brazzers{SEP}Brazzers - 4562 - Episode Title (2022) - Jayden Jaymes Alec Knight Jayden Jaymes Alec Knight Untagged Performer 2022-03-14 [FUHD-8K] Threesome Rough.mp4",
            "The path is wrong",
        )


class TestSettings(unittest.TestCase):
    def test_valid_config(self):
        self.assertEqual(
            validate_settings(MOCK_SETTINGS),
            True,
            "Validate should return True with valid settings",
        )

    def test_invalid_boolean(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["enable_renamer"] = None
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with an invalid boolean",
        )

    def test_invalid_filename_budget(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["renamer_filename_budget"] = 39
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with an invalid renamer_filename_budget",
        )

    def test_invalid_media_center(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["enable_actor_images"] = True
        mock_settings["media_server"] = "wombat"
        with self.assertRaises(ValueError):
            validate_media_server(mock_settings)

    def test_invalid_template(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["renamer_path_template"] = "$StashID - $Title <$Performers>"
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with an invalid renamer_path_template",
        )

    def test_invalid_uniqueness(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["renamer_path_template"] = "$Title $Performers"
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with an invalid renamer_path_template",
        )

    def test_missing_required_setting(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings.pop("dry_run")
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with a missing required key",
        )

    def test_missing_required_performer_setting(self):
        mock_settings = MOCK_SETTINGS.copy()
        mock_settings["enable_actor_images"] = True
        mock_settings.pop("actor_metadata_path")
        self.assertEqual(
            validate_settings(mock_settings),
            False,
            "Validate should return False with a missing required key",
        )


if __name__ == "__main__":
    unittest.main()
