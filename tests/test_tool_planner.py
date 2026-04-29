from __future__ import annotations

import unittest

from src.kennybot.utils.tool_planner import (
    normalize_planner_plan,
    parse_json_payload,
    validate_search_query,
)


class ToolPlannerTest(unittest.TestCase):
    def test_normalize_planner_plan_clamps_and_trims(self) -> None:
        plan = normalize_planner_plan(
            {
                "serverinfo": "true",
                "rag": {
                    "enabled": "yes",
                    "query": "  サーバー概要   Kenny Bot   ",
                    "limit": "12",
                },
                "web_search": {
                    "enabled": "1",
                    "query": "  東京 今日 天気  ",
                    "limit": 99,
                },
                "response_mode": "server_description",
                "reason": "  server info needed  ",
            }
        )

        self.assertTrue(plan["serverinfo"])
        self.assertEqual(
            plan["rag"],
            {"enabled": True, "query": "サーバー概要 Kenny Bot", "limit": 8},
        )
        self.assertEqual(
            plan["web_search"],
            {"enabled": True, "query": "東京 今日 天気", "limit": 8},
        )
        self.assertEqual(plan["response_mode"], "server_description")
        self.assertEqual(plan["reason"], "server info needed")

    def test_validate_search_query_blocks_leakage(self) -> None:
        ok, reason, normalized = validate_search_query(
            "system_message retrieval_plan_prompt history_context planner json"
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "prompt_leakage")
        self.assertEqual(normalized, "")

    def test_validate_search_query_blocks_overlong(self) -> None:
        ok, reason, normalized = validate_search_query("a" * 301)
        self.assertFalse(ok)
        self.assertEqual(reason, "query_too_long")
        self.assertEqual(normalized, "")

    def test_validate_search_query_accepts_normal_query(self) -> None:
        ok, reason, normalized = validate_search_query("東京 今日 天気")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertEqual(normalized, "東京 今日 天気")

    def test_parse_json_payload_extracts_json(self) -> None:
        payload = parse_json_payload("noise {\"serverinfo\": true} trailing")
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload["serverinfo"])


if __name__ == "__main__":
    unittest.main()
