# On-call runbook — Real Chat Agent (Phase 3)

## Quick health checks

```bash
curl -sf http://localhost:8080/health   # gateway phase=3
curl -sf http://localhost:8000/health   # orchestrator
docker compose ps
```

## Symptom: Chat stuck on "processing"

1. Check turn runtime: `GET /v1/sessions/{sid}/turns/{tid}` → `runtime`
2. **Celery:** `GET http://localhost:8000/status/{workflow_id}` — look for FAILED nodes or DLQ
3. **Temporal:** `GET /v1/workflows/{workflow_id}/status` — check `status`, `query.waiting_hitl`
4. If HITL waiting: POST `/approve` or `/reject`
5. Inspect worker logs: `docker compose logs worker --tail=100`
6. Temporal worker: `docker compose logs temporal-worker --tail=100`

## Symptom: Temporal 502 on submit

- Verify Temporal: `docker compose logs temporal --tail=50`
- Ensure `temporal-worker` is running and connected to `TEMPORAL_HOST`
- Postgres must have `temporal` database (init script `deploy/postgres/init/01-temporal.sql`)

## Symptom: Celery tasks not consumed

- RabbitMQ: http://localhost:15672 (guest/guest)
- Confirm `CELERY_BROKER_URL=amqp://guest:guest@rabbitmq:5672//`
- Restart: `docker compose restart worker beat`

## Symptom: Messages missing / wrong tenant

- RLS: confirm `X-Tenant-Id` matches session `tenant_id`
- Check `RLS_ENABLED` and JWT `tenant_id` claim

## Symptom: No SSE tokens

- Redis pub/sub channel `stream:{session_id}:{turn_id}`
- Verify `REDIS_URL` shared across gateway, worker, temporal-worker

## DLQ replay

```bash
curl http://localhost:8000/dlq
# Manual replay: fix root cause, re-submit message from UI
```

## Escalation data to collect

- `turn_id`, `workflow_id`, `runtime`, `workflow_template`
- Gateway + worker + temporal-worker logs (5 min window)
- Temporal Web UI: http://localhost:8233
