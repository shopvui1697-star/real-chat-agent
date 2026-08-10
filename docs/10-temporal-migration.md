# 10 — Temporal migration (Phase 3)

Guide for moving from **Celery fixed-depth ReAct (≤2 iter)** to **Temporal deep ReAct / research**.

## When to use which engine

| Path | Engine | Max tool rounds | Use case |
|------|--------|-----------------|----------|
| `chat_simple`, `chat_rag`, `chat_attachment*` | Celery | — | Default chat tiers |
| `chat_react` | Celery | **2** (YAML `max_iterations`) | Tools + quick ReAct |
| `chat_react_deep` | **Temporal** | 3–10 (`context.max_iterations`) | Long agent loops, HITL |
| `chat_research` | **Temporal** | up to 8 | Multi-round research + synthesis |

Gateway `resolve_route()` picks the engine from intent rules (`orchestrator/chat_rules.py`).

## Routing triggers

Temporal is selected when:

- `research_mode: true` → `chat_research`
- `deep_react: true` with tools → `chat_react_deep`
- `max_iterations > 2` with tools → `chat_react_deep`

Celery paths are unchanged — no YAML changes required for existing tiers.

## Local stack (Docker)

```bash
docker compose up --build -d
# Services: redis, postgres, rabbitmq, redpanda, temporal, orchestrator, gateway, worker, beat, temporal-worker
open http://localhost:8080/
./scripts/smoke-phase3.sh
```

| Service | Port |
|---------|------|
| Temporal gRPC | 7233 |
| Temporal Web UI | 8233 |
| RabbitMQ AMQP | 5672 |
| RabbitMQ management | 15672 |
| Redpanda Kafka | 19092 |

## HITL (human-in-the-loop)

1. Enable **HITL review** in UI or session config: `hitl_enabled: true`
2. Deep ReAct workflow pauses after `hitl_after_iteration` (default 1)
3. Approve / reject via API:

```bash
curl -X POST "http://localhost:8080/v1/sessions/$SESSION/turns/$TURN/approve"
curl -X POST "http://localhost:8080/v1/sessions/$SESSION/turns/$TURN/reject"
```

## Auth & RLS

- `AUTH_ENABLED=true` requires `Authorization: Bearer <JWT>` (HS256, `JWT_SECRET`)
- Dev fallback: `X-Tenant-Id` / `X-User-Id` headers
- PostgreSQL RLS filters sessions/messages by `app.tenant_id`

## Audit (Kafka)

Set `KAFKA_ENABLED=true` and `KAFKA_BOOTSTRAP=redpanda:9092`. Events: `turn_submitted`, `hitl_approved`, `hitl_rejected`, DLQ hooks.

## Migration checklist (prod)

1. Deploy Temporal cluster + `temporal-worker` (task queue `chat-deep`)
2. Point Celery broker to RabbitMQ (`CELERY_BROKER_URL`)
3. Enable RLS + tenant JWT claims
4. Route deep intents only — keep 80% traffic on Celery `chat_simple`
5. Monitor Temporal workflow backlog vs Celery queue depth
6. Run `./scripts/smoke-phase3.sh` in staging before cutover

## Rollback

If Temporal is unavailable, gateway returns `502` on deep/research submits. Fallback: disable `deep_react` / `research_mode` in session config so traffic stays on Celery `chat_react` (2 iter cap).
