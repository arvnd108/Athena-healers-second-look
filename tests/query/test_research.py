from __future__ import annotations

import uuid
from datetime import UTC, datetime

from secondlook.case.assumptions import DiseaseNotProgressing, assumption_from_dict
from secondlook.query.research import RESEARCH_ASSUMPTIONS, run_research

from .fakes import FakeCase, FakeEvent, FakeStore

CASE_ID = uuid.uuid4()


def _alteration_store():
    return FakeStore(
        case=FakeCase(id=CASE_ID, label="C", cancer_type="NSCLC"),
        events=[
            FakeEvent(
                id=uuid.uuid4(),
                event_type="ALTERATION_OBSERVED",
                payload={"gene": "EGFR", "variant": "T790M"},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )


def test_research_creates_one_finding_per_rendered_question_with_disease_not_progressing():
    store = _alteration_store()
    outcome = run_research(store, CASE_ID, dispatch_fn=lambda *a, **k: [])
    # NEW_ALTERATION has three templates.
    assert outcome.questions_created == 3
    assert outcome.findings_created == 3
    assert len(store.questions) == 3
    assert len(store.finding_rows) == 3
    for row in store.finding_rows:
        restored = [assumption_from_dict(p) for p in row.assumptions]
        assert restored == [DiseaseNotProgressing()]
        assert restored != []
    assert RESEARCH_ASSUMPTIONS == (DiseaseNotProgressing(),)


def test_research_is_not_idempotent_and_duplicates_on_a_second_call():
    # Documents the known gap: case/memory.py suppress_duplicates is unbuilt
    # (flagged when issue #10 was scoped). Do not "fix" this by deduping here.
    store = _alteration_store()
    first = run_research(store, CASE_ID, dispatch_fn=lambda *a, **k: [])
    second = run_research(store, CASE_ID, dispatch_fn=lambda *a, **k: [])
    assert first.findings_created == 3
    assert second.findings_created == 3
    assert len(store.questions) == 6
    assert len(store.finding_rows) == 6
