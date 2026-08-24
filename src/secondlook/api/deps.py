"""Shared FastAPI dependencies. No business logic."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from secondlook.case.store import CaseStore
from secondlook.signals.registry import dispatch

DEFAULT_DATABASE_URL = "postgresql+psycopg://athena:athena@localhost:5432/athena"


def get_session() -> Iterator[Session]:
    from sqlalchemy import create_engine

    url = os.environ.get("ATHENA_DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_engine(url)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def get_store(session: Session = Depends(get_session)) -> CaseStore:
    return CaseStore(session)


def get_existing_case(case_id: uuid.UUID, store: CaseStore = Depends(get_store)):
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case with id {case_id}")
    return case


def get_existing_finding(finding_id: uuid.UUID, store: CaseStore = Depends(get_store)):
    finding = store.get_finding(finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail=f"no finding with id {finding_id}")
    return finding


def get_graph():
    return None


def get_llm_client():
    return None


def get_dispatch():
    return dispatch


__all__ = [
    "get_dispatch",
    "get_existing_case",
    "get_existing_finding",
    "get_graph",
    "get_llm_client",
    "get_session",
    "get_store",
]
