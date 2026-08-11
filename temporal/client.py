"""Temporal client helpers for gateway."""

from __future__ import annotations

import json
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


def _activity_matches_step(node_id: str, capability: str, step_id: str) -> bool:
    if node_id == step_id or capability == step_id:
        return True
    aliases = {
        "llm_plan": ("llm_plan", "research_plan"),
        "llm_observe": ("llm_observe", "observe_"),
        "mcp_invoke": ("mcp_invoke", "mcp_"),
        "rag_retrieve": ("rag_retrieve", "rag_"),
        "context_build": ("context_build", "context_"),
        "llm_generate": ("llm_generate", "research_synthesis"),
        "persist_turn": ("persist_turn",),
    }
    prefixes = aliases.get(step_id, (step_id,))
    return any(node_id == p or node_id.startswith(p) for p in prefixes)


async def get_temporal_step_detail(workflow_id: str, step_id: str) -> dict[str, Any]:
    handle = await _get_handle(workflow_id)
    desc = await handle.describe()
    payload: dict[str, Any] = {
        "workflow_id": workflow_id,
        "step_id": step_id,
        "runtime": "temporal",
        "workflow_status": desc.status.name,
        "workflow_type": desc.workflow_type,
        "temporal_ui_url": f"http://localhost:8233/namespaces/default/workflows/{workflow_id}",
    }
    try:
        payload["query"] = await handle.query("status")
    except Exception:
        pass

    if desc.status.name == "COMPLETED":
        try:
            payload["workflow_result"] = await handle.result()
        except Exception:
            pass

    activities: list[dict[str, Any]] = []
    try:
        history = await handle.fetch_history()
        scheduled: dict[int, Any] = {}
        for event in history.events:
            sched_attrs = getattr(event, "activity_task_scheduled_event_attributes", None)
            if sched_attrs is not None:
                scheduled[event.event_id] = sched_attrs

            completed_attrs = getattr(event, "activity_task_completed_event_attributes", None)
            if completed_attrs is None:
                continue

            sched = scheduled.get(completed_attrs.scheduled_event_id)
            if sched is None:
                continue

            capability = ""
            node_id = ""
            try:
                raw = sched.input.payloads[0].data.decode()
                args = json.loads(raw)
                if isinstance(args, list) and len(args) >= 3:
                    capability = str(args[0])
                    node_id = str(args[2])
            except Exception:
                pass

            if not _activity_matches_step(node_id, capability, step_id):
                continue

            parsed: Any = None
            try:
                parsed = json.loads(completed_attrs.result.payloads[0].data.decode())
            except Exception:
                parsed = {"raw": "unparsed activity result"}

            activities.append(
                {
                    "node_id": node_id,
                    "capability": capability,
                    "result": parsed,
                    "scheduled_event_id": completed_attrs.scheduled_event_id,
                }
            )
    except Exception as exc:
        payload["history_note"] = f"Activity history unavailable: {exc}"

    payload["activities"] = activities
    if activities:
        payload["step"] = {
            "id": step_id,
            "status": "DONE",
            "result": activities[-1]["result"],
        }
    else:
        payload["step"] = {
            "id": step_id,
            "status": "PENDING" if desc.status.name == "RUNNING" else desc.status.name,
            "result": None,
        }
    return payload
