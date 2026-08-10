"""Intent routing and workflow template selection (ADR-03)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

WORKFLOW_MAP = {
    "chat_simple": "chat_simple.yaml",
    "chat_rag": "chat_rag.yaml",
    "chat_attachment": "chat_attachment.yaml",
    "chat_attachment_rag": "chat_attachment_rag.yaml",
    "chat_attachment_rag_sequential": "chat_attachment_rag_sequential.yaml",
    "chat_full": "chat_full.yaml",
    "chat_react": "chat_react.yaml",
    "chat_react_deep": "chat_react.yaml",
    "chat_research": "chat_react.yaml",
}


TEMPORAL_TEMPLATES = frozenset({"chat_react_deep", "chat_research"})

# Workflows that benefit from a stronger LLM (tools, multi-step, RAG+attachment).
SENIOR_LLM_WORKFLOWS = frozenset({
    "chat_react",
    "chat_react_deep",
    "chat_research",
    "chat_full",
    "chat_attachment_rag",
    "chat_attachment_rag_sequential",
})

SENIOR_LLM_INTENTS = frozenset({
    "needs_tools",
    "deep_react",
    "research",
    "kb_and_attachment",
    "rag_on_attachment",
})


def uses_temporal(template: str) -> bool:
    return template in TEMPORAL_TEMPLATES


class RuleResult(BaseModel):
    matched: bool
    actions: list[str] = Field(default_factory=list)
    eval_ms: float = 0.0
    rule_ids: list[str] = Field(default_factory=list)
    workflow_template: str = "chat_simple"
    intent: str = "simple"


def evaluate_chat_rules(context: dict[str, Any]) -> RuleResult:
    start = time.perf_counter()
    actions: list[str] = []
    rule_ids: list[str] = []
    intent = "simple"
    workflow = "chat_simple"

    needs_kb = bool(context.get("needs_kb"))
    has_attachments = bool(context.get("has_attachments"))
    needs_tools = bool(context.get("needs_tools"))
    query_scope = context.get("query_scope", "")
    force_template = context.get("workflow_template") or (context.get("session_config") or {}).get("workflow_template")

    if force_template and force_template in WORKFLOW_MAP:
        workflow = force_template
        intent = context.get("intent", "custom")
        rule_ids.append("force_template")
    elif context.get("research_mode"):
        intent = "research"
        workflow = "chat_research"
        rule_ids.append("route_research_temporal")
    elif needs_tools:
        intent = "needs_tools"
        if context.get("deep_react") or int(context.get("max_iterations") or 0) > 2:
            workflow = "chat_react_deep"
            intent = "deep_react"
            rule_ids.append("route_deep_react_temporal")
        else:
            workflow = "chat_react"
            rule_ids.append("route_tools")
    elif has_attachments and query_scope == "attachment":
        intent = "rag_on_attachment"
        workflow = "chat_attachment_rag_sequential"
        rule_ids.append("route_rag_on_attachment")
    elif has_attachments and needs_kb:
        intent = "kb_and_attachment"
        workflow = "chat_full" if context.get("prefer_full_pipeline") else "chat_attachment_rag"
        rule_ids.append("route_kb_and_attachment")
    elif has_attachments:
        intent = "attachment_only"
        workflow = "chat_attachment"
        rule_ids.append("route_attachment")
    elif needs_kb:
        intent = "kb_only"
        workflow = "chat_rag"
        rule_ids.append("route_kb")
    else:
        intent = "simple"
        workflow = "chat_simple"
        rule_ids.append("route_simple")

    llm_agent = preferred_llm_agent(intent, workflow)
    actions.extend([
        f"intent:{intent}",
        f"workflow:{workflow}",
        f"route:{llm_agent}",
    ])
    elapsed = (time.perf_counter() - start) * 1000
    return RuleResult(
        matched=True,
        actions=actions,
        eval_ms=round(elapsed, 3),
        rule_ids=rule_ids,
        workflow_template=workflow,
        intent=intent,
    )


def workflow_path(workflows_dir: Path, template: str) -> Path:
    filename = WORKFLOW_MAP.get(template, f"{template}.yaml")
    return workflows_dir / filename


def preferred_llm_agent(intent: str, workflow: str) -> str:
    if workflow in SENIOR_LLM_WORKFLOWS or intent in SENIOR_LLM_INTENTS:
        return "llm_senior_v1"
    return "llm_default_v1"


def preferred_llm_from_actions(actions: list[str]) -> str | None:
    for action in reversed(actions):
        if action == "route:llm_senior_v1":
            return "llm_senior_v1"
        if action == "route:llm_default_v1":
            return "llm_default_v1"
    return None
