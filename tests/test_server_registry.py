from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.kennybot.utils.local_rag import LocalRAG
from src.kennybot.utils.message_store import MessageStore
from src.kennybot.utils.runtime_settings import SettingsStore
from src.kennybot.utils.server_registry import ServerRegistryStore


class ServerRegistryStoreTest(TestCase):
    def test_upserts_and_reads_back_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "server.sqlite3"
            store = ServerRegistryStore(db_path)

            store.upsert_guild(
                972052382315855912,
                name="VRC world travel",
                settings={"logging": {"event_channel_id": 123}},
                metadata={"source": "test"},
            )
            store.upsert_channel(
                972052382315855912,
                1493246078357606430,
                name="general",
                kind="text",
                topic="test topic",
                settings={"slowmode": 0},
            )
            store.upsert_rag_document(
                scope="guild",
                guild_id=972052382315855912,
                source_path=Path(tmpdir) / "guild" / "faq.json",
                doc_type="faq.json",
                title="このサーバーはなにするところ？",
                summary="VRChat 上で世界旅行を楽しむサーバー",
                body="VRChat 上で世界旅行を楽しむサーバーです。",
                metadata={"kind": "faq"},
            )

            guild = store.get_guild(972052382315855912)
            self.assertIsNotNone(guild)
            assert guild is not None
            self.assertEqual(guild["name"], "VRC world travel")
            self.assertEqual(guild["settings"]["logging"]["event_channel_id"], 123)

            channel = store.get_channel(972052382315855912, 1493246078357606430)
            self.assertIsNotNone(channel)
            assert channel is not None
            self.assertEqual(channel["kind"], "text")
            self.assertEqual(channel["settings"]["slowmode"], 0)

            docs = store.list_rag_documents(guild_id=972052382315855912)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["title"], "このサーバーはなにするところ？")
            self.assertEqual(docs[0]["metadata"]["kind"], "faq")
            self.assertEqual(docs[0]["body"], "VRChat 上で世界旅行を楽しむサーバーです。")


class RuntimeSettingsRegistryMirrorTest(TestCase):
    def test_guild_settings_are_mirrored_to_sqlite_without_guild_yaml_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "bot_settings.yaml"
            store = SettingsStore(yaml_path)
            registry = ServerRegistryStore(Path(tmpdir) / "server.sqlite3")

            with patch("src.kennybot.utils.runtime_settings.get_server_registry", return_value=registry):
                store.set("logging.event_channel_id", 456, guild_id=123)

            guild = registry.get_guild(123)
            self.assertIsNotNone(guild)
            assert guild is not None
            self.assertEqual(guild["settings"]["logging"]["event_channel_id"], 456)
            saved = yaml_path.read_text(encoding="utf-8")
            self.assertIn("global:", saved)
            self.assertNotIn("guilds:", saved)


class LocalRAGRegistryMirrorTest(TestCase):
    def test_append_qa_records_rag_document_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = ServerRegistryStore(root / "server.sqlite3")
            rag = LocalRAG(root)

            with patch("src.kennybot.utils.local_rag.get_server_registry", return_value=registry):
                path = rag.append_guild_qa(
                    guild_id=111,
                    question="このサーバーはなにするところ？",
                    answer="VRChat の世界旅行イベントの案内をするサーバーです。",
                    tags=["faq", "intro"],
                )
                self.assertEqual(path.name, "server.sqlite3")
                path2 = rag.append_channel_qa(
                    guild_id=111,
                    channel_id=222,
                    question="参加方法は？",
                    answer="案内を確認して参加します。",
                    tags=["参加"],
                )
                self.assertEqual(path2.name, "server.sqlite3")

            guild_docs = registry.list_rag_documents(guild_id=111, scope="guild")
            self.assertEqual(len(guild_docs), 1)
            self.assertEqual(guild_docs[0]["title"], "このサーバーはなにするところ？")
            channel_docs = registry.list_rag_documents(guild_id=111, channel_id=222, scope="channel")
            self.assertEqual(len(channel_docs), 1)
            self.assertEqual(channel_docs[0]["title"], "参加方法は？")


class MessageStoreSqliteMirrorTest(TestCase):
    def test_add_message_is_saved_to_sqlite_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = ServerRegistryStore(root / "server.sqlite3")
            store = MessageStore(10, 20)

            with patch("src.kennybot.utils.message_store.get_server_registry", return_value=registry):
                store._registry = registry
                store.add_message("alice", "hello", 12345, author_id=99)

            rows = registry.list_message_logs(guild_id=10, channel_id=20, lines=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["author"], "alice")
            self.assertEqual(rows[0]["content"], "hello")
            recent = store.get_recent_messages(lines=5)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["content"], "hello")
