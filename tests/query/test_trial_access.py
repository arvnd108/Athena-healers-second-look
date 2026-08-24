from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.query.trial_access import match_trials_for_case
from secondlook.signals.trials import generate, lookup_candidate_trials

from .fakes import FakeCase, FakeEvent, FakeGraph, FakeStore

RECRUITING_ROW = (
    "NCT01234567",
    "ClinicalTrials.gov",
    "RECRUITING",
    "Phase 2",
    "Osimertinib in EGFR T790M NSCLC",
    "https://clinicaltrials.gov/study/NCT01234567",
)


def test_generate_output_is_unchanged_after_lookup_extraction():
    graph = FakeGraph(responses=[[RECRUITING_ROW]])
    change = Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M", "assay": "NGS"},
        triggering_event_id="evt-alt-1",
    )
    question = Question(
        kind=change.kind,
        text="Are there recruiting trials matching this alteration?",
        detail={**change.detail, "cancer_type": "NSCLC"},
        priority=1,
        triggering_change=change,
    )
    batch = generate(
        ChangeSet(changes=(change,), supersessions=(), unchanged_reason=None),
        question,
        graph=graph,
        now=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    assert len(batch.signals) == 1
    assert "NCT01234567" in batch.signals[0].source.citation_url
    assert batch.filtered_count == 0


def test_lookup_candidate_trials_uses_the_same_return_list_as_generate():
    graph = FakeGraph(responses=[[RECRUITING_ROW]])
    rows = lookup_candidate_trials("EGFR", graph=graph)
    assert rows == [RECRUITING_ROW]
    cypher, params = graph.calls[0]
    assert "TARGETS_BIOMARKER" in cypher
    # Post-#46 generate unpacks six columns defensively and reads criteria as
    # row[6] when present. The shared lookup must keep that seventh column.
    assert "t.eligibility_criteria" in cypher
    assert params["gene"] == "EGFR"


def test_match_trials_for_case_composes_lookup_extract_and_match():
    case_id = uuid.uuid4()
    store = FakeStore(
        case=FakeCase(id=case_id, label="C", cancer_type="NSCLC"),
        events=[
            FakeEvent(
                id=uuid.uuid4(),
                event_type="ALTERATION_OBSERVED",
                payload={"gene": "EGFR", "variant": "T790M"},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )
    row = (*RECRUITING_ROW, "Age >= 18")
    graph = FakeGraph(responses=[[row]])

    def extract(registry_id, criteria_text):
        assert registry_id == "NCT01234567"
        assert criteria_text == "Age >= 18"
        return SimpleNamespace(predicates=["pred"])

    def match_fn(extractions, case):
        assert "NCT01234567" in extractions
        assert case.alterations[0].gene == "EGFR"
        return [
            SimpleNamespace(
                trial_id="NCT01234567",
                bucket="needs_verification",
                matched_criteria=[],
                unresolved_criteria=["Age >= 18"],
                violated_criteria=[],
            )
        ]

    result = match_trials_for_case(
        store,
        case_id,
        graph=graph,
        extractor=SimpleNamespace(extract=extract),
        match_fn=match_fn,
    )
    assert result.empty_reason is None
    item = result.items[0]
    assert item.trial_id == "NCT01234567"
    assert item.bucket == "needs_verification"


def test_match_trials_for_case_empty_when_no_gene():
    case_id = uuid.uuid4()
    store = FakeStore(case=FakeCase(id=case_id, label="C", cancer_type="NSCLC"))
    result = match_trials_for_case(store, case_id, graph=FakeGraph())
    assert result.items == ()
    assert "gene" in result.empty_reason
