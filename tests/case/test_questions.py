"""Offline tests for question template rendering -- no LLM, no I/O.

`render_questions` is pure: real `Change` / `CaseState` values, never mocks.
"""

from __future__ import annotations

import pytest

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.questions import (
    PRIORITY_BY_CHANGE_KIND,
    QUESTION_TEMPLATES,
    Question,
    TemplateFillError,
    render_questions,
)
from secondlook.case.state import CaseState, TreatmentEntry


def _alteration() -> Change:
    return Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M", "assay": "NGS"},
        triggering_event_id="evt-alt-1",
    )


def _progression() -> Change:
    return Change(
        kind=ChangeKind.DISEASE_PROGRESSION,
        summary="disease progression recorded",
        detail={"status": "progression", "sites": ["lung"], "from": "stable"},
        triggering_event_id="evt-dx-1",
    )


def _state(*treatments: TreatmentEntry) -> CaseState:
    return CaseState(case_id="case-1", treatments=treatments)


def _set(*changes: Change) -> ChangeSet:
    return ChangeSet(changes=tuple(changes), supersessions=(), unchanged_reason=None)


def test_question_holds_kind_text_detail_priority_and_triggering_change():
    change = _alteration()
    question = Question(
        kind=ChangeKind.NEW_ALTERATION,
        text="What documented evidence exists for EGFR T790M in NSCLC?",
        detail={"gene": "EGFR", "variant": "T790M", "cancer_type": "NSCLC"},
        priority=1,
        triggering_change=change,
    )
    assert question.kind is ChangeKind.NEW_ALTERATION
    assert question.triggering_change is change
    assert question.detail["cancer_type"] == "NSCLC"
    assert question.priority == 1


class TestRenderNewAlteration:
    def test_yields_exactly_three_templated_questions_including_cancer_type(self):
        change = _alteration()
        questions = render_questions(_set(change), _state(), cancer_type="NSCLC")

        assert len(questions) == 3
        assert [q.text for q in questions] == [
            "What documented evidence exists for EGFR T790M in NSCLC?",
            "Are there recruiting trials matching EGFR T790M?",
            "Does EGFR T790M confer resistance to any component of the current regimen?",
        ]
        assert all(q.kind is ChangeKind.NEW_ALTERATION for q in questions)
        assert all(q.triggering_change is change for q in questions)
        assert all(q.detail["cancer_type"] == "NSCLC" for q in questions)
        assert all(
            q.priority == PRIORITY_BY_CHANGE_KIND[ChangeKind.NEW_ALTERATION] for q in questions
        )


class TestRenderDiseaseProgression:
    def test_last_regimen_comes_from_most_recent_started_treatment(self):
        change = _progression()
        state = _state(
            TreatmentEntry(
                regimen="gefitinib", line=1, action="started", reason=None, event_id="t1"
            ),
            TreatmentEntry(
                regimen="gefitinib", line=1, action="stopped", reason=None, event_id="t2"
            ),
            TreatmentEntry(
                regimen="osimertinib", line=2, action="started", reason=None, event_id="t3"
            ),
        )
        questions = render_questions(_set(change), state, cancer_type="NSCLC")
        assert len(questions) == 1
        assert questions[0].text == (
            "What resistance mechanisms are documented for osimertinib in NSCLC?"
        )

    def test_missing_treatment_history_uses_documented_fallback(self):
        change = _progression()
        questions = render_questions(_set(change), _state(), cancer_type="NSCLC")
        assert len(questions) == 1
        assert "prior therapy" in questions[0].text
        assert "NSCLC" in questions[0].text


class TestRenderDeterminismAndTables:
    def test_same_inputs_twice_are_byte_identical(self):
        change = _alteration()
        args = (_set(change), _state())
        first = render_questions(*args, cancer_type="NSCLC")
        second = render_questions(*args, cancer_type="NSCLC")
        assert first == second

    def test_priority_table_covers_every_change_kind(self):
        assert set(PRIORITY_BY_CHANGE_KIND) == set(ChangeKind)
        assert set(QUESTION_TEMPLATES) == set(ChangeKind)

    def test_missing_placeholder_raises_named_error_not_keyerror(self):
        change = Change(
            kind=ChangeKind.NEW_ALTERATION,
            summary="incomplete",
            detail={"gene": "EGFR"},  # variant missing
            triggering_event_id="evt-x",
        )
        with pytest.raises(TemplateFillError, match="variant"):
            render_questions(_set(change), _state(), cancer_type="NSCLC")

    def test_does_not_render_questions_from_supersessions_only(self):
        from secondlook.case.diff import Supersession

        empty = ChangeSet(
            changes=(),
            supersessions=(
                Supersession(
                    finding_id="f1",
                    broken_assumption="x",
                    triggering_event_id="e",
                    note="n",
                ),
            ),
            unchanged_reason=None,
        )
        assert render_questions(empty, _state(), cancer_type="NSCLC") == []
