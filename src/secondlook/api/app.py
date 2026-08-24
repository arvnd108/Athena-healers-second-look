"""Athena REST API (issue #59). MCP is a different transport over `query/`."""

from __future__ import annotations

import os

from fastapi import FastAPI

from secondlook.api.auth import configure_auth
from secondlook.api.routes import cases, findings

DEFAULT_BIND_HOST = "127.0.0.1"


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
