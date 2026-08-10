"""Phase 1 unit tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.chat_rules import evaluate_chat_rules, preferred_llm_agent
from orchestrator.dag import build_dag, load_workflow_spec
from steps.base import StepContext
from steps.llm import LlmGenerateExecutor
from steps.llm_models import resolve_llm_model
from steps.memory import MemoryLoadExecutor


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_chat_simple_workflow_is_dag():
    spec = load_workflow_spec(ROOT / "workflows" / "chat_simple.yaml")
    graph = build_dag(spec)
    assert list(graph.nodes) == ["rules_eval", "memory_load", "llm_generate", "persist_turn"]


def test_intent_routing_defaults_simple():
    result = evaluate_chat_rules({"needs_kb": False, "has_attachments": False, "needs_tools": False})
    assert "intent:simple" in result.actions
    assert "route:llm_default_v1" in result.actions


def test_intent_routing_tools_uses_senior_llm():
    result = evaluate_chat_rules({"needs_tools": True})
    assert "route:llm_senior_v1" in result.actions
    assert preferred_llm_agent("needs_tools", "chat_react") == "llm_senior_v1"


def test_resolve_llm_model_by_agent_env():
    with patch.dict(
        "os.environ",
        {
            "LLM_MODEL": "fallback",
            "LLM_MODEL_DEFAULT": "fast-combo",
            "LLM_MODEL_SENIOR": "premium-combo",
        },
        clear=False,
    ):
        assert resolve_llm_model(agent_name="llm_default_v1") == "fast-combo"
        assert resolve_llm_model(agent_name="llm_senior_v1") == "premium-combo"
        assert resolve_llm_model(agent_name="") == "fallback"


def test_llm_executor_picks_senior_model_from_env():
    executor = LlmGenerateExecutor()
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "mock",
            "LLM_MODEL": "fallback",
            "LLM_MODEL_SENIOR": "premium-combo",
        },
        clear=False,
    ):
        result = executor.execute(
            StepContext(
                workflow_id="wf1",
                node_id="llm_generate",
                capability="llm_generate",
                context={"user_message": "hello", "memory_snippets": []},
                agent_name="llm_senior_v1",
            )
        )
    assert result.context_delta["llm_usage"]["resolved_model"] == "premium-combo"
    assert result.context_delta["llm_usage"]["agent"] == "llm_senior_v1"


def test_memory_load_empty_session():
    executor = MemoryLoadExecutor()
    with patch("steps.memory._redis") as mock_redis_factory:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis
        result = executor.execute(
            StepContext(
                workflow_id="wf1",
                node_id="memory_load",
                capability="memory_load",
                context={"session_id": "sess_test"},
            )
        )
    assert result.context_delta["memory_snippets"] == []


def test_llm_mock_mode():
    executor = LlmGenerateExecutor()
    with patch.dict("os.environ", {"OPENAI_API_KEY": "mock"}):
        result = executor.execute(
            StepContext(
                workflow_id="wf1",
                node_id="llm_generate",
                capability="llm_generate",
                context={"user_message": "hello", "memory_snippets": []},
            )
        )
    assert "Echo: hello" in result.context_delta["assistant_message"]
