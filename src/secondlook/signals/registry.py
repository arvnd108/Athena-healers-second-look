"""Typed dispatch table -- not an orchestrator.

HARD RULE: which generators run for a `ChangeKind` is this dict, full
stop. Nothing here reads the clock, draws a random number, or asks an
LLM which specialist to call. If you find yourself writing a loop where
generator A's output decides whether generator B runs, stop -- that is
the nondeterminism CONCEPT_EVALUATION.md §3 exists to keep out.

`dispatch(change_set, question) -> list[Signal]` matches issue #6's
stated interface. Each mapped generator is called, in table order, and
its `.signals` are concatenated. Signals are never merged or deduplicated
*across* generators -- two generators contradicting each other is the
disagreement the UI must render, and collapsing it here would be the
false consensus the redesign forbids. Dedup inside one generator's own
output is that generator's business.

Mapping rationale, keyed off IMPLEMENTATION_PLAN.md §6 QUESTION_TEMPLATES:

- NEW_ALTERATION asks about documented evidence, recruiting trials, and
  resistance to the current regimen -> genomics + literature +
  pharmacology + trials.
- BIOMARKER_SHIFT asks what the crossing implies for treatment options
  -> literature + pharmacology (no new variant for genomics/trials to
  key off).
- DISEASE_PROGRESSION asks about documented resistance mechanisms ->
  literature + pharmacology + genomics (genomics still keys off any
  NEW_ALTERATION sitting on the same ChangeSet; if there isn't one it
  returns empty-with-reason).
- TREATMENT_LINE_CHANGE asks what is documented after this line ->
  literature + pharmacology + trials.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from secondlook.case.diff import ChangeKind, ChangeSet
from secondlook.case.questions import Question
from secondlook.signals import genomics, literature, pharmacology, trials
from secondlook.signals.types import Signal, SignalBatch

Generator = Callable[..., SignalBatch]

GENERATORS: dict[ChangeKind, tuple[Generator, ...]] = {
    ChangeKind.NEW_ALTERATION: (
        genomics.generate,
        literature.generate,
        pharmacology.generate,
        trials.generate,
    ),
    ChangeKind.BIOMARKER_SHIFT: (
        literature.generate,
        pharmacology.generate,
    ),
    ChangeKind.DISEASE_PROGRESSION: (
        genomics.generate,
        literature.generate,
        pharmacology.generate,
    ),
    ChangeKind.TREATMENT_LINE_CHANGE: (
        literature.generate,
        pharmacology.generate,
        trials.generate,
    ),
}


def dispatch(
    change_set: ChangeSet,
    question: Question,
    *,
    registry: dict[ChangeKind, tuple[Generator, ...]] | None = None,
    graph=None,
    dgidb_client=None,
    case=None,
    now: datetime | None = None,
) -> list[Signal]:
    """Look up `question.kind` in the table and call every mapped generator.

    `registry` is injectable so tests can spy; production callers omit it
    and get `GENERATORS`. Client handles (`graph`, `dgidb_client`, `now`)
    are forwarded identically to every generator -- dispatch does not
    decide which generator "needs" which client.
    """
    table = registry if registry is not None else GENERATORS
    generators = table[question.kind]
    signals: list[Signal] = []
    for generate in generators:
        batch = generate(
            change_set,
            question,
            graph=graph,
            dgidb_client=dgidb_client,
            case=case,
            now=now,
        )
        signals.extend(batch.signals)
    return signals
