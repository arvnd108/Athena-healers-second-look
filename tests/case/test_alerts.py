"""Offline and integration tests for the Emerging Evidence Radar.

Offline tests (no marker) cover the pure builder. Integration tests need
Postgres; CI does not currently run them (no Postgres service container
yet). Same pattern as tests/case/test_store.py:

    docker compose up -d postgres
    pytest tests/case/test_alerts.py -m integration
"""

from __future__ import annotations

import ast
import inspect
import os
import uuid
from pathlib import Path

import pytest

from secondlook.case.alerts import (
    EMPTY_REPORT_REASON,
    Alert,
    AlertStore,
    BuiltAlerts,
    build_alerts,
)
from secondlook.case.assumptions import Finding
from secondlook.case.evidence_diff import (
    UNRESOLVED_REASON,
    AffectedCasesReport,
    EvidenceChangeEvent,
)
from secondlook.case.models import NO_PHI_COLUMN_PATTERN


def _event(**overrides: object) -> EvidenceChangeEvent:
    fields: dict[str, object] = {
        "node_type": "EvidenceItem",
        "node_id": "EID123",
        "old_props": {"level": "B"},
        "new_props": {"level": "A"},
        "source": "CIViC",
        "retrieved_at": "2026-08-23T12:00:00Z",
        "gene": "EGFR",
        "variant_or_drug": "T790M",
    }
    fields.update(overrides)
    return EvidenceChangeEvent(**fields)  # type: ignore[arg-type]


def _finding(finding_id: str, case_id: str) -> Finding:
    return Finding(id=finding_id, case_id=case_id)


def _report(
    finding_ids: tuple[str, ...] = (),
    case_ids: tuple[str, ...] = (),
    event: EvidenceChangeEvent | None = None,
    unresolved: tuple[str, ...] = (),
) -> AffectedCasesReport:
    return AffectedCasesReport(
        finding_ids=finding_ids,
        evidence_change=event if event is not None else _event(),
        affected_case_ids=case_ids,
        empty_reason=None if finding_ids else "none superseded",
        unresolved_finding_ids=unresolved,
        unresolved_reason=UNRESOLVED_REASON if unresolved else None,
    )


def test_build_alerts_returns_one_alert_per_case_finding_pair():
    findings = {
        "f-b": _finding("f-b", "case-2"),
        "f-a": _finding("f-a", "case-1"),
    }
    result = build_alerts(
        _report(finding_ids=("f-b", "f-a"), case_ids=("case-2", "case-1")), findings
    )
    assert len(result.alerts) == 2
    assert {(a.case_id, a.finding_id) for a in result.alerts} == {
        ("case-1", "f-a"),
        ("case-2", "f-b"),
    }
    assert result.empty_reason is None


def test_build_alerts_is_sorted_by_case_id_then_finding_id():
    findings = {
        "f-z": _finding("f-z", "case-b"),
        "f-a": _finding("f-a", "case-b"),
        "f-m": _finding("f-m", "case-a"),
    }
    result = build_alerts(
        _report(finding_ids=("f-z", "f-a", "f-m"), case_ids=("case-b", "case-a")),
        findings,
    )
    assert [(a.case_id, a.finding_id) for a in result.alerts] == [
        ("case-a", "f-m"),
        ("case-b", "f-a"),
        ("case-b", "f-z"),
    ]


def test_build_alerts_is_byte_identical_across_100_runs():
    findings = {
        "f-2": _finding("f-2", "case-z"),
        "f-1": _finding("f-1", "case-a"),
    }
    report = _report(finding_ids=("f-2", "f-1"), case_ids=("case-z", "case-a"))
    first = build_alerts(report, findings)
    for _ in range(100):
        again = build_alerts(report, findings)
        assert again == first
        assert repr(again) == repr(first)
        assert again.alerts == first.alerts


def test_identical_evidence_change_yields_identical_dedup_key():
    findings = {"f1": _finding("f1", "c1")}
    a = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), findings)
    b = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), findings)
    assert a.alerts[0].dedup_key == b.alerts[0].dedup_key


def test_retrieved_at_alone_does_not_change_dedup_key():
    """Regression for the whole dedup requirement: retrieved_at changes on
    every scheduler run, so it must not be in the key.
    """
    findings = {"f1": _finding("f1", "c1")}
    first = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), findings)
    later = build_alerts(
        _report(
            finding_ids=("f1",),
            case_ids=("c1",),
            event=_event(retrieved_at="2026-08-30T12:00:00Z"),
        ),
        findings,
    )
    assert first.alerts[0].dedup_key == later.alerts[0].dedup_key, (
        "retrieved_at must not be in the dedup key; including it would make "
        "every scheduler run produce new alerts and defeat the entire dedup "
        "requirement"
    )


def test_new_props_content_change_yields_different_dedup_key():
    findings = {"f1": _finding("f1", "c1")}
    first = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), findings)
    changed = build_alerts(
        _report(
            finding_ids=("f1",),
            case_ids=("c1",),
            event=_event(new_props={"level": "A", "citation": "PMID:1"}),
        ),
        findings,
    )
    assert first.alerts[0].dedup_key != changed.alerts[0].dedup_key


def test_different_case_same_evidence_yields_different_dedup_key():
    a = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), {"f1": _finding("f1", "c1")})
    b = build_alerts(_report(finding_ids=("f1",), case_ids=("c2",)), {"f1": _finding("f1", "c2")})
    assert a.alerts[0].dedup_key != b.alerts[0].dedup_key


