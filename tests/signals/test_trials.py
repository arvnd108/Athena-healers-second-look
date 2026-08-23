"""Offline tests for signals/trials.py -- graph.query mocked, no live Trial nodes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.types import DocumentedSource, EvidenceClass, SignalKind
from secondlook.tier1.graph_schema import TRIAL_STATUSES

FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

RECRUITING_ROW = (
    "NCT01234567",
    "ClinicalTrials.gov",
    "RECRUITING",
    "Phase 2",
    "Osimertinib in EGFR T790M NSCLC",
    "https://clinicaltrials.gov/study/NCT01234567",
)
TERMINATED_ROW = (
    "NCT09999999",
    "ClinicalTrials.gov",
    "TERMINATED",
    "Phase 3",
    "Terminated EGFR trial",
    "https://clinicaltrials.gov/study/NCT09999999",
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
    return Question(
        kind=change.kind,
        text="Are there recruiting trials matching this alteration?",
        detail={**change.detail, **detail},
        priority=1,
        triggering_change=change,
    )


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


def _generate(**kwargs):
    from secondlook.signals.trials import generate as generate_trials

    return generate_trials(now=FIXED_NOW, **kwargs)


class TestTrialsZeroRows:
    def test_empty_graph_returns_empty_with_reason(self):
        graph = FakeGraph(responses=[[]])
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change, cancer_type="NSCLC"),
            graph=graph,
        )
        assert batch.signals == ()
        assert batch.empty_reason is not None
        assert "trial" in batch.empty_reason.lower()


class TestTrialsSyntheticRow:
    def test_recruiting_row_maps_to_trial_signal_with_citation(self):
        graph = FakeGraph(responses=[[RECRUITING_ROW]])
        change = _change()
        batch = _generate(
            change_set=_set(change),
            question=_question(change, cancer_type="NSCLC"),
            graph=graph,
        )
        assert len(batch.signals) == 1
        signal = batch.signals[0]
        assert signal.kind is SignalKind.TRIAL
        assert signal.evidence_class is EvidenceClass.DOCUMENTED
        assert isinstance(signal.source, DocumentedSource)
        assert "NCT01234567" in signal.source.citation_url
        assert "RECRUITING" in signal.claim or "Osimertinib" in signal.claim

    def test_terminated_row_is_dropped_from_a_mixed_set(self):
        graph = FakeGraph(responses=[[RECRUITING_ROW, TERMINATED_ROW]])
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert len(batch.signals) == 1
        assert "NCT01234567" in batch.signals[0].source.citation_url
        assert batch.filtered_count == 1

    def test_query_binds_gene_and_actionable_statuses_as_params(self):
        graph = FakeGraph(responses=[[]])
        change = _change()
        _generate(
            change_set=_set(change),
            question=_question(change, cancer_type="NSCLC"),
            graph=graph,
        )
        assert graph.calls
        cypher, params = graph.calls[0]
        assert "$gene" in cypher
        assert params["gene"] == "EGFR"
        assert "TARGETS_BIOMARKER" in cypher
        statuses = set(params["actionable_statuses"])
        assert statuses <= set(TRIAL_STATUSES)
        assert "RECRUITING" in statuses
        assert "TERMINATED" not in statuses

    def test_row_without_registry_id_or_url_is_refused(self):
        uncited = (None, "ClinicalTrials.gov", "RECRUITING", "Phase 2", "untitled", None)
        graph = FakeGraph(responses=[[uncited]])
        change = _change()
        batch = _generate(change_set=_set(change), question=_question(change), graph=graph)
        assert batch.signals == ()
        assert batch.filtered_count == 1
        assert batch.empty_reason is not None

    def test_unknown_status_raises_rather_than_passing_through(self):
        bad = (
            "NCT00000001",
            "ClinicalTrials.gov",
            "WEIRDLY_OPEN",
            "Phase 1",
            "bad status",
            "https://clinicaltrials.gov/study/NCT00000001",
        )
        graph = FakeGraph(responses=[[bad]])
        change = _change()
        with pytest.raises(ValueError, match="status"):
            _generate(change_set=_set(change), question=_question(change), graph=graph)
