"""Parse workflow YAML into a NetworkX DAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from pydantic import BaseModel, Field


class WorkflowStep(BaseModel):
    id: str
    after: list[str] = Field(default_factory=list)
    capability: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    optional: bool = False
    timeout_sec: int | None = None


class WorkflowSpec(BaseModel):
    name: str
    steps: list[WorkflowStep]
    max_iterations: int | None = None


def load_workflow_spec(path: str | Path) -> WorkflowSpec:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return WorkflowSpec.model_validate(data)


def load_workflow_spec_dict(data: dict[str, Any]) -> WorkflowSpec:
    return WorkflowSpec.model_validate(data)


def build_dag(spec: WorkflowSpec) -> nx.DiGraph:
    graph: nx.DiGraph = nx.DiGraph()
    for step in spec.steps:
        graph.add_node(step.id, step=step)
        for dep in step.after:
            graph.add_edge(dep, step.id)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Workflow spec contains a cycle")
    return graph


def get_ready_nodes(
    graph: nx.DiGraph,
    completed: set[str],
    running: set[str],
    failed: set[str],
    skipped: set[str] | None = None,
) -> list[str]:
    """Return node ids whose dependencies are all DONE/SKIPPED and not yet started."""
    if failed:
        return []
    skipped = skipped or set()
    terminal = completed | skipped
    ready: list[str] = []
    for node_id in graph.nodes:
        if node_id in completed or node_id in running or node_id in skipped:
            continue
        deps = list(graph.predecessors(node_id))
        if all(dep in terminal for dep in deps):
            ready.append(node_id)
    return sorted(ready)


def step_for_node(graph: nx.DiGraph, node_id: str) -> WorkflowStep:
    data = graph.nodes[node_id].get("step")
    if data is None:
        raise ValueError(f"No step metadata for node: {node_id}")
    if isinstance(data, WorkflowStep):
        return data
    return WorkflowStep.model_validate(data)


def node_capability(graph: nx.DiGraph, node_id: str) -> str:
    """Resolve capability from workflow step definition."""
    step = step_for_node(graph, node_id)
    if step.capability:
        return step.capability
    # Legacy RFQ demo mapping
    legacy = {
        "ocr": "ocr",
        "extract": "extract",
        "schema_validate": "validate",
        "risk_validate": "validate",
        "estimate": "estimate",
        "approve": "approve",
    }
    if node_id in legacy:
        return legacy[node_id]
    raise ValueError(f"No capability for node: {node_id}")


def node_params(graph: nx.DiGraph, node_id: str) -> dict[str, Any]:
    return step_for_node(graph, node_id).params


def simulate_duration(node_id: str) -> float:
    """Expected node duration for resync sweep heuristics."""
    durations = {
        "rules_eval": 0.1,
        "memory_load": 0.5,
        "llm_generate": 30.0,
        "persist_turn": 0.5,
        "ocr": 0.3,
        "extract": 0.5,
    }
    return durations.get(node_id, 2.0)
