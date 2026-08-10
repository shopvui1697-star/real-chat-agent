"""Celery task execution with chat step executors."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from celery.exceptions import Retry
from celery.signals import task_failure

from orchestrator.celery_app import celery_app
from orchestrator.dag import build_dag, load_workflow_spec_dict, node_capability, node_params
from orchestrator.dlq import audit_event
from orchestrator.idempotency import IdempotencyGuard
from orchestrator.orchestrator import Orchestrator
from orchestrator.registry import AgentRegistry
from orchestrator.state import StateStore, WorkflowStatus
from steps.base import StepContext
from steps.registry import get_executor

logger = logging.getLogger(__name__)

try:
    from steps.rag import seed_default_kb

    seed_default_kb()
except Exception:
    logger.warning("Could not seed default KB on import")


class TransientTaskError(RuntimeError):
    """Retryable step failure."""


class PermanentTaskError(RuntimeError):
    """Non-retryable step failure."""


def _workflow_cancelled(workflow_id: str) -> bool:
    state = StateStore().get(workflow_id)
    return state is not None and state.status == WorkflowStatus.CANCELLED


def _load_graph(state) -> Any:
    spec_data = state.context.get("_workflow_spec")
    if not spec_data:
        raise RuntimeError("Missing _workflow_spec in workflow context")
    spec = load_workflow_spec_dict(spec_data)
    return build_dag(spec), spec


@celery_app.task(
    bind=True,
    name="orchestrator.execute_node",
    autoretry_for=(TransientTaskError,),
    retry_backoff=True,
    retry_backoff_max=8,
    retry_jitter=True,
    max_retries=3,
)
def execute_node(
    self,
    workflow_id: str,
    node_id: str,
    agent_name: str,
    capability: str,
    dispatch_generation: int = 0,
) -> dict[str, Any]:
    idem = IdempotencyGuard()
    if not idem.acquire(workflow_id, node_id, dispatch_generation):
        cached = idem.get_result(workflow_id, node_id, dispatch_generation)
        if cached:
            return json.loads(cached)
        return {"status": "duplicate_skipped"}

    if _workflow_cancelled(workflow_id):
        idem.release(workflow_id, node_id, dispatch_generation)
        return {"status": "cancelled"}

    registry = AgentRegistry()
    agent = registry.get(agent_name)
    if agent is None:
        raise PermanentTaskError(f"Unknown agent: {agent_name}")

    state = StateStore().get(workflow_id)
    if state is None:
        raise PermanentTaskError(f"Workflow not found: {workflow_id}")

    graph, _spec = _load_graph(state)
    params = node_params(graph, node_id)
    executor = get_executor(capability)
    if executor is None:
        raise PermanentTaskError(f"No executor for capability: {capability}")

    attempt = self.request.retries + 1
    audit_event(
        "task_started",
        {
            "workflow_id": workflow_id,
            "node": node_id,
            "agent": agent_name,
            "capability": capability,
            "attempt": attempt,
            "generation": dispatch_generation,
        },
    )

    if _workflow_cancelled(workflow_id):
        idem.release(workflow_id, node_id, dispatch_generation)
        return {"status": "cancelled"}

    try:
        step_ctx = StepContext(
            workflow_id=workflow_id,
            node_id=node_id,
            capability=capability,
            context=dict(state.context),
            params=params,
            agent_name=agent_name,
        )
        step_result = executor.execute(step_ctx)
    except TransientTaskError:
        raise
    except Exception as exc:
        logger.exception("Step %s failed on %s", capability, node_id)
        err_text = str(exc).lower()
        is_transient = isinstance(exc, (ConnectionError, TimeoutError)) or any(
            code in err_text for code in ("429", "503", "502", "504", "rate limit")
        )
        action = Orchestrator().on_node_failure(
            workflow_id,
            node_id,
            agent_name,
            error=str(exc),
            to_dlq=not is_transient,
            attempt=attempt,
            dispatch_generation=dispatch_generation,
        )
        if action == "fallback":
            idem.release(workflow_id, node_id, dispatch_generation)
            return {"status": "fallback_dispatched"}
        if action == "cancelled":
            idem.release(workflow_id, node_id, dispatch_generation)
            return {"status": "cancelled"}
        if is_transient:
            raise TransientTaskError(str(exc)) from exc
        idem.release(workflow_id, node_id, dispatch_generation)
        raise PermanentTaskError(str(exc)) from exc

    result = {
        "node": node_id,
        "agent": agent_name,
        "capability": capability,
        "context_delta": step_result.context_delta,
        "output": step_result.output,
        "attempt": attempt,
        "generation": dispatch_generation,
    }

    accepted = Orchestrator().on_node_success(
        workflow_id,
        node_id,
        agent_name,
        result,
        dispatch_generation=dispatch_generation,
    )
    if accepted:
        idem.store_result(workflow_id, node_id, dispatch_generation, json.dumps(result))
        audit_event(
            "task_completed",
            {"workflow_id": workflow_id, "node": node_id, "agent": agent_name, "attempt": attempt},
        )
    else:
        idem.release(workflow_id, node_id, dispatch_generation)
    return result


@celery_app.task(name="orchestrator.resync_sweep")
def resync_sweep_task() -> dict[str, int]:
    from orchestrator.resync_sweep import ResyncSweep

    return ResyncSweep().run_once()


@task_failure.connect
def handle_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **other,
) -> None:
    if sender is None or sender.name != "orchestrator.execute_node":
        return
    if exception is None or isinstance(exception, Retry):
        return
    if not isinstance(exception, (TransientTaskError, PermanentTaskError)):
        return
    if kwargs is None:
        return

    task = sender
    if isinstance(exception, TransientTaskError) and task.request.retries < task.max_retries:
        return

    Orchestrator().on_node_failure(
        kwargs.get("workflow_id", ""),
        kwargs.get("node_id", ""),
        kwargs.get("agent_name", ""),
        error=str(exception),
        to_dlq=True,
        attempt=task.request.retries + 1,
        dispatch_generation=kwargs.get("dispatch_generation", 0),
    )
