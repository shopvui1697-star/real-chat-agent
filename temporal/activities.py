"""Temporal activities — wrap existing step executors."""

from __future__ import annotations

from typing import Any

from temporalio import activity

from steps.base import StepContext
from steps.registry import get_executor


@activity.defn(name="execute_capability")
def execute_capability(
    capability: str,
    workflow_id: str,
    node_id: str,
    context: dict[str, Any],
    params: dict[str, Any] | None = None,
    agent_name: str = "temporal_activity",
) -> dict[str, Any]:
    executor = get_executor(capability)
    if executor is None:
        raise ValueError(f"No executor for capability: {capability}")
    ctx = StepContext(
        workflow_id=workflow_id,
        node_id=node_id,
        capability=capability,
        context=context,
        params=params or {},
        agent_name=agent_name,
    )
    result = executor.execute(ctx)
    return {
        "context_delta": result.context_delta,
        "output": result.output,
    }


def merge_context(context: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(context)
    merged.update(delta)
    return merged
