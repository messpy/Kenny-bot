import sys
import tempfile
from pathlib import Path
import unittest
import textwrap


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_chunks,
    summarize_profile_chunks,
)


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
        self.assertIn("このサーバーはなにするところ？", preview["answer"])
        self.assertIn("旅行体験イベント", preview["answer"])

    def test_local_rag_returns_scoped_chunks_for_vrc_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild_dir = root / "data" / "channel_rag" / "10"
            channel_dir = guild_dir / "channels" / "20"
            guild_dir.mkdir(parents=True, exist_ok=True)
            channel_dir.mkdir(parents=True, exist_ok=True)
            (guild_dir / "chat_rag.md").write_text(
                textwrap.dedent(
                    """\
                    # Guild profile
                    これはサーバー全体の説明です。
                    - guild-only
                    """
                ),
                encoding="utf-8",
            )
            (channel_dir / "chat_rag.md").write_text(
                textwrap.dedent(
                    """\
                    # Channel profile
                    これはチャンネル固有の説明です。
                    - channel-only
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
            self.assertNotIn("Guild profile", preview["profile"])

            guild_preview = build_channel_profile_preview(
                root=root,
                guild_id=10,
                channel_id=20,
                scope="guild",
                question="このサーバーは何の場？",
            )
            self.assertIn("Guild profile", guild_preview["profile"])
            self.assertNotIn("Channel profile", guild_preview["profile"])

    def test_summary_is_deterministic_and_question_aware(self) -> None:
        chunks = build_profile_chunks(
            root=ROOT,
            guild_id=972052382315855912,
            channel_id=972052382315855912,
            scope="auto",
        )

        summary = summarize_profile_chunks(chunks, question="このサーバーはなにするところ？")

        self.assertTrue(summary.startswith("このサーバーはなにするところ？ に対しては"))
        self.assertIn("このサーバーはなにするところ？ に対しては", summary)
        self.assertIn("旅行体験イベント", summary)


if __name__ == "__main__":
    unittest.main()
