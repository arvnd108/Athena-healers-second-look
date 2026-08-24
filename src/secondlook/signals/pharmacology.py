"""Pharmacology signal generator -- DGIdb live, ChEMBL drug_class read-only.

HARD RULE: do not call `enrich_drug_classes()`. That function is a
write-path batch job: it hits ChEMBL's rate-limited API per unresolved
Drug and writes `drug_class` onto the graph. At signal-generation time
we *read* `Drug.drug_class` that the batch job already wrote.

Evidence class for a DGIdb hit: **contextual**. `DgidbClient.fetch_drugs`
returns `{name, approved, score}` -- no per-interaction citation URL or
resolvable source identifier. ARCHITECTURE.md §5 says `documented`
requires a citation always shown and clickable. We cannot honestly
attach one per row, so these are `cohort_context` / `contextual`, with a
caveat naming DGIdb as a curated-but-uncited-per-item aggregation.
(If a future client version carries a resolvable interaction URL, that
path can become `documented` without changing this function's
signature.)

ChEMBL `drug_class` on the node is the same situation -- a batch
enrichment property, not a paper -- so it is also `contextual`.

DgidbError is caught at the `fetch_drugs` call site and surfaced in
`empty_reason`. ChemblApiError is not caught: we never call ChEMBL here,
and swallowing it would hide a regression that reintroduced the write
path. Any other exception propagates.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet
from secondlook.case.questions import Question
from secondlook.dgidb import DgidbClient, DgidbError
from secondlook.signals.mapping import batch_from, clock, gene_in_scope
from secondlook.signals.types import EvidenceClass, Signal, SignalBatch, SignalKind
from secondlook.tier1.graph_connection import connect_graph

GENERATOR_VERSION = "signals.pharmacology/1"

_DRUG_CLASS_QUERY = """
MATCH (g:Gene {symbol: $gene})-[:HAS_VARIANT]->(v:Variant)
      -[:PREDICTS_RESPONSE_TO]->(d:Drug)
WHERE d.drug_class IS NOT NULL
RETURN DISTINCT d.name, d.drug_class
ORDER BY d.name
"""


def generate(
    change_set: ChangeSet,
    question: Question,
    *,
    graph=None,
    dgidb_client: DgidbClient | None = None,
    case=None,  # shared injectable surface with dispatch(); unused here
    now: datetime | None = None,
) -> SignalBatch:
    when = clock(now)
    gene = gene_in_scope(change_set, question)
    if not gene:
        return batch_from(
            [],
            filtered_count=0,
            empty_reason="no gene in scope on ChangeSet or Question",
        )

    graph = graph if graph is not None else connect_graph()
    client = dgidb_client if dgidb_client is not None else DgidbClient()

    dgidb_error: DgidbError | None = None
    try:
        interactions = client.fetch_drugs(gene)
    except DgidbError as exc:
        dgidb_error = exc
        interactions = []

    class_rows = graph.query(_DRUG_CLASS_QUERY, params={"gene": gene}).result_set

    signals: list[Signal] = []
    seen_drugs: set[str] = set()

    for row in sorted(interactions, key=lambda r: str(r.get("name") or "")):
        name = row.get("name")
        if not name or name in seen_drugs:
            continue
        seen_drugs.add(name)
        approved = "approved" if row.get("approved") else "not marked approved"
        score = row.get("score")
        claim = f"DGIdb lists {name} as an interaction with {gene} " f"({approved}, score={score})."
        signals.append(
            Signal(
                kind=SignalKind.COHORT_CONTEXT,
                evidence_class=EvidenceClass.CONTEXTUAL,
                claim=claim,
                source=None,
                confidence="stated",
                caveats=(
                    "DGIdb is a curated aggregation; no per-interaction citation URL "
                    "is available, so this is contextual, not documented.",
                ),
                computed_at=when,
                generator_version=GENERATOR_VERSION,
            )
        )

    for drug_name, drug_class in class_rows:
        if not drug_name or not drug_class:
            continue
        signals.append(
            Signal(
                kind=SignalKind.COHORT_CONTEXT,
                evidence_class=EvidenceClass.CONTEXTUAL,
                claim=f"{drug_name} is annotated on the graph as drug_class={drug_class!r}.",
                source=None,
                confidence="stated",
                caveats=(
                    "drug_class was written by the chembl_enrich batch job; this "
                    "generator only reads it.",
                ),
                computed_at=when,
                generator_version=GENERATOR_VERSION,
            )
        )

    if signals:
        return batch_from(signals, filtered_count=0, empty_reason="unused")

    if dgidb_error is not None:
        reason = f"DgidbError for gene={gene}: {dgidb_error}"
    else:
        reason = f"DGIdb returned no interactions and no Drug.drug_class for gene={gene}"
    return batch_from([], filtered_count=0, empty_reason=reason)
