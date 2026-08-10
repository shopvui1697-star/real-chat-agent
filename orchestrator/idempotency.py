"""Task idempotency locks (Redis SET NX)."""

from __future__ import annotations

import hashlib
import os

import redis


def get_redis() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def idempotency_key(workflow_id: str, node_id: str, generation: int) -> str:
    raw = f"{workflow_id}:{node_id}:{generation}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"idem:{digest}"


class IdempotencyGuard:
    TTL_SEC = 3600

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._redis = client or get_redis()

    def acquire(self, workflow_id: str, node_id: str, generation: int) -> bool:
        """Return True if this execution should proceed (first claimant)."""
        key = idempotency_key(workflow_id, node_id, generation)
        return bool(self._redis.set(key, "1", nx=True, ex=self.TTL_SEC))

    def release(self, workflow_id: str, node_id: str, generation: int) -> None:
        key = idempotency_key(workflow_id, node_id, generation)
        self._redis.delete(key)

    def result_key(self, workflow_id: str, node_id: str, generation: int) -> str:
        return f"result:{workflow_id}:{node_id}:{generation}"

    def store_result(
        self,
        workflow_id: str,
        node_id: str,
        generation: int,
        payload: str,
    ) -> None:
        key = self.result_key(workflow_id, node_id, generation)
        self._redis.setex(key, self.TTL_SEC, payload)

    def get_result(self, workflow_id: str, node_id: str, generation: int) -> str | None:
        return self._redis.get(self.result_key(workflow_id, node_id, generation))
