"""Live integration tests for semantic_retrieval.py -- real
sentence-transformers model, real FalkorDB vector index.

Deselected by default:
    pytest -m integration tests/secondlook/tier1/test_semantic_retrieval_integration.py
"""

from __future__ import annotations

import uuid

import pytest

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.semantic_retrieval import (
    EMBEDDING_DIM,
    backfill_evidence_embeddings,
    embed_text,
    ensure_vector_indexes,
    retrieve_semantic,
    retrieve_semantic_for_gene,
)

pytestmark = pytest.mark.integration


def test_embed_text_returns_the_confirmed_dimensionality():
    vector = embed_text("CDK4 amplification confers sensitivity to palbociclib.")
    assert isinstance(vector, list)
    assert len(vector) == EMBEDDING_DIM
    assert all(isinstance(v, float) for v in vector)


def test_vector_search_ranks_the_semantically_closer_sentence_first():
    """The exact sanity check the phase spec asks for: embed two clearly
    different sentences, plus a query closer to one, confirm the vector
    search returns the closer one first -- proof the index does similarity
    search, not insertion order."""
    falkordb = pytest.importorskip("falkordb")
    db = falkordb.FalkorDB(host="localhost", port=6379)
    graph = db.select_graph(f"tier1_semtest_{uuid.uuid4().hex[:10]}")
    try:
        graph.create_node_vector_index(
            "SanityCheckNode", "vec", dim=EMBEDDING_DIM, similarity_function="cosine"
        )
        oncology = "NTRK gene fusion confers sensitivity to larotrectinib in sarcoma."
        weather = "It is sunny and warm outside today with a light breeze."
        graph.query(
            "CREATE (:SanityCheckNode {label: 'oncology', vec: vecf32($v)})",
            params={"v": embed_text(oncology)},
        )
        graph.query(
            "CREATE (:SanityCheckNode {label: 'weather', vec: vecf32($v)})",
            params={"v": embed_text(weather)},
        )

        query_vec = embed_text("Larotrectinib treats NTRK fusion-positive tumors.")
        rows = graph.query(
            "CALL db.idx.vector.queryNodes('SanityCheckNode', 'vec', 2, vecf32($q)) "
            "YIELD node, score RETURN node.label, score ORDER BY score ASC",
            params={"q": query_vec},
        ).result_set

        assert rows[0][0] == "oncology", "the oncology sentence must rank closer"
        assert rows[0][1] < rows[1][1], "closer match must have the lower distance score"
    finally:
        try:
            graph.delete()
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown only
            print(f"teardown: could not delete test graph: {type(exc).__name__}: {exc}")


@pytest.fixture(scope="module")
def graph():
    g = connect_graph()
    ensure_vector_indexes(g)
    backfill_evidence_embeddings(g)
    return g


def test_backfill_is_idempotent_on_the_real_graph(graph):
    first = backfill_evidence_embeddings(graph)
    assert first.considered == 0, "everything should already be embedded from the fixture"

    second = backfill_evidence_embeddings(graph)
    assert second.already_embedded == first.already_embedded


def test_semantic_search_surfaces_ntrk_evidence_for_an_ntrk_query(graph):
    result = retrieve_semantic(
        "NTRK gene fusion kinase inhibitor sensitivity", top_k=5, graph=graph
    )

    assert len(result.items) > 0
    assert result.filtered_count == 0
    drugs = {item["drug"] for item in result.items}
    assert drugs & {"Larotrectinib", "Entrectinib", "Crizotinib"}
    for item in result.items:
        assert item["retrieval_mode"] == "semantic"
        assert item["citation"]["url"]


def test_unrelated_query_ranks_worse_than_a_relevant_one(graph):
    relevant = retrieve_semantic("NTRK fusion kinase inhibitor", top_k=1, graph=graph)
    unrelated = retrieve_semantic(
        "stock market financial derivatives trading", top_k=1, graph=graph
    )

    assert relevant.items[0]["similarity_score"] < unrelated.items[0]["similarity_score"]


def test_semantic_for_gene_scopes_results_to_the_given_gene(graph):
    """Every returned item must be structurally connected to TP53 -- checked
    against the graph directly, not a hardcoded guess at which drugs that
    is, since which of TP53's evidence items land in the global top-K
    nearest neighbors varies by query phrasing."""
    actually_tp53_connected = {
        row[0]
        for row in graph.query(
            "MATCH (:Gene {symbol: 'TP53'})-[:HAS_VARIANT]->(:Variant)"
            "-[:PREDICTS_RESPONSE_TO]->(d:Drug) RETURN DISTINCT d.name"
        ).result_set
    }

    result = retrieve_semantic_for_gene(
        "TP53", "chemotherapy drug resistance", top_k=5, graph=graph
    )

    assert len(result.items) > 0
    for item in result.items:
        assert item["drug"] in actually_tp53_connected, (
            f"{item['drug']!r} is not actually connected to TP53 -- "
            "gene scoping is not filtering correctly"
        )
