"""Emerging Evidence Radar: persist deduplicated alerts from an AffectedCasesReport.

`Alert` is declared here, not in `models.py`, on purpose. `PR_MERGE_SEQUENCE.md`
marks `src/secondlook/case/models.py` as Subsystem C only — other subsystems
import `Base` from it and add their own files (`diff.py`, `questions.py`,
`alerts.py`). Do not move this model into `models.py`.

An alert is NOT a new synthesized finding. It points at an existing
superseded finding and the evidence item that broke it, and stops there.
This module must never import `secondlook.synthesis`, call an LLM, or
create `Question` / `Finding` rows — that is Subsystem I's job, invoked
explicitly. `AlertStore` has no such methods; tests/case/test_alerts.py
asserts the absence the same way test_store.py asserts there is no
`update_event`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, Index, Text, select
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, Session, mapped_column

from secondlook.case.assumptions import Finding
from secondlook.case.evidence_diff import (
    UNRESOLVED_REASON,
    AffectedCasesReport,
    EvidenceChangeEvent,
)
from secondlook.case.models import Base, _uuid_pk

EMPTY_REPORT_REASON = "No superseded findings in this report; no alerts to write."


@dataclass(frozen=True)
class NewAlert:
    """One alert to be written. Pure value object — no DB identity yet."""

    case_id: str
    finding_id: str
    dedup_key: str
    evidence_change_summary: str
    evidence_node_type: str
    evidence_node_id: str
    evidence_source: str


@dataclass(frozen=True)
class BuiltAlerts:
    """Deterministic result of `build_alerts`.

    `alerts` is the sorted tuple of drafts. Missing `findings_by_id` entries
    are not silently dropped — they land in `unresolved_finding_ids` with
    `unresolved_reason`, matching `compute_affected_findings` on purpose
    (no silent gaps, ARCHITECTURE.md §8.1). An empty report sets
    `empty_reason` rather than returning a bare empty collection.
    """

    alerts: tuple[NewAlert, ...]
    unresolved_finding_ids: tuple[str, ...] = ()
    unresolved_reason: str | None = None
    empty_reason: str | None = None


class Alert(Base):
    """One persisted radar alert. Points at a finding + evidence item; never a synthesis."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_case_id_acknowledged", "case_id", "acknowledged"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), nullable=False)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id"), nullable=False)
    evidence_change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    evidence_node_type: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_source: Mapped[str] = mapped_column(Text, nullable=False)


def build_alerts(
    report: AffectedCasesReport,
    findings_by_id: dict[str, Finding],
) -> BuiltAlerts:
    """Pure. Deterministic. No I/O, no DB, no LLM.

    One `NewAlert` per (case, finding) pair in the report. Returns them in a
    stable sorted order (by case_id, then finding_id) so the same report
    always produces byte-identical output — the same determinism property
    D's own engine guarantees.

    `findings_by_id` is required because `AffectedCasesReport` exposes
    `finding_ids` and `affected_case_ids` as two separate flat tuples, which
    is not enough to know which finding belongs to which case. This is the
    same shape and the same reason `compute_affected_findings` already takes
    it. Do not change `AffectedCasesReport` to fold the pairing in — D is a
    separate subsystem and editing it here would widen the conflict surface.

    Dedup key is SHA-256 hex of:

        case_id | finding_id | node_type | node_id | canonical_json(new_props)

    Deliberately excluded:
    - `retrieved_at` — changes on every scheduler run; including it would
      make every run produce "new" alerts and defeat the entire dedup
      requirement.
    - `old_props` — the same end-state reached by two different paths is
      still the same alert-worthy fact.

    `new_props` is included deliberately: if the same node changes *again*
    with genuinely different content, that IS a new alert-worthy event and
    should re-alert.

    Finding ids present in the report but missing from `findings_by_id` are
    recorded in `unresolved_finding_ids` (same reason string as
    `compute_affected_findings`) rather than silently dropped.
    """
    drafts: list[NewAlert] = []
    unresolved: list[str] = []
    change = report.evidence_change
    summary = _summary(change)

    for finding_id in report.finding_ids:
        finding = findings_by_id.get(finding_id)
        if finding is None:
            unresolved.append(finding_id)
            continue
        drafts.append(
            NewAlert(
                case_id=finding.case_id,
                finding_id=finding.id,
                dedup_key=_dedup_key(finding.case_id, finding.id, change),
                evidence_change_summary=summary,
                evidence_node_type=change.node_type,
                evidence_node_id=change.node_id,
                evidence_source=change.source,
            )
        )

    alerts = tuple(sorted(drafts, key=lambda a: (a.case_id, a.finding_id)))
    unresolved_ids = tuple(unresolved)  # report.finding_ids is already sorted
    return BuiltAlerts(
        alerts=alerts,
        unresolved_finding_ids=unresolved_ids,
        unresolved_reason=UNRESOLVED_REASON if unresolved_ids else None,
        empty_reason=None if alerts else EMPTY_REPORT_REASON,
    )


