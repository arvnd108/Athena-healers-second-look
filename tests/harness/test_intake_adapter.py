"""Offline tests for the intake eval harness adapter — fake LLMClient, no network."""

from __future__ import annotations

import importlib
import json

import pytest

from secondlook.harness.adapters.intake import (
    INTAKE_PROMPT_TEMPLATE_ID,
    INTAKE_SUBSYSTEM,
    INTAKE_THRESHOLD,
    _expected_fields_present,
    _load_eval_set,
    completion_fn,
    evaluate_eval_set,
    score_intake,
)
from secondlook.harness.llm_eval import EvalCase, run_eval_set


def test_threshold_is_precommitted_at_70_percent():
    assert INTAKE_THRESHOLD == 0.70


def test_expected_fields_present_allows_extra_produced_fields():
    expected = {"event_type": "BIOMARKER_MEASURED", "fields": {"name": "PD-L1", "value": 62.0}}
    produced = {
        "event_type": "BIOMARKER_MEASURED",
        "fields": {"name": "PD-L1", "value": 62, "unit": "%", "note": "extra"},
    }
    assert _expected_fields_present(expected, produced) is True


def test_expected_fields_present_numeric_type_tolerance():
    expected = {"event_type": "BIOMARKER_MEASURED", "fields": {"value": 62.0}}
    produced = {"event_type": "BIOMARKER_MEASURED", "fields": {"value": 62}}
    assert _expected_fields_present(expected, produced) is True


def test_expected_fields_present_fails_on_wrong_value():
    expected = {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "EGFR"}}
    produced = {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "KRAS"}}
    assert _expected_fields_present(expected, produced) is False


def test_expected_fields_present_fails_on_wrong_event_type():
    expected = {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "EGFR"}}
    produced = {"event_type": "BIOMARKER_MEASURED", "fields": {"gene": "EGFR"}}
    assert _expected_fields_present(expected, produced) is False


def test_score_intake_fails_on_missing_expected_event():
    expected = {
        "expected_events": [
            {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "EGFR"}},
            {"event_type": "BIOMARKER_MEASURED", "fields": {"name": "PD-L1"}},
        ],
        "document_text": "EGFR and PD-L1 mentioned here",
    }
    actual = {
        "events": [
            {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "EGFR"}},
        ]
    }
    passed, _violations = score_intake(expected, actual, None)
    assert passed is False


def test_fabricated_string_field_not_in_document_is_a_violation():
    expected = {"expected_events": [], "document_text": "NGS identified EGFR T790M."}
    actual = {
        "events": [
            {
                "event_type": "ALTERATION_OBSERVED",
                "fields": {"gene": "BRCA1"},
            }
        ]
    }
    _passed, violations = score_intake(expected, actual, None)
    assert "fabricated_field_value" in violations


def test_fabricated_check_skips_numeric_and_iso_date_fields():
    expected = {
        "expected_events": [],
        "document_text": "PD-L1 TPS was sixty-two percent on February 14, 2026.",
    }
    actual = {
        "events": [
            {
                "event_type": "BIOMARKER_MEASURED",
                "fields": {"value": 62.0, "measured_on": "2026-02-14", "name": "PD-L1"},
            }
        ]
    }
    _passed, violations = score_intake(expected, actual, None)
    assert "fabricated_field_value" not in violations


def test_string_field_present_in_document_is_not_fabricated():
    expected = {"expected_events": [], "document_text": "NGS identified EGFR T790M."}
    actual = {
        "events": [
            {"event_type": "ALTERATION_OBSERVED", "fields": {"gene": "EGFR"}},
        ]
    }
    _passed, violations = score_intake(expected, actual, None)
    assert violations == []


def test_load_eval_set_missing_path_raises_runtime_error(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(RuntimeError, match="not found"):
        _load_eval_set(missing)


def test_yaml_cache_starts_unset_on_fresh_import():
    import secondlook.harness.adapters.intake as intake_mod

    reloaded = importlib.reload(intake_mod)
    assert reloaded._eval_set_cache is None


def _expected_to_json(event: dict) -> dict:
    return {
        "event_type": event["event_type"],
        "fields": {
            name: {"value": value, "confidence": 1.0} for name, value in event["fields"].items()
        },
        "occurred_at": None,
    }


class _FixtureReplayingLLM:
    def __init__(self, cases: list[dict]):
        self.cases = cases

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        needle = prompt.strip()
        for case in self.cases:
            text = str(case["document_text"]).strip()
            if needle == text or text in needle:
                return json.dumps([_expected_to_json(event) for event in case["expected_events"]])
        raise AssertionError(f"no fixture matched prompt {prompt[:120]!r}")


def test_evaluate_eval_set_gold_replay_is_perfect_field_recall():
    """Harness-consuming-the-fixture proof: a backend that returns the
    labeled events scores pass_rate 1.0 through the real adapter path.

    Verdict is not asserted as PASS here: several gold labels are
    normalizations that do not appear as substrings of `document_text`
    (`T790M` vs `p.Thr790Met`, `stopped` vs `discontinued`, `IHC score`).
    The string-only `fabricated_field_value` check flags those by design
    — same false-positive class the date/number exclusion exists to
    avoid, applied to the fields the issue left in scope. Field recall
    is still perfect; live-model BLOCKED on this set is signal, not a
    harness wiring failure.
    """
    data = _load_eval_set()
    fake = _FixtureReplayingLLM(data["cases"])
    result = evaluate_eval_set(llm_client=fake)
    assert result.pass_rate == 1.0
    assert result.threshold == 0.70
    assert "fabricated_field_value" in result.safety_violations
    assert result.verdict == "BLOCKED_BY_SAFETY_VIOLATION"


def test_full_eval_path_passes_when_produced_strings_are_in_the_document():
    """The happy path the gold fixtures cannot currently exercise."""
    document = (
        "NGS Panel Report (synthetic). Tumor tissue NGS identified EGFR "
        "T790M, classified as missense. Assay: 50-gene solid tumor NGS panel."
    )
    expected_event = {
        "event_type": "ALTERATION_OBSERVED",
        "fields": {
            "gene": "EGFR",
            "variant": "T790M",
            "variant_type": "missense",
            "assay": "50-gene solid tumor NGS panel",
        },
    }

    class _Exact:
        def complete(self, prompt: str, *, system: str | None = None) -> str:
            return json.dumps([_expected_to_json(expected_event)])

    result = run_eval_set(
        [
            EvalCase(
                input={
                    "document_id": "in-text",
                    "document_type": "sequencing",
                    "text": document,
                },
                expected={
                    "expected_events": [expected_event],
                    "document_text": document,
                },
            )
        ],
        subsystem=INTAKE_SUBSYSTEM,
        prompt_template_id=INTAKE_PROMPT_TEMPLATE_ID,
        completion_fn=completion_fn(llm_client=_Exact()),
        score_fn=score_intake,
        threshold=INTAKE_THRESHOLD,
    )
    assert result.pass_rate == 1.0
    assert result.verdict == "PASS"
