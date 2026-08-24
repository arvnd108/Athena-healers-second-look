"""Single-finding provenance for GET /api/findings/{id}."""

from __future__ import annotations

from secondlook.case.assumptions import assumption_from_dict
from secondlook.query.contracts import DecisionView, FindingDetail
from secondlook.query.result import QueryResult


def get_finding_detail(store, finding_id) -> QueryResult[FindingDetail]:
    row = store.get_finding(finding_id)
    if row is None:
        return QueryResult(empty_reason=f"no finding with id {finding_id}")

    payloads = row.assumptions or []
    if not isinstance(payloads, list):
        raise TypeError(
            f"finding {row.id} assumptions must be a list, got {type(payloads).__name__}"
        )
    descriptions = [assumption_from_dict(p).describe() for p in payloads]
    question = row.question
    decisions = [
        DecisionView(
            id=str(d.id),
            action=d.action,
            reason=d.reason,
            decided_by=d.decided_by,
            decided_at=d.decided_at,
        )
        for d in (row.decisions or [])
    ]
    detail = FindingDetail(
        id=str(row.id),
        claim=row.claim,
        evidence_class=row.evidence_class,
        evidence_ref=dict(row.evidence_ref or {}),
        evidence_level=row.evidence_level,
        status=row.status,
        assumptions=descriptions,
        question_text=question.text,
        case_id=str(question.case_id),
        decisions=decisions,
    )
    return QueryResult(items=(detail,))
