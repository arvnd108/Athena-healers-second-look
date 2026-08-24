"""Genomics signal generator -- wraps `tier1/retrieval.py`.

HARD RULE: this is a typed function, not an agent. Given a ChangeSet and
a Question it calls `retrieve_exact` (then `retrieve_relaxed` if exact
is empty) for each `NEW_ALTERATION` and maps every returned item to a
`documented` Signal. Two items that disagree on direction both become
Signals; this module does not pick a winner.

Why `SignalBatch` rather than `list[Signal]`: retrieve_exact already
returns `filtered_count` for uncited rows. A generator that found no
NEW_ALTERATION, or whose retrieval returned zero citable items, must
still say so -- `empty_reason` is that account. `dispatch()` concatenates
`.signals` into the issue's `(ChangeSet, Question) -> list[Signal]`
surface.

Confidence is a fixed map from CIViC evidence_level (see
`mapping.EVIDENCE_LEVEL_TO_CONFIDENCE`), not invented per call.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.mapping import (
    batch_from,
    clock,
    item_to_documented_signal,
    new_alterations,
    variant_match_key,
)
from secondlook.signals.types import Signal, SignalBatch
from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.retrieval import retrieve_exact, retrieve_relaxed

GENERATOR_VERSION = "signals.genomics/1"


def generate(
    change_set: ChangeSet,
    question: Question,
    *,
    graph=None,
    dgidb_client=None,  # shared injectable surface with dispatch(); unused here
    case=None,  # shared injectable surface with dispatch(); unused here
    now: datetime | None = None,
    retrieve_exact_fn=retrieve_exact,
    retrieve_relaxed_fn=retrieve_relaxed,
) -> SignalBatch:
    del dgidb_client
    when = clock(now)
    alterations = new_alterations(change_set, question)
    if not alterations:
        return batch_from(
            [],
            filtered_count=0,
            empty_reason="no NEW_ALTERATION in ChangeSet or Question.triggering_change",
        )

    graph = graph if graph is not None else connect_graph()
    signals: list[Signal] = []
    filtered_count = 0
    looked_up: list[str] = []

    for change in alterations:
        gene = change.detail.get("gene")
        variant = change.detail.get("variant")
        if not gene or not variant:
            filtered_count += 1
            continue
        looked_up.append(f"{gene} {variant}")
        hgvs_p, variant_name = variant_match_key(str(variant))
        exact = retrieve_exact_fn(str(gene), hgvs_p, variant_name=variant_name, graph=graph)
        filtered_count += exact.filtered_count
        result = exact
        if not exact.items:
            disease = question.detail.get("cancer_type")
            result = retrieve_relaxed_fn(
                str(gene),
                hgvs_p,
                disease,
                variant_name=variant_name,
                graph=graph,
            )
            filtered_count += result.filtered_count
        for item in result.items:
            signal = item_to_documented_signal(item, now=when, generator_version=GENERATOR_VERSION)
            if signal is None:
                filtered_count += 1
                continue
            signals.append(signal)

    scope = ", ".join(looked_up) or "NEW_ALTERATION"
    return batch_from(
        signals,
        filtered_count=filtered_count,
        empty_reason=(
            f"retrieve_exact/relaxed returned 0 citable items for {scope} "
            f"({filtered_count} filtered)"
        ),
    )
