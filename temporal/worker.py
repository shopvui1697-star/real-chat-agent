"""Temporal worker entrypoint."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from temporal.activities import execute_capability
from temporal.workflows import ChatReactDeepWorkflow, ChatResearchWorkflow

TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "chat-deep")
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")


async def main() -> None:
    client = await Client.connect(TEMPORAL_HOST)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ChatReactDeepWorkflow, ChatResearchWorkflow],
        activities=[execute_capability],
        activity_executor=ThreadPoolExecutor(max_workers=10),
    )
    print(f"Temporal worker listening on {TEMPORAL_HOST} queue={TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
