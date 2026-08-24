"""REST write-route authentication. Fail closed unless explicitly opted out."""

from __future__ import annotations

import os
import secrets
import warnings

from fastapi import Header, HTTPException

API_KEY_ENV = "ATHENA_API_KEY"
AUTH_DISABLED_ENV = "ATHENA_API_AUTH_DISABLED"
API_KEY_HEADER = "X-Athena-Api-Key"


def _disabled() -> bool:
    return os.environ.get(AUTH_DISABLED_ENV, "").strip().lower() in {"1", "true", "yes"}


def configure_auth() -> None:
    """Refuse to start unauthenticated write routes unless explicitly opted out.

    Mirrors `mcp_server.server.resolve_bind_host`: the unsafe mode requires an
    explicit, named flag and logs a loud warning. Issue #60 (MCP auth) is a
    separate decision and is not substituted by this gate.
    """
    if _disabled():
        warnings.warn(
            "REST write routes are running with authentication disabled "
            f"({AUTH_DISABLED_ENV}=true). Do not expose this on a reachable "
            "network. This is insecure and local-dev only. Set "
            f"{API_KEY_ENV} and unset {AUTH_DISABLED_ENV} before any "
            "non-loopback deployment.",
            stacklevel=2,
        )
        return
    if not os.environ.get(API_KEY_ENV):
        raise RuntimeError(
            f"{API_KEY_ENV} is unset. Set it so write routes require the "
            f"{API_KEY_HEADER} header, or set {AUTH_DISABLED_ENV}=true for "
            "local development only."
        )


def require_api_key(x_athena_api_key: str | None = Header(default=None, alias=API_KEY_HEADER)):
    if _disabled():
        return None
    expected = os.environ.get(API_KEY_ENV)
    if not expected:
        raise HTTPException(
            status_code=500,
            detail=f"{API_KEY_ENV} is unset; write routes cannot run open.",
        )
    if not x_athena_api_key or not secrets.compare_digest(x_athena_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    return x_athena_api_key
