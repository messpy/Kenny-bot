from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from src.kennybot.utils.tool_api import build_tool_response
from bin.tool_api_server import dispatch_tool_api_request


class ToolAPIBuildTest(TestCase):
    def _make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        guild_root = root / "data" / "server" / "972052382315855912"
        guild_root.mkdir(parents=True, exist_ok=True)
        (guild_root / "chat_rag.md").write_text(
            "\n".join(
                [
                    "# 定義",
                    "VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
                    "",
                    "# 体験内容",
                    "- 世界中の実在観光地を再現した3Dワールドを巡る",
                    "- スタッフによる現地解説・ガイド付き進行",
                ]
            ),
            encoding="utf-8",
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
        guild_root = root / "data" / "server" / "972052382315855912"
        guild_root.mkdir(parents=True, exist_ok=True)
        (guild_root / "chat_rag.md").write_text(
            "\n".join(
                [
                    "# 定義",
                    "VRC世界旅行とは、VRChat上で世界各地の観光地を巡る旅行体験イベントである。",
                ]
            ),
            encoding="utf-8",
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
        self.assertGreaterEqual(len(tools["tools"]), 3)
        rag_tool = next(tool for tool in tools["tools"] if tool["name"] == "rag")
        self.assertIn("channel_only", rag_tool["input"])

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
