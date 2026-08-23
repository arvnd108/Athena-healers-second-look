#!/usr/bin/env python
"""Citation-gate accepted-sentence rate against a configured LLM.

Issue #10's open question -- which open-weight models produce adequate
citation-constrained synthesis -- is unvalidated. This script does not
answer it. It makes the gate runnable against whatever `get_llm_client()`
constructs (typically `OpenAICompatibleClient` pointed at a candidate
via ATHENA_LLM_BASE_URL / ATHENA_LLM_MODEL).

No model has been evaluated with this script yet. Running it, then writing
the date, model version, and accepted-sentence rate into
validation/results.md (same discipline as IMPLEMENTATION_PLAN.md §4.1's
threshold-tuning rule: write the criterion, then evaluate, never fit
afterward) is what turns the open question into an answered one.

Usage::

    ATHENA_LLM_PROVIDER=openai_compatible \\
    ATHENA_LLM_BASE_URL=http://localhost:8000/v1 \\
    ATHENA_LLM_MODEL=<candidate> \\
        python validation/synthesis_citation_acceptance.py
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from secondlook.case.diff import Change, ChangeKind  # noqa: E402
from secondlook.case.questions import Question  # noqa: E402
from secondlook.signals.types import (  # noqa: E402
    ComputedMethod,
    DocumentedSource,
    EvidenceClass,
    Signal,
    SignalKind,
)
from secondlook.synthesis.generate import generate_synthesis  # noqa: E402
from secondlook.synthesis.llm_client import get_llm_client  # noqa: E402

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _question(text: str) -> Question:
    change = Change(
        kind=ChangeKind.NEW_ALTERATION,
        summary="EGFR T790M newly observed",
        detail={"gene": "EGFR", "variant": "T790M"},
        triggering_event_id="evt-1",
    )
    return Question(
        kind=ChangeKind.NEW_ALTERATION,
        text=text,
        detail={"gene": "EGFR", "variant": "T790M", "cancer_type": "NSCLC"},
        priority=3,
        triggering_change=change,
    )


def _documented(item_id: str, claim: str) -> Signal:
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
        computed_at=NOW,
        generator_version="validation",
    )


def _computed() -> Signal:
    return Signal(
        kind=SignalKind.COMPUTATIONAL,
        evidence_class=EvidenceClass.COMPUTED,
        claim="Proximity to ligand is 3.1 Å.",
        source=ComputedMethod(method="proximity", version="graph.py/1"),
        confidence="stated",
        caveats=("measurement, not a validated classifier",),
        computed_at=NOW,
        generator_version="validation",
    )


# Synthetic fixtures -- not a gold-standard set. Enough to exercise the
# gate against a candidate model; expand only by adding fixtures, never
# by retuning the gate to fit a model's failures.
FIXTURES: tuple[tuple[str, Question, list[Signal]], ...] = (
    (
        "single_documented",
        _question("What documented evidence exists for EGFR T790M in NSCLC?"),
        [
            _documented(
                "civic_12",
                "EGFR T790M confers sensitivity to osimertinib.",
            )
        ],
    ),
    (
        "documented_plus_computed",
        _question("What documented evidence exists for EGFR T790M in NSCLC?"),
        [
            _computed(),
            _documented(
                "civic_12",
                "EGFR T790M confers sensitivity to osimertinib.",
            ),
        ],
    ),
)


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\]])\s+")


def _sentence_count(text: str) -> int:
    return sum(1 for part in _SENTENCE_BOUNDARY.split(text.strip()) if part.strip())


def _rate(dropped: int, total: int) -> float | None:
    if total == 0:
        return None
    return 1.0 - (dropped / total)


def main() -> int:
    client = get_llm_client()
    if client is None:
        print(
            "ATHENA_LLM_ENABLED is false (or unset to a falsey value). "
            "This script needs a live client to measure a model; the "
            "disabled path is already covered by tests/synthesis/test_generate.py."
        )
        return 2

    print(
        "No model has been evaluated with this script yet. "
        "Rates below are for this run only; write them down with the date "
        "and ATHENA_LLM_MODEL before treating the open question as answered."
    )
    rates: list[float] = []
    for name, question, signals in FIXTURES:
        result = generate_synthesis(question, signals, llm_client=client, now=NOW)
        accepted_n = _sentence_count(result.text)
        total = accepted_n + result.dropped_sentence_count
        rate = _rate(result.dropped_sentence_count, total)
        rates.append(rate if rate is not None else 0.0)
        rate_s = "n/a" if rate is None else f"{rate:.3f}"
        print(
            f"{name}: accepted_rate={rate_s} "
            f"dropped={result.dropped_sentence_count} total={total} "
            f"llm_used={result.llm_used} cited_ids={result.cited_ids}"
        )

    if rates:
        avg = sum(rates) / len(rates)
        print(f"average_accepted_rate={avg:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
