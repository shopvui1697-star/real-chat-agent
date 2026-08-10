"""RAG retrieve/index — Redis-backed mock KB (Phase 2)."""

from __future__ import annotations

import json
import os
import re

import redis

from steps.base import StepContext, StepExecutor, StepResult
from steps.context_ref import resolve_field, store_payload


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _kb_key(namespace: str) -> str:
    return f"kb:{namespace}:docs"


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9\u00c0-\u1fff]{3,}", text.lower()))


class RagRetrieveExecutor(StepExecutor):
    capability = "rag_retrieve"

    def execute(self, ctx: StepContext) -> StepResult:
        query = ctx.context.get("user_message", "")
        top_k = int(ctx.params.get("top_k", 5))
        source = ctx.params.get("source")

        chunks: list[dict[str, str]] = []

        if source == "parsed_documents":
            parsed = resolve_field(ctx.context, "parsed_documents") or []
            if isinstance(parsed, list):
                for doc in parsed:
                    text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
                    if text:
                        chunks.append({"source": doc.get("name", "attachment"), "text": text[:2000]})
        else:
            namespace = ctx.context.get("rag_namespace", "default:kb")
            raw = _redis().get(_kb_key(namespace))
            docs: list[dict] = json.loads(raw) if raw else _default_kb()
            q_tokens = _tokenize(query)
            scored = []
            for doc in docs:
                text = doc.get("text", "")
                score = len(q_tokens & _tokenize(text))
                if score > 0 or not q_tokens:
                    scored.append((score, doc))
            scored.sort(key=lambda x: -x[0])
            for _, doc in scored[:top_k]:
                chunks.append({"source": doc.get("id", "kb"), "text": doc["text"][:1500]})

        meta = store_payload(f"{ctx.workflow_id}:{ctx.node_id}:rag", chunks)
        delta: dict = {"rag_chunks": chunks} if "inline" in meta else {
            "rag_chunks_ref": meta["rag_chunks_ref"],
            "rag_chunks_bytes": meta["rag_chunks_bytes"],
        }
        return StepResult(context_delta=delta, output={"count": len(chunks)})


class RagIndexExecutor(StepExecutor):
    capability = "rag_index"

    def execute(self, ctx: StepContext) -> StepResult:
        namespace = ctx.params.get("namespace_from", ctx.context.get("rag_namespace", "default:kb"))
        if isinstance(namespace, str) and namespace.startswith("context."):
            namespace = ctx.context.get(namespace.split(".", 1)[1], "default:kb")

        parsed = resolve_field(ctx.context, "parsed_documents") or []
        docs: list[dict] = []
        if isinstance(parsed, list):
            for i, doc in enumerate(parsed):
                text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
                docs.append({"id": f"doc_{i}", "text": text})

        key = _kb_key(str(namespace))
        existing_raw = _redis().get(key)
        existing = json.loads(existing_raw) if existing_raw else []
        existing.extend(docs)
        _redis().set(key, json.dumps(existing), ex=86400 * 30)

        return StepResult(
            context_delta={"index_status": "ok", "indexed_count": len(docs)},
            output={"namespace": namespace, "indexed": len(docs)},
        )


def _default_kb() -> list[dict]:
    return [
        {
            "id": "refund_policy",
            "text": "Refund policy: customers may request a full refund within 30 days of purchase. "
            "Contact support@example.com with order ID.",
        },
        {
            "id": "shipping",
            "text": "Shipping: standard delivery 3-5 business days. Express 1-2 days. Free shipping over $50.",
        },
        {
            "id": "support_hours",
            "text": "Support hours: Mon-Fri 9am-6pm UTC+7. Emergency line available for enterprise tenants.",
        },
    ]


def seed_default_kb() -> None:
    """Called on worker startup to ensure demo KB exists."""
    key = _kb_key("default:kb")
    if not _redis().exists(key):
        _redis().set(key, json.dumps(_default_kb()), ex=86400 * 30)
