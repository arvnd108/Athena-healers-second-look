#!/usr/bin/env python
"""Run held-out LLM eval sets and write an auditable results table.

Mirrors `validation/run_gold_standard.py`: pre-committed thresholds live
in the adapters; this script only runs and reports.

Usage::

    python validation/llm_eval_run.py
    python validation/llm_eval_run.py --subsystem synthesis
    python validation/llm_eval_run.py --subsystem synthesis --grounded-comparison
    python validation/llm_eval_run.py --subsystem criteria_extraction
    python validation/llm_eval_run.py --subsystem intake

Output: `validation/llm_eval_results.md`

Exit status is 0 only when every `EvalResult.verdict` is PASS (and, with
`--grounded-comparison`, when the grounded result is PASS).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from harness.eval_sets.synthesis import SYNTHESIS_EVAL_CASES  # noqa: E402

from secondlook.harness.adapters.criteria_extraction import (  # noqa: E402
    evaluate_existing_corpus,
)
from secondlook.harness.adapters.intake import (  # noqa: E402
    evaluate_eval_set as evaluate_intake_eval_set,
)
from secondlook.harness.adapters.synthesis import (  # noqa: E402
    SYNTHESIS_PROMPT_TEMPLATE_ID,
    SYNTHESIS_SUBSYSTEM,
    SYNTHESIS_THRESHOLD,
    grounded_completion_fn,
    score_synthesis,
    ungrounded_completion_fn,
)
from secondlook.harness.llm_eval import (  # noqa: E402
    EvalResult,
    GroundingComparison,
    render_markdown,
    run_eval_set,
    run_grounded_vs_ungrounded,
)
from secondlook.synthesis.llm_client import get_llm_client  # noqa: E402

RESULTS_PATH = REPO_ROOT / "validation" / "llm_eval_results.md"


def run_criteria_extraction() -> EvalResult:
    return evaluate_existing_corpus()


def run_synthesis_eval() -> EvalResult:
    client = get_llm_client()
    if client is None:
        raise RuntimeError(
            "synthesis eval needs a configured LLM client "
            "(ATHENA_LLM_ENABLED is off, or provider config is missing)"
        )
    return run_eval_set(
        SYNTHESIS_EVAL_CASES,
        subsystem=SYNTHESIS_SUBSYSTEM,
        prompt_template_id=SYNTHESIS_PROMPT_TEMPLATE_ID,
        completion_fn=grounded_completion_fn(llm_client=client),
        score_fn=score_synthesis,
        threshold=SYNTHESIS_THRESHOLD,
    )


def run_synthesis_comparison() -> GroundingComparison:
    client = get_llm_client()
    if client is None:
        raise RuntimeError(
            "synthesis grounding comparison needs a configured LLM client "
            "(ATHENA_LLM_ENABLED is off, or provider config is missing)"
        )
    return run_grounded_vs_ungrounded(
        SYNTHESIS_EVAL_CASES,
        subsystem=SYNTHESIS_SUBSYSTEM,
        prompt_template_id=SYNTHESIS_PROMPT_TEMPLATE_ID,
        grounded_completion_fn=grounded_completion_fn(llm_client=client),
        ungrounded_completion_fn=ungrounded_completion_fn(llm_client=client),
        score_fn=score_synthesis,
        threshold=SYNTHESIS_THRESHOLD,
    )


def run_intake_eval() -> EvalResult:
    client = get_llm_client()
    if client is None:
        raise RuntimeError(
            "intake eval needs a configured LLM client "
            "(ATHENA_LLM_ENABLED is off, or provider config is missing)"
        )
    return evaluate_intake_eval_set(llm_client=client)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subsystem",
        choices=["synthesis", "criteria_extraction", "intake", "all"],
        default="all",
    )
    parser.add_argument(
        "--grounded-comparison",
        action="store_true",
        help="run synthesis with and without retrieval (synthesis only)",
    )
    args = parser.parse_args(argv)

    if args.grounded_comparison and args.subsystem in {"criteria_extraction", "intake"}:
        print(
            f"error: --grounded-comparison is not valid with "
            f"--subsystem {args.subsystem}. {args.subsystem} has no "
            "ungrounded variant by design, not by omission.",
            file=sys.stderr,
        )
        return 2

    results: list[EvalResult] = []
    comparisons: list[GroundingComparison] = []

    try:
        if args.subsystem in {"synthesis", "all"}:
            if args.grounded_comparison:
                comparison = run_synthesis_comparison()
                comparisons.append(comparison)
                results.append(comparison.grounded)
            else:
                results.append(run_synthesis_eval())
        if args.subsystem in {"criteria_extraction", "all"}:
            results.append(run_criteria_extraction())
        if args.subsystem in {"intake", "all"}:
            results.append(run_intake_eval())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(render_markdown(results, comparisons=tuple(comparisons)))
    print(f"Wrote {RESULTS_PATH}")
    for result in results:
        print(f"{result.subsystem}: {result.verdict} ({result.pass_rate:.0%})")
    for comparison in comparisons:
        print(f"grounding delta ({comparison.subsystem}): " f"{comparison.pass_rate_delta:+.0%}")

    blocked = any(result.verdict != "PASS" for result in results)
    if any(comparison.grounded.verdict != "PASS" for comparison in comparisons):
        blocked = True
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
