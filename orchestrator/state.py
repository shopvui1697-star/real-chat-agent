"""Redis-backed workflow state store with CAS versioning."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

import redis
from pydantic import BaseModel, Field

from orchestrator.metrics import Metrics


class WorkflowStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DLQ = "DLQ"


class NodeState(BaseModel):
    id: str
    status: NodeStatus = NodeStatus.PENDING
    agent: str | None = None
    result: dict[str, Any] | None = None
    attempts: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    dispatched_at: str | None = None
    dispatch_generation: int = 0


class WorkflowState(BaseModel):
    id: str
    name: str
    status: WorkflowStatus = WorkflowStatus.QUEUED
    version: int = 1
    tenant_id: str = "default"
    cleanup_pending: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    routing_decisions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


class CasConflictError(Exception):
    """Compare-and-swap write lost to a concurrent writer."""


class StateStore:
    PREFIX = "workflow:"
    ACTIVE_INDEX = "workflows:active"
    TASK_PREFIX = "workflow:task:"

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._redis = client or get_redis()
        self._metrics = Metrics(client=self._redis)

    def _key(self, workflow_id: str) -> str:
        return f"{self.PREFIX}{workflow_id}"

    def _task_key(self, workflow_id: str, node_id: str) -> str:
        return f"{self.TASK_PREFIX}{workflow_id}:{node_id}"

    def create(
        self,
        name: str,
        node_ids: list[str],
        context: dict[str, Any],
        tenant_id: str = "default",
    ) -> WorkflowState:
        workflow_id = str(uuid4())
        nodes = {nid: NodeState(id=nid) for nid in node_ids}
        state = WorkflowState(
            id=workflow_id,
            name=name,
            context=context,
            tenant_id=tenant_id,
            version=1,
        )
        state.nodes = nodes
        self.save(state)
        self.add_to_active_index(workflow_id)
        return state

    def get(self, workflow_id: str) -> WorkflowState | None:
        raw = self._redis.get(self._key(workflow_id))
        if not raw:
            return None
        return WorkflowState.model_validate_json(raw)

    def save(self, state: WorkflowState) -> None:
        state.updated_at = _now()
        self._redis.set(self._key(state.id), state.model_dump_json())
        if state.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        ) and not state.cleanup_pending:
            self.remove_from_active_index(state.id)

    def save_with_cas(self, state: WorkflowState, expected_version: int) -> bool:
        key = self._key(state.id)
        while True:
            try:
                self._redis.watch(key)
                raw = self._redis.get(key)
                if raw is None:
                    self._redis.unwatch()
                    return False
                current = WorkflowState.model_validate_json(raw)
                if current.version != expected_version:
                    self._redis.unwatch()
                    self._metrics.incr("orchestrator_cas_conflict_total")
                    return False
                state.version = expected_version + 1
                state.updated_at = _now()
                pipe = self._redis.pipeline()
                pipe.set(key, state.model_dump_json())
                pipe.execute()
                if state.status in (
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.FAILED,
                    WorkflowStatus.CANCELLED,
                ) and not state.cleanup_pending:
                    self.remove_from_active_index(state.id)
                return True
            except redis.WatchError:
                continue

    def update_cas(
        self,
        workflow_id: str,
        mutator: Callable[[WorkflowState], WorkflowState],
        max_retries: int = 5,
    ) -> WorkflowState | None:
        for _ in range(max_retries):
            state = self.get(workflow_id)
            if state is None:
                return None
            expected = state.version
            new_state = mutator(state.model_copy(deep=True))
            if self.save_with_cas(new_state, expected):
                return new_state
        raise CasConflictError(f"CAS retries exhausted for workflow {workflow_id}")

    def delete(self, workflow_id: str) -> None:
        self._redis.delete(self._key(workflow_id))
        self.remove_from_active_index(workflow_id)

    def update_node(
        self,
        workflow_id: str,
        node_id: str,
        **updates: Any,
    ) -> WorkflowState:
        def mutate(state: WorkflowState) -> WorkflowState:
            node = state.nodes[node_id]
            for key, value in updates.items():
                setattr(node, key, value)
            return state

        result = self.update_cas(workflow_id, mutate)
        if result is None:
            raise KeyError(f"Workflow {workflow_id} not found")
        return result

    def append_routing_decision(
        self,
        workflow_id: str,
        decision: dict[str, Any],
    ) -> WorkflowState:
        def mutate(state: WorkflowState) -> WorkflowState:
            state.routing_decisions.append(decision)
            return state

        result = self.update_cas(workflow_id, mutate)
        if result is None:
            raise KeyError(f"Workflow {workflow_id} not found")
        return result

    def add_to_active_index(self, workflow_id: str) -> None:
        self._redis.sadd(self.ACTIVE_INDEX, workflow_id)

    def remove_from_active_index(self, workflow_id: str) -> None:
        self._redis.srem(self.ACTIVE_INDEX, workflow_id)

    def list_active_workflow_ids(self) -> list[str]:
        return list(self._redis.smembers(self.ACTIVE_INDEX))

    def set_task_id(self, workflow_id: str, node_id: str, task_id: str) -> None:
        self._redis.set(self._task_key(workflow_id, node_id), task_id, ex=86400)

    def get_task_id(self, workflow_id: str, node_id: str) -> str | None:
        return self._redis.get(self._task_key(workflow_id, node_id))

    def clear_task_id(self, workflow_id: str, node_id: str) -> None:
        self._redis.delete(self._task_key(workflow_id, node_id))
