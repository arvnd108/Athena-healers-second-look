"""Criteria-extraction adapter: wrap the existing pre-committed validator.

This module does **not** reimplement C1–C4. Those criteria live in
`validation/trial_extraction_accuracy.py` and
`docs/trial-extraction-validation-plan.md` and stay exactly as written.

The adapter maps that script's `Report` into the harness `EvalResult`
shape so extraction can be reported alongside synthesis. There is no
`completion_fn` / `score_fn` pair here: wrapping `Report` reuses the
already-reviewed scorer, and a second path through `run_eval_set` would
only recompute the same numbers.

There is no ungrounded variant. Criteria extraction's job is to structure
the text it is given; "extract this text without the text" is not a
meaningful condition.

Safety-violation categories are intentionally narrow and will grow when
issue #18 (guideline concordance) lands. This list does not claim
completeness.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Literal

from secondlook.harness.llm_eval import EvalResult, derive_verdict

CriteriaExtractionViolation = Literal[
    "wrong_predicate_type_present",
    "criterion_silently_dropped",
]


def _load_trial_extraction_accuracy():
    """Load the existing validator by path so it is not rewritten or copied."""
    repo_root = Path(__file__).resolve().parents[4]
    path = repo_root / "validation" / "trial_extraction_accuracy.py"
    spec = importlib.util.spec_from_file_location("trial_extraction_accuracy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass looks up the class module in sys.modules during decoration;
    # without this registration the live runner crashes before scoring.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_tea = _load_trial_extraction_accuracy()

#: Same number, same criterion as trial_extraction_accuracy.C2_THRESHOLD.
#: Do not invent a different threshold for the same measurement.
CRITERIA_EXTRACTION_THRESHOLD: float = _tea.C2_THRESHOLD

CRITERIA_EXTRACTION_SUBSYSTEM = "criteria_extraction"
# The existing validator scores RuleBasedExtractor. When that script grows
# an LLM-assisted run, bump this id; do not silently retag historical
# reports as a different method.
CRITERIA_EXTRACTION_PROMPT_TEMPLATE_ID = "criteria_extraction/rule_based"


def evaluate_existing_corpus() -> EvalResult:
    """Run the existing fixture corpus through the wrapped validator."""
    return from_trial_extraction_report(_tea.score(_tea.load_fixtures()))


def from_trial_extraction_report(report: Any) -> EvalResult:
    """Map a `trial_extraction_accuracy.Report` onto `EvalResult`.

    pass_rate is C2 regular-type recall (vacuous 1.0 when there are no
    AGE_RANGE/ECOG_MAX lines, matching `Report.passed`). C1 wrong buckets
    and C3 silent drops become named safety violations and therefore a
    BLOCKED verdict via `derive_verdict` -- not a local copy of that
    precedence. C4 remains owned by the original script's own exit code.
    """
    correct, total = report.c2_regular_recall
    pass_rate = 1.0 if total == 0 else correct / total
    violations: list[str] = []
    if not report.c1_no_wrong:
        violations.append("wrong_predicate_type_present")
    if not report.c3_nothing_dropped:
        violations.append("criterion_silently_dropped")
    return EvalResult(
        subsystem=CRITERIA_EXTRACTION_SUBSYSTEM,
        prompt_template_id=CRITERIA_EXTRACTION_PROMPT_TEMPLATE_ID,
        pass_rate=pass_rate,
        threshold=CRITERIA_EXTRACTION_THRESHOLD,
        safety_violations=violations,
        verdict=derive_verdict(pass_rate, CRITERIA_EXTRACTION_THRESHOLD, violations),
    )
