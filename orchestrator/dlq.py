"""Dead-letter queue for tasks that exhausted retries."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis


DLQ_REDIS_KEY = "dlq:entries"
AUDIT_PATH = Path(os.getenv("AUDIT_PATH", "data/audit.jsonl"))


def get_redis() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


class DLQ:
    def __init__(self, client: redis.Redis | None = None) -> None:
        self._redis = client or get_redis()

    def push(self, entry: dict[str, Any]) -> None:
        entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._redis.lpush(DLQ_REDIS_KEY, json.dumps(entry))
        _append_audit({"event": "dlq", **entry})

    def list_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        raw_items = self._redis.lrange(DLQ_REDIS_KEY, 0, limit - 1)
        return [json.loads(item) for item in raw_items]

    def count(self) -> int:
        return int(self._redis.llen(DLQ_REDIS_KEY))

    def clear(self) -> None:
        self._redis.delete(DLQ_REDIS_KEY)


def _append_audit(event: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def audit_event(event_type: str, payload: dict[str, Any]) -> None:
    entry = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    _append_audit(entry)
    try:
        from orchestrator.audit_kafka import publish_audit

        publish_audit(event_type, payload, tenant_id=str(payload.get("tenant_id", "default")))
    except Exception:
        pass
