"""Offline tests for signals/literature.py -- graph.query mocked, embed stubbed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.types import DocumentedSource, EvidenceClass, SignalKind
from secondlook.tier1 import semantic_retrieval

FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

SEMANTIC_ROW = (
    238,
    "A",
    "https://civicdb.org/evidence/238",
    "NTRK fusion evidence.",
    0.05,
    "Larotrectinib",
)


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


def _question(change: Change, text: str | None = None, **detail) -> Question:
    return Question(
        kind=change.kind,
        text=text or "What documented evidence exists?",
        detail={**change.detail, **detail},
        priority=1,
        triggering_change=change,
    )


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


@pytest.fixture(autouse=True)
def stub_embed_text(monkeypatch):
    monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text: [0.0] * 384)


def _generate(**kwargs):
    from secondlook.signals.literature import generate as generate_literature

    return generate_literature(now=FIXED_NOW, **kwargs)


class TestLiteratureMatchingData:
    def test_gene_scoped_hit_maps_to_documented_signal(self):
        graph = FakeGraph(by_needle={"HAS_VARIANT": [SEMANTIC_ROW]})
        change = _change(gene="NTRK3", variant="Fusion")
        batch = _generate(
            change_set=_set(change),
            question=_question(change, text="NTRK3 fusion sarcoma"),
            graph=graph,
        )
        assert len(batch.signals) == 1
        signal = batch.signals[0]
        assert signal.kind is SignalKind.DOCUMENTED_EVIDENCE
        assert signal.evidence_class is EvidenceClass.DOCUMENTED
        assert isinstance(signal.source, DocumentedSource)
        assert signal.source.citation_url == "https://civicdb.org/evidence/238"
        assert signal.confidence == "high"

    def test_no_gene_falls_back_to_unscoped_semantic_on_question_text(self):
        graph = FakeGraph(by_needle={"EvidenceItem": [SEMANTIC_ROW], "Publication": []})
        change = _biomarker()
        batch = _generate(
            change_set=_set(change),
            question=_question(change, text="TMB-high immunotherapy NSCLC"),
            graph=graph,
        )
        assert len(batch.signals) == 1
        assert any("query_vec" in (params or {}) for _, params in graph.calls)


class TestLiteratureNothingAndErrors:
    def test_no_hits_returns_empty_with_reason(self):
        graph = FakeGraph(by_needle={})
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.empty_reason is not None

    def test_uncited_row_is_counted(self):
        uncited = (238, "A", None, "summary", 0.05, "Larotrectinib")
        graph = FakeGraph(by_needle={"HAS_VARIANT": [uncited]})
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.filtered_count == 1
        assert batch.empty_reason is not None

    def test_retrieve_failure_propagates_uncaught(self):
        # retrieve_semantic[_for_gene] touch only FalkorDB + embed_text, neither
        # of which raises a custom exception type here (matching retrieval.py's
        # Mode 1/2, which don't wrap graph.query either) -- so this module
        # catches nothing and any failure surfaces to the caller undisguised.
        class Boom(RuntimeError):
            pass

        def boom(*_args, **_kwargs):
            raise Boom("falkordb connection refused")

        change = _change()
        with pytest.raises(Boom):
            _generate(
                change_set=_set(change),
                question=_question(change),
                graph=FakeGraph(),
                retrieve_for_gene_fn=boom,
            )

    def test_does_not_call_pubmed_write_path(self, monkeypatch):
        called = []

        def forbidden(*_a, **_k):
            called.append(True)
            raise AssertionError("pubmed write path must not run at signal time")

        monkeypatch.setattr("secondlook.tier1.pubmed_loader.fetch_pubmed_abstracts", forbidden)
        monkeypatch.setattr("secondlook.tier1.pubmed_loader.load_publication", forbidden)
        change = _change()
        _generate(change_set=_set(change), question=_question(change), graph=FakeGraph())
        assert called == []
