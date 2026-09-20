from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from apps.api.settings import Settings, get_settings

Role = Literal["customer", "agent", "admin"]

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "customer": frozenset({"session:create", "session:read", "chat:write", "after-sales:confirm"}),
    "agent": frozenset(
        {
            "session:read",
            "approval:read",
            "approval:decide",
            "approval:resume",
            "knowledge:read",
            "evaluation:read",
        }
    ),
    "admin": frozenset(
        {
            "session:create",
            "session:read",
            "chat:write",
            "after-sales:confirm",
            "approval:read",
            "approval:decide",
            "approval:resume",
            "knowledge:read",
            "knowledge:write",
            "evaluation:read",
        }
    ),
}


class DemoLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    principal_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,40}$")
    role: Role = "customer"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str
    role: Role
    scopes: frozenset[str]


bearer = HTTPBearer(auto_error=False)


def signing_key(settings: Settings) -> bytes:
    return hashlib.sha256(settings.app_secret.encode("utf-8")).digest()


def issue_token(request: DemoLoginRequest, settings: Settings) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.jwt_ttl_seconds)
    token = jwt.encode(
        {
            "sub": request.principal_id,
            "role": request.role,
            "scopes": sorted(ROLE_SCOPES[request.role]),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "aud": "commerce-agent-api",
        },
        signing_key(settings),
        algorithm="HS256",
    )
    return token, expires_at


async def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        payload = jwt.decode(
            credentials.credentials,
            signing_key(settings),
            algorithms=["HS256"],
            audience="commerce-agent-api",
        )
        role = payload["role"]
        if role not in ROLE_SCOPES:
            raise ValueError("unknown role")
        token_scopes = frozenset(str(item) for item in payload.get("scopes", []))
        if not token_scopes.issubset(ROLE_SCOPES[role]):
            raise ValueError("token grants scopes outside the role")
        return Principal(
            principal_id=str(payload["sub"]),
            role=role,
            scopes=token_scopes,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from error


def require_scope(scope: str) -> object:
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")
        return principal

    return dependency
