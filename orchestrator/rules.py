"""Rule engine with swappable interface (Python MVP, Drools-ready)."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from simpleeval import SimpleEval


class RuleResult(BaseModel):
    matched: bool
    actions: list[str] = Field(default_factory=list)
    eval_ms: float = 0.0
    rule_ids: list[str] = Field(default_factory=list)


class RuleEngine(ABC):
    @abstractmethod
    def evaluate(self, context: dict[str, Any]) -> RuleResult:
        ...


class PythonRuleEngine(RuleEngine):
    """AST-whitelisted evaluator via simpleeval — no raw eval()."""

    def __init__(self, rule: dict[str, Any] | None = None) -> None:
        self.rule = rule or {
            "if": "project_value > 1000000 && doc_type == 'RFQ'",
            "then": ["route:senior_estimator", "notify:pm"],
        }

    @staticmethod
    def _normalize_expression(expr: str) -> str:
        """Map JS-style operators from the test spec to Python."""
        return expr.replace("&&", " and ").replace("||", " or ")

    def evaluate(self, context: dict[str, Any]) -> RuleResult:
        start = time.perf_counter()
        evaluator = SimpleEval()
        evaluator.names = dict(context)
        condition = self._normalize_expression(self.rule["if"])
        matched = bool(evaluator.eval(condition))
        actions = list(self.rule.get("then", [])) if matched else []
        elapsed_ms = (time.perf_counter() - start) * 1000
        return RuleResult(
            matched=matched,
            actions=actions,
            eval_ms=round(elapsed_ms, 3),
            rule_ids=["estimate_rfq_rule"] if matched else [],
        )


def load_default_rule(path: str | Path | None = None) -> dict[str, Any]:
    rule_path = path or Path(__file__).resolve().parents[1] / "fixtures" / "rule.json"
    with open(rule_path, encoding="utf-8") as f:
        return json.load(f)


def preferred_estimator_from_actions(actions: list[str]) -> str | None:
    for action in actions:
        if action == "route:senior_estimator":
            return "estimator_senior"
        if action == "route:junior_estimator":
            return "estimator_junior"
    return None
