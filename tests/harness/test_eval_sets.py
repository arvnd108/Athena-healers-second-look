"""The synthesis eval set is hand-labeled and checked in, not generated."""

from __future__ import annotations

from secondlook.harness.llm_eval import EvalCase

from .eval_sets.synthesis import SYNTHESIS_EVAL_CASES


def test_synthesis_eval_set_has_at_least_five_hand_labeled_cases():
    assert len(SYNTHESIS_EVAL_CASES) >= 5
    for case in SYNTHESIS_EVAL_CASES:
        assert isinstance(case, EvalCase)
        assert case.input.get("question_text")
        assert case.expected, "every case must carry a human-written expected output"


def test_eval_set_covers_conflict_and_adversarial_recommendation_ask():
    texts = [case.input["question_text"] for case in SYNTHESIS_EVAL_CASES]
    assert any("should this patient take" in text.lower() for text in texts)
    claims = [
        signal["claim"]
        for case in SYNTHESIS_EVAL_CASES
        for signal in case.input.get("signals") or []
    ]
    assert any("sensitive" in c.lower() for c in claims)
    assert any("resistance" in c.lower() for c in claims)
