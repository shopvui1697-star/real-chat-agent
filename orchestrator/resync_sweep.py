"""Background resync sweep — level-triggered reconciliation for stuck workflows."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from orchestrator.cleanup import CascadeCleanup
from orchestrator.config import SWEEP_STUCK_MULTIPLIER
from orchestrator.dag import simulate_duration
from orchestrator.dlq import audit_event
from orchestrator.metrics import Metrics
from orchestrator.orchestrator import Orchestrator
from orchestrator.state import NodeStatus, StateStore, WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class ResyncSweep:
    def __init__(
        self,
        store: StateStore | None = None,
        orchestrator: Orchestrator | None = None,
    ) -> None:
        self.store = store or StateStore()
        self.orchestrator = orchestrator or Orchestrator(store=self.store)
        self.cleanup = CascadeCleanup(store=self.store)
        self.metrics = Metrics()

    def run_once(self) -> dict[str, int]:
        stats = {"cleanup": 0, "reconciled": 0, "re_driven": 0}
        for workflow_id in self.store.list_active_workflow_ids():
            state = self.store.get(workflow_id)
            if state is None:
                self.store.remove_from_active_index(workflow_id)
                continue

            if state.cleanup_pending:
                if self.cleanup.run_cleanup(workflow_id):
                    stats["cleanup"] += 1
                continue

            if state.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            ):
                self.store.remove_from_active_index(workflow_id)
                continue

            action = self._reconcile_workflow(state)
            if action:
                stats["reconciled"] += 1
                if action == "re_drive":
                    stats["re_driven"] += 1

        return stats

    def _reconcile_workflow(self, state: WorkflowState) -> str | None:
        now = datetime.now(timezone.utc)
        stuck_nodes: list[str] = []

        for node_id, node in state.nodes.items():
            if node.status != NodeStatus.RUNNING:
                continue
            started = _parse_ts(node.started_at or node.dispatched_at)
            if started is None:
                continue
            expected = simulate_duration(node_id) * SWEEP_STUCK_MULTIPLIER + 5
            elapsed = (now - started).total_seconds()
            if elapsed > expected:
                stuck_nodes.append(node_id)

        if not stuck_nodes:
            pending_or_running = any(
                n.status in (NodeStatus.PENDING, NodeStatus.RUNNING)
                for n in state.nodes.values()
            )
            if pending_or_running and state.status == WorkflowStatus.RUNNING:
                self.orchestrator.schedule(state.id)
            return None

        for node_id in stuck_nodes:
            self._reset_stuck_node(state.id, node_id)

        self.metrics.incr("orchestrator_resync_reconciled_total")
        audit_event(
            "resync_reconciled",
            {"workflow_id": state.id, "stuck_nodes": stuck_nodes},
        )
        self.orchestrator.schedule(state.id)
        return "re_drive"

    def _reset_stuck_node(self, workflow_id: str, node_id: str) -> None:
        def mutate(state: WorkflowState) -> WorkflowState:
            node = state.nodes[node_id]
            if node.status != NodeStatus.RUNNING:
                return state
            node.status = NodeStatus.PENDING
            node.agent = None
            node.dispatch_generation += 1
            node.started_at = None
            node.dispatched_at = None
            node.error = "reset_by_resync_sweep"
            return state

        self.store.update_cas(workflow_id, mutate)
        self.store.clear_task_id(workflow_id, node_id)
