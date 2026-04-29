from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.kennybot.utils.tool_api import build_tool_response
from bin.tool_api_server import dispatch_tool_api_request
from src.kennybot.utils.server_registry import ServerRegistryStore


class ToolAPIBuildTest(TestCase):
    def _make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        registry = ServerRegistryStore(root / "data" / "server" / "server.sqlite3")
        registry.upsert_rag_document(
            scope="guild",
            guild_id=972052382315855912,
            source_path="rag://guild/972052382315855912/profile",
            doc_type="markdown",
            title="定義",
            summary="VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
            body="\n".join(
                [
                    "VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
                    "",
                    "- 世界中の実在観光地を再現した3Dワールドを巡る",
                    "- スタッフによる現地解説・ガイド付き進行",
                ]
            ),
        )
        return root

    def test_serverinfo_response(self) -> None:
        root = self._make_root()
        response = build_tool_response(
            root=root,
            tool="serverinfo",
            payload={
                "guild_id": 972052382315855912,
                "channel_id": 1493246078357606430,
                "scope": "guild",
                "question": "このサーバーはなにするところ？",
            },
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"], "serverinfo")
        self.assertIn("VRC世界旅行", response["profile"])
        self.assertGreaterEqual(len(response["chunks"]), 1)

    def test_rag_response(self) -> None:
        root = self._make_root()
        response = build_tool_response(
            root=root,
            tool="rag",
            payload={
                "guild_id": 972052382315855912,
                "channel_id": 1493246078357606430,
                "query": "VRC世界旅行",
                "limit": 3,
            },
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"], "rag")
        self.assertIn("VRC世界旅行", response["context"])

    def test_server_stats_response(self) -> None:
        root = self._make_root()
        registry = ServerRegistryStore(root / "data" / "server" / "server.sqlite3")
        registry.upsert_message_log(
            guild_id=972052382315855912,
            channel_id=1493246078357606430,
            message_id=1,
            author_id=100,
            author="Alice",
            content="hello",
            timestamp="2026-04-28T00:00:00+09:00",
            metadata={"is_bot": False},
        )
        registry.upsert_message_log(
            guild_id=972052382315855912,
            channel_id=1493246078357606430,
            message_id=2,
            author_id=100,
            author="Alice",
            content="hello2",
            timestamp="2026-04-28T00:01:00+09:00",
            metadata={"is_bot": False},
        )
        registry.upsert_message_log(
            guild_id=972052382315855912,
            channel_id=1493246078357606430,
            message_id=3,
            author_id=101,
            author="Bot",
            content="ignore",
            timestamp="2026-04-28T00:02:00+09:00",
            metadata={"is_bot": True},
        )

        with patch("src.kennybot.utils.tool_api.get_server_registry", return_value=registry):
            response = build_tool_response(
                root=root,
                tool="server_stats",
                payload={
                    "guild_id": 972052382315855912,
                    "channel_id": 1493246078357606430,
                    "scope": "channel",
                    "member_count": 42,
                    "owner_name": "NEKO旅",
                },
            )
        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"], "server_stats")
        self.assertEqual(response["member_count"], 42)
        self.assertEqual(response["owner_name"], "NEKO旅")
        self.assertEqual(response["top_talkers"][0]["author"], "Alice")
        self.assertEqual(response["top_talkers"][0]["count"], 2)

    def test_web_search_response_uses_injected_searcher(self) -> None:
        root = self._make_root()

        class FakeSearcher:
            def __init__(self) -> None:
                self.config = SimpleNamespace(top_n=3)

            def search(self, query: str, *, news_only: bool | None = None):
                del news_only
                return [
                    SimpleNamespace(
                        title=f"Result for {query}",
                        url="https://example.com/1",
                        snippet="snippet 1",
                        date="2026-04-27",
                        source="web",
                    )
                ]

        response = build_tool_response(
            root=root,
            tool="web_search",
            payload={"query": "OpenAI docs", "limit": 1},
            searcher=FakeSearcher(),
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["tool"], "web_search")
        self.assertEqual(len(response["items"]), 1)
        self.assertIn("Result for OpenAI docs", response["context"])


class ToolAPIHTTPTest(TestCase):
    def _make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        registry = ServerRegistryStore(root / "data" / "server" / "server.sqlite3")
        registry.upsert_rag_document(
            scope="guild",
            guild_id=972052382315855912,
            source_path="rag://guild/972052382315855912/profile",
            doc_type="markdown",
            title="定義",
            summary="VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
            body="VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
        )
        return root

    def test_http_routes(self) -> None:
        root = self._make_root()
        class FakeSearcher:
            def __init__(self) -> None:
                self.config = SimpleNamespace(top_n=3)

            def search(self, query: str, *, news_only: bool | None = None):
                del news_only
                return [
                    SimpleNamespace(
                        title=f"Result for {query}",
                        url="https://example.com/1",
                        snippet="snippet 1",
                        date="2026-04-27",
                        source="web",
                    )
                ]

        status, health = dispatch_tool_api_request(root=root, method="GET", path="/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])

        status, tools = dispatch_tool_api_request(root=root, method="GET", path="/tools")
        self.assertEqual(status, 200)
        self.assertTrue(tools["ok"])
        self.assertGreaterEqual(len(tools["tools"]), 4)
        rag_tool = next(tool for tool in tools["tools"] if tool["name"] == "rag")
        self.assertIn("channel_only", rag_tool["input"])
        stats_tool = next(tool for tool in tools["tools"] if tool["name"] == "server_stats")
        self.assertIn("owner_name", stats_tool["input"])

        status, result = dispatch_tool_api_request(
            root=root,
            method="POST",
            path="/tool/serverinfo",
            raw_body=json.dumps(
                {
                    "guild_id": 972052382315855912,
                    "channel_id": 1493246078357606430,
                    "scope": "guild",
                    "question": "このサーバーはなにするところ？",
                }
            ),
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "serverinfo")
        self.assertIn("VRC世界旅行", result["profile"])

        registry = ServerRegistryStore(root / "data" / "server" / "server.sqlite3")
        with patch("src.kennybot.utils.tool_api.get_server_registry", return_value=registry):
            status, stats = dispatch_tool_api_request(
                root=root,
                method="POST",
                path="/tool/server_stats",
                raw_body=json.dumps(
                    {
                        "guild_id": 972052382315855912,
                        "channel_id": 1493246078357606430,
                        "scope": "guild",
                        "member_count": 55,
                        "owner_name": "NEKO旅",
                    }
                ),
            )
        self.assertEqual(status, 200)
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["tool"], "server_stats")
