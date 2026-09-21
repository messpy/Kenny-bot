from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from src.kennybot.ai.decision.metrics import DecisionMetricsStore
from src.kennybot.ai.decision.models import (
    DECISION_SCHEMA_VERSION,
    DecisionContext,
    ShadowDecision,
    ShadowMetrics,
)


logger = logging.getLogger(__name__)


class DecisionProvider(Protocol):
    async def evaluate(self, context: DecisionContext) -> ShadowDecision: ...


@dataclass
class _Breaker:
    threshold: int
    open_seconds: float
    failures: int = 0
    opened_at: float | None = None
    state: str = "closed"
    probe_in_flight: bool = False

    def allows(self, now: float) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open" and self.opened_at is not None and now - self.opened_at >= self.open_seconds:
            self.state = "half_open"
            self.probe_in_flight = False
        if self.state == "half_open" and not self.probe_in_flight:
            self.probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.state = "closed"
        self.probe_in_flight = False

    def record_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now
            self.state = "open"
            self.probe_in_flight = False


class DecisionShadowRunner:
    def __init__(
        self,
        *,
        provider: DecisionProvider | None,
        metrics: DecisionMetricsStore,
        timeout_seconds: float = 1.5,
        max_concurrency: int = 2,
        max_inflight: int = 2,
        circuit_breaker_failure_threshold: int = 5,
        circuit_breaker_open_seconds: float = 30.0,
        schema_version: str = DECISION_SCHEMA_VERSION,
        no_key: bool = False,
        unavailable_reason: str | None = None,
    ) -> None:
        self.provider = provider
        self.no_key = no_key
        self.unavailable_reason = unavailable_reason
        self.metrics = metrics
        self.timeout_seconds = max(0.1, timeout_seconds)
        self.max_concurrency = max(1, max_concurrency)
        self.max_inflight = max(1, max_inflight)
        self._tasks: set[asyncio.Task[None]] = set()
        self._metric_tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._breaker = _Breaker(max(1, circuit_breaker_failure_threshold), max(1.0, circuit_breaker_open_seconds))
        self._closing = False
        self.schema_version = schema_version
        self.eligible_count = 0
        self.submitted_count = 0
        self.completed_count = 0
        self.dropped_overload_count = 0
        self.skipped_disabled_count = 0
        self.skipped_no_key_count = 0
        self.circuit_open_count = 0
        self.skipped_unavailable_count = 0

    def submit(self, context: DecisionContext) -> bool:
        self.eligible_count += 1
        if self._closing or self.provider is None:
            if self.unavailable_reason:
                self.skipped_unavailable_count += 1
            elif self.no_key:
                self.skipped_no_key_count += 1
            else:
                self.skipped_disabled_count += 1
            return False
        if len(self._tasks) >= self.max_inflight:
            self.dropped_overload_count += 1
            self._write_status("dropped_overload")
            return False
        if not self._breaker.allows(time.monotonic()):
            self.circuit_open_count += 1
            self._write_status("circuit_open")
            return False
        task = asyncio.create_task(self._evaluate(context), name="kennybot-jev-shadow")
        self._tasks.add(task)
        self.submitted_count += 1
        task.add_done_callback(self._task_done)
        return True

    async def _evaluate(self, context: DecisionContext) -> None:
        evaluation_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            async with self._semaphore:
                decision = await asyncio.wait_for(self.provider.evaluate(context), timeout=self.timeout_seconds)  # type: ignore[union-attr]
            self._breaker.record_success()
            self.completed_count += 1
            metrics = ShadowMetrics(
                evaluation_id=evaluation_id,
                decision_schema_version=self.schema_version,
                status=decision.status,
                latency_ms=decision.latency_ms or (time.perf_counter() - started) * 1000,
                route=decision.route,
                route_probability=decision.route_probability,
                route_probabilities=decision.route_probabilities,
                external_information_probability=decision.external_information_probability,
                rag_probability=decision.rag_probability,
                server_info_probability=decision.server_info_probability,
                repair_probability=decision.repair_probability,
                existing_raw_plan="not_available",
                existing_effective_plan="not_available",
            )
            await asyncio.to_thread(self.metrics.safe_append, metrics)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = getattr(exc, "status", "timeout" if isinstance(exc, asyncio.TimeoutError) else "internal_error")
            self._breaker.record_failure(time.monotonic())
            metrics = ShadowMetrics(
                evaluation_id=evaluation_id,
                decision_schema_version=self.schema_version,
                status=str(status),
                latency_ms=(time.perf_counter() - started) * 1000,
                existing_raw_plan="not_available",
                existing_effective_plan="not_available",
            )
            await asyncio.to_thread(self.metrics.safe_append, metrics)
            logger.debug("Jev shadow evaluation failed: %s", status)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unhandled Jev shadow task failure")

    def _write_status(self, status: str) -> None:
        metrics = ShadowMetrics(
            evaluation_id=uuid.uuid4().hex,
            decision_schema_version=self.schema_version,
            status=status,
        )
        try:
            task = asyncio.create_task(asyncio.to_thread(self.metrics.safe_append, metrics))
            self._metric_tasks.add(task)
            task.add_done_callback(self._metric_tasks.discard)
        except RuntimeError:
            self.metrics.safe_append(metrics)

    async def shutdown(self, *, drain_seconds: float = 0.25) -> None:
        self._closing = True
        tasks = tuple(self._tasks)
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=max(0.0, drain_seconds))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                self._task_done(task)
        if self._metric_tasks:
            await asyncio.gather(*tuple(self._metric_tasks), return_exceptions=True)
