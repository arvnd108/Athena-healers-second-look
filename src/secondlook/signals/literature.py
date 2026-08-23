"""Literature signal generator -- wraps semantic retrieval, read-only.

HARD RULE: this generator has no write path. It does not call
`fetch_pubmed_abstracts` / `load_publication` -- those are batch loader
jobs. It only reads what's already in the graph (plus the embedding of
`Question.text` for the vector search).

`retrieve_semantic`/`retrieve_semantic_for_gene` touch only FalkorDB
(`graph.query`) and `embed_text` -- neither raises `PubmedApiError`
(that type belongs to `pubmed_loader.py`'s own HTTP calls, which this
module never invokes) or any other custom exception type of its own,
matching `tier1/retrieval.py`'s Mode 1/2 functions, which don't wrap
`graph.query` either. So nothing is caught here; any failure propagates
to the caller undisguised, per invariant #2 -- catching a type that
can't actually be raised by the call it wraps would be worse than
catching nothing, since it would misrepresent what failure modes this
generator actually handles.

When a gene is in scope, `retrieve_semantic_for_gene` scopes hits to
that gene's evidence. When it isn't (e.g. BIOMARKER_SHIFT), we fall back
to `retrieve_semantic(Question.text)` -- the question text is the query
the templates already filled.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.mapping import (
    batch_from,
    clock,
    gene_in_scope,
    item_to_documented_signal,
)
from secondlook.signals.types import Signal, SignalBatch
from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.semantic_retrieval import retrieve_semantic, retrieve_semantic_for_gene

GENERATOR_VERSION = "signals.literature/1"


def generate(
    change_set: ChangeSet,
    question: Question,
    *,
    graph=None,
    dgidb_client=None,
    now: datetime | None = None,
    retrieve_for_gene_fn=retrieve_semantic_for_gene,
    retrieve_fn=retrieve_semantic,
) -> SignalBatch:
    del dgidb_client
    when = clock(now)
    graph = graph if graph is not None else connect_graph()
    gene = gene_in_scope(change_set, question)
    query_text = question.text

    if gene:
        result = retrieve_for_gene_fn(gene, query_text, graph=graph)
    else:
        result = retrieve_fn(query_text, graph=graph)

    signals: list[Signal] = []
    filtered_count = result.filtered_count
    for item in result.items:
        signal = item_to_documented_signal(item, now=when, generator_version=GENERATOR_VERSION)
        if signal is None:
            filtered_count += 1
            continue
        signals.append(signal)

    scope = f"gene={gene}" if gene else f"query={query_text!r}"
    return batch_from(
        signals,
        filtered_count=filtered_count,
        empty_reason=f"semantic retrieve returned 0 citable items ({scope})",
    )
