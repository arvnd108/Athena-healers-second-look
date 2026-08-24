"""Case summary for MCP and the future REST GET /api/cases/{id}."""

from __future__ import annotations

from collections import Counter

from secondlook.case.models import QUESTION_STATUSES
from secondlook.query.contracts import AlterationView, CaseSummary, QuestionView
from secondlook.query.fold import fold_store_events
from secondlook.query.result import QueryResult


def get_case_summary(store, case_id) -> QueryResult[CaseSummary]:
    case = store.get_case(case_id)
    if case is None:
        return QueryResult(empty_reason=f"no case with id {case_id}")

    events = store.list_events(case_id)
    # Fold once here. Do not also call derive_state -- that would fold again.
    state = fold_store_events(case_id, events)
    questions = store.list_questions(case_id)
    findings = store.list_active_findings(case_id)

    counts = Counter(q.status for q in questions)
    question_counts = {status: int(counts.get(status, 0)) for status in sorted(QUESTION_STATUSES)}

    summary = CaseSummary(
        case_id=str(case.id),
        label=case.label,
        cancer_type=case.cancer_type,
        age_years=case.age_years,
        primary_site=case.primary_site,
        histology=case.histology,
        stage=case.stage,
        doid=case.doid,
        latest_assessment=state.latest_assessment,
        alterations=[AlterationView(gene=a.gene, variant=a.variant) for a in state.alterations],
        questions=[
            QuestionView(id=str(q.id), text=q.text, status=q.status, priority=q.priority)
            for q in questions
        ],
        question_counts=question_counts,
        active_finding_count=len(findings),
        empty_reason=None if events else "no events recorded for this case",
    )
    return QueryResult(items=(summary,))
