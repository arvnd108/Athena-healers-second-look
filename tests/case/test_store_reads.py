"""Offline tests for CaseStore list/serialize paths. No live Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from secondlook.case.assumptions import (
    AssumptionDeserializeError,
    NoAlterationIn,
    UnknownAssumptionTypeError,
    assumption_from_dict,
    assumption_to_dict,
)
from secondlook.case.models import Finding, Question
from secondlook.case.store import CaseStore


def _store_with_scalars(rows):
    session = MagicMock()
    session.scalars.return_value = rows
    return CaseStore(session), session


def test_list_questions_statement_orders_by_created_at_ascending():
    store, session = _store_with_scalars([])
    case_id = uuid.uuid4()
    store.list_questions(case_id)
    stmt = session.scalars.call_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "from questions" in compiled
    assert "created_at" in compiled
    assert "order by" in compiled


def test_list_questions_returns_rows_in_session_order():
    earlier = SimpleNamespace(
        id=uuid.uuid4(), text="first", created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    later = SimpleNamespace(
        id=uuid.uuid4(), text="second", created_at=datetime(2026, 2, 1, tzinfo=UTC)
    )
    store, _session = _store_with_scalars([earlier, later])
    assert store.list_questions(uuid.uuid4()) == [earlier, later]


def test_list_active_findings_statement_filters_active_and_orders_by_created_at():
    session = MagicMock()
    session.execute.return_value.all.return_value = []
    store = CaseStore(session)
    store.list_active_findings(uuid.uuid4())
    stmt = session.execute.call_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "active" in compiled
    assert "created_at" in compiled
    assert "order by" in compiled


def test_list_active_findings_deserializes_assumptions_and_skips_nothing():
    case_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        assumptions=[assumption_to_dict(NoAlterationIn(gene="EGFR"))],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = MagicMock()
    session.execute.return_value.all.return_value = [(row, case_id)]
    findings = CaseStore(session).list_active_findings(case_id)
    assert len(findings) == 1
    assert findings[0].case_id == str(case_id)
    assert findings[0].assumptions == (NoAlterationIn(gene="EGFR"),)


def test_list_active_findings_raises_on_corrupt_assumptions_rather_than_dropping_them():
    case_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        assumptions=[{"type": "NotARealAssumption", "gene": "EGFR"}],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = MagicMock()
    session.execute.return_value.all.return_value = [(row, case_id)]
    with pytest.raises(UnknownAssumptionTypeError):
        CaseStore(session).list_active_findings(case_id)


def test_list_active_findings_raises_on_malformed_payload_rather_than_omitting_it():
    case_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        assumptions=[{"type": "NoAlterationIn"}],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = MagicMock()
    session.execute.return_value.all.return_value = [(row, case_id)]
    with pytest.raises(AssumptionDeserializeError):
        CaseStore(session).list_active_findings(case_id)


def test_create_finding_serializes_assumption_objects_through_to_dict():
    session = MagicMock()
    store = CaseStore(session)
    store.create_finding(
        uuid.uuid4(),
        claim="no EGFR alteration",
        evidence_class="documented",
        evidence_ref={"source": "CIViC", "url": "https://civicdb.org/1"},
        assumptions=[NoAlterationIn(gene="EGFR")],
    )
    finding = session.add.call_args.args[0]
    assert isinstance(finding, Finding)
    assert finding.assumptions == [assumption_to_dict(NoAlterationIn(gene="EGFR"))]
    assert assumption_from_dict(finding.assumptions[0]) == NoAlterationIn(gene="EGFR")


def test_create_finding_round_trips_dict_payloads_through_the_same_shape():
    session = MagicMock()
    payload = assumption_to_dict(NoAlterationIn(gene="ALK"))
    CaseStore(session).create_finding(
        uuid.uuid4(),
        claim="no ALK alteration",
        evidence_class="documented",
        evidence_ref={"source": "CIViC", "url": "https://civicdb.org/2"},
        assumptions=[payload],
    )
    finding = session.add.call_args.args[0]
    assert finding.assumptions == [payload]


def test_get_finding_returns_none_when_session_has_no_row():
    session = MagicMock()
    session.get.return_value = None
    finding_id = uuid.uuid4()
    assert CaseStore(session).get_finding(finding_id) is None
    session.get.assert_called_once()
    args, _kwargs = session.get.call_args
    assert args[0] is Finding
    assert args[1] == finding_id


def test_get_finding_returns_the_session_row():
    session = MagicMock()
    row = SimpleNamespace(id=uuid.uuid4(), claim="documented evidence")
    session.get.return_value = row
    assert CaseStore(session).get_finding(row.id) is row


def test_orm_question_and_finding_are_the_row_types_listed():
    # Guard against accidentally returning domain Finding from list_questions.
    assert Question.__tablename__ == "questions"
    assert Finding.__tablename__ == "findings"