def _summary(change: EvidenceChangeEvent) -> str:
    return f"{change.node_type} {change.node_id} updated in {change.source}"


def _canonical_json(props: dict[str, object]) -> str:
    return json.dumps(props, sort_keys=True, separators=(",", ":"), default=str)


def _dedup_key(case_id: str, finding_id: str, change: EvidenceChangeEvent) -> str:
    material = (
        f"{case_id}|{finding_id}|{change.node_type}|{change.node_id}|"
        f"{_canonical_json(change.new_props)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AlertStore:
    """Repository over a SQLAlchemy Session. Injected, per the repo's DI
    convention (see store.py's module docstring).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_alerts(self, new_alerts: tuple[NewAlert, ...]) -> tuple[Alert, ...]:
        """Insert alerts whose dedup_key isn't already present. Returns only
        the rows actually created. Idempotent: calling twice with the same
        input creates nothing the second time.
        """
        if not new_alerts:
            return ()
        keys = [alert.dedup_key for alert in new_alerts]
        existing = set(
            self._session.scalars(select(Alert.dedup_key).where(Alert.dedup_key.in_(keys)))
        )
        created: list[Alert] = []
        now = datetime.now(UTC)
        for draft in new_alerts:
            if draft.dedup_key in existing:
                continue
            row = Alert(
                id=uuid.uuid4(),
                case_id=uuid.UUID(draft.case_id),
                finding_id=uuid.UUID(draft.finding_id),
                evidence_change_summary=draft.evidence_change_summary,
                created_at=now,
                acknowledged=False,
                acknowledged_at=None,
                dedup_key=draft.dedup_key,
                evidence_node_type=draft.evidence_node_type,
                evidence_node_id=draft.evidence_node_id,
                evidence_source=draft.evidence_source,
            )
            self._session.add(row)
            created.append(row)
            existing.add(draft.dedup_key)
        if created:
            self._session.flush()
        return tuple(created)

    def unacknowledged_for_case(self, case_id: str) -> tuple[Alert, ...]:
        stmt = (
            select(Alert)
            .where(Alert.case_id == uuid.UUID(case_id), Alert.acknowledged.is_(False))
            .order_by(Alert.created_at, Alert.id)
        )
        return tuple(self._session.scalars(stmt))

    def count_unacknowledged(self, case_id: str) -> int:
        """Backs the dashboard badge (issue #8 deliverable 2)."""
        return len(self.unacknowledged_for_case(case_id))

    def acknowledge(self, alert_id: str) -> Alert | None:
        """Mark acknowledged, stamping acknowledged_at. Returns None if the
        id doesn't exist — the caller decides whether that's an error.
        """
        row = self._session.get(Alert, uuid.UUID(alert_id))
        if row is None:
            return None
        row.acknowledged = True
        row.acknowledged_at = datetime.now(UTC)
        self._session.flush()
        return row
