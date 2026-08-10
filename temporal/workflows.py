"""Temporal workflows — deep ReAct and research (Phase 3)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal.activities import execute_capability, merge_context

ACTIVITY_OPTS = {
    "start_to_close_timeout": timedelta(seconds=120),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


@workflow.defn(name="ChatReactDeepWorkflow")
class ChatReactDeepWorkflow:
    """Deep ReAct loop (3–10 iterations) with optional HITL gate."""

    def __init__(self) -> None:
        self._approved = False
        self._rejected = False

    @workflow.signal
    def approve(self) -> None:
        self._approved = True

    @workflow.signal
    def reject(self) -> None:
        self._rejected = True

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "approved": self._approved,
            "rejected": self._rejected,
            "waiting_hitl": getattr(self, "_waiting_hitl", False),
            "iteration": getattr(self, "_iteration", 0),
        }

    @workflow.run
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        wf_id = workflow.info().workflow_id
        ctx = dict(context)
        max_iter = min(int(ctx.get("max_iterations", 5)), 10)
        hitl_after = int(ctx.get("hitl_after_iteration", 1))
        ctx["runtime"] = "temporal"
        ctx["workflow_engine"] = "chat_react_deep"

        # memory
        r = await workflow.execute_activity(
            execute_capability,
            args=["memory_load", wf_id, "memory_load", ctx, {}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        # tool catalog
        r = await workflow.execute_activity(
            execute_capability,
            args=["tool_catalog_resolve", wf_id, "tool_catalog_resolve", ctx, {}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        self._iteration = 0
        self._waiting_hitl = False

        for i in range(max_iter):
            self._iteration = i + 1
            ctx["react_iteration"] = i

            r = await workflow.execute_activity(
                execute_capability,
                args=["llm_generate", wf_id, "llm_plan", ctx, {"mode": "plan_tools"}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])
            planned = ctx.get("planned_tools") or []
            if not planned:
                break

            r = await workflow.execute_activity(
                execute_capability,
                args=[
                    "mcp_invoke",
                    wf_id,
                    "mcp_invoke",
                    ctx,
                    {"tools_from": "context.planned_tools", "server": "amap"},
                ],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])

            if ctx.get("needs_kb"):
                r = await workflow.execute_activity(
                    execute_capability,
                    args=["rag_retrieve", wf_id, "rag_retrieve", ctx, {"top_k": 4}],
                    **ACTIVITY_OPTS,
                )
                ctx = merge_context(ctx, r["context_delta"])

            r = await workflow.execute_activity(
                execute_capability,
                args=["context_build", wf_id, "context_build", ctx, {}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])

            r = await workflow.execute_activity(
                execute_capability,
                args=["llm_generate", wf_id, "llm_observe", ctx, {"mode": "observe"}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])

            if ctx.get("hitl_enabled") and (i + 1) >= hitl_after:
                self._waiting_hitl = True
                await workflow.wait_condition(
                    lambda: self._approved or self._rejected,
                    timeout=timedelta(hours=24),
                )
                self._waiting_hitl = False
                if self._rejected:
                    ctx["assistant_message"] = "Turn cancelled by reviewer (HITL reject)."
                    break

            if not ctx.get("needs_another_round", True) and i >= 1:
                break

        r = await workflow.execute_activity(
            execute_capability,
            args=["llm_generate", wf_id, "llm_generate", ctx, {"mode": "final_answer", "stream": True}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        r = await workflow.execute_activity(
            execute_capability,
            args=["persist_reply", wf_id, "persist_turn", ctx, {"store_memory": True}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])
        ctx["temporal_iterations"] = self._iteration
        return ctx


@workflow.defn(name="ChatResearchWorkflow")
class ChatResearchWorkflow:
    """Multi-step research: plan → tool/RAG loops → synthesis (up to 8 iterations)."""

    @workflow.run
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        wf_id = workflow.info().workflow_id
        ctx = dict(context)
        ctx["runtime"] = "temporal"
        ctx["workflow_engine"] = "chat_research"
        max_rounds = min(int(ctx.get("max_iterations", 6)), 8)

        r = await workflow.execute_activity(
            execute_capability,
            args=["memory_load", wf_id, "memory_load", ctx, {}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        r = await workflow.execute_activity(
            execute_capability,
            args=["tool_catalog_resolve", wf_id, "tool_catalog_resolve", ctx, {}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        notes: list[str] = []
        for round_idx in range(max_rounds):
            ctx["research_round"] = round_idx + 1
            r = await workflow.execute_activity(
                execute_capability,
                args=["llm_generate", wf_id, f"research_plan_{round_idx}", ctx, {"mode": "plan_tools"}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])

            if ctx.get("needs_kb", True):
                r = await workflow.execute_activity(
                    execute_capability,
                    args=["rag_retrieve", wf_id, f"rag_{round_idx}", ctx, {"top_k": 6}],
                    **ACTIVITY_OPTS,
                )
                ctx = merge_context(ctx, r["context_delta"])

            if ctx.get("planned_tools"):
                r = await workflow.execute_activity(
                    execute_capability,
                    args=[
                        "mcp_invoke",
                        wf_id,
                        f"mcp_{round_idx}",
                        ctx,
                        {"tools_from": "context.planned_tools"},
                    ],
                    **ACTIVITY_OPTS,
                )
                ctx = merge_context(ctx, r["context_delta"])

            r = await workflow.execute_activity(
                execute_capability,
                args=["context_build", wf_id, f"context_{round_idx}", ctx, {}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])

            r = await workflow.execute_activity(
                execute_capability,
                args=["llm_generate", wf_id, f"observe_{round_idx}", ctx, {"mode": "observe"}],
                **ACTIVITY_OPTS,
            )
            ctx = merge_context(ctx, r["context_delta"])
            notes.append(ctx.get("tool_observations", [""])[-1] if ctx.get("tool_observations") else "")

        ctx["research_notes"] = notes
        r = await workflow.execute_activity(
            execute_capability,
            args=[
                "llm_generate",
                wf_id,
                "research_synthesis",
                ctx,
                {"mode": "final_answer", "stream": True},
            ],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])

        r = await workflow.execute_activity(
            execute_capability,
            args=["persist_reply", wf_id, "persist_turn", ctx, {}],
            **ACTIVITY_OPTS,
        )
        ctx = merge_context(ctx, r["context_delta"])
        ctx["research_rounds"] = max_rounds
        return ctx
