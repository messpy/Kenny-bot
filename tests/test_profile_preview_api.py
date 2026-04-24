import json
import sys
import threading
from pathlib import Path
import unittest
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bin.profile_preview_server import ProfilePreviewHTTPServer  # noqa: E402


class ProfilePreviewAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ProfilePreviewHTTPServer(("127.0.0.1", 0), ROOT)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _url(self, path: str) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}{path}"

    def test_healthz(self) -> None:
        with request.urlopen(self._url("/healthz"), timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(body["ok"], True)

    def test_profile_preview_post(self) -> None:
        payload = {
            "guild_id": 972052382315855912,
            "channel_id": 1493246078357606430,
            "scope": "auto",
            "question": "このサーバーはなにするところ？",
            "limit": 6,
            "max_chars": 1200,
        }
        req = request.Request(
            self._url("/profile-preview"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(body["ok"])
        self.assertEqual(body["scope"], "auto")
        self.assertIn("VRC世界旅行とは", body["profile"])
        self.assertIn("このサーバーはなにするところ？", body["answer"])
        self.assertIn("management_log", body)

    def test_profile_preview_rejects_invalid_path(self) -> None:
        req = request.Request(
            self._url("/nope"),
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(req, timeout=5)

        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
