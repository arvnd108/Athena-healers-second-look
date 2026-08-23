"""Offline tests for signals/genomics.py -- mocks graph.query, no FalkorDB."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.dgidb import DgidbError
from secondlook.signals.types import DocumentedSource, EvidenceClass, SignalKind
from secondlook.tier1.retrieval import retrieve_exact

FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

CIVIC_ROW = (
    "Osimertinib",
    "sensitive",
    "A",
    "https://civicdb.org/evidence/12",
    "EGFR T790M confers sensitivity to osimertinib.",
    12,
)


class FakeResult:
    def __init__(self, rows: list[tuple]):
        self.result_set = rows


class FakeGraph:
    def __init__(self, responses: list[list[tuple]] | None = None):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params or {}))
        rows = self._responses.pop(0) if self._responses else []
        return FakeResult(rows)


def _change(gene: str = "EGFR", variant: str = "T790M") -> Change:
    return Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary=f"{gene} {variant} newly observed",
        detail={"gene": gene, "variant": variant, "assay": "NGS"},
        triggering_event_id="evt-alt-1",
    )


def _question(change: Change, **detail) -> Question:
    gene = change.detail.get("gene", "")
    variant = change.detail.get("variant", "")
    return Question(
        kind=change.kind,
        text=f"What documented evidence exists for {gene} {variant}?",
        detail={**change.detail, **detail},
        priority=1,
        triggering_change=change,
    )


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


def _generate(**kwargs):
    from secondlook.signals.genomics import generate as generate_genomics

    return generate_genomics(now=FIXED_NOW, **kwargs)


class TestGenomicsMatchingData:
    def test_exact_hit_maps_to_documented_signal_with_citation(self):
        graph = FakeGraph(responses=[[CIVIC_ROW]])
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)

        assert batch.filtered_count == 0
        assert batch.empty_reason is None
        assert len(batch.signals) == 1
        signal = batch.signals[0]
        assert signal.kind is SignalKind.DOCUMENTED_EVIDENCE
        assert signal.evidence_class is EvidenceClass.DOCUMENTED
        assert isinstance(signal.source, DocumentedSource)
        assert signal.source.citation_url == "https://civicdb.org/evidence/12"
        assert signal.confidence == "high"
        assert "osimertinib" in signal.claim.lower()

    def test_uses_variant_name_not_string_interpolated_cypher(self):
        graph = FakeGraph(responses=[[]])
        change = _change(gene="TP53' OR '1'='1", variant="R175H")
        _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert graph.calls, "retrieve_exact must have been invoked"
        cypher, params = graph.calls[0]
        assert "TP53' OR '1'='1" not in cypher
        assert params["gene"] == "TP53' OR '1'='1"
        assert params.get("variant_name") == "R175H"


class TestGenomicsNothingToSay:
    def test_no_graph_hits_returns_empty_with_reason(self):
        graph = FakeGraph(responses=[[], []])
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.empty_reason is not None
        assert "EGFR" in batch.empty_reason
        assert "T790M" in batch.empty_reason

    def test_no_relevant_change_returns_empty_with_reason(self):
        graph = FakeGraph(responses=[[]])
        change = Change(
            kind=ChangeKind.BIOMARKER_SHIFT,
            summary="TMB crossed 10.0 (8.0 -> 16.0)",
            detail={"name": "TMB", "from": 8.0, "to": 16.0, "threshold": 10.0},
            triggering_event_id="evt-bio-1",
        )
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.empty_reason is not None
        assert graph.calls == [], "must not query when no NEW_ALTERATION is in scope"


class TestGenomicsFilteredAndErrors:
    def test_uncited_row_is_counted_not_emitted(self):
        uncited = ("Osimertinib", "sensitive", "A", None, "summary", 12)
        graph = FakeGraph(responses=[[uncited], []])
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.filtered_count == 1
        assert batch.empty_reason is not None

    def test_corrupted_evidence_level_is_not_swallowed(self):
        bad = ("Osimertinib", "sensitive", "Z", "https://civicdb.org/e/1", "s", 1)
        graph = FakeGraph(responses=[[bad]])
        change = _change()
        with pytest.raises(ValueError, match="evidence_level"):
            _generate(change_set=_set(change), question=_question(change), graph=graph)

    def test_injected_retrieve_raising_is_not_caught_with_bare_except(self):
        def boom(*_args, **_kwargs):
            raise DgidbError("should not be swallowed")

        change = _change()
        with pytest.raises(DgidbError, match="should not be swallowed"):
            _generate(
                change_set=_set(change),
                question=_question(change),
                graph=FakeGraph(responses=[[]]),
                retrieve_exact_fn=boom,
                retrieve_relaxed_fn=retrieve_exact,
            )
