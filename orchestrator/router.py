"""Agent router: capability + load + circuit breaker."""

from __future__ import annotations

from orchestrator.circuit_breaker import CircuitBreaker
from orchestrator.registry import Agent, AgentRegistry


class Router:
    def __init__(
        self,
        registry: AgentRegistry | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.cb = circuit_breaker or CircuitBreaker()

    def pick_agent(
        self,
        capability: str,
        preferred: str | None = None,
    ) -> Agent | None:
        candidates = self.registry.by_capability(capability)
        if preferred:
            preferred_agent = self.registry.get(preferred)
            if (
                preferred_agent
                and preferred_agent.healthy
                and self.cb.is_available(preferred_agent.name)
            ):
                return preferred_agent

        for agent in candidates:
            if self.cb.is_available(agent.name):
                return agent

        return None

    def pick_fallback(self, capability: str, exclude: str) -> Agent | None:
        for agent in self.registry.by_capability(capability):
            if agent.name != exclude and self.cb.is_available(agent.name):
                return agent
        return None
