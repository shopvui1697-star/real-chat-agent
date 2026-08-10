"""Cascade cleanup on cancel/delete — idempotent, sweep-resumable."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.dlq import audit_event
from orchestrator.idempotency import IdempotencyGuard
from orchestrator.metrics import Metrics
from orchestrator.state import NodeStatus, StateStore, WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

ARTIFACTS_ROOT = Path(__file__).resolve().parents[1] / "data" / "artifacts"


class CascadeCleanup:
    def __init__(self, store: StateStore | None = None) -> None:
        self.store = store or StateStore()
        self.metrics = Metrics()
        self.idem = IdempotencyGuard()

    def request_cancel(self, workflow_id: str) -> WorkflowState | None:
        def mutate(state: WorkflowState) -> WorkflowState:
            if state.status in (WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED):
                return state
            state.status = WorkflowStatus.CANCELLED
            state.cleanup_pending = True
            for node in state.nodes.values():
                if node.status == NodeStatus.RUNNING:
                    node.status = NodeStatus.FAILED
                    node.error = "cancelled"
            return state

        updated = self.store.update_cas(workflow_id, mutate)
        if updated:
            audit_event("workflow_cancelled", {"workflow_id": workflow_id})
            self.run_cleanup(workflow_id)
            return self.store.get(workflow_id)
        return updated

    def request_delete(self, workflow_id: str) -> bool:
        state = self.store.get(workflow_id)
        if state is None:
            return False
        if state.status != WorkflowStatus.CANCELLED:
            self.request_cancel(workflow_id)
        self.run_cleanup(workflow_id)
        final = self.store.get(workflow_id)
        if final and not final.cleanup_pending:
            self.store.delete(workflow_id)
            audit_event("workflow_deleted", {"workflow_id": workflow_id})
            return True
        return False

    def run_cleanup(self, workflow_id: str) -> bool:
        """Best-effort ordered cleanup; returns True when fully done."""
        state = self.store.get(workflow_id)
        if state is None or not state.cleanup_pending:
            return True

        self._revoke_inflight_tasks(workflow_id, state)
        self._delete_artifacts(state.tenant_id, workflow_id)
        self._purge_auxiliary_keys(workflow_id, state)

        def mutate(s: WorkflowState) -> WorkflowState:
            if not s.cleanup_pending:
                return s
            s.cleanup_pending = False
            return s

        updated = self.store.update_cas(workflow_id, mutate)
        if updated:
            audit_event("cleanup_completed", {"workflow_id": workflow_id})
            self.metrics.incr("orchestrator_cleanup_completed_total")
            if updated.status == WorkflowStatus.CANCELLED:
                self.store.remove_from_active_index(workflow_id)
        return updated is not None and not updated.cleanup_pending

    def _revoke_inflight_tasks(self, workflow_id: str, state: WorkflowState) -> None:
        from orchestrator.celery_app import celery_app

        for node_id, node in state.nodes.items():
            if node.status != NodeStatus.RUNNING:
                continue
            task_id = self.store.get_task_id(workflow_id, node_id)
            if task_id:
                try:
                    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                    audit_event(
                        "task_revoked",
                        {"workflow_id": workflow_id, "node": node_id, "task_id": task_id},
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort revoke
                    logger.warning("Revoke failed for %s: %s", task_id, exc)

    def _delete_artifacts(self, tenant_id: str, workflow_id: str) -> None:
        path = ARTIFACTS_ROOT / tenant_id / workflow_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            audit_event(
                "artifacts_deleted",
                {"workflow_id": workflow_id, "tenant_id": tenant_id, "path": str(path)},
            )

    def _purge_auxiliary_keys(self, workflow_id: str, state: WorkflowState) -> None:
        for node_id, node in state.nodes.items():
            self.idem.release(workflow_id, node_id, node.dispatch_generation)
            self.store.clear_task_id(workflow_id, node_id)
