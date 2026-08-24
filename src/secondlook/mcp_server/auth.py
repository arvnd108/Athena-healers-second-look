"""Static bearer-token auth for the MCP server's remote bind.

Fail-closed when `--allow-remote` is used: `ATHENA_MCP_API_KEY` must be set
or the server refuses to start. Loopback (the default) and stdio do not use
this module.

Clients send `Authorization: Bearer <token>` — `BearerAuthBackend` in the
MCP SDK reads that header; there is no custom `X-` header on this transport.

`AuthSettings.issuer_url` and `AuthSettings.resource_server_url` exist only
to satisfy the SDK's requirement for turning on `RequireAuthMiddleware`.
They are not a real OAuth authorization server: this process never issues
tokens, never redirects, and never talks to an IdP. The placeholder URL
below is unused for real discovery — do not treat the `.well-known`
document the SDK may serve as an authorization-server endpoint.
"""

from __future__ import annotations

import os
import secrets

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

API_KEY_ENV = "ATHENA_MCP_API_KEY"

# Placeholder so AuthSettings can be constructed. Not a reachable issuer.
_AUTH_METADATA_URL = "https://athena.local/mcp"


class StaticTokenVerifier:
    """Local constant-time compare against `ATHENA_MCP_API_KEY`. Not OAuth."""

    def __init__(self, expected: str) -> None:
        self._expected = expected

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._expected):
            return None
        return AccessToken(token=token, client_id="mcp-static", scopes=["mcp"])


def configure_remote_auth() -> tuple[AuthSettings, StaticTokenVerifier]:
    """Refuse to start an unauthenticated remote bind.

    Called from `build_server` when `--allow-remote` is set, before the
    process binds. Raises `RuntimeError` if `ATHENA_MCP_API_KEY` is unset.
    """
    expected = os.environ.get(API_KEY_ENV)
    if not expected:
        raise RuntimeError(
            f"{API_KEY_ENV} is unset. Set it before passing --allow-remote; "
            "the MCP server will not bind beyond loopback unauthenticated."
        )
    auth = AuthSettings(
        issuer_url=_AUTH_METADATA_URL,
        resource_server_url=_AUTH_METADATA_URL,
    )
    return auth, StaticTokenVerifier(expected)
