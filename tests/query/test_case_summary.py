from __future__ import annotations

import uuid
from datetime import UTC, datetime

from secondlook.query.case_summary import get_case_summary

from .fakes import FakeCase, FakeEvent, FakeQuestion, FakeStore


def test_missing_case_is_empty_with_reason():
    result = get_case_summary(FakeStore(), uuid.uuid4())
    assert result.items == ()
    assert result.empty_reason is not None
    assert "no case" in result.empty_reason


def test_summary_folds_events_once_and_counts_questions():
    case_id = uuid.uuid4()
    store = FakeStore(
        case=FakeCase(id=case_id, label="C1", cancer_type="NSCLC", age_years=54),
        events=[
            FakeEvent(
                id=uuid.uuid4(),
                event_type="ALTERATION_OBSERVED",
                payload={"gene": "EGFR", "variant": "T790M"},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
        questions=[
            FakeQuestion(
                id=uuid.uuid4(),
                text="evidence?",
                status="open",
                priority=1,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
            FakeQuestion(
                id=uuid.uuid4(),
                text="trials?",
                status="answered",
                priority=2,
                created_at=datetime(2026, 1, 3, tzinfo=UTC),
            ),
        ],
        findings=(),
    )
    result = get_case_summary(store, case_id)
    assert result.empty_reason is None
    summary = result.items[0]
    assert summary.label == "C1"
    assert summary.cancer_type == "NSCLC"
    assert summary.alterations[0].gene == "EGFR"
    assert summary.question_counts["open"] == 1
    assert summary.question_counts["answered"] == 1
    assert summary.active_finding_count == 0
