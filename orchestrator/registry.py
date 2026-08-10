"""In-memory step registry with capability tags and health."""

from __future__ import annotations

from pydantic import BaseModel


class Agent(BaseModel):
    name: str
    caps: list[str]
    healthy: bool = True
    fail_rate: float = 0.0
    load: int = 0


CHAT_AGENTS: list[Agent] = [
    Agent(name="memory_worker_v1", caps=["memory_load", "memory_store"]),
    Agent(name="rag_worker_v1", caps=["rag_retrieve", "rag_index"]),
    Agent(name="parse_worker_v1", caps=["parse_docs"]),
    Agent(name="context_worker_v1", caps=["context_build"]),
    Agent(name="tool_catalog_v1", caps=["tool_catalog_resolve"]),
    Agent(name="mcp_amap_v1", caps=["mcp_invoke"]),
    Agent(name="llm_default_v1", caps=["llm_generate"]),
    Agent(name="llm_senior_v1", caps=["llm_generate"]),
    Agent(name="persist_worker_v1", caps=["persist_reply"]),
]

DEFAULT_AGENTS: list[Agent] = CHAT_AGENTS


class AgentRegistry:
    def __init__(self, agents: list[Agent] | None = None) -> None:
        self._agents: dict[str, Agent] = {
            a.name: a.model_copy() for a in (agents or DEFAULT_AGENTS)
        }

    def list_agents(self) -> list[Agent]:
        return list(self._agents.values())

    def get(self, name: str) -> Agent | None:
        agent = self._agents.get(name)
        return agent.model_copy() if agent else None

    def by_capability(self, capability: str, healthy_only: bool = True) -> list[Agent]:
        agents = [
            a.model_copy()
            for a in self._agents.values()
            if capability in a.caps and (not healthy_only or a.healthy)
        ]
        return sorted(agents, key=lambda a: (a.load, a.name))

    def increment_load(self, name: str) -> None:
        if name in self._agents:
            self._agents[name].load += 1

    def decrement_load(self, name: str) -> None:
        if name in self._agents and self._agents[name].load > 0:
            self._agents[name].load -= 1

    def set_health(self, name: str, healthy: bool) -> None:
        if name in self._agents:
            self._agents[name].healthy = healthy
