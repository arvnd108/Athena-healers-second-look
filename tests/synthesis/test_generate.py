"""Offline tests for generate_synthesis -- fake LLMClient, real citation gate."""

from __future__ import annotations

from datetime import UTC, datetime

from secondlook.case.diff import Change, ChangeKind
from secondlook.case.questions import Question
from secondlook.signals.types import (
    ComputedMethod,
    DocumentedSource,
    EvidenceClass,
    RegulatorySource,
    Signal,
    SignalKind,
)
from secondlook.synthesis.generate import generate_synthesis
from secondlook.synthesis.llm_client import LLMClientError

FIXED_NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _change() -> Change:
    return Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M"},
        triggering_event_id="evt-1",
    )


def _question() -> Question:
    change = _change()
    return Question(
        kind=ChangeKind.NEW_ALTERATION,
        text="What documented evidence exists for EGFR T790M in NSCLC?",
        detail={"gene": "EGFR", "variant": "T790M", "cancer_type": "NSCLC"},
        priority=3,
        triggering_change=change,
    )


def _documented(
    claim: str = "EGFR T790M confers sensitivity to osimertinib.",
    item_id: str = "civic_12",
) -> Signal:
    return Signal(
        kind=SignalKind.DOCUMENTED_EVIDENCE,
        evidence_class=EvidenceClass.DOCUMENTED,
        claim=claim,
        source=DocumentedSource(
            citation_url=f"https://civicdb.org/evidence/{item_id}",
            citation_id=item_id,
        ),
        confidence="high",
        caveats=(),
        computed_at=FIXED_NOW,
        generator_version="test",
    )


def _computed() -> Signal:
    return Signal(
        kind=SignalKind.COMPUTATIONAL,
        evidence_class=EvidenceClass.COMPUTED,
        claim="Proximity to ligand is 3.1 Å.",
        source=ComputedMethod(method="proximity", version="graph.py/1"),
        confidence="stated",
        caveats=("measurement, not a validated classifier",),
        computed_at=FIXED_NOW,
        generator_version="test",
    )


class FakeLLM:
    def __init__(self, text: str | None = None, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        if self.error is not None:
            raise self.error
        assert self.text is not None
        return self.text


class TestDisabledPath:
    def test_llm_client_none_returns_nonempty_citable_text(self):
        result = generate_synthesis(
            _question(),
            [_documented()],
            llm_client=None,
            now=FIXED_NOW,
        )
        assert result.llm_used is False
        assert result.text
        assert "osimertinib" in result.text.lower()
        assert "[ref:civic_12]" in result.text
        assert result.citable_item_count == 1

    def test_multi_sentence_claim_survives_intact_on_the_disabled_path(self):
        # Regression: the template path used to route its own constructed
        # text through enforce_citations(), which splits on ANY sentence-
        # ending punctuation -- including punctuation inside one claim.
        # A multi-sentence CIViC summary (a real shape, not a contrived
        # one) would have every sentence but the last silently dropped,
        # miscounted as "uncited" when it was never uncited at all.
        signal = _documented(
            claim="Osimertinib is a third-generation EGFR TKI. "
            "It has shown efficacy against T790M.",
        )
        result = generate_synthesis(_question(), [signal], llm_client=None, now=FIXED_NOW)

        assert result.dropped_sentence_count == 0
        assert "third-generation EGFR TKI" in result.text
        assert "efficacy against T790M" in result.text
        assert result.cited_ids == ["civic_12"]

    def test_zero_citable_items_never_calls_complete(self):
        fake = FakeLLM(text="should not be used")
        result = generate_synthesis(
            _question(),
            [_computed()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert fake.calls == []
        assert result.llm_used is False
        assert result.text
        assert result.citable_item_count == 0


class TestLlmSuccessPath:
    def test_well_formed_refs_are_kept(self):
        fake = FakeLLM(
            text="Osimertinib sensitivity is documented in EGFR T790M NSCLC.[ref:civic_12]"
        )
        result = generate_synthesis(
            _question(),
            [_documented()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert result.llm_used is True
        assert result.cited_ids == ["civic_12"]
        assert result.dropped_sentence_count == 0
        assert len(fake.calls) == 1
        prompt, system = fake.calls[0]
        assert "What documented evidence exists for EGFR T790M in NSCLC?" in prompt
        assert system is not None
        assert "recommendation" in system.lower() or "recommend" in system.lower()

    def test_uncited_sentence_is_dropped_by_the_real_gate(self):
        fake = FakeLLM(
            text=(
                "Osimertinib sensitivity is documented.[ref:civic_12] "
                "This drug should be started immediately."
            )
        )
        result = generate_synthesis(
            _question(),
            [_documented()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert result.dropped_sentence_count == 1
        assert "started immediately" not in result.text
        assert "[ref:civic_12]" in result.text

    def test_unknown_ref_id_is_dropped(self):
        fake = FakeLLM(text="Invented claim.[ref:not_a_real_id]")
        result = generate_synthesis(
            _question(),
            [_documented()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert result.dropped_sentence_count == 1
        assert result.cited_ids == []


class TestClientErrorDegrades:
    def test_llm_client_error_falls_through_to_disabled_path(self):
        fake = FakeLLM(error=LLMClientError("API down"))
        result = generate_synthesis(
            _question(),
            [_documented()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert result.llm_used is False
        assert result.text
        assert "[ref:civic_12]" in result.text


class TestCitableFilter:
    def test_computed_signal_is_never_sent_to_the_client_or_cited(self):
        fake = FakeLLM(text="Osimertinib sensitivity is documented.[ref:civic_12]")
        result = generate_synthesis(
            _question(),
            [_computed(), _documented()],
            llm_client=fake,
            now=FIXED_NOW,
        )
        assert len(fake.calls) == 1
        prompt, _system = fake.calls[0]
        assert "3.1" not in prompt
        assert "civic_12" in prompt
        assert "proximity" not in result.cited_ids
        assert result.cited_ids == ["civic_12"]

    def test_regulatory_source_with_url_is_citable(self):
        regulatory = Signal(
            kind=SignalKind.ACCESS_PATHWAY,
            evidence_class=EvidenceClass.REGULATORY,
            claim="Expanded access is listed on the osimertinib label.",
            source=RegulatorySource(
                instrument="USPI",
                citation_url="https://dailymed.nlm.nih.gov/dailymed/osimertinib",
            ),
            confidence="stated",
            caveats=(),
            computed_at=FIXED_NOW,
            generator_version="test",
        )
        result = generate_synthesis(
            _question(),
            [regulatory],
            llm_client=None,
            now=FIXED_NOW,
        )
        assert result.citable_item_count == 1
        assert result.text
