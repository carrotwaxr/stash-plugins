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


if __name__ == "__main__":
    unittest.main()
