"""Phase 3 tests — Temporal routing, auth, RLS helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import jwt
from fastapi.security import HTTPAuthorizationCredentials

from gateway.auth import get_auth_context
from gateway.main import _rules_preview_from_request, RulesPreviewRequest
from gateway.routing import resolve_route
from orchestrator.chat_rules import evaluate_chat_rules, uses_temporal
from temporal.activities import merge_context

ROOT = Path(__file__).resolve().parents[1]


def test_uses_temporal_templates():
    assert uses_temporal("chat_react_deep")
    assert uses_temporal("chat_research")
    assert not uses_temporal("chat_react")
    assert not uses_temporal("chat_simple")


def test_intent_deep_react_temporal():
    r = evaluate_chat_rules({"needs_tools": True, "deep_react": True, "max_iterations": 5})
    assert r.workflow_template == "chat_react_deep"
    assert r.intent == "deep_react"
    assert "route_deep_react_temporal" in r.rule_ids


def test_intent_research_temporal():
    r = evaluate_chat_rules({"research_mode": True})
    assert r.workflow_template == "chat_research"
    assert r.intent == "research"


def test_intent_max_iterations_routes_temporal():
    r = evaluate_chat_rules({"needs_tools": True, "max_iterations": 5})
    assert r.workflow_template == "chat_react_deep"


def test_resolve_route_celery_simple():
    engine, payload, template, _ctx = resolve_route(
        ROOT / "workflows",
        {"needs_kb": False, "has_attachments": False, "needs_tools": False},
    )
    assert engine == "celery"
    assert template == "chat_simple"
    assert "memory_load" in payload


def test_resolve_route_temporal_deep():
    engine, payload, template, ctx = resolve_route(
        ROOT / "workflows",
        {"needs_tools": True, "deep_react": True, "max_iterations": 5},
    )
    assert engine == "temporal"
    assert template == "chat_react_deep"
    assert payload == "chat_react_deep"
    assert ctx["intent"] == "deep_react"


def test_resolve_route_temporal_research():
    engine, _, template, _ = resolve_route(
        ROOT / "workflows",
        {"research_mode": True},
    )
    assert engine == "temporal"
    assert template == "chat_research"


def test_rules_preview_attachment_with_kb():
    preview = _rules_preview_from_request(
        RulesPreviewRequest(
            attachments=[{"name": "report.md", "text": "# Summary\nRevenue up"}],
            needs_kb=True,
            query_scope="",
        )
    )
    assert preview["workflow_template"] == "chat_attachment_rag"
    assert preview["intent"] == "kb_and_attachment"
    assert preview["llm_agent"] == "llm_senior_v1"


def test_rules_preview_file_only():
    preview = _rules_preview_from_request(
        RulesPreviewRequest(
            attachments=[{"name": "data.csv", "text": "a,b\n1,2"}],
            query_scope="attachment",
        )
    )
    assert preview["workflow_template"] == "chat_attachment_rag_sequential"
    assert preview["runtime"] == "celery"


def test_merge_context():
    merged = merge_context({"a": 1}, {"b": 2, "a": 3})
    assert merged == {"a": 3, "b": 2}


def test_auth_default_tenant():
    req = MagicMock()
    req.headers = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-1"}
    ctx = asyncio.run(get_auth_context(req, None))
    assert ctx["tenant_id"] == "tenant-a"
    assert ctx["user_id"] == "user-1"


def test_auth_jwt_claims():
    token = jwt.encode({"sub": "u99", "tenant_id": "t99"}, "dev-secret-change-me", algorithm="HS256")
    req = MagicMock()
    req.headers = {}
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    ctx = asyncio.run(get_auth_context(req, creds))
    assert ctx["tenant_id"] == "t99"
    assert ctx["user_id"] == "u99"
