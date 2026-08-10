"""Orchestrator FastAPI — workflow submit and status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from orchestrator.circuit_breaker import CircuitBreaker
from orchestrator.cleanup import CascadeCleanup
from orchestrator.dag import WorkflowSpec, load_workflow_spec
from orchestrator.dlq import DLQ
from orchestrator.metrics import Metrics
from orchestrator.orchestrator import Orchestrator
from orchestrator.registry import AgentRegistry
from orchestrator.resync_sweep import ResyncSweep
from orchestrator.state import StateStore, WorkflowState

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / "workflows" / "chat_simple.yaml"

app = FastAPI(title="Real Chat Agent Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()
store = StateStore()
cleanup = CascadeCleanup(store=store)


class SubmitRequest(BaseModel):
    workflow_yaml: str | None = None
    context: dict[str, Any] | None = None
    tenant_id: str = "default"


class SubmitResponse(BaseModel):
    workflow_id: str
    status: str
    version: int


class StatusResponse(BaseModel):
    workflow: WorkflowState
    agents: list[dict[str, Any]]
    dlq_count: int


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "2"}


@app.post("/submit", response_model=SubmitResponse)
def submit_workflow(body: SubmitRequest | None = None) -> SubmitResponse:
    tenant_id = body.tenant_id if body else "default"
    if body and body.workflow_yaml and body.context is not None:
        spec = WorkflowSpec.model_validate(yaml.safe_load(body.workflow_yaml))
        context = body.context
    else:
        spec = load_workflow_spec(DEFAULT_WORKFLOW)
        context = {
            "session_id": "sess_demo",
            "turn_id": "turn_demo",
            "user_message": "Hello from orchestrator demo",
            "needs_kb": False,
            "has_attachments": False,
            "needs_tools": False,
        }

    workflow_id = orchestrator.start_workflow(spec, context, tenant_id=tenant_id)
    state = store.get(workflow_id)
    return SubmitResponse(
        workflow_id=workflow_id,
        status=state.status.value if state else "QUEUED",
        version=state.version if state else 1,
    )


@app.get("/status/{workflow_id}", response_model=StatusResponse)
def get_status(workflow_id: str) -> StatusResponse:
    state = store.get(workflow_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    registry = AgentRegistry()
    agents = [a.model_dump() for a in registry.list_agents()]
    return StatusResponse(
        workflow=state,
        agents=agents,
        dlq_count=DLQ().count(),
    )


@app.post("/workflows/{workflow_id}/cancel")
def cancel_workflow(workflow_id: str) -> dict[str, Any]:
    state = store.get(workflow_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    updated = cleanup.request_cancel(workflow_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {
        "workflow_id": workflow_id,
        "status": updated.status.value,
        "cleanup_pending": updated.cleanup_pending,
        "version": updated.version,
    }


@app.post("/admin/sweep")
def trigger_sweep() -> dict[str, Any]:
    stats = ResyncSweep(store=store, orchestrator=orchestrator).run_once()
    return {"stats": stats}


@app.get("/metrics")
def metrics() -> str:
    cb_states = CircuitBreaker().all_states()
    if not cb_states:
        registry = AgentRegistry()
        cb_states = {a.name: "closed" for a in registry.list_agents()}
    return Metrics().render_prometheus(cb_states)


@app.get("/dlq")
def list_dlq(limit: int = 20) -> dict[str, Any]:
    entries = DLQ().list_entries(limit=limit)
    return {"count": DLQ().count(), "entries": entries}
