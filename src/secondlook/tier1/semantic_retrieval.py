"""Tier 1 retrieval Mode 3 -- semantic (vector) search, per
tier1-implementation-spec.md SS4.

Separate module from retrieval.py, not an extension of it: Modes 1/2 are
lightweight Cypher lookups with no heavy dependencies, and importing this
module pulls in sentence-transformers (and torch underneath it) --
forcing that onto every caller of retrieve_exact()/retrieve_relaxed() would
be the same "heavy dependency an unrelated caller shouldn't need" problem
the secondlook/__init__.py namespace-package fix exists to avoid.

Same hard rule as Modes 1/2: an item with no citation.url is never
constructed or returned. Every filtered item is counted, never log-only.

Two indexes, two different states of readiness, checked against real data
rather than assumed:
  - EvidenceItem.summary_embedding: 46/46 EvidenceItems have real CIViC
    summary text today. Backfilling and searching this index is fully
    functional now.
  - Publication.abstract_embedding: 0/33 Publication nodes have abstract
    text. civic_loader.py only ever sets pmid/title/year -- fetching and
    storing the actual abstract is Phase 5's job (Literature RAG). The
    index is created here (an index can exist before any node populates
    it), and retrieve_semantic() is wired to search it, but it will return
    nothing until Phase 5 populates Publication.abstract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import (
    EVIDENCE_LEVELS,
    RESULT_EVIDENCE_LEVELS,
    RETRIEVAL_MODES,
    assert_valid,
    with_enrichment_provenance,
)

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # confirmed live against the installed model, not assumed
SIMILARITY_METRIC = "cosine"

_model = None  # lazy singleton -- loading takes ~30s, must not happen at import time


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("loading embedding model %s (one-time)", EMBEDDING_MODEL_NAME)
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Embed one string with the configured model. Returns a plain list of
    floats -- FalkorDB's vecf32() parameter requires a plain list, not a
    numpy array."""
    return _get_model().encode(text).tolist()


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def ensure_vector_indexes(graph) -> None:
    """Create both vector indexes if they don't already exist. Idempotent:
    FalkorDB raises a specific error for "already indexed", caught here
    rather than the caller needing to know to expect it."""
    for label, prop in (
        ("EvidenceItem", "summary_embedding"),
        ("Publication", "abstract_embedding"),
    ):
        try:
            graph.create_node_vector_index(
                label, prop, dim=EMBEDDING_DIM, similarity_function=SIMILARITY_METRIC
            )
            logger.info("created vector index on %s.%s", label, prop)
        except Exception as exc:  # noqa: BLE001 -- FalkorDB's "already indexed"
            # is a generic redis.ResponseError with no dedicated exception
            # type to catch narrowly; only "already indexed" is swallowed,
            # anything else re-raises.
            if "already indexed" not in str(exc).lower():
                raise
            logger.info("vector index on %s.%s already exists", label, prop)


# ---------------------------------------------------------------------------
# Embedding backfill -- separate, idempotent enrichment pass, matching
# chembl_enrich.py's pattern: different failure/compute domain (local model
# inference vs. an external API), so it must not be inlined into
# civic_loader.py's CIViC load.
# ---------------------------------------------------------------------------


@dataclass
class BackfillSummary:
    considered: int = 0
    already_embedded: int = 0
    embedded: int = 0

    def to_dict(self) -> dict:
        return {
            "considered": self.considered,
            "already_embedded": self.already_embedded,
            "embedded": self.embedded,
        }


