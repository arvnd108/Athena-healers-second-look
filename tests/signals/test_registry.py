"""Offline tests for signals/registry.py -- dispatch table, not an orchestrator."""

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

FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _change() -> Change:
    return Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M", "assay": "NGS"},
        triggering_event_id="evt-alt-1",
    )


def _question(change: Change) -> Question:
    return Question(
        kind=change.kind,
        text="What documented evidence exists for EGFR T790M?",
        detail=dict(change.detail),
        priority=1,
        triggering_change=change,
    )


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


def _signal(claim: str, url: str) -> Signal:
    return Signal(
        kind=SignalKind.DOCUMENTED_EVIDENCE,
        evidence_class=EvidenceClass.DOCUMENTED,
        claim=claim,
        source=DocumentedSource(citation_url=url, citation_id=None),
        confidence="stated",
        caveats=(),
        computed_at=FIXED_NOW,
        generator_version="test",
    )


class TestRegistryTable:
    def test_every_change_kind_has_at_least_one_generator(self):
        from secondlook.signals.registry import GENERATORS

        for kind in ChangeKind:
            mapped = GENERATORS.get(kind)
            assert mapped, f"{kind} has no generators mapped"
            assert len(mapped) >= 1


class TestDispatch:
    def test_calls_every_mapped_generator_and_concatenates_unmerged(self):
        from secondlook.signals.registry import dispatch

        calls: list[str] = []
        sensitive = _signal("CIViC: sensitive to osimertinib.", "https://civicdb.org/e/1")
        resistant = _signal(
            "Paper: acquired resistance to osimertinib.", "https://pubmed.ncbi.nlm.nih.gov/1/"
        )

        def gen_a(change_set, question, **_kwargs):
            calls.append("a")
            return SignalBatch(signals=(sensitive,), empty_reason=None)

        def gen_b(change_set, question, **_kwargs):
            calls.append("b")
            return SignalBatch(signals=(resistant,), empty_reason=None)

        change = _change()
        result = dispatch(
            _set(change),
            _question(change),
            registry={ChangeKind.NEW_ALTERATION: (gen_a, gen_b)},
        )
        assert calls == ["a", "b"]
        assert result == [sensitive, resistant]

    def test_contradictory_signals_both_survive_untouched(self):
        from secondlook.signals.registry import dispatch

        sensitive = _signal("sensitive", "https://civicdb.org/e/1")
        resistant = _signal("resistant", "https://pubmed.ncbi.nlm.nih.gov/1/")

        def gen_civic(change_set, question, **_kwargs):
            return SignalBatch(signals=(sensitive,), empty_reason=None)

        def gen_paper(change_set, question, **_kwargs):
            return SignalBatch(signals=(resistant,), empty_reason=None)

        change = _change()
        result = dispatch(
            _set(change),
            _question(change),
            registry={ChangeKind.NEW_ALTERATION: (gen_civic, gen_paper)},
        )
        assert sensitive in result
        assert resistant in result
        assert result[0] is sensitive
        assert result[1] is resistant

    def test_dispatch_does_not_reorder_based_on_generator_output(self):
        from secondlook.signals.registry import dispatch

        order: list[str] = []

        def first(change_set, question, **_kwargs):
            order.append("first")
            return SignalBatch(
                signals=(_signal("from first", "https://example.org/a"),),
                empty_reason=None,
            )

        def second(change_set, question, **_kwargs):
            order.append("second")
            return SignalBatch(signals=(), empty_reason="nothing from second")

        change = _change()
        dispatch(
            _set(change),
            _question(change),
            registry={ChangeKind.NEW_ALTERATION: (first, second)},
        )
        assert order == ["first", "second"]
