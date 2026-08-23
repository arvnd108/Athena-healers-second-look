"""Automated Case Intake tests. Pure, offline -- runs entirely against
`StubExtractionBackend`, never a live model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from secondlook.case.models import EVENT_TYPES
from secondlook.intake.extraction import (
    LOW_CONFIDENCE_THRESHOLD,
    ClaudeExtractionBackend,
    StubExtractionBackend,
    extract_case_events,
)
from secondlook.intake.models import (
    DOCUMENT_TYPES,
    ExtractedEventCandidate,
    FieldValue,
    ProposedCaseEvent,
    UploadedDocument,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _doc(
    document_type: str = "sequencing", text: str = "synthetic report text"
) -> UploadedDocument:
    return UploadedDocument(document_id="doc-1", document_type=document_type, text=text)


# --- FieldValue / ProposedCaseEvent invariants ------------------------------


def test_field_value_rejects_confidence_outside_0_1():
    with pytest.raises(ValueError, match="confidence must be in"):
        FieldValue(value="EGFR", confidence=1.5)
    with pytest.raises(ValueError, match="confidence must be in"):
        FieldValue(value="EGFR", confidence=-0.1)


def test_field_value_accepts_boundary_confidences():
    FieldValue(value="EGFR", confidence=0.0)
    FieldValue(value="EGFR", confidence=1.0)


def test_proposed_case_event_status_cannot_be_anything_but_pending_confirmation():
    with pytest.raises(ValueError, match="pending_confirmation"):
        ProposedCaseEvent(
            proposed_id="p1",
            event_type="ALTERATION_OBSERVED",
            payload={},
            field_confidences={},
            low_confidence_fields=(),
            occurred_at=None,
            source_document_type="sequencing",
            generator_version="v1",
            status="confirmed",
        )


# --- extract_case_events: input validation ----------------------------------


def test_unknown_document_type_rejected():
    backend = StubExtractionBackend([])
    with pytest.raises(ValueError, match="Unknown document_type"):
        extract_case_events(_doc(document_type="xray"), backend, generator_version="v1")


def test_empty_generator_version_rejected():
    backend = StubExtractionBackend([])
    with pytest.raises(ValueError, match="generator_version is required"):
        extract_case_events(_doc(), backend, generator_version="")


def test_backend_returning_unknown_event_type_raises():
    candidate = ExtractedEventCandidate(event_type="NOT_A_REAL_TYPE", fields={})
    backend = StubExtractionBackend([candidate])
    with pytest.raises(ValueError, match="unknown event_type"):
        extract_case_events(_doc(), backend, generator_version="v1")


def test_all_document_types_are_accepted():
    backend = StubExtractionBackend([])
    for doc_type in DOCUMENT_TYPES:
        result = extract_case_events(_doc(document_type=doc_type), backend, generator_version="v1")
        assert result == []


# --- extract_case_events: mapping candidates to proposed events ------------


def test_extraction_maps_fields_and_confidences():
    candidate = ExtractedEventCandidate(
        event_type="ALTERATION_OBSERVED",
        fields={
            "gene": FieldValue("EGFR", confidence=0.95),
            "variant": FieldValue("T790M", confidence=0.9),
        },
        occurred_at="2026-02-14",
    )
    backend = StubExtractionBackend([candidate])
    result = extract_case_events(_doc(), backend, generator_version="v1")

    assert len(result) == 1
    event = result[0]
    assert event.event_type == "ALTERATION_OBSERVED"
    assert event.payload == {"gene": "EGFR", "variant": "T790M"}
    assert event.field_confidences == {"gene": 0.95, "variant": 0.9}
    assert event.occurred_at == "2026-02-14"
    assert event.status == "pending_confirmation"
    assert event.generator_version == "v1"
    assert event.source_document_type == "sequencing"


def test_low_confidence_fields_are_flagged_not_silently_accepted():
    candidate = ExtractedEventCandidate(
        event_type="ALTERATION_OBSERVED",
        fields={
            "gene": FieldValue("EGFR", confidence=0.95),
            "variant_type": FieldValue("missense", confidence=0.4),  # below default threshold
        },
    )
    backend = StubExtractionBackend([candidate])
    result = extract_case_events(_doc(), backend, generator_version="v1")

    assert result[0].low_confidence_fields == ("variant_type",)
    assert "gene" not in result[0].low_confidence_fields
    # The low-confidence value is still present in payload -- flagged for
    # review, not dropped.
    assert result[0].payload["variant_type"] == "missense"


def test_custom_low_confidence_threshold_is_respected():
    candidate = ExtractedEventCandidate(
        event_type="ALTERATION_OBSERVED",
        fields={"gene": FieldValue("EGFR", confidence=0.8)},
    )
    backend = StubExtractionBackend([candidate])
    result = extract_case_events(
        _doc(), backend, generator_version="v1", low_confidence_threshold=0.9
    )
    assert result[0].low_confidence_fields == ("gene",)


def test_default_low_confidence_threshold_is_documented_constant():
    assert LOW_CONFIDENCE_THRESHOLD == 0.7


def test_multiple_candidates_produce_multiple_proposed_events():
    candidates = [
        ExtractedEventCandidate(event_type="ALTERATION_OBSERVED", fields={}),
        ExtractedEventCandidate(event_type="BIOMARKER_MEASURED", fields={}),
    ]
    backend = StubExtractionBackend(candidates)
    result = extract_case_events(_doc(), backend, generator_version="v1")
    assert len(result) == 2
    assert {e.event_type for e in result} == {"ALTERATION_OBSERVED", "BIOMARKER_MEASURED"}


def test_proposed_ids_are_unique_per_call():
    candidates = [
        ExtractedEventCandidate(event_type="ALTERATION_OBSERVED", fields={}),
        ExtractedEventCandidate(event_type="ALTERATION_OBSERVED", fields={}),
    ]
    backend = StubExtractionBackend(candidates)
    result = extract_case_events(_doc(), backend, generator_version="v1")
    assert result[0].proposed_id != result[1].proposed_id


def test_no_candidates_produces_no_proposed_events():
    backend = StubExtractionBackend([])
    assert extract_case_events(_doc(), backend, generator_version="v1") == []


# --- privacy: raw document text never leaks into the output ----------------


def test_raw_document_text_never_appears_in_proposed_events():
    """UploadedDocument.text is consumed once and never persisted -- see
    UploadedDocument's docstring. The StubExtractionBackend here ignores
    its input entirely (as documented), which is itself part of the proof:
    nothing in extract_case_events() ever reads document.text back out.
    """
    secret_marker = "UNIQUE_MARKER_STRING_9f8e7d"
    document = _doc(text=f"Some clinical narrative containing {secret_marker}.")
    candidate = ExtractedEventCandidate(
        event_type="ALTERATION_OBSERVED", fields={"gene": FieldValue("EGFR", confidence=0.9)}
    )
    backend = StubExtractionBackend([candidate])
    result = extract_case_events(document, backend, generator_version="v1")

    assert secret_marker not in str(result)


# --- ClaudeExtractionBackend: exists, documented, deliberately unverified --


def test_claude_backend_can_be_constructed():
    ClaudeExtractionBackend()  # must not raise merely by instantiating


def test_claude_backend_raises_not_implemented_when_actually_called():
    """This is load-bearing, not an oversight: the class must not silently
    pretend to work. See its docstring for what's required before this
    changes.
    """
    backend = ClaudeExtractionBackend()
    with pytest.raises(NotImplementedError, match="documented skeleton"):
        backend.extract("some text", "sequencing")


def test_claude_backend_rejects_unknown_document_type_before_reaching_the_unimplemented_call():
    backend = ClaudeExtractionBackend()
    with pytest.raises(ValueError, match="No prompt defined"):
        backend.extract("some text", "xray")


# --- eval fixture set: shape validation + demonstration of the harness pattern --


def _load_eval_set() -> dict:
    with open(FIXTURES_DIR / "eval_set.yaml") as f:
        return yaml.safe_load(f)


def test_eval_set_loads_and_has_the_documented_schema():
    data = _load_eval_set()
    assert "config_version" in data
    assert len(data["cases"]) >= 2  # at least a couple of fixtures per doc type is the point


@pytest.mark.parametrize("case", _load_eval_set()["cases"], ids=lambda c: c["id"])
def test_eval_case_document_type_and_event_types_are_valid(case):
    """Validates the fixture data's own internal consistency -- every
    fixture must reference a real document_type and real event_types, so a
    typo in the eval set itself doesn't silently pass as "0 events
    expected." This is NOT an accuracy measurement (see module docstring
    below) -- it's a check that the eval set is well-formed and ready for
    subsystem P's harness to actually use.
    """
    assert case["document_type"] in DOCUMENT_TYPES
    for expected_event in case["expected_events"]:
        assert expected_event["event_type"] in EVENT_TYPES


def test_eval_set_demonstrates_the_harness_pattern_against_the_stub():
    """Runs each fixture's *expected* output back through
    extract_case_events() via a StubExtractionBackend seeded with exactly
    that expected output. This proves the eval set's shape is consumable
    by the real pipeline -- it does NOT measure real extraction accuracy,
    since the stub is seeded with the answer rather than actually reading
    `document_text`. Real accuracy measurement requires
    ClaudeExtractionBackend (or an equivalent working backend) and
    subsystem P's harness, neither of which exist yet.
    """
    data = _load_eval_set()
    for case in data["cases"]:
        candidates = [
            ExtractedEventCandidate(
                event_type=expected["event_type"],
                fields={
                    name: FieldValue(value, confidence=1.0)
                    for name, value in expected["fields"].items()
                },
            )
            for expected in case["expected_events"]
        ]
        backend = StubExtractionBackend(candidates)
        document = UploadedDocument(
            document_id=case["id"], document_type=case["document_type"], text=case["document_text"]
        )
        result = extract_case_events(document, backend, generator_version="eval-harness-demo")

        assert len(result) == len(case["expected_events"])
        for produced, expected in zip(result, case["expected_events"], strict=True):
            assert produced.event_type == expected["event_type"]
            assert produced.payload == expected["fields"]
