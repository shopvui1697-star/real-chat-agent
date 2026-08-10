"""Resolve tool specs for llm_plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from steps.base import StepContext, StepExecutor, StepResult

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "config" / "tools"


def load_all_tools() -> list[dict[str, Any]]:
    tools: list[dict] = []
    if not TOOLS_DIR.is_dir():
        return tools
    for path in sorted(TOOLS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tools.extend(data.get("tools", []))
    return tools


class ToolCatalogResolveExecutor(StepExecutor):
    capability = "tool_catalog_resolve"

    def execute(self, ctx: StepContext) -> StepResult:
        enabled = ctx.context.get("enabled_tools") or []
        session_config = ctx.context.get("session_config") or {}
        if not enabled and session_config.get("enabled_tools"):
            enabled = session_config["enabled_tools"]

        all_tools = load_all_tools()
        if enabled:
            allowed = set(enabled)
            catalog = [t for t in all_tools if t.get("id") in allowed or t.get("name") in allowed]
        else:
            catalog = all_tools

        return StepResult(
            context_delta={
                "tool_catalog": catalog,
                "tool_catalog_version": "phase2-mvp",
            },
            output={"tool_count": len(catalog)},
        )
