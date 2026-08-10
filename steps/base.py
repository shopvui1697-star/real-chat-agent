"""Base types for step executors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepContext:
    workflow_id: str
    node_id: str
    capability: str
    context: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    agent_name: str = ""


@dataclass
class StepResult:
    context_delta: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)


class StepExecutor(ABC):
    capability: str

    @abstractmethod
    def execute(self, ctx: StepContext) -> StepResult:
        raise NotImplementedError
