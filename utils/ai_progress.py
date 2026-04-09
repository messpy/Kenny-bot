from __future__ import annotations

import asyncio
from collections import deque


class AIProgressTracker:
    def __init__(self, concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self._lock = asyncio.Lock()
        self._waiting: deque[str] = deque()
        self._active: set[str] = set()
        self._next_id = 0

    async def create_ticket(self) -> str:
        async with self._lock:
            self._next_id += 1
            ticket = f"ai-{self._next_id}"
            self._waiting.append(ticket)
            return ticket

    async def acquire(self, ticket: str) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            try:
                self._waiting.remove(ticket)
            except ValueError:
                pass
            self._active.add(ticket)

    async def release(self, ticket: str) -> None:
        should_release = False
        async with self._lock:
            try:
                self._waiting.remove(ticket)
            except ValueError:
                pass
            if ticket in self._active:
                self._active.remove(ticket)
                should_release = True
        if should_release:
            self._semaphore.release()

    def render(self, ticket: str, elapsed_seconds: int) -> str:
        if ticket in self._waiting:
            return f"{self._waiting.index(ticket) + 1}人待ち"
        return f"{max(1, int(elapsed_seconds))}秒推論中"
