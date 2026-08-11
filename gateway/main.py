"""Chat Gateway API — Phase 3: Temporal, auth, SSE."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gateway.auth import get_auth_context
from gateway.routing import resolve_route
from gateway.store import (
    MessageStore,
    engine_for_turn,
    init_db,
    save_turn_workflow,
    workflow_for_turn,
)
from orchestrator.audit_kafka import publish_audit
from orchestrator.chat_rules import evaluate_chat_rules, preferred_llm_from_actions, uses_temporal

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"
UI_DIR = ROOT / "ui"
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")

app = FastAPI(title="Real Chat Agent Gateway", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = MessageStore()

if UI_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="ui-assets")


@app.get("/")
def chat_ui() -> FileResponse:
    index = UI_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(index)


@app.get("/step.html")
def step_ui() -> FileResponse:
    page = UI_DIR / "step.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Step UI not found")
    return FileResponse(page)


class SessionConfig(BaseModel):
    rag_enabled: bool = False
    needs_tools: bool = False
    deep_react: bool = False
    research_mode: bool = False
    hitl_enabled: bool = False
    max_iterations: int = 5
    workflow_template: str | None = None
    enabled_tools: list[str] = Field(default_factory=list)
    rag_namespace: str = "default:kb"


class CreateSessionRequest(BaseModel):
    title: str | None = None
    config: SessionConfig | None = None


class UpdateSessionRequest(BaseModel):
    config: SessionConfig


class AttachmentInput(BaseModel):
    name: str = "attachment.txt"
    text: str = ""


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)
    attachments: list[AttachmentInput] = Field(default_factory=list)
    needs_kb: bool | None = None
    needs_tools: bool | None = None
    deep_react: bool | None = None
    research_mode: bool | None = None
    query_scope: str | None = None


class RulesPreviewRequest(BaseModel):
    attachments: list[AttachmentInput] = Field(default_factory=list)
    needs_kb: bool = False
    needs_tools: bool = False
    deep_react: bool = False
    research_mode: bool = False
    query_scope: str | None = None
    max_iterations: int = 5
    workflow_template: str | None = None


def _rules_preview_from_request(body: RulesPreviewRequest) -> dict[str, Any]:
    attachments = [{"name": a.name, "text": a.text} for a in body.attachments if a.text.strip()]
    context: dict[str, Any] = {
        "attachments": attachments,
        "has_attachments": len(attachments) > 0,
        "needs_kb": body.needs_kb,
        "needs_tools": body.needs_tools or body.deep_react,
        "deep_react": body.deep_react,
        "research_mode": body.research_mode,
        "query_scope": body.query_scope or "",
        "max_iterations": body.max_iterations,
        "workflow_template": body.workflow_template,
    }
    rules = evaluate_chat_rules(context)
    llm_agent = preferred_llm_from_actions(rules.actions) or "llm_default_v1"
    runtime = "temporal" if uses_temporal(rules.workflow_template) else "celery"
    return {
        "intent": rules.intent,
        "workflow_template": rules.workflow_template,
        "runtime": runtime,
        "llm_agent": llm_agent,
        "rule_ids": rules.rule_ids,
        "actions": rules.actions,
        "eval_ms": rules.eval_ms,
    }


@app.post("/v1/rules/preview")
def preview_rules(body: RulesPreviewRequest) -> dict[str, Any]:
    return _rules_preview_from_request(body)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "3"}


@app.post("/v1/sessions")
def create_session(
    body: CreateSessionRequest | None = None,
    auth: dict = Depends(get_auth_context),
) -> dict[str, Any]:
    cfg = body.config.model_dump() if body and body.config else {}
    session = store.create_session(
        title=body.title if body else None,
        tenant_id=auth["tenant_id"],
        config=cfg,
    )
    full = store.get_session(session["session_id"], tenant_id=auth["tenant_id"])
    return full or session


@app.patch("/v1/sessions/{session_id}")
def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    auth: dict = Depends(get_auth_context),
) -> dict[str, Any]:
    if not store.get_session(session_id, tenant_id=auth["tenant_id"]):
        raise HTTPException(status_code=404, detail="Session not found")
    updated = store.update_session_config(session_id, body.config.model_dump(), auth["tenant_id"])
    return updated or {}


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, auth: dict = Depends(get_auth_context)) -> dict[str, Any]:
    session = store.get_session(session_id, tenant_id=auth["tenant_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/v1/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    limit: int = 50,
    auth: dict = Depends(get_auth_context),
) -> dict[str, Any]:
    if not store.get_session(session_id, tenant_id=auth["tenant_id"]):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": store.list_messages(session_id, limit=limit, tenant_id=auth["tenant_id"])}


def _build_context(
    session_id: str,
    turn_id: str,
    body: SendMessageRequest,
    session: dict,
    auth: dict,
) -> dict[str, Any]:
    cfg = session.get("config") or {}
    attachments = [{"name": a.name, "text": a.text} for a in body.attachments if a.text.strip()]
    needs_kb = body.needs_kb if body.needs_kb is not None else bool(cfg.get("rag_enabled"))
    needs_tools = body.needs_tools if body.needs_tools is not None else bool(cfg.get("needs_tools"))
    deep_react = body.deep_react if body.deep_react is not None else bool(cfg.get("deep_react"))
    research_mode = body.research_mode if body.research_mode is not None else bool(cfg.get("research_mode"))

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "tenant_id": auth["tenant_id"],
        "user_id": auth["user_id"],
        "user_message": body.content,
        "attachments": attachments,
        "has_attachments": len(attachments) > 0,
        "needs_kb": needs_kb,
        "needs_tools": needs_tools or deep_react,
        "deep_react": deep_react,
        "research_mode": research_mode,
        "hitl_enabled": bool(cfg.get("hitl_enabled")),
        "max_iterations": int(cfg.get("max_iterations", 5)),
        "query_scope": body.query_scope or "",
        "session_config": cfg,
        "enabled_tools": cfg.get("enabled_tools") or [],
        "rag_namespace": cfg.get("rag_namespace", "default:kb"),
        "stream_channel": f"stream:{session_id}:{turn_id}",
        "prefer_full_pipeline": cfg.get("workflow_template") == "chat_full",
        "workflow_template": cfg.get("workflow_template"),
    }


async def _start_temporal(template: str, context: dict, workflow_id: str) -> None:
    from temporal.client import start_deep_react, start_research

    if template == "chat_research":
        await start_research(context, workflow_id)
    else:
        await start_deep_react(context, workflow_id)


@app.post("/v1/sessions/{session_id}/messages", status_code=202)
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    auth: dict = Depends(get_auth_context),
) -> dict[str, Any]:
    session = store.get_session(session_id, tenant_id=auth["tenant_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    turn_id = f"turn_{uuid4().hex[:12]}"
    store.add_user_message(session_id, turn_id, body.content, tenant_id=auth["tenant_id"])

    context = _build_context(session_id, turn_id, body, session, auth)
    engine, payload, template, context = resolve_route(WORKFLOWS, context)

    publish_audit("turn_submitted", {"turn_id": turn_id, "template": template, "engine": engine}, auth["tenant_id"])

    if engine == "temporal":
        workflow_id = turn_id
        try:
            await _start_temporal(template, context, workflow_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Temporal error: {exc}") from exc
        save_turn_workflow(turn_id, workflow_id, engine="temporal")
        return {
            "turn_id": turn_id,
            "workflow_id": workflow_id,
            "workflow_template": template,
            "intent": context.get("intent"),
            "runtime": "temporal",
            "status": "RUNNING",
            "stream_url": f"/v1/sessions/{session_id}/turns/{turn_id}/stream",
            "poll_url": f"/v1/sessions/{session_id}/turns/{turn_id}",
        }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={"workflow_yaml": payload, "context": context, "tenant_id": auth["tenant_id"]},
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Orchestrator error: {resp.text}")
        data = resp.json()

    save_turn_workflow(turn_id, data["workflow_id"], engine="celery")
    return {
        "turn_id": turn_id,
        "workflow_id": data["workflow_id"],
        "workflow_template": template,
        "intent": context.get("intent"),
        "runtime": "celery",
        "status": data["status"],
        "stream_url": f"/v1/sessions/{session_id}/turns/{turn_id}/stream",
        "poll_url": f"/v1/sessions/{session_id}/turns/{turn_id}",
    }


@app.post("/v1/sessions/{session_id}/turns/{turn_id}/approve")
async def approve_turn(
    session_id: str,
    turn_id: str,
    auth: dict = Depends(get_auth_context),
) -> dict[str, str]:
    if engine_for_turn(turn_id) != "temporal":
        raise HTTPException(status_code=400, detail="HITL only for Temporal workflows")
    from temporal.client import signal_approve

    await signal_approve(turn_id)
    publish_audit("hitl_approved", {"turn_id": turn_id}, auth["tenant_id"])
    return {"status": "approved", "turn_id": turn_id}


@app.post("/v1/sessions/{session_id}/turns/{turn_id}/reject")
async def reject_turn(
    session_id: str,
    turn_id: str,
    auth: dict = Depends(get_auth_context),
) -> dict[str, str]:
    if engine_for_turn(turn_id) != "temporal":
        raise HTTPException(status_code=400, detail="HITL only for Temporal workflows")
    from temporal.client import signal_reject

    await signal_reject(turn_id)
    publish_audit("hitl_rejected", {"turn_id": turn_id}, auth["tenant_id"])
    return {"status": "rejected", "turn_id": turn_id}


@app.get("/v1/sessions/{session_id}/turns/{turn_id}")
async def get_turn(
    session_id: str,
    turn_id: str,
    wait: bool = False,
    auth: dict = Depends(get_auth_context),
) -> dict[str, Any]:
    if not store.get_session(session_id, tenant_id=auth["tenant_id"]):
        raise HTTPException(status_code=404, detail="Session not found")

    workflow_id = workflow_for_turn(turn_id)
    runtime = engine_for_turn(turn_id)
    workflow_status = None
    nodes_summary = None
    temporal_query = None
    react_iteration = None

    if workflow_id and runtime == "temporal":
        from temporal.client import get_workflow_status

        for _ in range(20 if wait else 1):
            ts = await get_workflow_status(workflow_id)
            workflow_status = ts.get("status")
            temporal_query = ts.get("query")
            if ts.get("result"):
                react_iteration = ts["result"].get("temporal_iterations") or ts["result"].get("research_rounds")
            if workflow_status in ("COMPLETED", "FAILED", "CANCELED", "TERMINATED") or not wait:
                break
            await asyncio.sleep(1.5)
    elif workflow_id:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{ORCHESTRATOR_URL}/status/{workflow_id}")
            if resp.status_code == 200:
                wf = resp.json()["workflow"]
                workflow_status = wf["status"]
                nodes_summary = {
                    nid: {"status": n["status"], "error": n.get("error")}
                    for nid, n in wf.get("nodes", {}).items()
                }
                react_iteration = wf.get("context", {}).get("react_iteration")

    messages = [m for m in store.list_messages(session_id, tenant_id=auth["tenant_id"]) if m.get("turn_id") == turn_id]
    assistant = next((m for m in messages if m["role"] == "assistant"), None)

    if wait and not assistant and workflow_id and runtime == "temporal":
        for _ in range(10):
            if workflow_status == "COMPLETED":
                messages = [
                    m
                    for m in store.list_messages(session_id, tenant_id=auth["tenant_id"])
                    if m.get("turn_id") == turn_id
                ]
                assistant = next((m for m in messages if m["role"] == "assistant"), None)
                if assistant:
                    break
            await asyncio.sleep(0.5)

    if wait and not assistant and workflow_id and runtime == "celery":
        import time

        for _ in range(20):
            messages = [m for m in store.list_messages(session_id, tenant_id=auth["tenant_id"]) if m.get("turn_id") == turn_id]
            assistant = next((m for m in messages if m["role"] == "assistant"), None)
            if assistant:
                break
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(f"{ORCHESTRATOR_URL}/status/{workflow_id}")
                if resp.status_code == 200 and resp.json()["workflow"]["status"] in ("COMPLETED", "FAILED"):
                    break
            time.sleep(1.5)
        messages = [m for m in store.list_messages(session_id, tenant_id=auth["tenant_id"]) if m.get("turn_id") == turn_id]
        assistant = next((m for m in messages if m["role"] == "assistant"), None)

    status = "completed" if assistant else ("failed" if workflow_status in ("FAILED", "TERMINATED") else "processing")

    return {
        "turn_id": turn_id,
        "session_id": session_id,
        "workflow_id": workflow_id,
        "runtime": runtime,
        "workflow_status": workflow_status,
        "nodes": nodes_summary,
        "temporal_query": temporal_query,
        "react_iteration": react_iteration,
        "status": status,
        "assistant_message": assistant["content"] if assistant else None,
        "messages": messages,
    }


@app.get("/v1/sessions/{session_id}/turns/{turn_id}/stream")
async def stream_turn(session_id: str, turn_id: str) -> StreamingResponse:
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    channel = f"stream:{session_id}:{turn_id}"
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    async def event_generator():
        client = redis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub()
        pubsub.subscribe(channel)
        yield f"event: connected\ndata: {json.dumps({'channel': channel})}\n\n"
        try:
            while True:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    payload = json.loads(message["data"])
                    event = payload.get("event", "message")
                    yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
                    if event == "done":
                        break
                await asyncio.sleep(0.05)
                wf_id = workflow_for_turn(turn_id)
                runtime = engine_for_turn(turn_id)
                if wf_id and runtime == "celery":
                    with httpx.Client(timeout=5.0) as http:
                        r = http.get(f"{ORCHESTRATOR_URL}/status/{wf_id}")
                        if r.status_code == 200 and r.json()["workflow"]["status"] in ("COMPLETED", "FAILED"):
                            await asyncio.sleep(0.5)
                            break
                elif wf_id and runtime == "temporal":
                    from temporal.client import get_workflow_status

                    ts = await get_workflow_status(wf_id)
                    if ts.get("status") in ("COMPLETED", "FAILED", "TERMINATED", "CANCELED"):
                        await asyncio.sleep(0.5)
                        break
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()
            client.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/v1/workflows/{workflow_id}/status")
async def proxy_workflow_status(workflow_id: str) -> dict[str, Any]:
    if engine_for_turn(workflow_id) == "temporal" or workflow_id.startswith("turn_"):
        from temporal.client import get_workflow_status

        return await get_workflow_status(workflow_id)
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"{ORCHESTRATOR_URL}/status/{workflow_id}")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=resp.text)
        return resp.json()


@app.get("/v1/workflows/{workflow_id}/steps/{step_id}")
async def get_workflow_step(workflow_id: str, step_id: str) -> dict[str, Any]:
    if engine_for_turn(workflow_id) == "temporal" or workflow_id.startswith("turn_"):
        from temporal.client import get_temporal_step_detail

        return await get_temporal_step_detail(workflow_id, step_id)

    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"{ORCHESTRATOR_URL}/status/{workflow_id}")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=resp.text)
        wf = resp.json()["workflow"]
        node = wf.get("nodes", {}).get(step_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Step not found: {step_id}")
        spec_step = next(
            (s for s in wf.get("context", {}).get("_workflow_spec", {}).get("steps", []) if s.get("id") == step_id),
            None,
        )
        return {
            "workflow_id": workflow_id,
            "step_id": step_id,
            "runtime": "celery",
            "workflow_name": wf.get("name"),
            "workflow_status": wf.get("status"),
            "capability": spec_step.get("capability") if spec_step else None,
            "step": node,
        }
