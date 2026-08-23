"""Signal envelope -- the atomic unit OncoSphere generators emit.

HARD RULE, unconditional: a `documented` signal with no citation URL is
never constructed. A `computed` signal carries a method/version, never a
citation-shaped field. These are enforced by the source-type union
(`DocumentedSource | ComputedMethod | RegulatorySource | None`) plus
`Signal.__post_init__`, not by a later validator a caller could skip.
Mirrors `tier1/retrieval.py`'s `_row_to_item` refusing to build an item
with no `citation_url`.

Why a companion `SignalBatch` rather than a bare `list[Signal]`: a
generator that has nothing to say must still tell the caller *why*
(ARCHITECTURE.md §3, invariant #1). `RetrievalResult.filtered_count` is
the established pattern for "we saw rows but dropped them";
`empty_reason` covers the whole-generator case (no relevant Change, no
gene in scope, upstream returned zero rows). `dispatch()` concatenates
`.signals` into the issue's `(ChangeSet, Question) -> list[Signal]`
surface; callers that need the reason call the generator directly.

Disagreement is not resolved here. Two contradictory signals are both
valid `Signal` values; rendering them side by side is subsystem I/M.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from secondlook.tier1.graph_schema import assert_valid

# Bounded like graph_schema.EVIDENCE_LEVELS -- a string field checked
# against a frozenset at construction, not a new validation idiom.
CONFIDENCE_LEVELS: frozenset[str] = frozenset({"stated", "low", "moderate", "high"})


class SignalKind(StrEnum):
    DOCUMENTED_EVIDENCE = "documented_evidence"
    TRIAL = "trial"
    ACCESS_PATHWAY = "access_pathway"
    COMPUTATIONAL = "computational"
    COMBINATION = "combination"
    COHORT_CONTEXT = "cohort_context"


class EvidenceClass(StrEnum):
    DOCUMENTED = "documented"
    COMPUTED = "computed"
    REGULATORY = "regulatory"
    CONTEXTUAL = "contextual"


@dataclass(frozen=True)
class DocumentedSource:
    """A human published this. `citation_url` is required and must be non-empty."""

    citation_url: str
    citation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.citation_url:
            raise ValueError("documented source requires a non-empty citation_url")


@dataclass(frozen=True)
class ComputedMethod:
    """A model or measurement produced this. No citation-shaped field on purpose."""

    method: str
    version: str


@dataclass(frozen=True)
class RegulatorySource:
    """A pathway defined by an instrument (label, expanded-access programme)."""

    instrument: str
    citation_url: str | None = None


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    evidence_class: EvidenceClass
    claim: str
    source: DocumentedSource | ComputedMethod | RegulatorySource | None
    confidence: str  # one of CONFIDENCE_LEVELS; asserted below
    caveats: tuple[str, ...]
    computed_at: datetime
    generator_version: str

    def __post_init__(self) -> None:
        assert_valid(self.confidence, CONFIDENCE_LEVELS, "confidence")
        if self.evidence_class is EvidenceClass.DOCUMENTED:
            if not isinstance(self.source, DocumentedSource):
                raise ValueError(
                    "documented signal requires a DocumentedSource "
                    f"(got {type(self.source).__name__})"
                )
        elif self.evidence_class is EvidenceClass.COMPUTED:
            if not isinstance(self.source, ComputedMethod):
                raise ValueError(
                    "computed signal requires a ComputedMethod, never a citation-shaped field "
                    f"(got {type(self.source).__name__})"
                )
        elif self.evidence_class is EvidenceClass.REGULATORY:
            if not isinstance(self.source, RegulatorySource):
                raise ValueError(
                    "regulatory signal requires a RegulatorySource "
                    f"(got {type(self.source).__name__})"
                )


@dataclass(frozen=True)
class SignalBatch:
    """Generator return value: signals plus a structured account of nothing.

    `empty_reason` is populated iff `signals` is empty -- never a silent
    `[]`, never `None` standing in for the batch itself. `filtered_count`
    is how many candidate rows were seen and dropped (no citation, non-
    actionable trial status, ...), matching `RetrievalResult`.
    """

    signals: tuple[Signal, ...]
    empty_reason: str | None
    filtered_count: int = 0

    def __post_init__(self) -> None:
        if self.signals and self.empty_reason is not None:
            raise ValueError("empty_reason is only set when signals is empty")
        if not self.signals and not self.empty_reason:
            raise ValueError("an empty SignalBatch must carry empty_reason -- never a silent gap")
