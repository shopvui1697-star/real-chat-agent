"""PostgreSQL-backed session and message store (Phase 1 MVP)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


def _dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://chat:chat@localhost:5432/chat_agent",
    )


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                title TEXT,
                config JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}'"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                turn_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at)"
        )
        _setup_rls(conn)
        conn.commit()


def _setup_rls(conn) -> None:
    if os.getenv("RLS_ENABLED", "true").lower() != "true":
        return
    conn.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
    conn.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    conn.execute("DROP POLICY IF EXISTS sessions_tenant ON sessions")
    conn.execute("DROP POLICY IF EXISTS messages_tenant ON messages")
    conn.execute(
        """
        CREATE POLICY sessions_tenant ON sessions
        USING (tenant_id = current_setting('app.tenant_id', true))
        """
    )
    conn.execute(
        """
        CREATE POLICY messages_tenant ON messages
        USING (
            session_id IN (
                SELECT id FROM sessions
                WHERE tenant_id = current_setting('app.tenant_id', true)
            )
        )
        """
    )


@contextmanager
def _connect(tenant_id: str | None = None):
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        if tenant_id and os.getenv("RLS_ENABLED", "true").lower() == "true":
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def turn_workflow_key(turn_id: str) -> str:
    return f"turn:{turn_id}:workflow"


def turn_engine_key(turn_id: str) -> str:
    return f"turn:{turn_id}:engine"


def save_turn_workflow(turn_id: str, workflow_id: str, engine: str = "celery") -> None:
    import redis

    client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    client.set(turn_workflow_key(turn_id), workflow_id, ex=86400)
    client.set(turn_engine_key(turn_id), engine, ex=86400)


def workflow_for_turn(turn_id: str) -> str | None:
    import redis

    client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    return client.get(turn_workflow_key(turn_id))


def engine_for_turn(turn_id: str) -> str:
    import redis

    client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    return client.get(turn_engine_key(turn_id)) or "celery"


class MessageStore:
    def create_session(
        self,
        title: str | None = None,
        tenant_id: str = "default",
        config: dict | None = None,
    ) -> dict[str, Any]:
        session_id = f"sess_{uuid4().hex[:12]}"
        cfg = config or {}
        with _connect(tenant_id) as conn:
            conn.execute(
                "INSERT INTO sessions (id, tenant_id, title, config) VALUES (%s, %s, %s, %s::jsonb)",
                (session_id, tenant_id, title or "New chat", json.dumps(cfg)),
            )
            conn.commit()
        return {"session_id": session_id, "title": title or "New chat", "tenant_id": tenant_id, "config": cfg}

    def update_session_config(self, session_id: str, config: dict, tenant_id: str = "default") -> dict[str, Any] | None:
        with _connect(tenant_id) as conn:
            conn.execute(
                "UPDATE sessions SET config = %s::jsonb WHERE id = %s",
                (json.dumps(config), session_id),
            )
            conn.commit()
        return self.get_session(session_id, tenant_id=tenant_id)

    def get_session(self, session_id: str, tenant_id: str = "default") -> dict[str, Any] | None:
        with _connect(tenant_id) as conn:
            row = conn.execute(
                "SELECT id, tenant_id, title, config, created_at FROM sessions WHERE id = %s",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        cfg = row["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        return {
            "session_id": row["id"],
            "tenant_id": row["tenant_id"],
            "title": row["title"],
            "config": cfg or {},
            "created_at": row["created_at"].isoformat(),
        }

    def add_user_message(
        self,
        session_id: str,
        turn_id: str,
        content: str,
        message_id: str | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        mid = message_id or f"msg_{uuid4().hex[:12]}"
        with _connect(tenant_id) as conn:
            conn.execute(
                """
                INSERT INTO messages (id, session_id, turn_id, role, content)
                VALUES (%s, %s, %s, 'user', %s)
                """,
                (mid, session_id, turn_id, content),
            )
            conn.commit()
        return {"message_id": mid, "role": "user", "content": content, "turn_id": turn_id}

    def add_assistant_message(
        self,
        session_id: str,
        turn_id: str,
        content: str,
        message_id: str,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        with _connect(tenant_id) as conn:
            conn.execute(
                """
                INSERT INTO messages (id, session_id, turn_id, role, content)
                VALUES (%s, %s, %s, 'assistant', %s)
                """,
                (message_id, session_id, turn_id, content),
            )
            conn.commit()
        return {"message_id": message_id, "role": "assistant", "content": content}

    def list_messages(self, session_id: str, limit: int = 50, tenant_id: str = "default") -> list[dict[str, Any]]:
        with _connect(tenant_id) as conn:
            rows = conn.execute(
                """
                SELECT id, turn_id, role, content, created_at
                FROM messages WHERE session_id = %s
                ORDER BY created_at ASC LIMIT %s
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {
                "message_id": r["id"],
                "turn_id": r["turn_id"],
                "role": r["role"],
                "content": r["content"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
