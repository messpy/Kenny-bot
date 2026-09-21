from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from src.kennybot.ai.decision.metrics import DecisionMetricsStore
from src.kennybot.ai.decision.models import DecisionContext, ShadowDecision
from src.kennybot.ai.decision.providers.jev import JevProvider, JevProviderError
from src.kennybot.ai.decision.shadow import DecisionShadowRunner, _Breaker


class FakeProvider:
    def __init__(self, decision: ShadowDecision | None = None, *, delay: float = 0.0, error: Exception | None = None):
        self.decision = decision or ShadowDecision(
            route="normal_chat",
            route_probability=0.9,
            route_probabilities={"normal_chat": 0.9},
            external_information_probability=0.1,
            rag_probability=0.2,
            server_info_probability=0.1,
            repair_probability=0.0,
        )
        self.delay = delay
        self.error = error
        self.calls = 0

    async def evaluate(self, context: DecisionContext) -> ShadowDecision:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.decision


class DecisionShadowTests(unittest.IsolatedAsyncioTestCase):
    def test_circuit_breaker_allows_one_half_open_probe(self) -> None:
        breaker = _Breaker(threshold=1, open_seconds=10)
        breaker.record_failure(0.0)
        self.assertFalse(breaker.allows(1.0))
        self.assertTrue(breaker.allows(11.0))
        self.assertFalse(breaker.allows(11.0))
        breaker.record_success()
        self.assertTrue(breaker.allows(11.0))

    async def test_disabled_provider_call_and_task_are_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            runner = DecisionShadowRunner(
                provider=None,
                metrics=DecisionMetricsStore(Path(directory) / "metrics.jsonl"),
            )
            self.assertFalse(runner.submit(DecisionContext("hello", False, False, False)))
            await runner.shutdown()
            self.assertEqual(provider.calls, 0)
            self.assertEqual(runner.submitted_count, 0)
            self.assertEqual(runner.skipped_disabled_count, 1)

    async def test_missing_key_is_tracked_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = DecisionShadowRunner(
                provider=None,
                no_key=True,
                metrics=DecisionMetricsStore(Path(directory) / "metrics.jsonl"),
            )
            self.assertFalse(runner.submit(DecisionContext("hello", False, False, False)))
            self.assertEqual(runner.skipped_no_key_count, 1)

    async def test_success_is_normalized_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            provider = FakeProvider()
            runner = DecisionShadowRunner(
                provider=provider,
                metrics=DecisionMetricsStore(path),
                max_inflight=2,
            )
            self.assertTrue(runner.submit(DecisionContext("hello", False, False, False)))
            await asyncio.sleep(0.05)
            await runner.shutdown()
            self.assertEqual(provider.calls, 1)
            self.assertEqual(runner.completed_count, 1)
            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["route"], "normal_chat")

    async def test_provider_error_does_not_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            runner = DecisionShadowRunner(
                provider=FakeProvider(error=JevProviderError("http_error")),
                metrics=DecisionMetricsStore(path),
            )
            self.assertTrue(runner.submit(DecisionContext("hello", False, False, False)))
            await asyncio.sleep(0.05)
            await runner.shutdown()
            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["status"], "http_error")

    async def test_overload_drops_before_task_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(delay=0.2)
            runner = DecisionShadowRunner(
                provider=provider,
                metrics=DecisionMetricsStore(Path(directory) / "metrics.jsonl"),
                max_inflight=1,
            )
            self.assertTrue(runner.submit(DecisionContext("one", False, False, False)))
            self.assertFalse(runner.submit(DecisionContext("two", False, False, False)))
            self.assertEqual(runner.dropped_overload_count, 1)
            self.assertEqual(len(runner._tasks), 1)
            await runner.shutdown(drain_seconds=0.01)

    async def test_shutdown_cancels_pending_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(delay=10)
            runner = DecisionShadowRunner(
                provider=provider,
                metrics=DecisionMetricsStore(Path(directory) / "metrics.jsonl"),
            )
            runner.submit(DecisionContext("hello", False, False, False))
            await runner.shutdown(drain_seconds=0.01)
            self.assertFalse(runner._tasks)


class JevProviderTests(unittest.TestCase):
    def test_payload_contains_one_state_and_multiple_questions(self) -> None:
        provider = JevProvider(api_key="test")
        payload = provider._payload(DecisionContext("hello", True, False, False, ("image/png",)))
        self.assertEqual(payload["model"], "jev-latest")
        self.assertIn("state", payload)
        questions = payload["questions"]
        self.assertIsInstance(questions, dict)
        self.assertEqual(questions["route"]["type"], "choice")
        self.assertEqual(questions["needs_rag"]["type"], "noul")

    def test_invalid_probability_is_rejected(self) -> None:
        with self.assertRaises(JevProviderError):
            JevProvider._number({"value": float("nan")}, ("value",), required=True)
