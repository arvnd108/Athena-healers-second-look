"""Live integration tests for pubmed_loader.py -- real PubMed E-utilities,
real FalkorDB, real embedding model.

Deselected by default:
    pytest -m integration tests/secondlook/tier1/test_pubmed_loader_integration.py
"""

from __future__ import annotations

import pytest

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.pubmed_loader import (
    fetch_pubmed_abstracts,
    load_publication,
    load_publications_for_query,
)
from secondlook.tier1.semantic_retrieval import ensure_vector_indexes, retrieve_semantic

pytestmark = pytest.mark.integration

NARROW_QUERY = "NTRK1 fusion sarcoma larotrectinib"


def test_fetch_returns_real_abstracts_with_text():
    results = fetch_pubmed_abstracts(NARROW_QUERY, max_results=5)

    assert len(results) > 0
    with_abstract = [r for r in results if r["abstract"]]
    assert with_abstract, "expected at least one real result to carry abstract text"
    sample = with_abstract[0]
    assert sample["pmid"].isdigit()
    assert sample["year"] is not None


def test_load_publication_is_idempotent_on_the_real_graph():
    graph = connect_graph()
    results = fetch_pubmed_abstracts(NARROW_QUERY, max_results=3)
    with_abstract = [r for r in results if r["abstract"]]
    assert with_abstract, "need at least one real abstract to test against"
    pub = with_abstract[0]

    first = load_publication(pub, graph=graph)
    count_after_first = graph.query(
        "MATCH (p:Publication {pmid: $pmid}) RETURN count(p)", params={"pmid": pub["pmid"]}
    ).result_set[0][0]
    second = load_publication(pub, graph=graph)
    count_after_second = graph.query(
        "MATCH (p:Publication {pmid: $pmid}) RETURN count(p)", params={"pmid": pub["pmid"]}
    ).result_set[0][0]

    assert first is True
    assert second is True
    assert count_after_first == 1
    assert count_after_second == 1, "MERGE must not create a duplicate node on rerun"


def test_end_to_end_literature_hit_surfaces_through_retrieve_semantic():
    """The capstone proof: the 0/33-populated gap flagged in Phase 4 is
    actually closed, not just that the code compiles. Fetches a real,
    narrow PubMed query, loads it, and confirms retrieve_semantic() (built
    in Phase 4, untouched here) now returns a real PubMed-sourced item."""
    graph = connect_graph()
    ensure_vector_indexes(graph)

    summary = load_publications_for_query(NARROW_QUERY, max_results=5, graph=graph)
    assert summary.written > 0, "expected at least one real abstract to be written"

    result = retrieve_semantic(
        "NTRK1 gene fusion sarcoma larotrectinib treatment", top_k=10, graph=graph
    )

    literature_items = [item for item in result.items if item["source"] == "PubMed"]
    assert literature_items, "expected at least one PubMed-sourced item in the top-k results"
    item = literature_items[0]
    assert item["evidence_level"] == "literature"
    assert item["citation"]["id"].isdigit()
    assert item["citation"]["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/")
    assert item["retrieval_mode"] == "semantic"
