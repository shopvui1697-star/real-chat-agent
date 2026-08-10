"""Celery application — RabbitMQ broker + Redis backend (Phase 3)."""

import os

from celery import Celery

from orchestrator.config import SWEEP_INTERVAL_SEC

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

celery_app = Celery(
    "redex_orchestrator",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["orchestrator.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "resync-sweep": {
            "task": "orchestrator.resync_sweep",
            "schedule": float(SWEEP_INTERVAL_SEC),
        },
    },
)
