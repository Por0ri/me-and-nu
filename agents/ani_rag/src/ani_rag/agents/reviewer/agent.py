"""Role boundary for independent quality and editorial review."""

from __future__ import annotations

import asyncio

from ani_rag.workers import ReviewWorker
from ani_rag.workers import run_worker_forever


class IndependentReviewAgent:
    def __init__(self, worker: ReviewWorker):
        self.worker = worker

    async def run_once(self) -> bool:
        return await self.worker.run_once()

    async def run_forever(
        self, *, stop_event: asyncio.Event, poll_interval_seconds: float = 2.0,
    ) -> None:
        await run_worker_forever(
            self.run_once, stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds, worker_name="reviewer",
        )
