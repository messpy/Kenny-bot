import sys
import json
import os
import tempfile
from pathlib import Path
import unittest
import textwrap
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_management_log,
    build_profile_chunks,
    summarize_profile_chunks,
    write_jsonl_log,
)
from src.kennybot.utils.config import get_prompt
from src.kennybot.utils.profile_preview_api import build_profile_preview_response
from src.kennybot.utils.profile_preview_api import _normalize_ai_answer


class ProfilePreviewTests(unittest.TestCase):
    def test_profile_scope_guild_is_explicit(self) -> None:
        preview = build_channel_profile_preview(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="guild",
            question="このサーバーはなにするところ？",
        )

        self.assertIn("VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。", preview["profile"])
        self.assertEqual(preview["scope"], "guild")

    def test_profile_preview_uses_vrc_world_travel_guild_rag(self) -> None:
        preview = build_channel_profile_preview(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="auto",
            question="このサーバーはなにするところ？",
        )

        self.assertIn("VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。", preview["profile"])
        self.assertIn("開始日は2022年04月09日", preview["profile"])
        self.assertTrue(preview["answer"].startswith("ここは、VRC世界旅行のサーバーです。"))
        self.assertIn("VRChat上で世界各地の観光地を巡る旅行体験イベント", preview["answer"])
        self.assertNotIn("に対しては", preview["answer"])

    def test_local_rag_returns_scoped_chunks_for_vrc_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server_guild_dir = root / "data" / "server" / "10"
            server_channel_dir = server_guild_dir / "channels" / "20"
            legacy_guild_dir = root / "data" / "channel_rag" / "10"
            legacy_channel_dir = legacy_guild_dir / "channels" / "20"
            server_guild_dir.mkdir(parents=True, exist_ok=True)
            server_channel_dir.mkdir(parents=True, exist_ok=True)
            legacy_guild_dir.mkdir(parents=True, exist_ok=True)
            legacy_channel_dir.mkdir(parents=True, exist_ok=True)
            (server_guild_dir / "chat_rag.md").write_text(
                textwrap.dedent(
                    """\
                    # Server profile
                    これは server 側の説明です。
                    - server-only
                    """
                ),
                encoding="utf-8",
            )
            (server_channel_dir / "chat_rag.md").write_text(
                textwrap.dedent(
                    """\
                    # Channel profile
                    これは server のチャンネル説明です。
                    - channel-only
                    """
                ),
                encoding="utf-8",
            )
            (legacy_guild_dir / "chat_rag.md").write_text(
                textwrap.dedent(
                    """\
                    # Legacy profile
                    これは channel_rag 側の説明です。
                    - legacy-only
                    """
                ),
                encoding="utf-8",
            )

            preview = build_channel_profile_preview(
                root=root,
                guild_id=10,
                channel_id=20,
                scope="channel",
                question="このチャンネルは何の場？",
            )

            self.assertIn("Channel profile", preview["profile"])
            self.assertIn("channel-only", preview["profile"])
            self.assertNotIn("Legacy profile", preview["profile"])

            guild_preview = build_channel_profile_preview(
                root=root,
                guild_id=10,
                channel_id=20,
                scope="guild",
                question="このサーバーは何の場？",
            )
            self.assertIn("Server profile", guild_preview["profile"])
            self.assertNotIn("Legacy profile", guild_preview["profile"])

    def test_summary_is_deterministic_and_question_aware(self) -> None:
        chunks = build_profile_chunks(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="auto",
        )

        summary = summarize_profile_chunks(chunks, question="このサーバーはなにするところ？")

        self.assertTrue(summary.startswith("ここは、VRC世界旅行のサーバーです。"))
        self.assertIn("VRChat上で世界各地の観光地を巡る旅行体験イベント", summary)
        self.assertNotIn("に対しては", summary)

    def test_build_profile_management_log_contains_summary_fields(self) -> None:
        preview = build_channel_profile_preview(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="auto",
            question="このサーバーはなにするところ？",
        )

        log = build_profile_management_log(preview)

        self.assertEqual(log["title"], "Bot 管理ログ")
        self.assertEqual(log["description"], "サーバー・チャンネル・ワールドの説明に応答しました。")
        self.assertEqual(log["level"], "info")
        self.assertTrue(any(name == "質問" for name, _, _ in log["fields"]))
        self.assertTrue(any(name == "返信" for name, _, _ in log["fields"]))
        profile_field = next(value for name, value, _ in log["fields"] if name == "プロフィール")
        self.assertLessEqual(len(profile_field), 500)
        self.assertNotIn("に対しては", profile_field)

    def test_write_jsonl_log_appends_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile_preview.log"
            entry = {
                "title": "Bot 管理ログ",
                "description": "test",
                "level": "info",
                "fields": [("質問", "テスト", False)],
            }

            write_jsonl_log(path, entry)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            decoded = json.loads(lines[0])
            self.assertEqual(decoded["title"], "Bot 管理ログ")
            self.assertEqual(decoded["level"], "info")

    def test_profile_preview_response_returns_json_payload(self) -> None:
        response = build_profile_preview_response(
            root=ROOT,
            payload={
                "guild_id": 972052382315855912,
                "channel_id": 1493246078357606430,
                "scope": "auto",
                "question": "このサーバーはなにするところ？",
                "use_ai": False,
                "ollama_model": "llama3.2:1b",
            },
        )

        self.assertEqual(response["guild_id"], 972052382315855912)
        self.assertEqual(response["channel_id"], 1493246078357606430)
        self.assertEqual(response["scope"], "auto")
        self.assertIn("management_log", response)
        self.assertIn("profile_summary", response)
        self.assertIn("ai_status", response)
        self.assertEqual(response["ai_status"]["mode"], "fallback")
        self.assertEqual(response["ai_status"]["model"], "llama3.2:1b")

    def test_profile_preview_response_uses_ai_when_available(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def chat_simple(self, model: str, prompt: str, stream: bool = False, **kwargs: object) -> str:
                self.calls.append((model, prompt))
                return "ここは、AIで整形したサーバー説明です。"

        fake_client = FakeClient()
        response = build_profile_preview_response(
            root=ROOT,
            payload={
                "guild_id": 972052382315855912,
                "channel_id": 1493246078357606430,
                "scope": "auto",
                "question": "このサーバーはなにするところ？",
                "use_ai": True,
                "ollama_model": "llama3.2:1b",
            },
            ai_client=fake_client,
        )

        self.assertEqual(response["answer"], "ここは、AIで整形したサーバー説明です。")
        self.assertTrue(fake_client.calls)
        prompt = fake_client.calls[0][1]
        self.assertIn("ユーザーに見せる最終回答だけ", prompt)
        profile_section = prompt.split("[プロフィール]", 1)[-1]
        self.assertNotIn("RAG:", profile_section)
        self.assertNotIn("chat_rag.md", profile_section)
        self.assertEqual(response["ai_status"]["mode"], "ai")

    def test_profile_preview_response_uses_ollama_http_when_available(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "message": {"content": "ここは、AIで整形したサーバー説明です。"}
                }

        old_host = os.environ.get("OLLAMA_HOST")
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"
        try:
            with patch("src.kennybot.utils.profile_preview_api.requests.post", return_value=FakeResponse()) as mocked_post:
                response = build_profile_preview_response(
                    root=ROOT,
                    payload={
                        "guild_id": 972052382315855912,
                        "channel_id": 1493246078357606430,
                        "scope": "auto",
                        "question": "このサーバーはなにするところ？",
                        "use_ai": True,
                        "ollama_model": "llama3.2:1b",
                    },
                )
        finally:
            if old_host is None:
                os.environ.pop("OLLAMA_HOST", None)
            else:
                os.environ["OLLAMA_HOST"] = old_host

        self.assertEqual(response["answer"], "ここは、AIで整形したサーバー説明です。")
        self.assertEqual(response["ai_status"]["mode"], "ai")
        self.assertEqual(response["ai_status"]["reason"], "ollama_http_ok")
        self.assertEqual(mocked_post.call_count, 1)

    def test_normalize_ai_answer_strips_meta_prefixes(self) -> None:
        answer = _normalize_ai_answer(
            "（モック応答）場所の説明を優先して返しました。ここは、VRC世界旅行のサーバーです。",
            "このサーバーはなにするところ？",
        )

        self.assertEqual(answer, "ここは、VRC世界旅行のサーバーです。")

    def test_normalize_ai_answer_strips_internal_labels_anywhere(self) -> None:
        answer = _normalize_ai_answer(
            "ここは案内チャンネルです。\n[RAG:chat_rag.md / 定義] 世界中の実在観光地を再現した3Dワールドを巡ります。",
            "このチャンネルは何をする場所？",
        )

        self.assertEqual(
            answer,
            "ここは案内チャンネルです。\n世界中の実在観光地を再現した3Dワールドを巡ります。",
        )

    def test_capability_prompt_retains_completeness_instruction(self) -> None:
        prompt = get_prompt("chat", "capability_prompt")
        self.assertIn("カテゴリごとにできるだけ漏れなく", prompt)
        self.assertIn("確認できたものは省かずに", prompt)


if __name__ == "__main__":
    unittest.main()
