"""Phase 1 unit tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.chat_rules import evaluate_chat_rules
from orchestrator.dag import build_dag, load_workflow_spec
from steps.base import StepContext
from steps.llm import LlmGenerateExecutor
from steps.memory import MemoryLoadExecutor


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_chat_simple_workflow_is_dag():
    spec = load_workflow_spec(ROOT / "workflows" / "chat_simple.yaml")
    graph = build_dag(spec)
    assert list(graph.nodes) == ["rules_eval", "memory_load", "llm_generate", "persist_turn"]


def test_intent_routing_defaults_simple():
    result = evaluate_chat_rules({"needs_kb": False, "has_attachments": False, "needs_tools": False})
    assert "intent:simple" in result.actions


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
