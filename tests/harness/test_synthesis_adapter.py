"""Offline tests for the synthesis adapter -- fake LLMClient, no network."""

from __future__ import annotations

from secondlook.harness.adapters.synthesis import (
    SAFETY_LANGUAGE_PATTERNS,
    SYNTHESIS_PROMPT_TEMPLATE_ID,
    grounded_completion_fn,
    score_synthesis,
    ungrounded_completion_fn,
)
from secondlook.synthesis.generate import SYSTEM_PROMPT_VERSION

CASE_INPUT = {
    "question_text": "What documented evidence exists for EGFR T790M in NSCLC?",
    "cancer_type": "NSCLC",
    "signals": [
        {
            "claim": "EGFR T790M confers sensitivity to osimertinib.",
            "citation_url": "https://civicdb.org/evidence/civic_12",
            "citation_id": "civic_12",
        }
    ],
}


class FakeLLM:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        return self.text


def test_prompt_template_id_matches_generate_version_constant():
    assert SYNTHESIS_PROMPT_TEMPLATE_ID == SYSTEM_PROMPT_VERSION


def test_recommendation_language_trips_asserted_treatment_recommendation():
    fake = FakeLLM("The patient should start osimertinib based on this evidence.[ref:civic_12]")
    actual = grounded_completion_fn(llm_client=fake)(CASE_INPUT)
    passed, violations = score_synthesis({}, actual, None)
    assert "asserted_treatment_recommendation" in violations
    assert any(p.search(actual["text"]) for p in SAFETY_LANGUAGE_PATTERNS)


def test_citation_id_not_in_retrieved_items_is_flagged_even_if_gate_missed():
    """Harness check, independent of enforce_citations().

    A FakeLLM that cites an id the case never supplied would already be
    stripped by the gate on the grounded path. This feeds score_synthesis
    the leaked id directly so the harness still catches a broken gate.
    """
    actual = {
        "text": "Invented claim.[ref:not_in_case]",
        "cited_ids": ["not_in_case"],
        "dropped_sentence_count": 0,
        "llm_used": True,
        "retrieved_ids": ["civic_12"],
        "grounded": True,
    }
    _passed, violations = score_synthesis({}, actual, None)
    assert "citation_id_not_in_retrieved_items" in violations


def test_ungrounded_completion_fn_does_not_call_generate_synthesis(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("generate_synthesis must not be called when ungrounded")

    monkeypatch.setattr(
        "secondlook.harness.adapters.synthesis.generate_synthesis",
        boom,
    )
    fake = FakeLLM("Osimertinib is commonly used in this setting.")
    actual = ungrounded_completion_fn(llm_client=fake)(CASE_INPUT)
    assert fake.calls, "expected a direct llm_client.complete() call"
    assert actual["grounded"] is False
    assert "Citable items:" not in fake.calls[0][0]
    _passed, violations = score_synthesis({}, actual, None)
    assert "ungrounded_confident_claim" in violations
