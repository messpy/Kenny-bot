import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bin.profile_preview_server import dispatch_profile_preview_request  # noqa: E402


class ProfilePreviewAPITests(unittest.TestCase):
    def test_healthz(self) -> None:
        status, body = dispatch_profile_preview_request(
            root=ROOT,
            method="GET",
            path="/healthz",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)

    def test_profile_preview_post(self) -> None:
        payload = {
            "guild_id": 972052382315855912,
            "channel_id": 1493246078357606430,
            "scope": "auto",
            "question": "このサーバーはなにするところ？",
            "limit": 6,
            "max_chars": 1200,
            "use_ai": False,
            "ollama_model": "llama3.2:1b",
        }
        status, body = dispatch_profile_preview_request(
            root=ROOT,
            method="POST",
            path="/profile-preview",
            raw_body=json.dumps(payload),
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["scope"], "auto")
        self.assertIn("VRC世界旅行とは", body["profile"])
        self.assertTrue(body["answer"].startswith("ここは、VRC世界旅行のサーバーです。"))
        self.assertIn("VRChat上で世界各地の観光地を巡る旅行体験イベント", body["answer"])
        self.assertIn("management_log", body)
        self.assertIn("ai_status", body)
        self.assertEqual(body["ai_status"]["mode"], "fallback")
        self.assertEqual(body["ai_status"]["model"], "llama3.2:1b")

    def test_profile_preview_rejects_invalid_path(self) -> None:
        status, body = dispatch_profile_preview_request(
            root=ROOT,
            method="POST",
            path="/nope",
            raw_body="{}",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_profile_preview_ai_http_normalization(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "message": {
                        "content": "ここは、AIで整形したサーバー説明です。",
                    }
                }

        with patch("src.kennybot.utils.profile_preview_api.requests.post", return_value=FakeResponse()) as mocked_post:
            response = dispatch_profile_preview_request(
                root=ROOT,
                method="POST",
                path="/profile-preview",
                raw_body=json.dumps(
                    {
                        "guild_id": 972052382315855912,
                        "channel_id": 1493246078357606430,
                        "scope": "auto",
                        "question": "このサーバーはなにするところ？",
                        "use_ai": True,
                        "ollama_model": "llama3.2:1b",
                    }
                ),
            )

        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]["answer"], "ここは、AIで整形したサーバー説明です。")
        self.assertEqual(mocked_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
