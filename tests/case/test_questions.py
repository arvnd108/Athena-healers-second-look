"""Offline tests for the minimal Question type issue #6 needs.

The full QUESTION_TEMPLATES renderer is issue #10 -- this file only
covers the dataclass that lets `(ChangeSet, Question) -> list[Signal]`
type-check against real instances.
"""

from __future__ import annotations

from secondlook.case.diff import Change, ChangeKind
from secondlook.case.questions import Question


def test_question_holds_kind_text_detail_priority_and_triggering_change():
    change = Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M", "assay": "NGS"},
        triggering_event_id="evt-1",
    )
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
