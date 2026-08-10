"""Large payload refs — Redis side store (ADR-05 MVP; S3 in production)."""

from __future__ import annotations

import json
import os
from typing import Any

import redis

INLINE_THRESHOLD = int(os.getenv("CONTEXT_INLINE_THRESHOLD", "32768"))


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def store_payload(ref_key: str, data: Any) -> dict[str, Any]:
    """Store payload; return ref metadata for context_delta."""
    raw = json.dumps(data, ensure_ascii=False)
    size = len(raw.encode("utf-8"))
    if size <= INLINE_THRESHOLD:
        return {"inline": data, "bytes": size}
    key = f"ctxref:{ref_key}"
    _redis().set(key, raw, ex=86400)
    return {f"{ref_key}_ref": key, f"{ref_key}_bytes": size}


def load_payload(ref: str | None, inline: Any = None) -> Any:
    if inline is not None:
        return inline
    if not ref:
        return None
    raw = _redis().get(ref)
    if raw is None:
        return None
    return json.loads(raw)


def resolve_field(context: dict[str, Any], field: str) -> Any:
    """Load field from context, following *_ref if present."""
    ref_key = f"{field}_ref"
    if ref_key in context:
        return load_payload(context[ref_key])
    return context.get(field)
