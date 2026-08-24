"""In-memory store stand-in for query-layer tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FakeCase:
    id: uuid.UUID
    label: str
    cancer_type: str
    age_years: int | None = None
    primary_site: str | None = None
    histology: str | None = None
    stage: str | None = None
    doid: str | None = None


@dataclass
class FakeEvent:
    id: uuid.UUID
    event_type: str
    payload: dict
    occurred_at: datetime
    recorded_at: datetime


@dataclass
class FakeQuestion:
    id: uuid.UUID
    text: str
    status: str
    priority: int
    created_at: datetime


@dataclass
class FakeStore:
    case: FakeCase | None = None
    events: list[FakeEvent] = field(default_factory=list)
    questions: list[FakeQuestion] = field(default_factory=list)
    findings: tuple = ()

    def get_case(self, case_id):
        if self.case is not None and self.case.id == case_id:
            return self.case
        return None

    def list_events(self, case_id):
        del case_id
        return sorted(self.events, key=lambda e: e.occurred_at)

    def list_questions(self, case_id):
        del case_id
        return sorted(self.questions, key=lambda q: q.created_at)

    def list_active_findings(self, case_id):
        del case_id
        return self.findings

    def derive_state(self, case_id):
        from secondlook.query.fold import fold_store_events

        return fold_store_events(case_id, self.list_events(case_id))


class FakeGraph:
    def __init__(self, responses: list[list[tuple]] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params or {}))
        rows = self._responses.pop(0) if self._responses else []

        class _Result:
            def __init__(self, result_set):
                self.result_set = result_set

        return _Result(rows)
