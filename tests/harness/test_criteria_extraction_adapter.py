"""Offline tests for wrapping trial_extraction_accuracy.Report into EvalResult."""

from __future__ import annotations

import sys
from pathlib import Path

_VALIDATION = Path(__file__).resolve().parents[2] / "validation"
if str(_VALIDATION) not in sys.path:
    sys.path.insert(0, str(_VALIDATION))

from trial_extraction_accuracy import LineOutcome, Report  # noqa: E402

from secondlook.harness.adapters.criteria_extraction import (  # noqa: E402
    _load_trial_extraction_accuracy,
    from_trial_extraction_report,
)


def _outcome(expected: str, actual: str, *, line: str = "line") -> LineOutcome:
    return LineOutcome(
        registry_id="NCT00000000",
        line=line,
        expected=expected,
        actual=actual,
        expected_section="inclusion",
        actual_section="inclusion",
    )


def test_wrong_predicate_blocks_regardless_of_high_c2_recall():
    """High regular-type recall cannot override a C1 wrong extraction.

    Constructed by hand so the block is not just re-deriving Report.passed.
    """
    outcomes = [_outcome("AGE_RANGE", "AGE_RANGE", line=f"age {i}") for i in range(9)]
    outcomes.append(_outcome("ECOG_MAX", "AGE_RANGE", line="ecog wrong bucket"))
    report = Report(
        outcomes=outcomes,
        counts={"NCT00000000": (10, 10)},
        fixtures=1,
    )
    correct, total = report.c2_regular_recall
    assert total > 0
    assert correct / total >= 0.70
    assert report.c1_no_wrong is False

    result = from_trial_extraction_report(report)
    assert "wrong_predicate_type_present" in result.safety_violations
    assert result.verdict == "BLOCKED_BY_SAFETY_VIOLATION"
    assert result.pass_rate >= result.threshold


def test_silent_drop_is_a_safety_violation():
    report = Report(
        outcomes=[_outcome("AGE_RANGE", "AGE_RANGE")],
        counts={"NCT00000000": (2, 1)},
        fixtures=1,
    )
    assert report.c3_nothing_dropped is False
    result = from_trial_extraction_report(report)
    assert "criterion_silently_dropped" in result.safety_violations
    assert result.verdict == "BLOCKED_BY_SAFETY_VIOLATION"


def test_validator_loads_when_not_already_imported():
    """The live runner does not pre-import trial_extraction_accuracy."""
    sys.modules.pop("trial_extraction_accuracy", None)
    module = _load_trial_extraction_accuracy()
    assert module.C2_THRESHOLD == 0.70
    assert "trial_extraction_accuracy" in sys.modules
