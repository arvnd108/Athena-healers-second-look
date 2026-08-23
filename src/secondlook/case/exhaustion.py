"""Guideline-grounded exhaustion assessment.

Pure. Deterministic. No I/O, no LLM. Looks up structured guideline
recommendations for a cancer type and compares them against the case's
treatment history. Routing/framing context, not a hard gate
(`ARCHITECTURE.md` stage 2): an untried standard option must surface
first and loudly; insufficient data resolves to `unknown`, stated
explicitly.

`cancer_type` is an explicit parameter because it lives on the `Case` row,
not on `CaseState`. This function does not fetch it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from secondlook.case.state import CaseState
from secondlook.tier1.guideline_kb_loader import (
    GuidelineKnowledgeBase,
    GuidelineRecommendation,
)

ExhaustionStatus = Literal["exhausted", "not_exhausted", "unknown"]

NO_DATA_REASON = "No guideline data exists for this cancer type."
NO_APPLICABLE_REASON = (
    "Guideline entries exist for this cancer type but none apply to the recorded "
    "molecular profile."
)
STAGE_UNKNOWN_REASON = (
    "Cannot assess exhaustion: a guideline recommendation is stage-gated and "
    "this case has no stage data (tracked in #51)."
)
ALL_TRIED_REASON = "All applicable standard recommendations have been tried."


@dataclass(frozen=True)
class ExhaustionAssessment:
    status: ExhaustionStatus
    untried_standard_option: GuidelineRecommendation | None
    citation: str | None
    reason: str  # ALWAYS populated — no silent gaps


def assess_exhaustion(
    case: CaseState,
    cancer_type: str,
    guideline_kb: GuidelineKnowledgeBase,
) -> ExhaustionAssessment:
    """Pure. Deterministic. No I/O, no LLM.

    Looks up guideline_kb's recommendations for cancer_type. For each:
    - if applicable_stages is non-empty, this recommendation cannot be
      conclusively evaluated (stage doesn't exist on CaseState yet, #51) —
      contributes to an "unknown" result, never silently skipped and never
      guessed either way.
    - if molecular_requirement is set, only applies when case.alterations
      contains a matching (gene, variant).
    - "tried" = case.treatments contains a TreatmentEntry with matching
      regimen (case-insensitive), same convention as DrugNotYetTried.

    Returns the FIRST untried, applicable, stage-resolvable standard
    recommendation as not_exhausted (with citation). If every applicable
    recommendation was tried, exhausted. If any applicable recommendation
    could not be resolved due to missing stage data, unknown — even if
    other recommendations were clearly tried, because the safe direction
    when we cannot fully check is "we don't know," not "probably fine."
    If cancer_type has no KB entries at all, unknown, with a reason saying
    no guideline data exists for this cancer type (not the same as
    "definitely no standard option exists").
    """
    recs = guideline_kb.recommendations_for(cancer_type)
    if not recs:
        return ExhaustionAssessment(
            status="unknown",
            untried_standard_option=None,
            citation=None,
            reason=NO_DATA_REASON,
        )

    first_untried: GuidelineRecommendation | None = None
    stage_unresolved = False
    applicable_count = 0

    for rec in recs:
        if rec.molecular_requirement is not None and not _has_alteration(
            case, rec.molecular_requirement
        ):
            continue
        applicable_count += 1
        if rec.applicable_stages:
            stage_unresolved = True
            continue
        if _regimen_tried(case, rec.regimen):
            continue
        if first_untried is None:
            first_untried = rec

    if first_untried is not None:
        return ExhaustionAssessment(
            status="not_exhausted",
            untried_standard_option=first_untried,
            citation=first_untried.instrument,
            reason=(
                f"Untried standard option: {first_untried.regimen} " f"(line {first_untried.line})."
            ),
        )
    if stage_unresolved:
        return ExhaustionAssessment(
            status="unknown",
            untried_standard_option=None,
            citation=None,
            reason=STAGE_UNKNOWN_REASON,
        )
    if applicable_count == 0:
        return ExhaustionAssessment(
            status="unknown",
            untried_standard_option=None,
            citation=None,
            reason=NO_APPLICABLE_REASON,
        )
    return ExhaustionAssessment(
        status="exhausted",
        untried_standard_option=None,
        citation=None,
        reason=ALL_TRIED_REASON,
    )


def _has_alteration(case: CaseState, requirement: tuple[str, str]) -> bool:
    gene, variant = requirement
    return any(a.gene == gene and a.variant == variant for a in case.alterations)


def _regimen_tried(case: CaseState, regimen: str) -> bool:
    target = regimen.lower()
    return any(t.regimen.lower() == target for t in case.treatments)
