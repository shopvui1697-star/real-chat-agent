"""Redis-backed circuit breaker per agent."""

from __future__ import annotations

import os
import time
from enum import Enum

import redis


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


FAILURE_THRESHOLD = 5
FAILURE_WINDOW_SEC = 60
COOLDOWN_SEC = 30


def get_redis() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


class CircuitBreaker:
    PREFIX = "cb:"

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._redis = client or get_redis()

    def _failures_key(self, agent: str) -> str:
        return f"{self.PREFIX}{agent}:failures"

    def _state_key(self, agent: str) -> str:
        return f"{self.PREFIX}{agent}:state"

    def _opened_at_key(self, agent: str) -> str:
        return f"{self.PREFIX}{agent}:opened_at"

    def get_state(self, agent: str) -> CircuitState:
        raw = self._redis.get(self._state_key(agent))
        if raw == CircuitState.OPEN.value:
            opened_at = float(self._redis.get(self._opened_at_key(agent)) or 0)
            if time.time() - opened_at >= COOLDOWN_SEC:
                self._redis.set(self._state_key(agent), CircuitState.HALF_OPEN.value)
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN
        if raw == CircuitState.HALF_OPEN.value:
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED

    def is_available(self, agent: str) -> bool:
        state = self.get_state(agent)
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self, agent: str) -> None:
        self._redis.delete(self._failures_key(agent))
        self._redis.set(self._state_key(agent), CircuitState.CLOSED.value)
        self._redis.delete(self._opened_at_key(agent))

    def record_failure(self, agent: str) -> CircuitState:
        key = self._failures_key(agent)
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, FAILURE_WINDOW_SEC)

        state = self.get_state(agent)
        if state == CircuitState.HALF_OPEN:
            self._redis.set(self._state_key(agent), CircuitState.OPEN.value)
            self._redis.set(self._opened_at_key(agent), str(time.time()))
            return CircuitState.OPEN

        if count >= FAILURE_THRESHOLD:
            self._redis.set(self._state_key(agent), CircuitState.OPEN.value)
            self._redis.set(self._opened_at_key(agent), str(time.time()))
            return CircuitState.OPEN

        return self.get_state(agent)

    def all_states(self) -> dict[str, str]:
        agents = set()
        for key in self._redis.scan_iter(f"{self.PREFIX}*:state"):
            agent = key.split(":")[1]
            agents.add(agent)
        return {agent: self.get_state(agent).value for agent in sorted(agents)}

    def reset(self, agent: str) -> None:
        self._redis.delete(
            self._failures_key(agent),
            self._state_key(agent),
            self._opened_at_key(agent),
        )
