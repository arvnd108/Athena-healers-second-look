"""Patient-side dependency-tracked diff: `CaseState × CaseState → ChangeSet`.

Pure. Deterministic. No I/O, no database access, no LLM, no network.
Identical inputs produce byte-identical output, always.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from secondlook.case.assumptions import Finding
from secondlook.case.evidence_state import EvidenceSnapshot
from secondlook.case.state import CaseState, DiseaseAssessment

UNCHANGED_REASON = "No tracked field changed between these states."


class ChangeKind(StrEnum):
    NEW_ALTERATION = "new_alteration"
    BIOMARKER_SHIFT = "biomarker_shift"
    TREATMENT_LINE_CHANGE = "treatment_line_change"
    DISEASE_PROGRESSION = "disease_progression"


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    summary: str
    detail: dict
    triggering_event_id: str


@dataclass(frozen=True)
class Supersession:
    finding_id: str
    broken_assumption: str  # from Assumption.describe()
    triggering_event_id: str
    note: str


@dataclass(frozen=True)
class ChangeSet:
    changes: tuple[Change, ...]
    supersessions: tuple[Supersession, ...]
    unchanged_reason: str | None  # populated when empty — never a silent gap

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.supersessions


def compute_diff(
    previous: CaseState,
    current: CaseState,
    active_findings: tuple[Finding, ...],
    *,
    biomarker_thresholds: dict[str, float],
    evidence: EvidenceSnapshot | None = None,
) -> ChangeSet:
    """Pure. Deterministic. Same inputs -> byte-identical output, always.

    `evidence` defaults to an empty snapshot so patient-side callers that
    don't yet have Subsystem A's snapshot can still evaluate the four
    built-in assumptions (none of which inspect it). Keyword-only so the
    required positional signature matches IMPLEMENTATION_PLAN.md §3.3.
    """
    snapshot = evidence if evidence is not None else EvidenceSnapshot()
    changes: list[Change] = []

    # 1. New alterations — identity is (gene, variant); assay is informational.
    prev_alts = {(a.gene, a.variant) for a in previous.alterations}
    for alt in current.alterations:
        if (alt.gene, alt.variant) not in prev_alts:
            changes.append(
                Change(
                    kind=ChangeKind.NEW_ALTERATION,
                    summary=f"{alt.gene} {alt.variant} newly observed",
                    detail={"gene": alt.gene, "variant": alt.variant, "assay": alt.assay},
                    triggering_event_id=alt.event_id,
                )
            )

    # 2. Biomarker crossing a configured threshold (not any change — a crossing).
    # Sort names so dict insertion order cannot leak into output order.
    for name in sorted(current.biomarkers):
        cur = current.biomarkers[name]
        prev = previous.biomarkers.get(name)
        thresh = biomarker_thresholds.get(name)
        if prev is None or thresh is None:
            continue
        if (prev.value < thresh) != (cur.value < thresh):
            changes.append(
                Change(
                    kind=ChangeKind.BIOMARKER_SHIFT,
                    summary=f"{name} crossed {thresh} ({prev.value} -> {cur.value})",
                    detail={
                        "name": name,
                        "from": prev.value,
                        "to": cur.value,
                        "threshold": thresh,
                    },
                    triggering_event_id=cur.event_id,
                )
            )

    # 3. Treatment line changes — new (regimen, action, line) tuples in current.
    prev_treatments = {(t.regimen.lower(), t.action, t.line) for t in previous.treatments}
    for treatment in current.treatments:
        identity = (treatment.regimen.lower(), treatment.action, treatment.line)
        if identity not in prev_treatments:
            changes.append(
                Change(
                    kind=ChangeKind.TREATMENT_LINE_CHANGE,
                    summary=f"{treatment.regimen} {treatment.action}",
                    detail={
                        "regimen": treatment.regimen,
                        "action": treatment.action,
                        "line": treatment.line,
                    },
                    triggering_event_id=treatment.event_id,
                )
            )

    # 4. Disease assessment changes — a new or different recorded status.
    prev_status = _assessment_status(previous)
    assessment = current.latest_assessment
    if assessment is not None and assessment.status != prev_status:
        changes.append(
            Change(
                kind=ChangeKind.DISEASE_PROGRESSION,
                summary=_disease_summary(assessment),
                detail={
                    "status": assessment.status,
                    "sites": list(assessment.sites),
                    "from": prev_status,
                },
                triggering_event_id=assessment.event_id,
            )
        )

    # 5. Supersession — re-evaluate every active finding's assumptions.
    # Iterate `finding.assumptions` in the order they appear (already
    # deterministic from Subsystem C). Do not re-sort: that would hide an
    # upstream determinism bug. Break on the first failure: exactly one
    # Supersession per finding even if multiple assumptions break.
    supersessions: list[Supersession] = []
    for finding in active_findings:
        for assumption in finding.assumptions:
            if not assumption.holds(current, snapshot):
                supersessions.append(
                    Supersession(
                        finding_id=finding.id,
                        broken_assumption=assumption.describe(),
                        triggering_event_id=assumption.triggering_event(current),
                        note=(
                            f"This finding assumed {assumption.describe()}. "
                            f"That is no longer true."
                        ),
                    )
                )
                break

    return ChangeSet(
        changes=tuple(changes),
        supersessions=tuple(supersessions),
        unchanged_reason=None if (changes or supersessions) else UNCHANGED_REASON,
    )


def _assessment_status(state: CaseState) -> str | None:
    assessment = state.latest_assessment
    if assessment is None:
        return None
    return assessment.status


def _disease_summary(assessment: DiseaseAssessment) -> str:
    if assessment.status == "progression":
        return "disease progression recorded"
    return f"disease recorded as {assessment.status}"
