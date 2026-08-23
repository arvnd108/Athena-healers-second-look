"""Typed assumption predicates over case (and, later, evidence) state.

Every `Finding` records the assumptions it depends on as structured
predicates, not prose. When state changes, re-evaluate; any that now
return False → the finding is superseded, with a machine-generated reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from secondlook.case.evidence_state import EvidenceSnapshot
from secondlook.case.state import CaseState


class Assumption(Protocol):
    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool: ...

    def describe(self) -> str: ...

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        """`(gene, variant)` or `(gene, drug)` pairs this assumption is sensitive to.

        Used to index findings for the reverse lookup in `evidence_diff.py`,
        so a new evidence item triggers re-evaluation of only the findings
        that could possibly be affected — not a scan of every active finding.

        An assumption with nothing evidence-side to key on (e.g. a pure
        case-state predicate about biomarkers or disease status) returns
        `frozenset()` — this is expected, not a bug. It means the assumption
        is only ever re-evaluated via the patient-side `compute_diff()` path,
        never via `compute_affected_findings()`.
        """
        ...


@dataclass(frozen=True)
class NoAlterationIn:
    gene: str

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        return not any(a.gene == self.gene for a in case.alterations)

    def describe(self) -> str:
        return f"no known alteration in {self.gene}"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        # Wildcard convention: the empty string in the *variant* slot means
        # "any variant in this gene". `FindingsByEvidenceKey.lookup(gene, v)`
        # must therefore also return findings indexed under `(gene, "")`, so
        # a new evidence item for EGFR T790M re-evaluates a finding that
        # assumed no alteration in EGFR at all.
        return frozenset({(self.gene, "")})


@dataclass(frozen=True)
class BiomarkerBelow:
    name: str
    threshold: float

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        reading = case.biomarkers.get(self.name)
        return reading is None or reading.value < self.threshold

    def describe(self) -> str:
        return f"{self.name} below {self.threshold}"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        # Pure case-state predicate: nothing evidence-side to key on. A new
        # paper about this biomarker does not, by itself, break the
        # assumption — only a new BIOMARKER_MEASURED event on the case does,
        # via `compute_diff()`.
        return frozenset()


@dataclass(frozen=True)
class DrugNotYetTried:
    drug: str

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        tried = {t.regimen.lower() for t in case.treatments}
        return self.drug.lower() not in tried

    def describe(self) -> str:
        return f"{self.drug} not previously administered"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        # Wildcard convention: the empty string in the *gene* slot means
        # "no specific gene" (this assumption is about a drug, not a
        # variant). `FindingsByEvidenceKey.lookup(gene, drug)` must therefore
        # also return findings indexed under `("", drug)`, so a new evidence
        # item about osimertinib — regardless of which gene it is filed
        # under — re-evaluates a finding that assumed osimertinib was naive.
        return frozenset({("", self.drug)})


@dataclass(frozen=True)
class DiseaseNotProgressing:
    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        assessment = case.latest_assessment
        if assessment is None:
            return True
        return assessment.status != "progression"

    def describe(self) -> str:
        return "disease not recorded as progressing"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        # Pure case-state predicate: nothing evidence-side to key on. Only a
        # DISEASE_ASSESSMENT event on the case can break this, via
        # `compute_diff()`.
        return frozenset()


@dataclass(frozen=True)
class Finding:
    """A cited finding answering a question, with typed assumptions.

    `case_id` is stored on the finding so `compute_affected_findings` can
    group superseded findings by case without a parallel lookup map (keeps
    that function inside its documented four-parameter signature).

    `case_state` is the patient snapshot used when re-evaluating on the
    evidence-change path. This function is pure and has no store to load
    from; callers pass the current folded state here. Patient-side
    `compute_diff` ignores this field and uses its `current` argument.
    """

    id: str
    case_id: str
    assumptions: tuple[Assumption, ...] = ()
    case_state: CaseState | None = None

    def collected_evidence_keys(self) -> frozenset[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for assumption in self.assumptions:
            keys.update(assumption.evidence_keys())
        return frozenset(keys)