def backfill_evidence_embeddings(graph) -> BackfillSummary:
    """Embed EvidenceItem.summary for every node missing summary_embedding.

    Batches the model.encode() call (fast) but writes one Cypher query per
    node (FalkorDB has no clean batch property-update for per-row distinct
    vectors without meaningfully more complexity) -- at today's scale
    (tens of items) this is sub-second either way.
    """
    summary = BackfillSummary()
    already = graph.query(
        "MATCH (e:EvidenceItem) WHERE e.summary_embedding IS NOT NULL RETURN count(e)"
    ).result_set[0][0]
    summary.already_embedded = already

    rows = graph.query(
        "MATCH (e:EvidenceItem) WHERE e.summary_embedding IS NULL "
        "AND e.summary IS NOT NULL AND e.summary <> '' "
        "RETURN e.civic_id, e.summary"
    ).result_set
    summary.considered = len(rows)
    if not rows:
        return summary

    model = _get_model()
    vectors = model.encode([summary_text for _civic_id, summary_text in rows]).tolist()
    retrieved_at = datetime.now(UTC).isoformat()

    for (civic_id, _summary_text), vector in zip(rows, vectors, strict=True):
        # with_enrichment_provenance() returns the property itself PLUS its
        # metadata (it's shaped for a plain `SET n += $props` merge). Here
        # the property (summary_embedding) is set separately via vecf32() --
        # a vector needs that explicit cast, a generic += merge can't do it
        # -- so the placeholder value must be dropped before use, or `e +=
        # $props` would immediately overwrite the correctly-typed vector
        # with a plain `null`. Only the three generated `*_source` /
        # `*_retrieved_at` / `*_source_version` keys are wanted here.
        props = with_enrichment_provenance(
            {"summary_embedding": None}, source=EMBEDDING_MODEL_NAME, retrieved_at=retrieved_at
        )
        del props["summary_embedding"]
        graph.query(
            "MATCH (e:EvidenceItem {civic_id: $id}) "
            "SET e.summary_embedding = vecf32($v), e += $props",
            params={"id": civic_id, "v": vector, "props": props},
        )
        summary.embedded += 1

    return summary


# ---------------------------------------------------------------------------
# Mode 3 -- semantic search
# ---------------------------------------------------------------------------


@dataclass
class RetrievalResult:
    items: list[dict] = field(default_factory=list)
    filtered_count: int = 0


_EVIDENCE_VECTOR_QUERY = """
CALL db.idx.vector.queryNodes('EvidenceItem', 'summary_embedding', $top_k, vecf32($query_vec))
YIELD node AS e, score
OPTIONAL MATCH (e)-[:SUPPORTS]->(v:Variant)-[:PREDICTS_RESPONSE_TO]->(d:Drug)
RETURN e.civic_id, e.evidence_level, e.citation_url, e.summary, score, d.name
ORDER BY score ASC
"""

# Structurally wired up per spec requirement 3, but returns nothing until
# Phase 5 populates Publication.abstract / abstract_embedding -- see the
# module docstring. Left in rather than omitted so retrieve_semantic()
# needs no changes when Phase 5 lands.
_PUBLICATION_VECTOR_QUERY = """
CALL db.idx.vector.queryNodes('Publication', 'abstract_embedding', $top_k, vecf32($query_vec))
YIELD node AS p, score
RETURN p.pmid, p.title, score
ORDER BY score ASC
"""


def _evidence_item_to_item(row) -> dict | None:
    civic_id, evidence_level, citation_url, summary_text, score, drug_name = row
    if not citation_url:
        return None
    assert_valid(evidence_level, EVIDENCE_LEVELS, "evidence_level")
    assert_valid("semantic", RETRIEVAL_MODES, "retrieval_mode")
    return {
        "type": "documented",
        "source": "CIViC",
        "evidence_level": evidence_level,
        "citation": {"id": str(civic_id), "url": citation_url},
        "summary": summary_text,
        "drug": drug_name,
        "trial_status": None,
        "retrieval_mode": "semantic",
        "similarity_score": score,
    }


def _publication_to_item(row) -> dict | None:
    """NOT exercised by any real data yet (see module docstring) -- returns
    a Tier 1 output item for a literature-only (PubMed) hit. evidence_level
    is "literature", validated against RESULT_EVIDENCE_LEVELS rather than
    EVIDENCE_LEVELS: "literature" is not a CIViC A-E grade, it describes an
    ungraded PubMed hit, so it's checked against the output-item-shape
    allowed set, not the graph-property allowed set. See
    graph_schema.RESULT_EVIDENCE_LEVELS docstring for the full distinction.
    """
    pmid, title, score = row
    citation_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None
    if not citation_url:
        return None
    evidence_level = "literature"
    assert_valid(evidence_level, RESULT_EVIDENCE_LEVELS, "evidence_level")
    return {
        "type": "documented",
        "source": "PubMed",
        "evidence_level": evidence_level,
        "citation": {"id": str(pmid), "url": citation_url},
        "summary": title,
        "drug": None,
        "trial_status": None,
        "retrieval_mode": "semantic",
        "similarity_score": score,
    }


