"""Audit events → Kafka/Redpanda (Phase 3)."""

from __future__ import annotations

import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "false").lower() == "true"
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
KAFKA_TOPIC = os.getenv("KAFKA_AUDIT_TOPIC", "chat.audit")

_producer = None


def _get_producer():
    global _producer
    if _producer is not None:
        return _producer
    if not KAFKA_ENABLED:
        return None
    try:
        from kafka import KafkaProducer

        _producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )
        return _producer
    except Exception as exc:
        logger.warning("Kafka producer unavailable: %s", exc)
        return None


def publish_audit(event_type: str, payload: dict[str, Any], tenant_id: str = "default") -> None:
    message = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "payload": payload,
    }
    producer = _get_producer()
    if producer is None:
        logger.debug("audit (local): %s %s", event_type, payload.get("workflow_id"))
        return
    try:
        producer.send(KAFKA_TOPIC, value=message, key=tenant_id.encode())
        producer.flush(timeout=5)
    except Exception as exc:
        logger.warning("Kafka publish failed: %s", exc)
