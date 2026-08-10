"""Gateway routing — Celery orchestrator vs Temporal."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from orchestrator.chat_rules import evaluate_chat_rules, uses_temporal, workflow_path

RouteTarget = Literal["celery", "temporal"]


def resolve_route(workflows_dir: Path, context: dict[str, Any]) -> tuple[RouteTarget, str, str, dict[str, Any]]:
    """Return (engine, workflow_yaml_or_temporal_name, template, enriched_context)."""
    rules = evaluate_chat_rules(context)
    enriched = {
        **context,
        "intent": rules.intent,
        "workflow_template": rules.workflow_template,
    }

    if uses_temporal(rules.workflow_template):
        return "temporal", rules.workflow_template, rules.workflow_template, enriched

    path = workflow_path(workflows_dir, rules.workflow_template)
    if not path.is_file():
        path = workflows_dir / "chat_simple.yaml"
        enriched["workflow_template"] = "chat_simple"
    yaml_text = path.read_text(encoding="utf-8")
    return "celery", yaml_text, enriched["workflow_template"], enriched
