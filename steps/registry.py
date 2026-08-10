"""Capability → executor registry."""

from __future__ import annotations

from steps.base import StepExecutor
from steps.context import ContextBuildExecutor
from steps.llm import LlmGenerateExecutor
from steps.mcp import McpInvokeExecutor
from steps.memory import MemoryLoadExecutor, MemoryStoreExecutor
from steps.parse import ParseDocsExecutor
from steps.persist import PersistReplyExecutor
from steps.rag import RagIndexExecutor, RagRetrieveExecutor
from steps.tool_catalog import ToolCatalogResolveExecutor

_EXECUTORS: dict[str, StepExecutor] = {
    "memory_load": MemoryLoadExecutor(),
    "memory_store": MemoryStoreExecutor(),
    "parse_docs": ParseDocsExecutor(),
    "rag_retrieve": RagRetrieveExecutor(),
    "rag_index": RagIndexExecutor(),
    "context_build": ContextBuildExecutor(),
    "tool_catalog_resolve": ToolCatalogResolveExecutor(),
    "mcp_invoke": McpInvokeExecutor(),
    "llm_generate": LlmGenerateExecutor(),
    "persist_reply": PersistReplyExecutor(),
}


def get_executor(capability: str) -> StepExecutor | None:
    return _EXECUTORS.get(capability)
