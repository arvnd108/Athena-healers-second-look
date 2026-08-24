"""Athena REST API (issue #59). MCP is a different transport over `query/`."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from secondlook.api.auth import configure_auth
from secondlook.api.routes import cases, findings

DEFAULT_BIND_HOST = "127.0.0.1"

# docker-compose.yml deliberately serves the frontend (nginx, port 8080) and
# this API (port 8000) on different ports -- see web/Dockerfile's comment on
# why VITE_API_BASE has to be a browser-reachable URL, not Docker's internal
# DNS. Different ports means different origins by browser rules, so without
# CORS headers here, every fetch the frontend makes is blocked before it
# reaches any route -- confirmed live (issue #100), not assumed: the API's
# own responses looked fine over curl, which doesn't enforce CORS, while the
# actual built frontend in an actual browser failed on every single request.
DEFAULT_CORS_ORIGINS = "http://localhost:8080"


def _cors_origins() -> list[str]:
    raw = os.environ.get("ATHENA_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    configure_auth()
    app = FastAPI(
        title="Athena REST API",
        version="1.0.0",
        description=(
            "System of record for case writes. Auth is an API key on POST routes; "
            "MCP remote bind uses a separate ATHENA_MCP_API_KEY bearer token."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Athena-Api-Key"],
    )
    app.include_router(cases.router)
    app.include_router(findings.router)
    return app


def main(argv: list[str] | None = None) -> int:
    del argv
    import uvicorn

    host = os.environ.get("ATHENA_API_HOST", DEFAULT_BIND_HOST)
    port = int(os.environ.get("ATHENA_API_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
    return 0
