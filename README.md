# Real Chat Agent

Production chat platform: **orchestrator DAG steps**, **workflow tiers by intent**, **fixed-depth ReAct (max 2 iter MVP)**.

## Phase 1 — Docker quick start

**Stack:** Redis · PostgreSQL · Orchestrator API · Chat Gateway · Celery worker · Celery beat

```bash
cd real-chat-agent
cp .env.example .env          # OPENAI_API_KEY=mock for local demo
docker compose up --build -d
```

| Service | URL |
|---------|-----|
| Chat Gateway + **UI** | http://localhost:8080/ |
| Orchestrator API | http://localhost:8000 |
| Redis | localhost:6379 |
| PostgreSQL | localhost:5432 (`chat` / `chat`) |

### Smoke test

```bash
chmod +x scripts/smoke.sh
./scripts/smoke.sh
```

### Manual curl

```bash
# Create session
curl -s -X POST http://localhost:8080/v1/sessions -H 'Content-Type: application/json' -d '{"title":"Test"}'

# Send message (replace SESSION_ID)
curl -s -X POST http://localhost:8080/v1/sessions/SESSION_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Hello Phase 1"}'

# Poll turn (replace SESSION_ID, TURN_ID)
curl -s "http://localhost:8080/v1/sessions/SESSION_ID/turns/TURN_ID?wait=true"
```

### Real LLM / 9Router

Set in `.env`:

```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=http://host.docker.internal:20128/v1   # optional 9Router
LLM_MODEL=gpt-4o-mini
docker compose up -d --build
```

Default `OPENAI_API_KEY=mock` returns echo replies without calling external APIs.

## Local dev (without Docker)

```bash
docker compose up -d redis postgres
pip install -r requirements.txt
export PYTHONPATH=$PWD OPENAI_API_KEY=mock
./scripts/run.sh api        # terminal 1 — port 8000
./scripts/run.sh gateway    # terminal 2 — port 8080
./scripts/run.sh worker     # terminal 3
./scripts/run.sh beat       # terminal 4 (optional)
pytest tests/ -v
```

## Phase 1 pipeline

```
rules_eval → memory_load → llm_generate → persist_turn
```

Workflow: [`workflows/chat_simple.yaml`](workflows/chat_simple.yaml)

## What this project is

| Source | Role |
|--------|------|
| [hello-agent](../hello-agent/) | Reference patterns & reusable modules |
| [mini-agent-orchestrator](../mini-agent-orchestrator/) | Runtime base: DAG, retry, DLQ, resync |

## Documentation index

| Document | Description |
|----------|-------------|
| [01 — Overview & Goals](docs/01-overview-and-goals.md) | Vision, principles |
| [02 — Architecture](docs/02-architecture.md) | Adaptive tiers, ReAct limits |
| [04 — Integration Design](docs/04-integration-design.md) | Step contract, context refs |
| [05 — Chat Workflows](docs/05-chat-workflows.md) | YAML templates |
| [06 — API Design](docs/06-api-design.md) | REST, SSE (Phase 2) |
| [07 — Deployment](docs/07-deployment.md) | Worker pools, 9Router |
| [08 — Roadmap](docs/08-implementation-roadmap.md) | Phase 1 → 3 |
| [09 — ADR (VI)](docs/09-design-decisions-vi.md) | Design decisions |
| [10 — Temporal migration](docs/10-temporal-migration.md) | Celery → Temporal |
| [On-call runbook](docs/runbooks/oncall.md) | Ops |

## Phase 3 — Temporal deep ReAct + SaaS infra

**Stack adds:** RabbitMQ · Redpanda (Kafka audit) · Temporal · temporal-worker · RLS · OIDC stub

```bash
docker compose up --build -d
chmod +x scripts/smoke-phase3.sh
./scripts/smoke-phase3.sh
```

| Service | URL |
|---------|-----|
| Temporal Web UI | http://localhost:8233 |
| RabbitMQ management | http://localhost:15672 (guest/guest) |
| Redpanda Kafka | localhost:19092 |

### UI modes (http://localhost:8080/)

| Toggle | Engine | Workflow |
|--------|--------|----------|
| (none) | Celery | `chat_simple` |
| KB / RAG | Celery | `chat_rag` |
| Tools (ReAct) | Celery | `chat_react` (max 2 iter) |
| **Deep ReAct** | **Temporal** | `chat_react_deep` (3–10 iter) |
| **Research mode** | **Temporal** | `chat_research` |
| HITL review | Temporal | pause + approve/reject API |

See [10 — Temporal migration](docs/10-temporal-migration.md) and [on-call runbook](docs/runbooks/oncall.md).

## Status

**Phase 3** — Temporal workflows, RabbitMQ broker, Kafka audit, PostgreSQL RLS, auth stub, Helm skeleton.

### Phase 2 recap

Workflow tiers, RAG/parse/context, Celery ReAct (max 2 iter), SSE stream, intent routing.

### UI modes — Celery (Phase 2)

| Toggle | Workflow |
|--------|----------|
| (none) | `chat_simple` |
| KB / RAG | `chat_rag` |
| Attachment text | `chat_attachment` |
| KB + Attachment | `chat_attachment_rag` |
| Search in file | `chat_attachment_rag_sequential` |
| Tools (ReAct) | `chat_react` |
