"""Evidence-side reverse diff: which findings does this evidence change break?

Pure. Deterministic. No I/O, no database access, no LLM, no network.
Identical inputs produce byte-identical output, always.
"""

from __future__ import annotations

from dataclasses import dataclass

from secondlook.case.assumptions import Assumption, Finding
from secondlook.case.evidence_state import EvidenceSnapshot
from secondlook.case.finding_index import FindingsByEvidenceKey
from secondlook.case.state import CaseState

NO_AFFECTED_REASON = (
    "No indexed finding's evidence-sensitive assumptions were broken by this " "evidence change."
)
UNRESOLVED_REASON = (
    "Finding ids were indexed under this evidence key but were absent from "
    "findings_by_id; they were not re-evaluated."
)


@dataclass(frozen=True)
class EvidenceChangeEvent:
    """A write that changed an existing evidence-graph node.

    Shape matches subsystem A's change-event stream (`node_type`, `node_id`,
    `old_props`, `new_props`, `source`, `retrieved_at`) plus the lookup key
    (`gene`, `variant_or_drug`).

    `gene` and `variant_or_drug` are populated by the caller — this module
    does not parse them out of `node_id`. Empty-string slots are meaningful:
    they participate in the same wildcard convention as `assumptions.py`
    (`(gene, "")` = any variant in the gene; `("", drug)` = no specific gene).
    """

    node_type: str
    node_id: str
    old_props: dict[str, object]
    new_props: dict[str, object]
    source: str
    retrieved_at: str
    gene: str
    variant_or_drug: str


@dataclass(frozen=True)
class AffectedCasesReport:
    finding_ids: tuple[str, ...]
    evidence_change: EvidenceChangeEvent
    affected_case_ids: tuple[str, ...]
    empty_reason: str | None = None  # populated when nothing was superseded
    unresolved_finding_ids: tuple[str, ...] = ()
    unresolved_reason: str | None = None


def compute_affected_findings(
    change_event: EvidenceChangeEvent,
    finding_index: FindingsByEvidenceKey,
    findings_by_id: dict[str, Finding],
    evidence: EvidenceSnapshot,
) -> AffectedCasesReport:
    """Deterministic. Looks up findings whose assumptions declared
    sensitivity to `(change_event.gene, change_event.variant_or_drug)` via
    `evidence_keys()`, re-evaluates only those against `evidence`, returns
    superseded findings grouped by case.

    `findings_by_id` and `evidence` are required so this stays pure: no
    database session, no I/O. `Finding.case_id` is the grouping key;
    `Finding.case_state` is the patient snapshot passed to `holds()`.
    """
    candidate_ids = tuple(
        sorted(finding_index.lookup(change_event.gene, change_event.variant_or_drug))
    )

    superseded: list[str] = []
    unresolved: list[str] = []
    for finding_id in candidate_ids:
        finding = findings_by_id.get(finding_id)
        if finding is None:
            unresolved.append(finding_id)
            continue
        if _finding_broken_by_event(finding, change_event, evidence):
            superseded.append(finding_id)

    finding_ids = tuple(superseded)  # already walking sorted candidate_ids
    affected_case_ids = tuple(sorted({findings_by_id[fid].case_id for fid in finding_ids}))
    unresolved_ids = tuple(unresolved)

    return AffectedCasesReport(
        finding_ids=finding_ids,
        evidence_change=change_event,
        affected_case_ids=affected_case_ids,
        empty_reason=None if finding_ids else NO_AFFECTED_REASON,
        unresolved_finding_ids=unresolved_ids,
        unresolved_reason=UNRESOLVED_REASON if unresolved_ids else None,
    )


def _finding_broken_by_event(
    finding: Finding,
    change_event: EvidenceChangeEvent,
    evidence: EvidenceSnapshot,
) -> bool:
    """Re-evaluate only assumptions whose keys match this event, not all."""
    case = finding.case_state if finding.case_state is not None else CaseState()
    for assumption in finding.assumptions:
        if not _assumption_matches_event(
            assumption, change_event.gene, change_event.variant_or_drug
        ):
            continue
        if not assumption.holds(case, evidence):
            return True
    return False


def _assumption_matches_event(assumption: Assumption, gene: str, variant_or_drug: str) -> bool:
    keys = assumption.evidence_keys()
    return (gene, variant_or_drug) in keys or (gene, "") in keys or ("", variant_or_drug) in keys
