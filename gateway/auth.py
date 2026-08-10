"""OIDC/JWT auth stub (Phase 3 MVP)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
DEV_TENANT = os.getenv("DEFAULT_TENANT_ID", "default")

_bearer = HTTPBearer(auto_error=False)


def decode_token(token: str) -> dict[str, Any]:
    try:
        import jwt

        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    tenant_id = request.headers.get("X-Tenant-Id", DEV_TENANT)
    user_id = request.headers.get("X-User-Id", "anonymous")
    roles: list[str] = []

    if credentials and credentials.credentials:
        claims = decode_token(credentials.credentials)
        tenant_id = str(claims.get("tenant_id") or claims.get("tid") or tenant_id)
        user_id = str(claims.get("sub") or user_id)
        raw_roles = claims.get("roles") or claims.get("role") or []
        if isinstance(raw_roles, str):
            roles = [raw_roles]
        else:
            roles = list(raw_roles)
    elif AUTH_ENABLED:
        raise HTTPException(status_code=401, detail="Authorization required")

    return {"tenant_id": tenant_id, "user_id": user_id, "roles": roles}
