import os
import unittest

from utils.paths import PathEscapeError, fit_bytes, is_inside, join_under, sanitize_component


class TestSanitizeComponent(unittest.TestCase):
    def test_table(self):
        cases = [
            ("plain name", "plain name"),
            ("a:b", "a-b"),
            ('a<b>c"d/e\\f|g?h*i', "a b c d e f g h i"),
            ("a\x00b\x1fc\x7fd", "a b c d"),
            ("a   b \t c", "a b c"),
            ("  .name. ", "name"),
            (". .", "_"),
            (".", "_"),
            ("..", "_"),
            ("", "_"),
            ("   ", "_"),
            ("Tom & Jerry", "Tom & Jerry"),
            ("CON", "CON_"),
            ("nul.txt", "nul_.txt"),
            ("com1", "com1_"),
            ("LPT9.tar.gz", "LPT9_.tar.gz"),
            ("console", "console"),
            ("COM0", "COM0"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(sanitize_component(value), expected)

    def test_invariants(self):
        for value in ["../..", "a/../b", "\\\\x", "...", " . ", "?", "a.", "a ."]:
            out = sanitize_component(value)
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)
            self.assertNotIn(out, ("", ".", ".."))
            self.assertEqual(out, out.strip(" ."))


class TestFitBytes(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(fit_bytes("abc.mp4", 255, ".mp4"), "abc.mp4")

    def test_ascii_with_suffix(self):
        out = fit_bytes("x" * 300 + ".mp4", 255, ".mp4")
        self.assertTrue(out.endswith(".mp4"))
        self.assertEqual(len(out.encode()), 255)

    def test_cjk(self):
        name = "漢" * 200 + ".mkv"
        out = fit_bytes(name, 255, ".mkv")
        self.assertTrue(out.endswith(".mkv"))
        self.assertLessEqual(len(out.encode()), 255)
        self.assertEqual(out, "漢" * 83 + ".mkv")
        out.encode("utf-8").decode("utf-8")

    def test_no_suffix(self):
        out = fit_bytes("é" * 200)
        self.assertLessEqual(len(out.encode()), 255)
        self.assertEqual(out, "é" * 127)

    def test_suffix_too_long(self):
        with self.assertRaises(ValueError):
            fit_bytes("a.mp4", 3, ".mp4")


class TestJoinUnder(unittest.TestCase):
    def test_base_slash_variants(self):
        expected = os.path.join("/media/lib", "a", "b.mp4")
        self.assertEqual(join_under("/media/lib", "a", "b.mp4"), expected)
        self.assertEqual(join_under("/media/lib/", "a", "b.mp4"), expected)

    def test_dotdot_stays_inside(self):
        self.assertEqual(join_under("/media/lib", "a/../b"), os.path.join("/media/lib", "b"))

    def test_dotdot_escape(self):
        with self.assertRaises(PathEscapeError):
            join_under("/media/lib", "..", "x")
        with self.assertRaises(PathEscapeError):
            join_under("/media/lib", "a/../../lib2/x")

    def test_absolute_part(self):
        self.assertEqual(join_under("/media/lib", "/etc/passwd"), os.path.join("/media/lib", "etc", "passwd"))

    def test_empty_base(self):
        with self.assertRaises(ValueError):
            join_under("", "a")

    def test_escape_is_valueerror(self):
        self.assertTrue(issubclass(PathEscapeError, ValueError))


class TestIsInside(unittest.TestCase):
    def test_table(self):
        self.assertTrue(is_inside("/media/lib/x/y", "/media/lib"))
        self.assertTrue(is_inside("/media/lib/x", "/media/lib/"))
        self.assertTrue(is_inside("/media/lib", "/media/lib"))
        self.assertFalse(is_inside("/media/lib2/x", "/media/lib"))
        self.assertFalse(is_inside("/media/lib/../other", "/media/lib"))
        self.assertTrue(is_inside("/media/lib/Mixed/Case", "/media/lib"))
        self.assertFalse(is_inside("/media/LIB/x", "/media/lib"))

    def test_relative_vs_absolute(self):
        self.assertFalse(is_inside("rel/x", "/media/lib"))


if __name__ == "__main__":
    unittest.main()
