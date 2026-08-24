from __future__ import annotations

import uuid
from datetime import UTC, datetime

from secondlook.query.queue import get_queue

from .fakes import FakeCase, FakeQuestion, FakeStore


def test_queue_counts_questions_without_needing_events():
    case_id = uuid.uuid4()
    store = FakeStore(
        case=FakeCase(id=case_id, label="C", cancer_type="NSCLC"),
        questions=[
            FakeQuestion(
                id=uuid.uuid4(),
                text="a",
                status="open",
                priority=1,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            FakeQuestion(
                id=uuid.uuid4(),
                text="b",
                status="answered",
                priority=2,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ],
    )
    result = get_queue(store, case_id)
    view = result.items[0]
    assert view.question_counts["open"] == 1
    assert view.question_counts["answered"] == 1
    assert len(view.questions) == 2
    assert result.empty_reason is None


def test_empty_queue_still_returns_a_view_with_reason():
    case_id = uuid.uuid4()
    store = FakeStore(case=FakeCase(id=case_id, label="C", cancer_type="NSCLC"))
    result = get_queue(store, case_id)
    assert result.items[0].empty_reason is not None
    assert result.items[0].questions == []
