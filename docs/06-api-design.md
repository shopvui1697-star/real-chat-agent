# 06 — API Design (Draft)

Public **Chat Gateway** API plus internal orchestrator endpoints. OpenAPI will be generated from FastAPI at implementation time.

## Base URLs

| Environment | Chat Gateway | Orchestrator (internal) |
|-------------|--------------|-------------------------|
| Local | `http://localhost:8080` | `http://localhost:8000` |
| Production | `https://chat.api.example.com` | Cluster-internal only |

## Authentication (Phase 2+)

```
Authorization: Bearer <JWT>
```

JWT claims: `sub`, `tenant_id`, `roles`. Gateway validates OIDC; forwards `tenant_id` on every orchestrator submit.

MVP: `X-Tenant-Id` header + API key.

---

## Sessions

### Create session

```
POST /v1/sessions
```

**Request**

```json
{
  "title": "Support chat",
  "config": {
    "rag_enabled": false,
    "mcp_servers": [],
    "workflow_template": "chat_turn"
  }
}
```

**Response** `201`

```json
{
  "session_id": "sess_abc123",
  "created_at": "2026-08-10T04:00:00Z",
  "config": { "rag_enabled": false }
}
```

### List sessions

```
GET /v1/sessions?limit=20&cursor=
```

### Get session

```
GET /v1/sessions/{session_id}
```

Returns metadata + last message preview (not full history).

### Delete session

```
DELETE /v1/sessions/{session_id}
```

Triggers orchestrator cascade cleanup for any in-flight turn workflows tied to the session.

---

## Messages & turns

### Send message (start turn)

```
POST /v1/sessions/{session_id}/messages
```

**Request**

```json
{
  "content": "What is our refund policy?",
  "attachments": []
}
```

**Response** `202 Accepted`

```json
{
  "turn_id": "turn_xyz",
  "workflow_id": "wf_789",
  "status": "queued",
  "stream_url": "/v1/sessions/sess_abc123/turns/turn_xyz/stream"
}
```

Gateway persists user message, submits workflow, returns immediately.

### List messages

```
GET /v1/sessions/{session_id}/messages?limit=50&before=
```

**Response**

```json
{
  "messages": [
    {
      "id": "msg_1",
      "role": "user",
      "content": "...",
      "created_at": "..."
    },
    {
      "id": "msg_2",
      "role": "assistant",
      "content": "...",
      "status": "completed",
      "step_trace": [
        {"node": "memory_load", "latency_ms": 45},
        {"node": "rag_retrieve", "latency_ms": 340, "chunks": 3},
        {"node": "llm_generate", "token_usage": {"prompt": 2100, "completion": 180}}
      ],
      "created_at": "..."
    }
  ]
}
```

### Get turn status

```
GET /v1/sessions/{session_id}/turns/{turn_id}
```

Proxies orchestrator `/status/{workflow_id}` with **per-step** timeline (one node per capability):

```json
{
  "turn_id": "turn_xyz",
  "workflow_id": "wf_789",
  "status": "running",
  "current_node": "llm_generate",
  "nodes": [
    {"id": "rules_eval", "capability": "rules", "status": "done", "latency_ms": 12},
    {"id": "memory_load", "capability": "memory_load", "status": "done", "latency_ms": 45},
    {"id": "rag_retrieve", "capability": "rag_retrieve", "status": "skipped"},
    {"id": "context_build", "capability": "context_build", "status": "done", "latency_ms": 89},
    {"id": "llm_generate", "capability": "llm_generate", "status": "running"},
    {"id": "persist_reply", "capability": "persist_reply", "status": "pending"}
  ]
}
```

### Cancel turn

```
POST /v1/sessions/{session_id}/turns/{turn_id}/cancel
```

Maps to orchestrator `POST /workflows/{workflow_id}/cancel`.

---

## Streaming

### SSE stream

```
GET /v1/sessions/{session_id}/turns/{turn_id}/stream
Accept: text/event-stream
```

**Events**

| Event | Payload | When |
|-------|---------|------|
| `node_start` | `{"node": "rag_retrieve", "capability": "rag_retrieve"}` | Any step begins |
| `node_done` | `{"node": "rag_retrieve", "latency_ms": 340}` | Step completes |
| `token` | `{"content": "Hel"}` | **`llm_generate` only** |
| `mcp_start` | `{"node": "mcp_invoke", "tool": "maps_weather"}` | MCP step |
| `mcp_done` | `{"node": "mcp_invoke", "latency_ms": 1200}` | MCP step |
| `done` | `{"message_id": "msg_2", "status": "completed"}` | Turn complete |
| `error` | `{"code": "...", "node": "llm_generate", "message": "..."}` | Step or turn failure |

MVP fallback: client polls `GET .../turns/{turn_id}` until terminal state.

---

## Session configuration

### Update config

```
PATCH /v1/sessions/{session_id}/config
```

```json
{
  "rag_enabled": true,
  "rag_namespace": "acme:default",
  "mcp_servers": ["amap"]
}
```

Affects subsequent turns only.

---

## Knowledge base (Phase 2)

### Upload document

```
POST /v1/knowledge/documents
Content-Type: multipart/form-data
```

### List documents

```
GET /v1/knowledge/documents
```

Indexing runs as async workflow (`capability: index_rag`).

---

## Admin & ops

Internal or role-gated (`admin`).

| Method | Path | Maps to |
|--------|------|---------|
| GET | `/v1/admin/metrics` | Orchestrator `/metrics` + gateway counters |
| GET | `/v1/admin/dlq` | Orchestrator `/dlq` |
| POST | `/v1/admin/dlq/replay/{id}` | DLQ replay |
| POST | `/v1/admin/sweep` | Resync sweep |
| POST | `/v1/admin/rules/simulate` | Rules simulate with chat context |
| GET | `/v1/admin/agents` | Registry listing + health |

---

## Internal orchestrator API (existing)

Retained from mini-agent-orchestrator; called by gateway, not public:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/submit` | Start chat workflow |
| GET | `/status/{workflow_id}` | Turn progress |
| POST | `/workflows/{id}/cancel` | Cancel |
| DELETE | `/workflows/{id}` | Delete + cleanup |
| POST | `/admin/sweep` | Manual resync |
| GET | `/metrics` | Prometheus text |
| GET | `/health` | Liveness |

Submit body for chat (gateway selects template; orchestrator runs full step DAG):

```json
{
  "workflow_yaml": "<contents of chat_turn.yaml>",
  "context": {
    "session_id": "...",
    "turn_id": "...",
    "user_message": "...",
    "needs_kb": false,
    "rag_namespace": "acme:default",
    "stream_channel": "stream:sess:turn"
  },
  "tenant_id": "acme"
}
```

Orchestrator merges `context_delta` from each step (`memory_snippets`, `rag_chunks`, `prompt_messages`, `assistant_message`) into the workflow context blob.

---

## Error model

```json
{
  "error": {
    "code": "turn_failed",
    "message": "Agent execution failed after retries",
    "workflow_id": "wf_789",
    "dlq_id": "dlq_001"
  }
}
```

| HTTP | Code | When |
|------|------|------|
| 400 | `validation_error` | Empty message, bad config |
| 404 | `session_not_found` | Unknown session |
| 409 | `turn_in_progress` | Duplicate send without idempotency key |
| 429 | `rate_limited` | Tenant quota |
| 503 | `orchestrator_unavailable` | Submit failed |

---

## Idempotency

Clients may send:

```
Idempotency-Key: <uuid>
```

on `POST .../messages`. Gateway dedupes within 24h — same key returns original `turn_id` without re-submitting workflow.