def test_empty_report_returns_empty_tuple_with_reason():
    result = build_alerts(_report(), {})
    assert result.alerts == ()
    assert result.empty_reason == EMPTY_REPORT_REASON
    assert isinstance(result, BuiltAlerts)


def test_missing_finding_is_unresolved_not_silently_dropped():
    """Consistent with compute_affected_findings' unresolved_finding_ids."""
    findings = {"f-known": _finding("f-known", "c1")}
    result = build_alerts(
        _report(finding_ids=("f-ghost", "f-known"), case_ids=("c1",)),
        findings,
    )
    assert [a.finding_id for a in result.alerts] == ["f-known"]
    assert result.unresolved_finding_ids == ("f-ghost",)
    assert result.unresolved_reason == UNRESOLVED_REASON


def test_summary_is_factual_and_cites_the_node():
    findings = {"f1": _finding("f1", "c1")}
    result = build_alerts(_report(finding_ids=("f1",), case_ids=("c1",)), findings)
    assert result.alerts[0].evidence_change_summary == "EvidenceItem EID123 updated in CIViC"


def test_alerts_module_never_triggers_synthesis():
    """An alert is not a synthesized finding. This module must not import
    the synthesis package or grow a method that creates Question/Finding rows.
    """
    import secondlook.case.alerts as alerts_mod

    source = Path(inspect.getsourcefile(alerts_mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    synthesis_imports = [
        m for m in imported if m == "secondlook.synthesis" or m.startswith("secondlook.synthesis.")
    ]
    assert not synthesis_imports, f"alerts.py must not import synthesis: {synthesis_imports}"

    forbidden = {
        "create_question",
        "create_finding",
        "create_synthesis",
        "generate",
        "generate_question",
        "generate_finding",
    }
    present = forbidden & set(dir(AlertStore))
    assert not present, f"AlertStore must never create questions, findings, or synthesis: {present}"


def test_alert_table_has_no_phi_shaped_column():
    """test_models.py only walks tables already imported onto Base.metadata,
    so Alert is checked here rather than by editing C's test file.
    """
    offending = [
        f"alerts.{column.name}"
        for column in Alert.__table__.columns
        if NO_PHI_COLUMN_PATTERN.search(column.name)
    ]
    assert not offending, f"PHI-shaped column name(s) on Alert: {offending}"


# ---------------------------------------------------------------------------
# Integration — Postgres. Deselected by default.
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "ATHENA_DATABASE_URL", "postgresql+psycopg://athena:athena@localhost:5432/athena"
)


@pytest.fixture
def session():
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("psycopg")
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from secondlook.case.models import Base

    # Importing Alert registers it on Base.metadata for create_all.
    assert Alert.__tablename__ == "alerts"

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip(
            "Postgres not reachable at ATHENA_DATABASE_URL; run `docker compose up -d postgres`"
        )

    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
        db_session.rollback()
    Base.metadata.drop_all(engine)


def _seed_finding(session) -> tuple[str, str]:
    from secondlook.case.store import CaseStore

    store = CaseStore(session)
    case = store.create_case(label="Case A", cancer_type="NSCLC")
    question = store.create_question(case.id, text="Does T790M change therapy?", priority=1)
    finding = store.create_finding(
        question.id,
        claim="EGFR T790M observed",
        evidence_class="documented",
        evidence_ref={"source": "CIViC"},
        assumptions=[],
    )
    return str(case.id), str(finding.id)


@pytest.mark.integration
def test_record_alerts_inserts_rows(session):
    case_id, finding_id = _seed_finding(session)
    findings = {finding_id: _finding(finding_id, case_id)}
    drafts = build_alerts(_report(finding_ids=(finding_id,), case_ids=(case_id,)), findings)
    created = AlertStore(session).record_alerts(drafts.alerts)
    assert len(created) == 1
    assert created[0].dedup_key == drafts.alerts[0].dedup_key
    assert created[0].acknowledged is False


@pytest.mark.integration
def test_record_alerts_is_idempotent(session):
    case_id, finding_id = _seed_finding(session)
    findings = {finding_id: _finding(finding_id, case_id)}
    drafts = build_alerts(_report(finding_ids=(finding_id,), case_ids=(case_id,)), findings)
    store = AlertStore(session)
    first = store.record_alerts(drafts.alerts)
    second = store.record_alerts(drafts.alerts)
    assert len(first) == 1
    assert second == ()


@pytest.mark.integration
def test_unacknowledged_count_and_acknowledge_round_trip(session):
    case_id, finding_id = _seed_finding(session)
    findings = {finding_id: _finding(finding_id, case_id)}
    drafts = build_alerts(_report(finding_ids=(finding_id,), case_ids=(case_id,)), findings)
    store = AlertStore(session)
    created = store.record_alerts(drafts.alerts)
    assert store.count_unacknowledged(case_id) == 1
    assert len(store.unacknowledged_for_case(case_id)) == 1

    ack = store.acknowledge(str(created[0].id))
    assert ack is not None
    assert ack.acknowledged is True
    assert ack.acknowledged_at is not None
    assert store.count_unacknowledged(case_id) == 0
    assert store.unacknowledged_for_case(case_id) == ()


@pytest.mark.integration
def test_acknowledge_unknown_id_returns_none(session):
    store = AlertStore(session)
    assert store.acknowledge(str(uuid.uuid4())) is None
