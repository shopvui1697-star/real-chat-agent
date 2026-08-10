"""Persist assistant reply and optional memory update."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import redis

from gateway.store import MessageStore
from steps.base import StepContext, StepExecutor, StepResult


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


class PersistReplyExecutor(StepExecutor):
    capability = "persist_reply"

    def execute(self, ctx: StepContext) -> StepResult:
        session_id = ctx.context.get("session_id", "")
        turn_id = ctx.context.get("turn_id", "")
        assistant = ctx.context.get("assistant_message", "")
        user_message = ctx.context.get("user_message", "")

        message_id = f"msg_{uuid4().hex[:12]}"
        tenant_id = ctx.context.get("tenant_id", "default")
        store = MessageStore()
        store.add_assistant_message(
            session_id=session_id,
            turn_id=turn_id,
            message_id=message_id,
            content=assistant,
            tenant_id=tenant_id,
        )

        if ctx.params.get("store_memory", True):
            key = f"session:{session_id}:memory"
            raw = _redis().get(key)
            snippets: list[str] = json.loads(raw) if raw else []
            summary = f"User: {user_message[:200]} | Assistant: {assistant[:200]}"
            snippets.append(summary)
            snippets = snippets[-20:]
            _redis().set(key, json.dumps(snippets))

        return StepResult(
            context_delta={
                "persisted_message_id": message_id,
                "turn_status": "completed",
            },
            output={"message_id": message_id},
        )
