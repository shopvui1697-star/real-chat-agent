"""Temporal client helpers for gateway."""

from __future__ import annotations

import os
from typing import Any

from temporalio.client import Client, WorkflowHandle

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "chat-deep")

_client: Client | None = None


async def get_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(TEMPORAL_HOST)
    return _client


async def start_deep_react(context: dict[str, Any], workflow_id: str) -> str:
    from temporal.workflows import ChatReactDeepWorkflow

    client = await get_client()
    handle = await client.start_workflow(
        ChatReactDeepWorkflow.run,
        context,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return handle.id


async def start_research(context: dict[str, Any], workflow_id: str) -> str:
    from temporal.workflows import ChatResearchWorkflow

    client = await get_client()
    handle = await client.start_workflow(
        ChatResearchWorkflow.run,
        context,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return handle.id


async def signal_approve(workflow_id: str) -> None:
    handle = await _get_handle(workflow_id)
    await handle.signal("approve")


async def signal_reject(workflow_id: str) -> None:
    handle = await _get_handle(workflow_id)
    await handle.signal("reject")


async def get_workflow_status(workflow_id: str) -> dict[str, Any]:
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id)
    desc = await handle.describe()
    result: dict[str, Any] = {
        "workflow_id": workflow_id,
        "status": desc.status.name,
        "runtime": "temporal",
    }
    try:
        query = await handle.query("status")
        result["query"] = query
    except Exception:
        pass
    if desc.status.name == "COMPLETED":
        try:
            result["result"] = await handle.result()
        except Exception:
            pass
    return result


async def _get_handle(workflow_id: str) -> WorkflowHandle:
    client = await get_client()
    return client.get_workflow_handle(workflow_id)
