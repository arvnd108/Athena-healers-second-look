"""MCP server entry: loopback bind by default, no auth (see issue #60)."""

from __future__ import annotations

import argparse
import logging
import os
import warnings

from secondlook.mcp_server.tools import (
    get_access_pathways_tool,
    get_case_summary_tool,
    get_recent_changes_tool,
    match_trials_tool,
    search_evidence_tool,
)
from secondlook.mcp_server.view import ReadOnlyCaseView

logger = logging.getLogger(__name__)

DEFAULT_BIND_HOST = "127.0.0.1"
REMOTE_BIND_HOST = "0.0.0.0"
DEFAULT_DATABASE_URL = "postgresql+psycopg://athena:athena@localhost:5432/athena"


def resolve_bind_host(*, allow_remote: bool = False) -> str:
    if allow_remote:
        warnings.warn(
            "MCP server is binding beyond loopback with no authentication. "
            "Do not expose this on a reachable network until issue #60 "
            "(https://github.com/healers-second-look/Athena/issues/60) lands.",
            stacklevel=2,
        )
        return REMOTE_BIND_HOST
    return DEFAULT_BIND_HOST


def register_tools(mcp, view: ReadOnlyCaseView, graph=None) -> None:
    """Attach the five read tools. `mcp` is a FastMCP-like object with `.tool()`."""

    @mcp.tool()
    def get_case_summary(case_id: str) -> dict:
        return get_case_summary_tool(view, case_id)

    @mcp.tool()
    def get_recent_changes(case_id: str, since: str | None = None) -> dict:
        from datetime import datetime

        parsed = datetime.fromisoformat(since) if since else None
        return get_recent_changes_tool(view, case_id, since=parsed)

    @mcp.tool()
    def search_evidence(
        gene: str, variant: str | None = None, cancer_type: str | None = None
    ) -> dict:
        return search_evidence_tool(view, gene, variant, cancer_type, graph=graph)

    @mcp.tool()
    def match_trials(case_id: str) -> dict:
        return match_trials_tool(view, case_id, graph=graph)

    @mcp.tool()
    def get_access_pathways(drug: str, country: str) -> dict:
        return get_access_pathways_tool(view, drug, country, graph=graph)


def build_server(view: ReadOnlyCaseView, *, graph=None, allow_remote: bool = False):
    from mcp.server.fastmcp import FastMCP

    host = resolve_bind_host(allow_remote=allow_remote)
    mcp = FastMCP("athena-secondlook", host=host)
    register_tools(mcp, view, graph=graph)
    return mcp


def open_read_only_view(database_url: str | None = None) -> ReadOnlyCaseView:
    """Build the type boundary MCP tools receive. Never a writable store handle."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from secondlook.case.store import CaseStore

    url = database_url or os.environ.get("ATHENA_DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_engine(url)
    return ReadOnlyCaseView(CaseStore(Session(engine)))


def _optional_graph():
    from secondlook.tier1.graph_connection import connect_graph

    return connect_graph()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Athena read-only MCP server")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Bind 0.0.0.0. Unauthenticated; see issue #60. Off by default.",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "sse", "streamable-http"),
        help="MCP transport. stdio is the local-client default.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    host = resolve_bind_host(allow_remote=args.allow_remote)
    logger.info(
        "MCP bind host %s transport=%s (authentication is issue #60; not implemented)",
        host,
        args.transport,
    )
    view = open_read_only_view()
    graph = _optional_graph()
    mcp = build_server(view, graph=graph, allow_remote=args.allow_remote)
    mcp.run(transport=args.transport)
    return 0
