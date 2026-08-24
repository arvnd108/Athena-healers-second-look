from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from secondlook.case.assumptions import (
    DiseaseNotProgressing,
    UnknownAssumptionTypeError,
    assumption_to_dict,
)
from secondlook.query.finding_detail import get_finding_detail


def test_missing_finding_is_empty_with_reason():
    store = SimpleNamespace(get_finding=lambda _id: None)
    result = get_finding_detail(store, uuid.uuid4())
    assert result.items == ()
    assert "no finding" in result.empty_reason


def test_finding_detail_includes_provenance_question_case_and_decisions():
    case_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    question = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=case_id,
        text="What documented evidence exists for EGFR T790M in NSCLC?",
    )
    decided_at = datetime(2026, 8, 1, tzinfo=UTC)
    decision = SimpleNamespace(
        id=uuid.uuid4(),
        action="investigating",
        reason="need more data",
        decided_by="clinician-1",
        decided_at=decided_at,
    )
    row = SimpleNamespace(
        id=finding_id,
        claim="EGFR T790M is documented in NSCLC.",
        evidence_class="documented",
        evidence_ref={"source": "synthesis", "cited_ids": ["civic:1"]},
        evidence_level=None,
        status="active",
        assumptions=[assumption_to_dict(DiseaseNotProgressing())],
        question=question,
        decisions=[decision],
    )
    store = SimpleNamespace(get_finding=lambda _id: row)
    result = get_finding_detail(store, finding_id)
    assert result.empty_reason is None
    detail = result.items[0]
    assert detail.id == str(finding_id)
    assert detail.claim == row.claim
    assert detail.evidence_class == "documented"
    assert detail.evidence_ref == row.evidence_ref
    assert detail.question_text == question.text
    assert detail.case_id == str(case_id)
    assert detail.assumptions == ["disease not recorded as progressing"]
    assert len(detail.decisions) == 1
    assert detail.decisions[0].action == "investigating"
    assert detail.decisions[0].decided_by == "clinician-1"


def test_corrupt_assumptions_raise_rather_than_dropping():
    row = SimpleNamespace(
        id=uuid.uuid4(),
        claim="x",
        evidence_class="documented",
        evidence_ref={},
        evidence_level=None,
        status="active",
        assumptions=[{"type": "NotARealAssumption"}],
        question=SimpleNamespace(id=uuid.uuid4(), case_id=uuid.uuid4(), text="q"),
        decisions=[],
    )
    store = SimpleNamespace(get_finding=lambda _id: row)
    with pytest.raises(UnknownAssumptionTypeError):
        get_finding_detail(store, row.id)
