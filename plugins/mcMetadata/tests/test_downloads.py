"""Image downloads: session-cookie auth, apikey fallback, HTML/login errors, default performer images."""
import http.server
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import performer as performer_module  # noqa: E402
import utils.files as files_module  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1500 + b"IEND\xaeB`\x82"


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    mode = "image"

    def do_GET(self):
        type(self).seen.append({"path": self.path, "cookie": self.headers.get("Cookie")})
        if type(self).mode == "html":
            body = b"<html>login</html>"
            ctype = "text/html"
        elif type(self).mode == "small":
            body, ctype = PNG[:50], "image/png"
        else:
            body, ctype = PNG, "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class DownloadTest(unittest.TestCase):
    def setUp(self):
        _Handler.seen = []
        _Handler.mode = "image"
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, "poster.png")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_sends_session_cookie_and_no_apikey(self):
        settings = {"dry_run": False, "session_cookie": {"name": "session", "value": "abc123"}}
        url = files_module.authenticated_url(f"{self.base}/scene/1/screenshot?t=5", settings, "KEY")
        self.assertNotIn("apikey", url)
        self.assertTrue(files_module.download_image(url, self.dest, settings))
        self.assertEqual(_Handler.seen[0]["cookie"], "session=abc123")
        self.assertTrue(os.path.exists(self.dest))

    def test_apikey_fallback_without_cookie(self):
        settings = {"dry_run": False}
        url = files_module.authenticated_url(f"{self.base}/x?t=5", settings, "K/1")
        self.assertTrue(url.endswith("&apikey=K%2F1"))
        self.assertEqual(files_module.authenticated_url("http://h/x", settings, "K"), "http://h/x?apikey=K")
        self.assertTrue(files_module.download_image(url, self.dest, settings))
        self.assertIsNone(_Handler.seen[0]["cookie"])

    def test_no_auth_when_nothing_configured(self):
        self.assertEqual(files_module.authenticated_url("http://h/x?t=1", {}, ""), "http://h/x?t=1")

    def test_html_response_explains_login_redirect(self):
        _Handler.mode = "html"
        with patch.object(files_module.log, "error") as err:
            ok = files_module.download_image(f"{self.base}/i?apikey=SECRET", self.dest, {"dry_run": False, "session_cookie": {"name": "session", "value": "SECRETCOOKIE"}})
        self.assertFalse(ok)
        msg = " ".join(str(c.args[0]) for c in err.call_args_list)
        self.assertIn("login", msg)
        self.assertIn("isn't authenticated", msg)
        self.assertIn("API key", msg)
        self.assertNotIn("SECRET", msg)
        self.assertNotIn("SECRETCOOKIE", msg)
        self.assertFalse(os.path.exists(self.dest))

    def test_size_validation_still_applies(self):
        _Handler.mode = "small"
        self.assertFalse(files_module.download_image(f"{self.base}/i", self.dest, {"dry_run": False}))
        self.assertFalse(os.path.exists(self.dest))

    def test_magic_validation_still_applies(self):
        with patch.object(files_module, "RETRY_DELAY", 0):
            with patch.object(files_module, "_is_valid_image", return_value=False):
                self.assertFalse(files_module.download_image(f"{self.base}/i", self.dest, {"dry_run": False}))


class DefaultPerformerImageTest(unittest.TestCase):
    def _run(self, image_path):
        tmp = tempfile.mkdtemp()
        settings = {"dry_run": False, "enable_actor_images": True, "media_server": "jellyfin", "actor_images_path": tmp}
        performer = {"name": "Jane", "image_path": image_path}
        with patch.object(performer_module, "get_actor_image_path", return_value=os.path.join(tmp, "J", "Jane", "folder.jpg")), \
                patch.object(performer_module, "download_image") as dl:
            performer_module.process_performer(performer, settings, "KEY")
        return dl

    def test_default_image_skipped(self):
        self.assertFalse(self._run("http://s/performer/1/image?t=1&default=true").called)

    def test_real_image_downloaded_with_auth(self):
        dl = self._run("http://s/performer/1/image?t=1")
        self.assertTrue(dl.called)
        self.assertEqual(dl.call_args.args[0], "http://s/performer/1/image?t=1&apikey=KEY")


if __name__ == "__main__":
    unittest.main()