def retrieve_semantic(query_text: str, top_k: int = 10, *, graph=None) -> RetrievalResult:
    """Mode 3: embed query_text, vector-search both indexes, return the
    combined, citation-filtered, score-sorted result.

    `source` reflects whichever node type actually matched (CIViC evidence
    summary vs. PubMed abstract) -- set per-item, not globally, since a
    single call can return both once Phase 5 populates Publication.
    """
    graph = graph if graph is not None else connect_graph()
    query_vec = embed_text(query_text)

    items: list[dict] = []
    filtered_count = 0

    evidence_rows = graph.query(
        _EVIDENCE_VECTOR_QUERY, params={"top_k": top_k, "query_vec": query_vec}
    ).result_set
    for row in evidence_rows:
        item = _evidence_item_to_item(row)
        if item is None:
            filtered_count += 1
            logger.warning("retrieve_semantic: dropped EvidenceItem %s -- no citation_url", row[0])
            continue
        items.append(item)

    publication_rows = graph.query(
        _PUBLICATION_VECTOR_QUERY, params={"top_k": top_k, "query_vec": query_vec}
    ).result_set
    for row in publication_rows:
        item = _publication_to_item(row)
        if item is None:
            filtered_count += 1
            logger.warning("retrieve_semantic: dropped Publication %s -- no citation_url", row[0])
            continue
        items.append(item)

    items.sort(key=lambda item: item["similarity_score"])
    return RetrievalResult(items=items[:top_k], filtered_count=filtered_count)


# ---------------------------------------------------------------------------
# Combined structured traversal + vector search, per spec requirement 4:
# "traverse from Gene node, then vector-search from there". A real,
# callable, tested function -- not just a comment -- since a query that
# only exists in a docstring can silently rot.
# ---------------------------------------------------------------------------

_GENE_SCOPED_VECTOR_QUERY = """
MATCH (gene:Gene {symbol: $gene})-[:HAS_VARIANT]->(v:Variant)
CALL db.idx.vector.queryNodes('EvidenceItem', 'summary_embedding', $top_k, vecf32($query_vec))
YIELD node AS e, score
MATCH (e)-[:SUPPORTS]->(v)
OPTIONAL MATCH (v)-[:PREDICTS_RESPONSE_TO]->(d:Drug)
RETURN e.civic_id, e.evidence_level, e.citation_url, e.summary, score, d.name
ORDER BY score ASC
"""


def retrieve_semantic_for_gene(
    gene: str, query_text: str, top_k: int = 10, *, graph=None
) -> RetrievalResult:
    """Semantic search scoped to evidence connected to a specific gene.

    FalkorDB's vector index is a global ANN scan -- there is no syntax to
    pre-filter the index itself by graph position, confirmed live. This
    query instead runs the structural traversal (Gene -> Variant) and the
    global vector search in the same Cypher statement, then joins on
    (EvidenceItem)-[:SUPPORTS]->(Variant) to keep only hits that actually
    connect to the traversed gene. Verified live: querying "CDK4
    amplification cell cycle inhibitor" scoped to TP53 correctly returns
    only the TP53-connected evidence item, not the four other top-K
    matches an unscoped search would include.
    """
    graph = graph if graph is not None else connect_graph()
    query_vec = embed_text(query_text)

    rows = graph.query(
        _GENE_SCOPED_VECTOR_QUERY, params={"gene": gene, "top_k": top_k, "query_vec": query_vec}
    ).result_set

    items: list[dict] = []
    filtered_count = 0
    for row in rows:
        item = _evidence_item_to_item(row)
        if item is None:
            filtered_count += 1
            continue
        items.append(item)

    items.sort(key=lambda item: item["similarity_score"])
    return RetrievalResult(items=items[:top_k], filtered_count=filtered_count)
