"""Compose ChangeSet → questions → signals → synthesis → findings.

Called by POST /api/cases/{id}/research. Not idempotent: there is no
`case/memory.py` `suppress_duplicates` (flagged when issue #10 was scoped).
Calling this twice against the same ChangeSet creates duplicate rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from secondlook.case.assumptions import DiseaseNotProgressing
from secondlook.case.diff import ChangeSet
from secondlook.case.questions import render_questions
from secondlook.query.recent_changes import compute_recent_diff
from secondlook.signals.registry import dispatch
from secondlook.synthesis.generate import SynthesisResult, generate_synthesis

# The one forward-looking, generically-applicable assumption among the four.
# NoAlterationIn is valid only in an alteration's *absence* — the opposite of a
# finding produced *because* an alteration was observed. BiomarkerBelow and
# DrugNotYetTried are narrower than what a synthesis-derived finding is about.
# A real per-ChangeKind mapping is future clinical-product work.
RESEARCH_ASSUMPTIONS = (DiseaseNotProgressing(),)


@dataclass(frozen=True)
class CreatedFindingView:
    id: str
    claim: str
    question_id: str


@dataclass(frozen=True)
class ResearchOutcome:
    questions_created: int
    findings_created: int
    findings: tuple[CreatedFindingView, ...]


def evidence_from_synthesis(result: SynthesisResult) -> tuple[str, dict]:
    """Map a SynthesisResult onto Finding.evidence_class / evidence_ref.

    SynthesisResult has no single evidence_class. Cited output is `documented`
    (something published was named). A no-citation template result is
    `computed`. evidence_ref always records `source=synthesis` plus cited_ids.
    """
    cited = list(result.cited_ids)
    evidence_class = "documented" if cited else "computed"
    return evidence_class, {"source": "synthesis", "cited_ids": cited}


def run_research(
    store,
    case_id,
    *,
    graph=None,
    dgidb_client=None,
    llm_client=None,
    now: datetime | None = None,
    dispatch_fn=dispatch,
) -> ResearchOutcome:
    case = store.get_case(case_id)
    if case is None:
        raise ValueError(f"no case with id {case_id}")

    change_set, _boundary = compute_recent_diff(store, case_id, since=None)
    if change_set is None:
        change_set = ChangeSet(
            changes=(),
            supersessions=(),
            unchanged_reason="no events recorded for this case",
        )

    state = store.derive_state(case_id)
    rendered = render_questions(change_set, state, cancer_type=case.cancer_type)
    created: list[CreatedFindingView] = []
    for question in rendered:
        row = store.create_question(
            case_id,
            text=question.text,
            priority=question.priority,
            triggered_by={
                "kind": question.kind.value,
                "summary": question.triggering_change.summary,
                "triggering_event_id": question.triggering_change.triggering_event_id,
                "detail": question.detail,
            },
        )
        signals = dispatch_fn(
            change_set,
            question,
            graph=graph,
            dgidb_client=dgidb_client,
            case=state,
            now=now,
        )
        synthesis = generate_synthesis(question, signals, llm_client=llm_client, now=now)
        evidence_class, evidence_ref = evidence_from_synthesis(synthesis)
        finding = store.create_finding(
            row.id,
            claim=synthesis.text,
            evidence_class=evidence_class,
            evidence_ref=evidence_ref,
            # See RESEARCH_ASSUMPTIONS: DiseaseNotProgressing is the only
            # forward-looking generic default among the four Assumption types.
            # Do not attach assumptions=[] — an assumption-free finding can
            # never be superseded.
            assumptions=list(RESEARCH_ASSUMPTIONS),
            evidence_level=None,
        )
        created.append(
            CreatedFindingView(id=str(finding.id), claim=finding.claim, question_id=str(row.id))
        )
    return ResearchOutcome(
        questions_created=len(rendered),
        findings_created=len(created),
        findings=tuple(created),
    )
