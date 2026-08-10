"""Session memory load step."""

from __future__ import annotations

import json
import os

import redis

from steps.base import StepContext, StepExecutor, StepResult


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


class MemoryLoadExecutor(StepExecutor):
    capability = "memory_load"

    def execute(self, ctx: StepContext) -> StepResult:
        session_id = ctx.context.get("session_id", "")
        key = f"session:{session_id}:memory"
        raw = _redis().get(key)
        snippets: list[str] = json.loads(raw) if raw else []

        return StepResult(
            context_delta={"memory_snippets": snippets},
            output={"count": len(snippets)},
        )


class MemoryStoreExecutor(StepExecutor):
    capability = "memory_store"

    def execute(self, ctx: StepContext) -> StepResult:
        session_id = ctx.context.get("session_id", "")
        user_message = ctx.context.get("user_message", "")
        assistant = ctx.context.get("assistant_message", "")
        key = f"session:{session_id}:memory"
        raw = _redis().get(key)
        snippets: list[str] = json.loads(raw) if raw else []
        summary = f"User: {user_message[:200]} | Assistant: {assistant[:200]}"
        snippets.append(summary)
        snippets = snippets[-20:]
        _redis().set(key, json.dumps(snippets))
        return StepResult(context_delta={"memory_stored": True}, output={"count": len(snippets)})
