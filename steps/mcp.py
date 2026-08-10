"""MCP tool invoke — mock implementation for Phase 2."""

from __future__ import annotations

import json
from typing import Any

from steps.base import StepContext, StepExecutor, StepResult


def _mock_tool_result(tool: str, args: dict[str, Any]) -> str:
    if "weather" in tool:
        city = args.get("city", "Hanoi")
        return f"Weather in {city}: 28°C, partly cloudy, humidity 72%."
    if "search" in tool or "text_search" in tool:
        kw = args.get("keywords", "restaurant")
        return f"Found 3 places matching '{kw}': (1) Cafe A (2) Bistro B (3) Grill C."
    if "direction" in tool:
        return f"Driving from {args.get('origin', 'A')} to {args.get('destination', 'B')}: ~15km, 25 min."
    return json.dumps({"status": "ok", "tool": tool, "args": args})


class McpInvokeExecutor(StepExecutor):
    capability = "mcp_invoke"

    def execute(self, ctx: StepContext) -> StepResult:
        planned = ctx.context.get("planned_tools") or []
        if not planned and ctx.params.get("tools_from") == "context.planned_tools":
            planned = ctx.context.get("planned_tools") or []

        single_tool = ctx.params.get("tool")
        if single_tool:
            planned = [{"name": single_tool, "arguments": ctx.params.get("arguments", {})}]

        results: list[dict[str, Any]] = []
        for item in planned:
            if isinstance(item, str):
                name, args = item, {}
            else:
                name = item.get("name") or item.get("tool", "unknown")
                args = item.get("arguments") or item.get("args") or {}
            results.append({
                "tool": name,
                "server": ctx.params.get("server", "amap"),
                "result": _mock_tool_result(name, args),
            })

        existing = list(ctx.context.get("mcp_results") or [])
        merged = existing + results

        return StepResult(
            context_delta={"mcp_results": merged},
            output={"invoked": len(results)},
        )
