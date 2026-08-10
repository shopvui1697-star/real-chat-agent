"""Phase 2 tests."""

from __future__ import annotations

from orchestrator.chat_rules import evaluate_chat_rules
from orchestrator.dag import build_dag, load_workflow_spec
from steps.base import StepContext
from steps.context import ContextBuildExecutor
from steps.rag import RagRetrieveExecutor
from steps.tool_catalog import ToolCatalogResolveExecutor

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_intent_routing_kb():
    r = evaluate_chat_rules({"needs_kb": True, "has_attachments": False, "needs_tools": False})
    assert r.workflow_template == "chat_rag"
    assert r.intent == "kb_only"


def test_intent_routing_react():
    r = evaluate_chat_rules({"needs_tools": True})
    assert r.workflow_template == "chat_react"


def test_intent_routing_attachment_kb_parallel():
    r = evaluate_chat_rules({"needs_kb": True, "has_attachments": True, "needs_tools": False})
    assert r.workflow_template == "chat_attachment_rag"


def test_chat_react_workflow_dag():
    spec = load_workflow_spec(ROOT / "workflows" / "chat_react.yaml")
    assert spec.max_iterations == 2
    graph = build_dag(spec)
    assert "llm_plan" in graph.nodes
    assert "mcp_invoke" in graph.nodes


def test_context_build_with_rag():
    ex = ContextBuildExecutor()
    result = ex.execute(
        StepContext(
            workflow_id="wf",
            node_id="context_build",
            capability="context_build",
            context={
                "user_message": "refund?",
                "rag_chunks": [{"source": "policy", "text": "30 day refund"}],
            },
        )
    )
    assert "prompt_messages" in result.context_delta
    assert "30 day" in result.context_delta["prompt_messages"][0]["content"]


def test_tool_catalog_resolve():
    ex = ToolCatalogResolveExecutor()
    result = ex.execute(
        StepContext(
            workflow_id="wf",
            node_id="tool_catalog_resolve",
            capability="tool_catalog_resolve",
            context={"enabled_tools": ["amap.maps_weather"]},
        )
    )
    assert len(result.context_delta["tool_catalog"]) >= 1
