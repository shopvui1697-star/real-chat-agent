"""Prometheus-style metrics (in-memory + Redis counters)."""

from __future__ import annotations

import os
from typing import Any

import redis


def get_redis() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


class Metrics:
    PREFIX = "metrics:"

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._redis = client or get_redis()

    def incr(self, name: str, labels: dict[str, str] | None = None, amount: int = 1) -> None:
        key = self._key(name, labels)
        self._redis.incrby(key, amount)

    def observe_rule_eval_ms(self, ms: float, cache_hit: bool = False) -> None:
        bucket = "hit" if cache_hit else "miss"
        key = f"{self.PREFIX}rule_eval_ms:{bucket}"
        self._redis.lpush(key, str(ms))
        self._redis.ltrim(key, 0, 999)

    def get_counters(self) -> dict[str, Any]:
        counters: dict[str, int] = {}
        for key in self._redis.scan_iter(f"{self.PREFIX}*"):
            if ":rule_eval_ms:" in key:
                continue
            val = int(self._redis.get(key) or 0)
            counters[key.removeprefix(self.PREFIX)] = val
        return counters

    def render_prometheus(self, cb_states: dict[str, str]) -> str:
        lines: list[str] = []
        counters = self.get_counters()
        for name, value in sorted(counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        lines.append("# TYPE circuit_breaker_state gauge")
        for agent, state in sorted(cb_states.items()):
            for s in ("closed", "open", "half_open"):
                val = 1 if state == s else 0
                lines.append(f'circuit_breaker_state{{agent="{agent}",state="{s}"}} {val}')

        return "\n".join(lines) + "\n"

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return f"{self.PREFIX}{name}"
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{self.PREFIX}{name}{{{label_str}}}"
