"""Construction-time rules for the Signal envelope.

A documented signal with no citation URL is unconstructable -- same
structural refusal `tier1/retrieval.py`'s `_row_to_item` applies to
uncited evidence items. A computed signal cannot carry a citation-shaped
source. These are TypeError/ValueError at init, not a later validator.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from secondlook.signals.types import (
    CONFIDENCE_LEVELS,
    ComputedMethod,
    DocumentedSource,
    EvidenceClass,
    Signal,
    SignalKind,
)

FIXED_NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _documented(**overrides) -> Signal:
    kwargs = dict(
        kind=SignalKind.DOCUMENTED_EVIDENCE,
        evidence_class=EvidenceClass.DOCUMENTED,
        claim="EGFR T790M predicts osimertinib sensitivity.",
        source=DocumentedSource(citation_url="https://civicdb.org/evidence/238", citation_id="238"),
        confidence="high",
        caveats=(),
        computed_at=FIXED_NOW,
        generator_version="signals.types/test",
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


class TestDocumentedSourceRefusesEmptyCitation:
    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="citation_url"):
            DocumentedSource(citation_url="")

    def test_none_url_raises(self):
        with pytest.raises(ValueError, match="citation_url"):
            DocumentedSource(citation_url=None)  # type: ignore[arg-type]


class TestDocumentedSignalConstruction:
    def test_well_formed_signal_round_trips(self):
        signal = _documented()
        assert signal.evidence_class is EvidenceClass.DOCUMENTED
        assert isinstance(signal.source, DocumentedSource)
        assert signal.source.citation_url == "https://civicdb.org/evidence/238"

    def test_documented_class_without_documented_source_raises(self):
        with pytest.raises(ValueError, match="documented"):
            _documented(source=None)

    def test_documented_class_with_computed_method_raises(self):
        with pytest.raises(ValueError, match="documented"):
            _documented(source=ComputedMethod(method="mCSM-lig", version="1.0"))


class TestComputedSignalConstruction:
    def test_computed_requires_method_not_citation(self):
        signal = Signal(
            kind=SignalKind.COMPUTATIONAL,
            evidence_class=EvidenceClass.COMPUTED,
            claim="Proximity measurement to ligand: 3.1 Å.",
            source=ComputedMethod(method="proximity", version="graph.py/1"),
            confidence="stated",
            caveats=("measurement, not a validated classifier",),
            computed_at=FIXED_NOW,
            generator_version="signals.types/test",
        )
        assert isinstance(signal.source, ComputedMethod)
        assert not hasattr(signal.source, "citation_url")

    def test_computed_class_with_documented_source_raises(self):
        with pytest.raises(ValueError, match="computed"):
            Signal(
                kind=SignalKind.COMPUTATIONAL,
                evidence_class=EvidenceClass.COMPUTED,
                claim="should not construct",
                source=DocumentedSource(citation_url="https://example.org/x"),
                confidence="stated",
                caveats=(),
                computed_at=FIXED_NOW,
                generator_version="signals.types/test",
            )


class TestConfidenceAllowedSet:
    def test_unknown_confidence_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            _documented(confidence="pretty sure")

    def test_allowed_set_is_the_architecture_four(self):
        assert CONFIDENCE_LEVELS == frozenset({"stated", "low", "moderate", "high"})
