import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kennybot.utils.local_rag import LocalRAG
from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_chunks,
    profile_candidate_ids,
    summarize_profile_chunks,
)


class ProfilePreviewTests(unittest.TestCase):
    def test_profile_candidate_ids_prefers_guild_id(self) -> None:
        self.assertEqual(profile_candidate_ids(guild_id=972052382315855912, channel_id=123), [972052382315855912])

    def test_profile_preview_uses_vrc_world_travel_guild_rag(self) -> None:
        preview = build_channel_profile_preview(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            question="このサーバーはなにするところ？",
        )

        self.assertIn("VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。", preview["profile"])
        self.assertIn("開始日は2022年04月09日", preview["profile"])
        self.assertIn("このサーバーはなにするところ？", preview["answer"])
        self.assertIn("旅行体験イベント", preview["answer"])

    def test_local_rag_returns_scoped_chunks_for_vrc_server(self) -> None:
        rag = LocalRAG(ROOT)
        chunks = rag.retrieve(
            "",
            limit=6,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            channel_only=True,
        )

        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].source, "RAG:chat_rag.md")
        self.assertIn("VRC世界旅行", chunks[0].body)

    def test_summary_is_deterministic_and_question_aware(self) -> None:
        chunks = build_profile_chunks(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
        )

        summary = summarize_profile_chunks(chunks, question="このサーバーはなにするところ？")

        self.assertTrue(summary.startswith("このサーバーはなにするところ？ に対しては"))
        self.assertIn("このサーバーはなにするところ？ に対しては", summary)
        self.assertIn("旅行体験イベント", summary)


if __name__ == "__main__":
    unittest.main()
