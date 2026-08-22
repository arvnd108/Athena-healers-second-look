"""Classification logic for the pre-committed proximity criterion.

The criterion itself lives in docs/validation-plan.md and must not be edited
to fit these tests. Synthetic distances only — the gold-standard numbers are
the evaluation, not the unit test.
"""

import sys
from pathlib import Path

_VALIDATION = Path(__file__).resolve().parent.parent / "validation"
if str(_VALIDATION) not in sys.path:
    sys.path.insert(0, str(_VALIDATION))

from evaluate_proximity_criterion import (  # noqa: E402
    evaluate_case,
    overall_verdict,
)


def test_contact_mediated_correctly_banded_passes():
    result = evaluate_case(mechanism="contact_mediated", band="in_contact")
    assert result == "pass"
    result = evaluate_case(mechanism="contact_mediated", band="pocket_adjacent")
    assert result == "pass"


def test_contact_mediated_banded_distant_fails():
    result = evaluate_case(mechanism="contact_mediated", band="distant")
    assert result == "fail"


def test_ambiguous_case_is_excluded_not_counted_either_way():
    result = evaluate_case(mechanism="ambiguous", band="distant")
    assert result == "excluded"
    result = evaluate_case(mechanism="ambiguous", band="in_contact")
    assert result == "excluded"

    verdict = overall_verdict(["pass", "pass", "excluded"])
    assert verdict.passed is True
    assert verdict.n_scored == 2
    assert verdict.n_correct == 2
    assert verdict.n_excluded == 1
    assert verdict.n_fail == 0


def test_one_failing_scored_case_fails_overall_even_with_exclusions():
    verdict = overall_verdict(["pass", "fail", "excluded"])
    assert verdict.passed is False
    assert verdict.n_scored == 2
    assert verdict.n_correct == 1
    assert verdict.n_fail == 1
    assert verdict.n_excluded == 1
