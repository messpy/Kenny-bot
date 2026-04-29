import sys
import json
import os
import tempfile
from pathlib import Path
import unittest
import textwrap
from unittest.mock import patch
import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["KENNYBOT_DB_BACKEND"] = "sqlite"

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_management_log,
    build_profile_chunks,
    select_display_profile_chunks,
    summarize_profile_chunks,
    write_jsonl_log,
)
from src.kennybot.utils.config import get_prompt
from src.kennybot.utils.profile_preview_api import build_profile_preview_response
from src.kennybot.utils.profile_preview_api import _normalize_ai_answer
from src.kennybot.utils.server_registry import ServerRegistryStore


class ProfilePreviewTests(unittest.TestCase):
    def _seed_doc(
        self,
        *,
        root: Path,
        guild_id: int,
        channel_id: int | None,
        scope: str,
        title: str,
        body: str,
        source_path: str,
    ) -> None:
        registry = ServerRegistryStore(root / "data" / "server" / "server.sqlite3")
        registry.upsert_rag_document(
            scope=scope,
            guild_id=guild_id,
            channel_id=channel_id,
            source_path=source_path,
            doc_type="markdown",
            title=title,
            summary=body.splitlines()[0],
            body=body,
        )

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
        self.assertIn("VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。", preview["answer"])
        self.assertIn("VRChat上で世界各地の観光地を巡る旅行体験イベント", preview["answer"])
        self.assertNotIn("ここは、", preview["answer"])

    def test_local_rag_returns_scoped_chunks_for_vrc_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="Server profile", body="これは server 側の説明です。\n- server-only", source_path="rag://guild/10/server-profile")
            self._seed_doc(root=root, guild_id=10, channel_id=20, scope="channel", title="Channel profile", body="これは server のチャンネル説明です。\n- channel-only", source_path="rag://guild/10/channel/20/channel-profile")

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
            self.assertNotIn("legacy-only", guild_preview["profile"])

    def test_summary_is_deterministic_and_question_aware(self) -> None:
        chunks = build_profile_chunks(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="auto",
        )

        summary = summarize_profile_chunks(chunks, question="このサーバーはなにするところ？")

        self.assertIn("VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。", summary)
        self.assertIn("VRChat上で世界各地の観光地を巡る旅行体験イベント", summary)
        self.assertNotIn("ここは、", summary)

    def test_display_profile_filters_operational_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="ワールド概要", body="- このワールドは交流用の場所です。", source_path="rag://guild/10/overview")
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="運用方針", body="- 前提は簡潔に説明する。", source_path="rag://guild/10/ops")
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="オーナー向けメモ", body="- 交流の場として扱う。", source_path="rag://guild/10/owner-memo")

            chunks = build_profile_chunks(
                root=root,
                guild_id=10,
                channel_id=10,
                scope="guild",
            )
            display_chunks = select_display_profile_chunks(chunks)
            preview = build_channel_profile_preview(
                root=root,
                guild_id=10,
                channel_id=10,
                scope="guild",
                question="このサーバーはなにするところ？",
            )

            self.assertEqual([chunk.title for chunk in display_chunks], ["ワールド概要"])
            self.assertIn("このワールドは交流用の場所です。", preview["profile"])
            self.assertNotIn("運用方針", preview["profile"])
            self.assertNotIn("オーナー向けメモ", preview["profile"])
            self.assertNotIn("交流の場として扱う", preview["answer"])

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

    def test_profile_preview_answer_prioritizes_question_target_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="適当", body="`適当` は、特定のテーマを決めずに気軽に話すための雑談チャンネルです。", source_path="rag://guild/10/tekitou")
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="サーバー全体の説明", body="このサーバー全体は小規模な私用サーバーです。", source_path="rag://guild/10/summary")
            self._seed_doc(root=root, guild_id=10, channel_id=None, scope="guild", title="bot-events", body="`bot-events` は、Bot の通知を見るチャンネルです。", source_path="rag://guild/10/bot-events")

            preview = build_channel_profile_preview(
                root=root,
                guild_id=10,
                channel_id=20,
                scope="guild",
                question="適当って何をするチャンネル？",
            )

            first_paragraph = preview["answer"].split("\n\n", 1)[0]
            self.assertIn("適当", first_paragraph)
            self.assertIn("雑談チャンネル", first_paragraph)

    def test_profile_preview_response_uses_ai_when_available(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def chat_simple(self, model: str, prompt: str, stream: bool = False, **kwargs: object) -> str:
                self.calls.append((model, prompt))
                return "AIで整形したサーバー説明です。"

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

        self.assertEqual(response["answer"], "AIで整形したサーバー説明です。")
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
                    "message": {"content": "AIで整形したサーバー説明です。"}
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

        self.assertEqual(response["answer"], "AIで整形したサーバー説明です。")
        self.assertEqual(response["ai_status"]["mode"], "ai")
        self.assertEqual(response["ai_status"]["reason"], "ollama_http_ok")
        self.assertEqual(mocked_post.call_count, 1)

    def test_profile_preview_response_falls_back_to_ollama_client_before_summary_fallback(self) -> None:
        class FakeHttpErrorResponse:
            status_code = 404

        class FakeClient:
            def chat_simple(self, model: str, prompt: str, stream: bool = False, **kwargs: object) -> str:
                return "AIクライアントで整形した説明です。"

        old_host = os.environ.get("OLLAMA_HOST")
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"
        try:
            with (
                patch(
                    "src.kennybot.utils.profile_preview_api.requests.post",
                    side_effect=requests.HTTPError(response=FakeHttpErrorResponse()),
                ) as mocked_post,
                patch(
                    "src.kennybot.utils.profile_preview_api._ollama_client_chat",
                    return_value="AIクライアントで整形した説明です。",
                ) as mocked_client_chat,
            ):
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

        self.assertEqual(response["answer"], "AIクライアントで整形した説明です。")
        self.assertEqual(response["ai_status"]["mode"], "ai")
        self.assertEqual(response["ai_status"]["reason"], "ollama_client_ok_after_http_error")
        self.assertEqual(mocked_post.call_count, 1)
        self.assertEqual(mocked_client_chat.call_count, 1)

    def test_normalize_ai_answer_strips_meta_prefixes(self) -> None:
        answer = _normalize_ai_answer(
            "（モック応答）場所の説明を優先して返しました。VRC世界旅行のサーバーです。",
            "このサーバーはなにするところ？",
        )

        self.assertEqual(answer, "VRC世界旅行のサーバーです。")

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
