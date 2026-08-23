"""Shared mapping helpers for signal generators.

CIViC evidence_level -> Confidence is a fixed table, not invented per
call. Retrieval items become documented Signals the same way in
genomics.py and literature.py so a missing citation_url can never become
a Signal (DocumentedSource refuses it; we also skip here and count).
"""

from __future__ import annotations

from datetime import UTC, datetime

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.types import (
    DocumentedSource,
    EvidenceClass,
    Signal,
    SignalBatch,
    SignalKind,
)

# CIViC A/B = validated/clinical; C = case study; D/E = pre-clinical /
# inferential. "literature" is an ungraded PubMed hit -- we do not invent
# a grade, so confidence is "stated".
EVIDENCE_LEVEL_TO_CONFIDENCE: dict[str, str] = {
    "A": "high",
    "B": "high",
    "C": "moderate",
    "D": "low",
    "E": "low",
    "literature": "stated",
}


def clock(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def batch_from(
    signals: list[Signal],
    *,
    filtered_count: int,
    empty_reason: str,
) -> SignalBatch:
    packed = tuple(signals)
    return SignalBatch(
        signals=packed,
        empty_reason=None if packed else empty_reason,
        filtered_count=filtered_count,
    )


def gene_in_scope(change_set: ChangeSet, question: Question) -> str | None:
    for candidate in (question.triggering_change, *change_set.changes):
        gene = candidate.detail.get("gene")
        if gene:
            return str(gene)
    gene = question.detail.get("gene")
    return str(gene) if gene else None


def new_alterations(change_set: ChangeSet, question: Question) -> list[Change]:
    found = [c for c in change_set.changes if c.kind is ChangeKind.NEW_ALTERATION]
    trig = question.triggering_change
    if trig.kind is ChangeKind.NEW_ALTERATION and trig not in found:
        found.append(trig)
    return found


def variant_match_key(variant: str) -> tuple[str | None, str | None]:
    """Return (hgvs_p, variant_name) -- exactly one set, matching retrieve_exact."""
    if variant.startswith("p.") or variant.startswith("NP_") or ":p." in variant:
        return variant, None
    return None, variant


def item_to_documented_signal(
    item: dict,
    *,
    now: datetime,
    generator_version: str,
) -> Signal | None:
    citation = item.get("citation") or {}
    url = citation.get("url")
    if not url:
        return None
    level = item.get("evidence_level")
    confidence = EVIDENCE_LEVEL_TO_CONFIDENCE.get(level, "stated")
    claim = item.get("summary") or ""
    if not claim:
        drug = item.get("drug") or "an agent"
        claim = f"{drug} associated at evidence level {level}."
    caveats: list[str] = []
    if item.get("retrieval_mode") == "relaxed":
        rtype = item.get("relaxation_type") or "relaxed"
        caveats.append(f"relaxed match ({rtype}); not an exact-variant hit")
    citation_id = citation.get("id")
    return Signal(
        kind=SignalKind.DOCUMENTED_EVIDENCE,
        evidence_class=EvidenceClass.DOCUMENTED,
        claim=claim,
        source=DocumentedSource(
            citation_url=url,
            citation_id=str(citation_id) if citation_id is not None else None,
        ),
        confidence=confidence,
        caveats=tuple(caveats),
        computed_at=now,
        generator_version=generator_version,
    )
