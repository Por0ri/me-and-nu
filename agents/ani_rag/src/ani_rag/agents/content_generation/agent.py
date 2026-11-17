"""Role boundary for evidence-grounded card and standalone-detail generation."""

from __future__ import annotations

import asyncio

from ani_rag.workers import GenerationQueueReconciler, GenerationWorker
from ani_rag.workers import run_worker_forever


class ContentGenerationAgent:
    def __init__(self, worker: GenerationWorker, queue_reconciler: GenerationQueueReconciler):
        self.worker = worker
        self.queue_reconciler = queue_reconciler

    async def run_once(self) -> bool:
        dispatched = await self.queue_reconciler.run_once()
        generated = await self.worker.run_once()
        return dispatched > 0 or generated

    async def run_forever(
        self, *, stop_event: asyncio.Event, poll_interval_seconds: float = 2.0,
    ) -> None:
        await run_worker_forever(
            self.run_once, stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds, worker_name="content_generation",
        )
