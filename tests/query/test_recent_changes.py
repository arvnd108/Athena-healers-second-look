from __future__ import annotations

import uuid
from datetime import UTC, datetime

from secondlook.case.diff import ChangeKind
from secondlook.query.recent_changes import get_recent_changes

from .fakes import FakeCase, FakeEvent, FakeStore

CASE_ID = uuid.uuid4()
EGFR_ID = uuid.uuid4()
KRAS_ID = uuid.uuid4()
TMB_LOW_ID = uuid.uuid4()
TMB_HIGH_ID = uuid.uuid4()


def _store_with(events):
    return FakeStore(
        case=FakeCase(id=CASE_ID, label="C", cancer_type="NSCLC"),
        events=events,
    )


def test_since_none_picks_boundary_by_recorded_at_not_occurred_at():
    """Backdated event: clinical date is earliest, insertion date is latest."""
    egfr = FakeEvent(
        id=EGFR_ID,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    kras = FakeEvent(
        id=KRAS_ID,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "KRAS", "variant": "G12C"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    result = get_recent_changes(_store_with([egfr, kras]), CASE_ID, since=None)
    payload = result.items[0]
    assert payload.boundary_event_id == str(KRAS_ID)
    kinds = [c.kind for c in payload.changes]
    assert ChangeKind.NEW_ALTERATION.value in kinds
    assert any("KRAS" in c.summary for c in payload.changes)
    assert not any("EGFR" in c.summary for c in payload.changes)


def test_explicit_since_is_clinical_occurred_at_and_ignores_recorded_at():
    egfr = FakeEvent(
        id=EGFR_ID,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    kras = FakeEvent(
        id=KRAS_ID,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "KRAS", "variant": "G12C"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    result = get_recent_changes(
        _store_with([egfr, kras]),
        CASE_ID,
        since=datetime(2026, 1, 15, tzinfo=UTC),
    )
    payload = result.items[0]
    assert any("EGFR" in c.summary for c in payload.changes)
    assert not any("KRAS" in c.summary for c in payload.changes)


def test_empty_biomarker_thresholds_do_not_detect_a_real_crossing():
    low = FakeEvent(
        id=TMB_LOW_ID,
        event_type="BIOMARKER_MEASURED",
        payload={"name": "TMB", "value": 8.0, "unit": "mut/Mb"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    high = FakeEvent(
        id=TMB_HIGH_ID,
        event_type="BIOMARKER_MEASURED",
        payload={"name": "TMB", "value": 16.0, "unit": "mut/Mb"},
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
        recorded_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    result = get_recent_changes(_store_with([low, high]), CASE_ID)
    payload = result.items[0]
    assert all(c.kind != ChangeKind.BIOMARKER_SHIFT.value for c in payload.changes)

    configured = get_recent_changes(
        _store_with([low, high]), CASE_ID, biomarker_thresholds={"TMB": 10.0}
    )
    assert any(c.kind == ChangeKind.BIOMARKER_SHIFT.value for c in configured.items[0].changes)


def test_no_events_is_empty_with_reason():
    result = get_recent_changes(_store_with([]), CASE_ID)
    assert result.items == ()
    assert result.empty_reason is not None
