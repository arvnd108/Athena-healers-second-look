"""Offline tests for signals/pharmacology.py -- DgidbClient + graph.query mocked."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.dgidb import DgidbError
from secondlook.signals.types import EvidenceClass, SignalKind

FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    def __init__(self, by_needle: dict[str, list[tuple]] | None = None):
        self._by_needle = dict(by_needle or {})
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params or {}))
        for needle, rows in self._by_needle.items():
            if needle in cypher:
                return FakeResult(rows)
        return FakeResult([])


class FakeDgidb:
    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls: list[str] = []

    def fetch_drugs(self, gene_symbol: str) -> list[dict]:
        self.calls.append(gene_symbol)
        if self.error is not None:
            raise self.error
        return list(self.rows)


def _change(gene: str = "EGFR", variant: str = "T790M") -> Change:
    return Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary=f"{gene} {variant} newly observed",
        detail={"gene": gene, "variant": variant, "assay": "NGS"},
        triggering_event_id="evt-alt-1",
    )


def _biomarker() -> Change:
    return Change(
        kind=ChangeKind.BIOMARKER_SHIFT,
        summary="TMB crossed 10.0 (8.0 -> 16.0)",
        detail={"name": "TMB", "from": 8.0, "to": 16.0, "threshold": 10.0},
        triggering_event_id="evt-bio-1",
    )


def _question(change: Change) -> Question:
    return Question(
        kind=change.kind,
        text="What treatment options follow this change?",
        detail=dict(change.detail),
        priority=1,
        triggering_change=change,
    )


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


def _generate(**kwargs):
    from secondlook.signals.pharmacology import generate as generate_pharmacology

    return generate_pharmacology(now=FIXED_NOW, **kwargs)


class TestPharmacologyMatchingData:
    def test_dgidb_hit_is_contextual_because_no_per_item_citation(self):
        client = FakeDgidb(rows=[{"name": "Osimertinib", "approved": True, "score": 0.9}])
        graph = FakeGraph(by_needle={"drug_class": []})
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change),
            graph=graph,
            dgidb_client=client,
        )
        assert client.calls == ["EGFR"]
        assert len(batch.signals) == 1
        signal = batch.signals[0]
        assert signal.kind is SignalKind.COHORT_CONTEXT
        assert signal.evidence_class is EvidenceClass.CONTEXTUAL
        assert signal.source is None
        assert "Osimertinib" in signal.claim
        assert any("DGIdb" in c for c in signal.caveats)

    def test_reads_drug_class_off_graph_not_via_enrich_write_path(self, monkeypatch):
        called = []

        def forbidden(*_a, **_k):
            called.append(True)
            raise AssertionError("enrich_drug_classes is a write-path batch job")

        monkeypatch.setattr("secondlook.tier1.chembl_enrich.enrich_drug_classes", forbidden)
        client = FakeDgidb(rows=[])
        graph = FakeGraph(by_needle={"drug_class": [("Osimertinib", "EGFR inhibitor")]})
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change),
            graph=graph,
            dgidb_client=client,
        )
        assert called == []
        assert len(batch.signals) == 1
        assert batch.signals[0].evidence_class is EvidenceClass.CONTEXTUAL
        assert "EGFR inhibitor" in batch.signals[0].claim


class TestPharmacologyNothingAndErrors:
    def test_no_gene_returns_empty_with_reason(self):
        change = _biomarker()
        batch = _generate(
            change_set=_set(change),
            question=_question(change),
            graph=FakeGraph(),
            dgidb_client=FakeDgidb(),
        )
        assert batch.signals == ()
        assert batch.empty_reason is not None
        assert "gene" in batch.empty_reason.lower()

    def test_empty_dgidb_and_no_drug_class_returns_empty_with_reason(self):
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change),
            graph=FakeGraph(by_needle={"drug_class": []}),
            dgidb_client=FakeDgidb(rows=[]),
        )
        assert batch.signals == ()
        assert batch.empty_reason is not None

    def test_dgidb_error_is_caught_and_surfaced_not_swallowed_silently(self):
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change),
            graph=FakeGraph(by_needle={"drug_class": []}),
            dgidb_client=FakeDgidb(error=DgidbError("DGIdb down")),
        )
        assert batch.signals == ()
        assert batch.empty_reason is not None
        assert "DgidbError" in batch.empty_reason or "DGIdb" in batch.empty_reason

    def test_unrelated_exception_is_not_caught(self):
        class Unexpected(RuntimeError):
            pass

        change = _change()
        with pytest.raises(Unexpected):
            _generate(
                change_set=_set(change),
                question=_question(change),
                graph=FakeGraph(),
                dgidb_client=FakeDgidb(error=Unexpected("nope")),
            )
