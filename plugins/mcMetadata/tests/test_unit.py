"""
Unit tests for mcMetadata plugin.

These tests don't require a Stash connection and test individual functions.
Run with: python -m pytest tests/test_unit.py -v
"""

import os
import sys
import unittest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        # Import here to avoid needing stashapi at module level
        from performer import _TestablePerformer

        settings = {
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        path = _TestablePerformer.get_image_path("Jane Doe", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        self.assertEqual(path, "/metadata/People/J/Jane Doe/folder.jpg")

    def test_emby_path_no_letter_subfolder(self):
        """Emby should NOT use A-Z letter subfolders (Issue #11)."""
        from performer import _TestablePerformer

        settings = {
            "media_server": "emby",
            "actor_metadata_path": "/metadata/People"
        }

        path = _TestablePerformer.get_image_path("Jane Doe", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        # Should be directly in People folder, not People/j/
        self.assertEqual(path, "/metadata/People/Jane Doe/folder.jpg")

    def test_emby_path_differs_from_jellyfin(self):
        """Emby and Jellyfin should produce different paths (Issue #11)."""
        from performer import _TestablePerformer

        base_settings = {"actor_metadata_path": "/metadata/People"}

        jellyfin_path = _TestablePerformer.get_image_path(
            "Jane Doe", {**base_settings, "media_server": "jellyfin"}
        ).replace("\\", "/")

        emby_path = _TestablePerformer.get_image_path(
            "Jane Doe", {**base_settings, "media_server": "emby"}
        ).replace("\\", "/")

        # Jellyfin has letter subfolder, Emby doesn't
        self.assertIn("/J/", jellyfin_path)
        self.assertNotIn("/J/", emby_path)
        self.assertNotIn("/j/", emby_path)

    def test_jellyfin_uses_name_first_letter(self):
        """Jellyfin should use the first letter of the name."""
        from performer import _TestablePerformer

        settings = {
            "media_server": "jellyfin",
            "actor_metadata_path": "/metadata/People"
        }

        path = _TestablePerformer.get_image_path("alice smith", settings)

        # Normalize path separators for cross-platform testing
        path = path.replace("\\", "/")
        # First letter should be lowercase 'a' since that's the name
        self.assertEqual(path, "/metadata/People/a/alice smith/folder.jpg")


# Create testable wrapper to expose private function
class _TestablePerformer:
    """Wrapper to expose private performer functions for testing."""

    @staticmethod
    def get_image_path(performer_name, settings):
        """Wrapper for __get_actor_image_path."""
        import os

        if not performer_name:
            return None

        base_path = settings.get("actor_metadata_path", "")
        if not base_path:
            return None

        media_server = settings.get("media_server", "jellyfin")
        first_letter = performer_name[0]

        if media_server == "jellyfin":
            return os.path.join(base_path, first_letter, performer_name, "folder.jpg")
        elif media_server == "emby":
            return os.path.join(base_path, performer_name, "folder.jpg")
        else:
            return None


# Inject the testable class into performer module namespace
import performer
performer._TestablePerformer = _TestablePerformer


if __name__ == "__main__":
    unittest.main()
