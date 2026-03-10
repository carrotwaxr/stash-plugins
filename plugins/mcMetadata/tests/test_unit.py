"""
Unit tests for mcMetadata plugin.

These tests don't require a Stash connection and test individual functions.
Run with: python -m pytest tests/test_unit.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock stashapi before importing any plugin modules (not available outside Stash runtime)
sys.modules["stashapi"] = MagicMock()
sys.modules["stashapi.log"] = MagicMock()

from utils.nfo import escape_xml, build_nfo_xml
from utils.replacer import get_new_path, resolve_conditionals


class TestEscapeXml(unittest.TestCase):
    """Test XML escape function."""

    def test_escape_ampersand(self):
        """Ampersands should be escaped."""
        self.assertEqual(escape_xml("Tom & Jerry"), "Tom &amp; Jerry")

    def test_escape_less_than(self):
        """Less than signs should be escaped."""
        self.assertEqual(escape_xml("a < b"), "a &lt; b")

    def test_escape_greater_than(self):
        """Greater than signs should be escaped."""
        self.assertEqual(escape_xml("a > b"), "a &gt; b")

    def test_escape_quotes(self):
        """Quotes should be escaped."""
        self.assertEqual(escape_xml('He said "hello"'), "He said &quot;hello&quot;")

    def test_escape_apostrophe(self):
        """Apostrophes should be escaped."""
        self.assertEqual(escape_xml("It's fine"), "It&apos;s fine")

    def test_escape_multiple_special_chars(self):
        """Multiple special characters should all be escaped."""
        result = escape_xml("Tom & Jerry's \"Big\" Adventure < 2024 >")
        self.assertEqual(
            result,
            "Tom &amp; Jerry&apos;s &quot;Big&quot; Adventure &lt; 2024 &gt;"
        )

    def test_escape_none(self):
        """None should return empty string."""
        self.assertEqual(escape_xml(None), "")

    def test_escape_empty_string(self):
        """Empty string should return empty string."""
        self.assertEqual(escape_xml(""), "")

    def test_escape_no_special_chars(self):
        """Strings without special chars should be unchanged."""
        self.assertEqual(escape_xml("Normal Title"), "Normal Title")


class TestBuildNfoXml(unittest.TestCase):
    """Test NFO XML generation."""

    def setUp(self):
        """Create a mock scene for testing."""
        self.mock_scene = {
            "id": "123",
            "title": "Test Scene",
            "details": "A test scene description",
            "date": "2024-01-15",
            "rating100": 80,
            "studio": {"name": "Test Studio"},
            "performers": [
                {"name": "Jane Doe"},
                {"name": "John Smith"}
            ],
            "tags": [
                {"name": "Tag1"},
                {"name": "Tag2"}
            ],
            "files": [{"path": "/path/to/video.mp4"}]
        }

    def test_basic_nfo_generation(self):
        """Basic NFO should be generated with all fields."""
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn('<?xml version="1.0" encoding="utf-8"', nfo)
        self.assertIn("<title>Test Scene</title>", nfo)
        self.assertIn("<studio>Test Studio</studio>", nfo)
        self.assertIn("<premiered>2024-01-15</premiered>", nfo)
        self.assertIn("<year>2024</year>", nfo)
        self.assertIn("<name>Jane Doe</name>", nfo)
        self.assertIn("<name>John Smith</name>", nfo)
        self.assertIn("<tag>Tag1</tag>", nfo)
        self.assertIn("<tag>Tag2</tag>", nfo)
        self.assertIn('<uniqueid type="stash">123</uniqueid>', nfo)

    def test_nfo_escapes_ampersand_in_title(self):
        """Ampersands in title should be escaped (Issue #9)."""
        self.mock_scene["title"] = "Tom & Jerry"
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<title>Tom &amp; Jerry</title>", nfo)
        self.assertNotIn("<title>Tom & Jerry</title>", nfo)

    def test_nfo_escapes_ampersand_in_studio(self):
        """Ampersands in studio name should be escaped."""
        self.mock_scene["studio"]["name"] = "Bang & Olufsen Studios"
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<studio>Bang &amp; Olufsen Studios</studio>", nfo)

    def test_nfo_escapes_ampersand_in_performer(self):
        """Ampersands in performer names should be escaped."""
        self.mock_scene["performers"] = [{"name": "Jack & Jill"}]
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<name>Jack &amp; Jill</name>", nfo)

    def test_nfo_escapes_ampersand_in_tag(self):
        """Ampersands in tag names should be escaped."""
        self.mock_scene["tags"] = [{"name": "Leather & Lace"}]
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<tag>Leather &amp; Lace</tag>", nfo)

    def test_nfo_handles_missing_title(self):
        """Missing title should use filename."""
        self.mock_scene["title"] = None
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<title>video.mp4</title>", nfo)

    def test_nfo_handles_empty_title(self):
        """Empty title should use filename."""
        self.mock_scene["title"] = ""
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<title>video.mp4</title>", nfo)

    def test_nfo_handles_missing_studio(self):
        """Missing studio should not crash."""
        self.mock_scene["studio"] = None
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<studio></studio>", nfo)

    def test_nfo_handles_missing_date(self):
        """Missing date should produce empty fields."""
        self.mock_scene["date"] = None
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<premiered></premiered>", nfo)
        self.assertIn("<year></year>", nfo)

    def test_nfo_handles_no_performers(self):
        """No performers should not crash."""
        self.mock_scene["performers"] = []
        nfo = build_nfo_xml(self.mock_scene)

        self.assertNotIn("<actor>", nfo)

    def test_nfo_handles_no_tags(self):
        """No tags should not crash."""
        self.mock_scene["tags"] = []
        nfo = build_nfo_xml(self.mock_scene)

        # Should still have the Adult genre
        self.assertIn("<genre>Adult</genre>", nfo)

    def test_nfo_plot_uses_cdata(self):
        """Plot/details should use CDATA (already implemented)."""
        self.mock_scene["details"] = "Some <description> with & special chars"
        nfo = build_nfo_xml(self.mock_scene)

        # Details are wrapped in CDATA so don't need escaping
        self.assertIn("<![CDATA[Some <description> with & special chars]]>", nfo)


class TestPerformerImagePath(unittest.TestCase):
    """Test performer image path generation for different media servers."""

    def test_jellyfin_path_has_letter_subfolder(self):
        """Jellyfin should use A-Z letter subfolders."""
        from performer import get_actor_image_path

        settings = {
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        path = get_actor_image_path("Jane Doe", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        self.assertEqual(path, "/metadata/People/J/Jane Doe/folder.jpg")

    def test_emby_path_no_letter_subfolder(self):
        """Emby should NOT use A-Z letter subfolders (Issue #11)."""
        from performer import get_actor_image_path

        settings = {
            "media_server": "emby",
            "actor_metadata_path": "/metadata/People"
        }

        path = get_actor_image_path("Jane Doe", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        # Should be directly in People folder, not People/j/
        self.assertEqual(path, "/metadata/People/Jane Doe/folder.jpg")

    def test_emby_path_differs_from_jellyfin(self):
        """Emby and Jellyfin should produce different paths (Issue #11)."""
        from performer import get_actor_image_path

        base_settings = {"actor_metadata_path": "/metadata/People"}

        jellyfin_path = get_actor_image_path(
            "Jane Doe", {**base_settings, "media_server": "jellyfin"}
        ).replace("\\", "/")

        emby_path = get_actor_image_path(
            "Jane Doe", {**base_settings, "media_server": "emby"}
        ).replace("\\", "/")

        # Jellyfin has letter subfolder, Emby doesn't
        self.assertIn("/J/", jellyfin_path)
        self.assertNotIn("/J/", emby_path)
        self.assertNotIn("/j/", emby_path)

    def test_jellyfin_uses_name_first_letter(self):
        """Jellyfin should use the first letter of the name."""
        from performer import get_actor_image_path

        settings = {
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        path = get_actor_image_path("alice smith", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        # First letter should be lowercase 'a' since that's the name
        self.assertEqual(path, "/metadata/People/a/alice smith/folder.jpg")

    def test_plex_returns_none(self):
        """Plex should return None (no external performer image support)."""
        from performer import get_actor_image_path

        settings = {
            "media_server": "plex",
            "actor_metadata_path": "/metadata/People"
        }

        path = get_actor_image_path("Jane Doe", settings)
        self.assertIsNone(path)

    def test_unknown_server_returns_none(self):
        """Unknown media server should return None."""
        from performer import get_actor_image_path

        settings = {
            "media_server": "unknown",
            "actor_metadata_path": "/metadata/People"
        }

        path = get_actor_image_path("Jane Doe", settings)
        self.assertIsNone(path)


class TestNfoArtworkReferences(unittest.TestCase):
    """Test NFO artwork thumb tags."""

    def setUp(self):
        self.mock_scene = {
            "id": "123",
            "title": "Test Scene",
            "details": "A test scene",
            "date": "2024-01-15",
            "rating100": 80,
            "studio": {"name": "Test Studio"},
            "performers": [
                {"name": "Jane Doe"},
                {"name": "John Smith"}
            ],
            "tags": [{"name": "Tag1"}],
            "files": [{"path": "/videos/Test Scene.mp4"}]
        }

    def test_poster_thumb_when_video_path_provided(self):
        """NFO should include poster thumb tag when video_path is given."""
        nfo = build_nfo_xml(self.mock_scene, video_path="/videos/Test Scene.mp4")

        self.assertIn('<thumb aspect="poster">Test Scene-poster.jpg</thumb>', nfo)

    def test_no_poster_thumb_without_video_path(self):
        """NFO should not include poster thumb when video_path is None."""
        nfo = build_nfo_xml(self.mock_scene)

        self.assertNotIn('<thumb aspect="poster">', nfo)

    def test_actor_thumb_with_jellyfin_settings(self):
        """Actor blocks should include thumb tags when actor images enabled."""
        settings = {
            "enable_actor_images": True,
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        nfo = build_nfo_xml(self.mock_scene, settings=settings)

        self.assertIn("<thumb>/metadata/People/J/Jane Doe/folder.jpg</thumb>", nfo)
        self.assertIn("<thumb>/metadata/People/J/John Smith/folder.jpg</thumb>", nfo)

    def test_no_actor_thumb_without_settings(self):
        """Actor blocks should not include thumb tags when no settings."""
        nfo = build_nfo_xml(self.mock_scene)

        self.assertNotIn("<thumb>", nfo)

    def test_no_actor_thumb_when_images_disabled(self):
        """Actor blocks should not include thumb tags when actor images disabled."""
        settings = {
            "enable_actor_images": False,
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        nfo = build_nfo_xml(self.mock_scene, settings=settings)

        # Should not have actor thumbs (poster thumb also absent without video_path)
        self.assertNotIn("<thumb>", nfo)

    def test_no_actor_thumb_for_plex(self):
        """Plex should not generate actor thumb tags (no People folder support)."""
        settings = {
            "enable_actor_images": True,
            "media_server": "plex",
            "actor_metadata_path": "/metadata/People"
        }

        nfo = build_nfo_xml(self.mock_scene, settings=settings)

        # Actor blocks should exist but without thumb tags
        self.assertIn("<name>Jane Doe</name>", nfo)
        self.assertNotIn("<thumb>", nfo)

    def test_backward_compatible_no_args(self):
        """build_nfo_xml() should work with just scene arg (backward compatible)."""
        nfo = build_nfo_xml(self.mock_scene)

        self.assertIn("<title>Test Scene</title>", nfo)
        self.assertNotIn("<thumb", nfo)


class TestRequireStashIdSetting(unittest.TestCase):
    """Test the requireStashId setting behavior for hook processing (Issue #14)."""

    def test_should_process_scene_without_stash_id_when_require_disabled(self):
        """Scene without stash_id should be processed when requireStashId is OFF."""
        settings = {"require_stash_id": False}
        scene = {"id": "123", "title": "Local Scene", "stash_ids": []}

        # Logic from mcMetadata.py hook handler
        require_stash_id = settings.get("require_stash_id", False)
        stash_ids = scene.get("stash_ids", [])
        should_skip = require_stash_id and not stash_ids

        self.assertFalse(should_skip)

    def test_should_skip_scene_without_stash_id_when_require_enabled(self):
        """Scene without stash_id should be skipped when requireStashId is ON."""
        settings = {"require_stash_id": True}
        scene = {"id": "123", "title": "Local Scene", "stash_ids": []}

        require_stash_id = settings.get("require_stash_id", False)
        stash_ids = scene.get("stash_ids", [])
        should_skip = require_stash_id and not stash_ids

        self.assertTrue(should_skip)

    def test_should_process_scene_with_stash_id_when_require_enabled(self):
        """Scene with stash_id should be processed even when requireStashId is ON."""
        settings = {"require_stash_id": True}
        scene = {
            "id": "123",
            "title": "StashDB Scene",
            "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": "abc123"}]
        }

        require_stash_id = settings.get("require_stash_id", False)
        stash_ids = scene.get("stash_ids", [])
        should_skip = require_stash_id and not stash_ids

        self.assertFalse(should_skip)

    def test_should_process_scene_with_stash_id_when_require_disabled(self):
        """Scene with stash_id should be processed when requireStashId is OFF."""
        settings = {"require_stash_id": False}
        scene = {
            "id": "123",
            "title": "StashDB Scene",
            "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": "abc123"}]
        }

        require_stash_id = settings.get("require_stash_id", False)
        stash_ids = scene.get("stash_ids", [])
        should_skip = require_stash_id and not stash_ids

        self.assertFalse(should_skip)

    def test_default_require_stash_id_is_false(self):
        """requireStashId should default to False if not set."""
        settings = {}  # No require_stash_id key

        require_stash_id = settings.get("require_stash_id", False)

        self.assertFalse(require_stash_id)

    def test_scene_with_empty_stash_ids_treated_as_no_stash_id(self):
        """Empty stash_ids array should be treated as no stash_id."""
        settings = {"require_stash_id": True}
        scene = {"id": "123", "stash_ids": []}

        stash_ids = scene.get("stash_ids", [])

        self.assertEqual(len(stash_ids), 0)
        self.assertFalse(bool(stash_ids))  # Empty list is falsy


class TestNfoExcludeFields(unittest.TestCase):
    """Test NFO field exclusion (#113)."""

    def setUp(self):
        self.mock_scene = {
            "id": "123",
            "title": "Test Scene",
            "details": "Description",
            "date": "2024-01-15",
            "rating100": 80,
            "studio": {"name": "Test Studio"},
            "performers": [{"name": "Jane Doe"}],
            "tags": [{"name": "Tag1"}],
            "files": [{"path": "/path/to/video.mp4"}],
        }

    def test_no_exclusions_produces_all_fields(self):
        """Empty exclude list should produce all fields (backward compatible)."""
        settings = {"nfo_exclude_fields": []}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<criticrating>", nfo)
        self.assertIn("<uniqueid", nfo)
        self.assertIn("<rating>", nfo)
        self.assertIn("<userrating>", nfo)

    def test_exclude_uniqueid(self):
        """Should omit uniqueid when excluded."""
        settings = {"nfo_exclude_fields": ["uniqueid"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<uniqueid", nfo)
        self.assertIn("<title>", nfo)

    def test_exclude_rating_fields(self):
        """Should omit all rating fields when excluded."""
        settings = {"nfo_exclude_fields": ["criticrating", "rating", "userrating"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<criticrating>", nfo)
        self.assertNotIn("<rating>", nfo)
        self.assertNotIn("<userrating>", nfo)
        self.assertIn("<title>", nfo)

    def test_exclude_multiple_fields(self):
        """Should handle excluding multiple unrelated fields."""
        settings = {"nfo_exclude_fields": ["sorttitle", "originaltitle", "year"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<sorttitle>", nfo)
        self.assertNotIn("<originaltitle>", nfo)
        self.assertNotIn("<year>", nfo)
        self.assertIn("<title>", nfo)
        self.assertIn("<premiered>", nfo)

    def test_exclude_does_not_affect_performers(self):
        """Performers should always be included regardless of exclusions."""
        settings = {"nfo_exclude_fields": ["uniqueid", "criticrating"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<actor>", nfo)
        self.assertIn("<name>Jane Doe</name>", nfo)

    def test_exclude_does_not_affect_tags(self):
        """Tags should always be included regardless of exclusions."""
        settings = {"nfo_exclude_fields": ["uniqueid"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<tag>Tag1</tag>", nfo)

    def test_exclude_genre(self):
        """Should omit genre when excluded."""
        settings = {"nfo_exclude_fields": ["genre"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<genre>", nfo)

    def test_no_settings_produces_all_fields(self):
        """No settings at all should produce all fields (backward compatible)."""
        nfo = build_nfo_xml(self.mock_scene)
        self.assertIn("<criticrating>", nfo)
        self.assertIn("<uniqueid", nfo)

    def test_none_exclude_list_produces_all_fields(self):
        """None exclude list treated as empty."""
        settings = {"nfo_exclude_fields": None}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<criticrating>", nfo)


class TestConditionalTemplates(unittest.TestCase):
    """Test conditional template block syntax (#112)."""

    def setUp(self):
        """Scene with all fields populated."""
        self.full_scene = {
            "id": "1",
            "title": "Test Title",
            "date": "2024-01-15",
            "studio": {"name": "TestStudio", "parent_studio": None},
            "stash_ids": [{"stash_id": "abc123", "endpoint": "https://stashdb.org"}],
            "performers": [],
            "tags": [],
            "files": [{"path": "/video.mp4", "height": 1080, "width": 1920}],
        }
        self.no_date_scene = {**self.full_scene, "date": None}

    def test_conditional_included_when_var_has_value(self):
        """Block should be included when variable resolves."""
        result = resolve_conditionals("{$ReleaseDate - }$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15 - $Title")

    def test_conditional_removed_when_var_empty(self):
        """Block should be removed when variable has no value."""
        result = resolve_conditionals("{$ReleaseDate - }$Title", self.no_date_scene)
        self.assertEqual(result, "$Title")

    def test_multiple_conditionals(self):
        """Multiple conditional blocks should each resolve independently."""
        result = resolve_conditionals("{$ReleaseDate - }{$Studio/}$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15 - TestStudio/$Title")

    def test_conditional_with_no_vars_passes_through(self):
        """Braces with no variables inside are literal text."""
        result = resolve_conditionals("{novar}$Title", self.full_scene)
        self.assertEqual(result, "{novar}$Title")

    def test_conditional_var_at_start_of_block(self):
        """`{$ReleaseDate}` with no surrounding text should work."""
        result = resolve_conditionals("{$ReleaseDate}$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15$Title")

    def test_conditional_var_at_end_of_block(self):
        """`{- $ReleaseDate}` should work."""
        result = resolve_conditionals("{- $ReleaseDate}$Title", self.full_scene)
        self.assertEqual(result, "- 2024-01-15$Title")

    def test_conditional_multiple_vars_all_present(self):
        """Block with multiple vars should be included when all resolve."""
        result = resolve_conditionals("{$Studio $ReleaseDate - }$Title", self.full_scene)
        self.assertEqual(result, "TestStudio 2024-01-15 - $Title")

    def test_conditional_multiple_vars_one_missing(self):
        """Block with multiple vars should be removed if any var missing."""
        result = resolve_conditionals("{$Studio $ReleaseDate - }$Title", self.no_date_scene)
        self.assertEqual(result, "$Title")

    def test_end_to_end_with_conditional(self):
        """Full get_new_path with conditional template should work."""
        template = "{$ReleaseDate - }$Title"
        path = get_new_path(self.full_scene, "/base/", template, 250)
        self.assertEqual(path, "/base/2024-01-15 - Test Title.mp4")

    def test_end_to_end_conditional_empty(self):
        """Full get_new_path with empty conditional should produce clean output."""
        template = "{$ReleaseDate - }$Title"
        path = get_new_path(self.no_date_scene, "/base/", template, 250)
        self.assertEqual(path, "/base/Test Title.mp4")


class TestHookTriggerMode(unittest.TestCase):
    """Test hookTriggerMode setting behavior (#111)."""

    def test_always_mode_processes_unorganized_scene(self):
        """'always' mode should process scenes regardless of organized status."""
        settings = {"hook_trigger_mode": "always"}
        scene = {"id": "1", "organized": False}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_always_mode_processes_organized_scene(self):
        """'always' mode should process organized scenes too."""
        settings = {"hook_trigger_mode": "always"}
        scene = {"id": "1", "organized": True}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_on_organized_skips_unorganized_scene(self):
        """'on_organized' mode should skip unorganized scenes."""
        settings = {"hook_trigger_mode": "on_organized"}
        scene = {"id": "1", "organized": False}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertTrue(should_skip)

    def test_on_organized_processes_organized_scene(self):
        """'on_organized' mode should process organized scenes."""
        settings = {"hook_trigger_mode": "on_organized"}
        scene = {"id": "1", "organized": True}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_default_mode_is_always(self):
        """Missing hookTriggerMode should default to 'always'."""
        settings = {}
        mode = settings.get("hook_trigger_mode", "always")
        self.assertEqual(mode, "always")


if __name__ == "__main__":
    unittest.main()
