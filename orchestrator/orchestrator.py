"""Workflow orchestrator: schedule ready nodes and handle completion."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from orchestrator.chat_rules import evaluate_chat_rules, preferred_llm_from_actions
from orchestrator.dag import (
    WorkflowSpec,
    build_dag,
    get_ready_nodes,
    load_workflow_spec_dict,
    node_capability,
    step_for_node,
)
from orchestrator.dlq import audit_event
from orchestrator.metrics import Metrics
from orchestrator.router import Router
from orchestrator.state import NodeStatus, StateStore, WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        store: StateStore | None = None,
        router: Router | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.store = store or StateStore()
        self.router = router or Router()
        self.metrics = metrics or Metrics()

    @staticmethod
    def _spec_from_state(state: WorkflowState) -> WorkflowSpec:
        spec_data = state.context.get("_workflow_spec")
        if not spec_data:
            raise ValueError("Workflow context missing _workflow_spec")
        return load_workflow_spec_dict(spec_data)

    def start_workflow(
        self,
        spec: WorkflowSpec,
        context: dict[str, Any],
        tenant_id: str = "default",
    ) -> str:
        graph = build_dag(spec)
        enriched_context = {
            **context,
            "_workflow_spec": spec.model_dump(),
        }
        state = self.store.create(
            name=spec.name,
            node_ids=list(graph.nodes),
            context=enriched_context,
            tenant_id=tenant_id,
        )

        def mutate(s: WorkflowState) -> WorkflowState:
            s.status = WorkflowStatus.RUNNING
            return s

        updated = self.store.update_cas(state.id, mutate)
        assert updated is not None
        audit_event(
            "workflow_started",
            {"workflow_id": state.id, "name": spec.name, "version": updated.version},
        )
        self.schedule(state.id)
        return state.id

    def schedule(self, workflow_id: str, spec: WorkflowSpec | None = None) -> None:
        state = self.store.get(workflow_id)
        if state is None or state.status == WorkflowStatus.CANCELLED:
            return

        if spec is None:
            spec = self._spec_from_state(state)

        graph = build_dag(spec)
        self._handle_stragglers(workflow_id, spec, graph)

        state = self.store.get(workflow_id)
        if state is None or state.status == WorkflowStatus.CANCELLED:
            return

        completed = {nid for nid, n in state.nodes.items() if n.status == NodeStatus.DONE}
        skipped = {nid for nid, n in state.nodes.items() if n.status == NodeStatus.SKIPPED}
        running = {nid for nid, n in state.nodes.items() if n.status == NodeStatus.RUNNING}
        failed = {
            nid
            for nid, n in state.nodes.items()
            if n.status in (NodeStatus.FAILED, NodeStatus.DLQ)
        }

        terminal_count = len(completed) + len(skipped)
        if failed:

            def fail_wf(s: WorkflowState) -> WorkflowState:
                s.status = WorkflowStatus.FAILED
                return s

            self.store.update_cas(workflow_id, fail_wf)
            return

        if terminal_count == len(state.nodes):

            def complete_wf(s: WorkflowState) -> WorkflowState:
                s.status = WorkflowStatus.COMPLETED
                return s

            self.store.update_cas(workflow_id, complete_wf)
            audit_event("workflow_completed", {"workflow_id": workflow_id})
            return

        ready = get_ready_nodes(graph, completed, running, failed, skipped)
        for node_id in ready:
            self._dispatch_node(workflow_id, node_id, spec, graph)

    def _should_skip_node(self, graph: Any, node_id: str, state: WorkflowState) -> bool:
        step = step_for_node(graph, node_id)
        if not step.optional:
            return False
        ctx = state.context
        cap = step.capability or node_id
        if cap == "rag_retrieve":
            if step.params.get("source") == "parsed_documents":
                return False
            return not ctx.get("needs_kb")
        if cap == "parse_docs":
            return not ctx.get("has_attachments")
        if cap == "mcp_invoke":
            planned = ctx.get("planned_tools") or []
            return len(planned) == 0
        return False

    def _mark_skipped(self, workflow_id: str, node_id: str, reason: str = "optional") -> None:
        def mutate(s: WorkflowState) -> WorkflowState:
            n = s.nodes[node_id]
            n.status = NodeStatus.SKIPPED
            n.finished_at = datetime.now(timezone.utc).isoformat()
            n.result = {"skipped": True, "reason": reason}
            return s

        self.store.update_cas(workflow_id, mutate)
        audit_event("node_skipped", {"workflow_id": workflow_id, "node": node_id, "reason": reason})

    def _handle_stragglers(self, workflow_id: str, spec: WorkflowSpec, graph: Any) -> None:
        state = self.store.get(workflow_id)
        if state is None:
            return
        now = datetime.now(timezone.utc)
        for node_id, node in state.nodes.items():
            if node.status != NodeStatus.RUNNING:
                continue
            step = step_for_node(graph, node_id)
            timeout = step.timeout_sec or (60 if step.capability == "parse_docs" else 30)
            started = node.started_at or node.dispatched_at
            if not started:
                continue
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            elapsed = (now - started_dt).total_seconds()
            if elapsed <= timeout:
                continue
            if step.optional:
                if node.agent:
                    self.router.registry.decrement_load(node.agent)
                self._mark_skipped(workflow_id, node_id, reason="timeout_optional")
            else:
                logger.warning("Straggler node %s exceeded %ss (workflow %s)", node_id, timeout, workflow_id)

    def _run_rules_inline(self, workflow_id: str, node_id: str) -> None:
        state = self.store.get(workflow_id)
        if state is None:
            return

        result = evaluate_chat_rules(state.context)
        self.metrics.observe_rule_eval_ms(result.eval_ms)
        llm_agent = preferred_llm_from_actions(result.actions) or "llm_default_v1"
        context_delta = {
            "intent": result.intent,
            "workflow_template": result.workflow_template,
            "llm_agent": llm_agent,
        }

        def mutate(s: WorkflowState) -> WorkflowState:
            n = s.nodes[node_id]
            n.status = NodeStatus.DONE
            n.finished_at = datetime.now(timezone.utc).isoformat()
            n.result = {"context_delta": context_delta, "actions": result.actions}
            s.context.update(context_delta)
            return s

        self.store.update_cas(workflow_id, mutate)
        self.store.append_routing_decision(
            workflow_id,
            {
                "node": node_id,
                "matched": result.matched,
                "actions": result.actions,
                "eval_ms": result.eval_ms,
            },
        )
        audit_event(
            "rule_evaluated",
            {
                "workflow_id": workflow_id,
                "node": node_id,
                "matched": result.matched,
                "actions": result.actions,
            },
        )
        self.schedule(workflow_id)

    def _dispatch_node(
        self,
        workflow_id: str,
        node_id: str,
        spec: WorkflowSpec,
        graph: Any,
    ) -> None:
        state = self.store.get(workflow_id)
        if state is None or state.status == WorkflowStatus.CANCELLED:
            return

        node = state.nodes.get(node_id)
        if node is not None and node.status in (NodeStatus.RUNNING, NodeStatus.DONE):
            return

        capability = node_capability(graph, node_id)

        if self._should_skip_node(graph, node_id, state):
            self._mark_skipped(workflow_id, node_id)
            self.schedule(workflow_id, spec)
            return

        if capability == "rules":
            self._run_rules_inline(workflow_id, node_id)
            return

        preferred_agent: str | None = None
        if capability == "llm_generate":
            rule_result = evaluate_chat_rules(state.context)
            preferred_agent = preferred_llm_from_actions(rule_result.actions) or "llm_default_v1"

        agent = self.router.pick_agent(capability, preferred=preferred_agent)
        if agent is None:
            self.store.update_node(
                workflow_id,
                node_id,
                status=NodeStatus.FAILED,
                error="No healthy agent available",
            )
            self.schedule(workflow_id, spec)
            return

        now = datetime.now(timezone.utc).isoformat()
        current = self.store.get(workflow_id)
        generation = 0
        if current and node_id in current.nodes:
            generation = current.nodes[node_id].dispatch_generation

        self.store.update_node(
            workflow_id,
            node_id,
            status=NodeStatus.RUNNING,
            agent=agent.name,
            started_at=now,
            dispatched_at=now,
            dispatch_generation=generation,
        )
        self.router.registry.increment_load(agent.name)

        from orchestrator.tasks import execute_node

        async_result = execute_node.delay(
            workflow_id=workflow_id,
            node_id=node_id,
            agent_name=agent.name,
            capability=capability,
            dispatch_generation=generation,
        )
        self.store.set_task_id(workflow_id, node_id, async_result.id)
        logger.info(
            "Dispatched node %s (%s) on agent %s for workflow %s (gen=%s)",
            node_id,
            capability,
            agent.name,
            workflow_id,
            generation,
        )

    def on_node_success(
        self,
        workflow_id: str,
        node_id: str,
        agent_name: str,
        result: dict[str, Any],
        dispatch_generation: int = 0,
    ) -> bool:
        from orchestrator.circuit_breaker import CircuitBreaker

        state = self.store.get(workflow_id)
        if state is None:
            return False
        if state.status == WorkflowStatus.CANCELLED:
            audit_event(
                "node_result_discarded",
                {"workflow_id": workflow_id, "node": node_id, "reason": "cancelled"},
            )
            self.router.registry.decrement_load(agent_name)
            return False

        node = state.nodes.get(node_id)
        if node and node.status == NodeStatus.DONE:
            return True

        CircuitBreaker().record_success(agent_name)
        self.router.registry.decrement_load(agent_name)

        context_delta = result.get("context_delta") or {}

        def mutate(s: WorkflowState) -> WorkflowState:
            if s.status == WorkflowStatus.CANCELLED:
                return s
            n = s.nodes[node_id]
            if n.status == NodeStatus.DONE:
                return s
            n.status = NodeStatus.DONE
            n.result = result
            n.finished_at = datetime.now(timezone.utc).isoformat()
            if context_delta:
                s.context.update(context_delta)
            return s

        updated = self.store.update_cas(workflow_id, mutate)
        if updated and updated.status != WorkflowStatus.CANCELLED:
            self.store.clear_task_id(workflow_id, node_id)
            audit_event(
                "node_completed",
                {
                    "workflow_id": workflow_id,
                    "node": node_id,
                    "agent": agent_name,
                    "generation": dispatch_generation,
                    "version": updated.version,
                },
            )
            self.schedule(workflow_id)
            return True
        return False

    def on_node_failure(
        self,
        workflow_id: str,
        node_id: str,
        agent_name: str,
        error: str,
        to_dlq: bool = False,
        attempt: int = 1,
        dispatch_generation: int = 0,
    ) -> str:
        """Return action taken: dlq | fallback | retry."""
        from orchestrator.circuit_breaker import CircuitBreaker
        from orchestrator.dlq import DLQ

        state = self.store.get(workflow_id)
        if state is None or state.status == WorkflowStatus.CANCELLED:
            return "cancelled"

        cb_state = CircuitBreaker().record_failure(agent_name)
        self.metrics.incr("task_retry_total", {"node": node_id, "attempt": str(attempt)})

        if to_dlq:
            self.router.registry.decrement_load(agent_name)

            def mutate_dlq(s: WorkflowState) -> WorkflowState:
                n = s.nodes[node_id]
                n.status = NodeStatus.DLQ
                n.error = error
                n.attempts = attempt
                n.finished_at = datetime.now(timezone.utc).isoformat()
                s.status = WorkflowStatus.FAILED
                return s

            self.store.update_cas(workflow_id, mutate_dlq)
            self.store.clear_task_id(workflow_id, node_id)
            DLQ().push(
                {
                    "workflow_id": workflow_id,
                    "node_id": node_id,
                    "agent": agent_name,
                    "error": error,
                    "attempts": attempt,
                    "context": state.context,
                }
            )
            self.metrics.incr("task_dlq_total")
            audit_event(
                "node_dlq",
                {"workflow_id": workflow_id, "node": node_id, "error": error},
            )
            return "dlq"

        if cb_state.value == "open":
            graph = build_dag(self._spec_from_state(state))
            capability = node_capability(graph, node_id)
            fallback = self.router.pick_fallback(capability, exclude=agent_name)
            if fallback:
                audit_event(
                    "circuit_breaker_fallback",
                    {
                        "workflow_id": workflow_id,
                        "node": node_id,
                        "from_agent": agent_name,
                        "to_agent": fallback.name,
                    },
                )
                self.router.registry.decrement_load(agent_name)

                def mutate_fb(s: WorkflowState) -> WorkflowState:
                    n = s.nodes[node_id]
                    n.status = NodeStatus.RUNNING
                    n.agent = fallback.name
                    n.attempts = attempt
                    n.dispatched_at = datetime.now(timezone.utc).isoformat()
                    return s

                self.store.update_cas(workflow_id, mutate_fb)
                from orchestrator.tasks import execute_node

                gen = state.nodes[node_id].dispatch_generation
                async_result = execute_node.delay(
                    workflow_id=workflow_id,
                    node_id=node_id,
                    agent_name=fallback.name,
                    capability=capability,
                    dispatch_generation=gen,
                )
                self.store.set_task_id(workflow_id, node_id, async_result.id)
                return "fallback"

        return "retry"
