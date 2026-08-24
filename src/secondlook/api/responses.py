"""Unwrap QueryResult at the REST boundary. The envelope is MCP/internal."""

from __future__ import annotations

from fastapi import HTTPException

from secondlook.query.contracts import ChangeSetForApi
from secondlook.query.result import QueryResult


def first_item(result: QueryResult):
    if result.items:
        return result.items[0]
    raise HTTPException(status_code=500, detail=result.empty_reason or "empty query result")


def changeset_for_response(result: QueryResult[ChangeSetForApi]) -> ChangeSetForApi:
    """A real case with nothing new is 200 + explained empty, never the envelope."""
    if result.items:
        return result.items[0]
    return ChangeSetForApi(
        changes=[],
        supersessions=[],
        boundary_event_id=None,
        empty_reason=result.empty_reason,
    )
