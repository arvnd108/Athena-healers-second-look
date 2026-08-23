"""Offline tests for the generic LLM eval runner.

No call-site knowledge: completion and scoring are injected fakes.
"""

from __future__ import annotations

import inspect

from secondlook.harness import llm_eval
from secondlook.harness.llm_eval import (
    EvalCase,
    run_eval_set,
    run_grounded_vs_ungrounded,
)


def _case(n: int = 0) -> EvalCase:
    return EvalCase(input={"n": n}, expected={"ok": True}, tolerance=None)


def _always_pass(expected: dict, actual: dict, tolerance: dict | None) -> tuple[bool, list[str]]:
    return True, []


def _echo(case_input: dict) -> dict:
    return dict(case_input)


def test_run_eval_set_all_pass_yields_pass():
    result = run_eval_set(
        [_case(1), _case(2)],
        subsystem="demo",
        prompt_template_id="demo/v1",
        completion_fn=_echo,
        score_fn=_always_pass,
        threshold=0.70,
    )
    assert result.verdict == "PASS"
    assert result.pass_rate == 1.0
    assert result.threshold == 0.70
    assert result.safety_violations == []
    assert result.subsystem == "demo"
    assert result.prompt_template_id == "demo/v1"


def test_run_eval_set_below_threshold_yields_fail():
    def score(expected, actual, tolerance):
        return actual["n"] == 1, []

    result = run_eval_set(
        [_case(1), _case(2), _case(3)],
        subsystem="demo",
        prompt_template_id="demo/v1",
        completion_fn=_echo,
        score_fn=score,
        threshold=0.70,
    )
    assert result.pass_rate == 1 / 3
    assert result.verdict == "FAIL"


def test_safety_violation_on_otherwise_perfect_set_is_blocked_not_pass():
    """A 100% pass rate with one safety violation is still blocked.

    Same precedence as validation.py HARD_REQUIRED_CONTROLS: the overall
    rate cannot override a named safety failure.
    """

    def score(expected, actual, tolerance):
        if actual["n"] == 2:
            return True, ["asserted_treatment_recommendation"]
        return True, []

    result = run_eval_set(
        [_case(1), _case(2)],
        subsystem="demo",
        prompt_template_id="demo/v1",
        completion_fn=_echo,
        score_fn=score,
        threshold=0.70,
    )
    assert result.pass_rate == 1.0
    assert result.verdict == "BLOCKED_BY_SAFETY_VIOLATION"
    assert "asserted_treatment_recommendation" in result.safety_violations


def test_empty_cases_is_zero_pass_rate_not_zerodivision():
    result = run_eval_set(
        [],
        subsystem="demo",
        prompt_template_id="demo/v1",
        completion_fn=_echo,
        score_fn=_always_pass,
        threshold=0.70,
    )
    assert result.pass_rate == 0.0
    assert result.verdict == "FAIL"
    assert result.safety_violations == []


def test_grounded_vs_ungrounded_positive_delta_when_grounded_is_better():
    # Same score_fn is required by the API; encode the two rates via
    # distinct completion outputs that one scorer can distinguish.
    def score(expected, actual, tolerance):
        return bool(actual.get("grounded")), []

    comparison = run_grounded_vs_ungrounded(
        [_case(1), _case(2)],
        subsystem="demo",
        prompt_template_id="demo/v1",
        grounded_completion_fn=lambda case_input: {"grounded": True},
        ungrounded_completion_fn=lambda case_input: {"grounded": False},
        score_fn=score,
        threshold=0.70,
    )
    assert comparison.grounded.pass_rate == 1.0
    assert comparison.ungrounded.pass_rate == 0.0
    assert comparison.pass_rate_delta == 1.0
    assert comparison.subsystem == "demo"


def test_grounded_vs_ungrounded_negative_delta_when_ungrounded_scores_higher():
    """Delta is grounded minus ungrounded, not an assumed lift.

    A tautological implementation that always reports a non-negative delta
    would pass the 'grounded is better' case and hide a retrieval that
    the model is ignoring.
    """

    def score(expected, actual, tolerance):
        return bool(actual.get("win")), []

    comparison = run_grounded_vs_ungrounded(
        [_case(1)],
        subsystem="demo",
        prompt_template_id="demo/v1",
        grounded_completion_fn=lambda case_input: {"win": False},
        ungrounded_completion_fn=lambda case_input: {"win": True},
        score_fn=score,
        threshold=0.0,
    )
    assert comparison.pass_rate_delta == -1.0
    assert comparison.grounded.pass_rate == 0.0
    assert comparison.ungrounded.pass_rate == 1.0


def test_generic_core_does_not_import_call_sites():
    import ast

    tree = ast.parse(inspect.getsource(llm_eval))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    blob = " ".join(imported)
    assert "secondlook.synthesis" not in blob
    assert "criteria_extraction" not in blob
    assert "generate_synthesis" not in blob
