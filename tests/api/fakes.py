"""In-memory CaseStore stand-in for FastAPI TestClient tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from secondlook.case.models import EVENT_TYPES
from secondlook.case.store import domain_finding_from_row, serialize_assumptions
from secondlook.query.fold import fold_store_events


@dataclass
class MemCase:
    id: uuid.UUID
    label: str
    cancer_type: str
    age_years: int | None = None
    primary_site: str | None = None
    histology: str | None = None
    stage: str | None = None
    doid: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MemEvent:
    id: uuid.UUID
    case_id: uuid.UUID
    event_type: str
    payload: dict
    occurred_at: datetime
    recorded_at: datetime
    source_document: str | None = None
    recorded_by: str | None = None


@dataclass
class MemQuestion:
    id: uuid.UUID
    case_id: uuid.UUID
    text: str
    status: str
    priority: int
    triggered_by: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    findings: list = field(default_factory=list)


@dataclass
class MemDecision:
    id: uuid.UUID
    finding_id: uuid.UUID
    action: str
    reason: str
    decided_by: str
    decided_at: datetime


@dataclass
class MemFinding:
    id: uuid.UUID
    question_id: uuid.UUID
    claim: str
    evidence_class: str
    evidence_ref: dict
    assumptions: list
    evidence_level: str | None = None
    status: str = "active"
    question: MemQuestion | None = None
    decisions: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryStore:
    def __init__(self) -> None:
        self.cases: dict[uuid.UUID, MemCase] = {}
        self.events: list[MemEvent] = []
        self.questions: list[MemQuestion] = []
        self.findings: list[MemFinding] = []

    def create_case(self, **kwargs) -> MemCase:
        case = MemCase(id=uuid.uuid4(), **kwargs)
        self.cases[case.id] = case
        return case

    def get_case(self, case_id):
        return self.cases.get(case_id)

    def append_event(
        self,
        case_id,
        *,
        event_type,
        payload,
        occurred_at,
        source_document=None,
        recorded_by=None,
    ):
        if event_type not in EVENT_TYPES:
            raise ValueError(event_type)
        event = MemEvent(
            id=uuid.uuid4(),
            case_id=case_id,
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at,
            recorded_at=datetime.now(UTC),
            source_document=source_document,
            recorded_by=recorded_by,
        )
        self.events.append(event)
        return event

    def list_events(self, case_id):
        return sorted(
            [e for e in self.events if e.case_id == case_id],
            key=lambda e: e.occurred_at,
        )

    def derive_state(self, case_id):
        return fold_store_events(case_id, self.list_events(case_id))

    def create_question(
        self,
        case_id,
        *,
        text,
        status="open",
        priority,
        triggered_by=None,
        suppressed_by=None,
    ):
        del suppressed_by
        question = MemQuestion(
            id=uuid.uuid4(),
            case_id=case_id,
            text=text,
            status=status,
            priority=priority,
            triggered_by=triggered_by,
        )
        self.questions.append(question)
        return question

    def list_questions(self, case_id):
        return sorted(
            [q for q in self.questions if q.case_id == case_id],
            key=lambda q: q.created_at,
        )

    def create_finding(
        self,
        question_id,
        *,
        claim,
        evidence_class,
        evidence_ref,
        assumptions,
        evidence_level=None,
        status="active",
    ):
        question = next(q for q in self.questions if q.id == question_id)
        finding = MemFinding(
            id=uuid.uuid4(),
            question_id=question_id,
            claim=claim,
            evidence_class=evidence_class,
            evidence_ref=evidence_ref,
            assumptions=serialize_assumptions(assumptions),
            evidence_level=evidence_level,
            status=status,
            question=question,
        )
        self.findings.append(finding)
        question.findings.append(finding)
        return finding

    def get_finding(self, finding_id):
        for finding in self.findings:
            if finding.id == finding_id:
                return finding
        return None

    def list_active_findings(self, case_id):
        rows = []
        for finding in self.findings:
            if finding.status != "active" or finding.question is None:
                continue
            if finding.question.case_id != case_id:
                continue
            rows.append(domain_finding_from_row(finding, case_id))
        return tuple(rows)

    def create_decision(self, finding_id, *, action, reason, decided_by):
        finding = self.get_finding(finding_id)
        if finding is None:
            raise ValueError(f"No finding with id {finding_id}")
        decision = MemDecision(
            id=uuid.uuid4(),
            finding_id=finding_id,
            action=action,
            reason=reason,
            decided_by=decided_by,
            decided_at=datetime.now(UTC),
        )
        finding.decisions.append(decision)
        return decision
