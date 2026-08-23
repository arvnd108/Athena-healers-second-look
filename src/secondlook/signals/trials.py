"""Trials signal generator -- Cypher against the Trial label.

The Trial Intelligence & Eligibility Matcher (issue #7) and
`ctgov_loader.py` (issue #8) do not exist yet, so the live graph has
zero Trial nodes. This function still runs a real, parameterized query
against the schema issue #2 already landed (`TRIAL`, `TRIAL_STATUSES`,
`TARGETS_BIOMARKER`, `RECRUITS_FOR`). It returns `[]` today because the
query returns zero rows -- the honest structural reason -- not because
of a hand-written stub. The moment issue #8's loader writes Trial
nodes, this file starts returning live Signals with no code change.

Actionable statuses (a documented subset of `TRIAL_STATUSES`):
`RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`. Anything
else (TERMINATED, COMPLETED, ...) is dropped and counted. The allowlist
is applied in Cypher *and* in Python so a mocked row set that includes
a TERMINATED row is filtered the same way the live graph will be.

A Trial row with no `eligibility_url` and no `registry_id` cannot become
a documented Signal (no citation URL) and is counted in `filtered_count`.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.mapping import batch_from, clock, gene_in_scope
from secondlook.signals.types import (
    DocumentedSource,
    EvidenceClass,
    Signal,
    SignalBatch,
    SignalKind,
)
from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import TRIAL_STATUSES, assert_valid

GENERATOR_VERSION = "signals.trials/1"

# Statuses a patient can still be referred to. Kept as a frozenset subset
# of TRIAL_STATUSES so an upstream typo fails `assert_valid` rather than
# rendering as "not recruiting".
ACTIONABLE_TRIAL_STATUSES: frozenset[str] = frozenset(
    {
        "RECRUITING",
        "NOT_YET_RECRUITING",
        "ENROLLING_BY_INVITATION",
    }
)
assert ACTIONABLE_TRIAL_STATUSES <= TRIAL_STATUSES

_RETURN = (
    "RETURN t.registry_id, t.registry, t.status, t.phase, "
    "t.brief_title, t.eligibility_url "
    "ORDER BY t.registry_id"
)

_TRIALS_BY_GENE = f"""
MATCH (t:Trial)-[:TARGETS_BIOMARKER]->(g:Gene {{symbol: $gene}})
WHERE t.status IN $actionable_statuses
{_RETURN}
"""

_TRIALS_BY_GENE_AND_DISEASE = f"""
MATCH (t:Trial)-[:TARGETS_BIOMARKER]->(g:Gene {{symbol: $gene}})
MATCH (t)-[:RECRUITS_FOR]->(d:Disease {{name: $disease}})
WHERE t.status IN $actionable_statuses
{_RETURN}
"""


def generate(
    change_set: ChangeSet,
    question: Question,
    *,
    graph=None,
    dgidb_client=None,
    now: datetime | None = None,
) -> SignalBatch:
    del dgidb_client
    when = clock(now)
    gene = gene_in_scope(change_set, question)
    if not gene:
        return batch_from(
            [],
            filtered_count=0,
            empty_reason="no gene in scope to match Trial-[:TARGETS_BIOMARKER]->Gene",
        )

    graph = graph if graph is not None else connect_graph()
    disease = question.detail.get("cancer_type")
    params = {
        "gene": gene,
        "actionable_statuses": sorted(ACTIONABLE_TRIAL_STATUSES),
    }
    if disease:
        params["disease"] = disease
        result = graph.query(_TRIALS_BY_GENE_AND_DISEASE, params=params)
    else:
        result = graph.query(_TRIALS_BY_GENE, params=params)

    signals: list[Signal] = []
    filtered_count = 0
    seen: set[str] = set()

    for row in result.result_set:
        registry_id, registry, status, phase, brief_title, eligibility_url = row
        assert_valid(str(status) if status is not None else "", TRIAL_STATUSES, "status")
        if status not in ACTIONABLE_TRIAL_STATUSES:
            filtered_count += 1
            continue
        citation_url = eligibility_url or (
            f"https://clinicaltrials.gov/study/{registry_id}" if registry_id else None
        )
        if not citation_url:
            filtered_count += 1
            continue
        ident = str(registry_id or citation_url)
        if ident in seen:
            continue
        seen.add(ident)
        title = brief_title or ident
        phase_bit = f", {phase}" if phase else ""
        registry_bit = registry or "trial registry"
        claim = f"{title} ({ident}{phase_bit}) is {status} on {registry_bit}."
        signals.append(
            Signal(
                kind=SignalKind.TRIAL,
                evidence_class=EvidenceClass.DOCUMENTED,
                claim=claim,
                source=DocumentedSource(
                    citation_url=str(citation_url),
                    citation_id=str(registry_id) if registry_id else None,
                ),
                confidence="stated",
                caveats=(),
                computed_at=when,
                generator_version=GENERATOR_VERSION,
            )
        )

    return batch_from(
        signals,
        filtered_count=filtered_count,
        empty_reason=(
            f"no Trial rows for gene={gene} after actionable-status filter "
            f"({filtered_count} filtered)"
        ),
    )
