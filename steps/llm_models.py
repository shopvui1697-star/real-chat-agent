"""Resolve LLM model name from agent profile and env (9Router combo aliases)."""

from __future__ import annotations

import os
from typing import Any

# llm_default_v1 → fast/simple; llm_senior_v1 → strong/complex workflows
AGENT_MODEL_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "llm_default_v1": ("LLM_MODEL_DEFAULT", "LLM_MODEL_FAST"),
    "llm_senior_v1": ("LLM_MODEL_SENIOR", "LLM_MODEL_STRONG"),
}


def resolve_llm_model(*, agent_name: str = "", context: dict[str, Any] | None = None) -> str:
    """Pick model for this LLM call.

    Priority: context override → agent-specific env → LLM_MODEL fallback.
    """
    ctx = context or {}
    override = (ctx.get("llm_model") or "").strip()
    if override:
        return override

    for env_key in AGENT_MODEL_ENV_KEYS.get(agent_name, ()):
        value = os.getenv(env_key, "").strip()
        if value:
            return value

    return os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
