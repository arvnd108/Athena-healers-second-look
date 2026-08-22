"""Live integration tests for retrieval.py Modes 1/2, against the real
loaded graph (`secondlook_tier1`, populated by civic_loader.py +
chembl_enrich.py).

Deselected by default; run explicitly with:
    pytest -m integration tests/secondlook/tier1/test_retrieval_integration.py

Read-only against the shared graph -- no fixtures create or delete data
here, unlike civic_loader's integration tests. One exception:
test_malformed_item_missing_citation_url_never_surfaces uses its own
disposable scratch graph (created and deleted within the test) rather
than touching the shared one, to prove the citation filter against a real
Cypher query path without ever risking a malformed node becoming visible
in the shared demo graph.
"""

from __future__ import annotations

import uuid

import pytest

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.retrieval import retrieve_exact, retrieve_relaxed

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def graph():
    return connect_graph()


def test_exact_missense_hit_on_real_data(graph):
    """TP53 R273H is real, loaded CIViC evidence (level D, resistance)."""
    result = retrieve_exact("TP53", "NP_000537.3:p.Arg273His", graph=graph)

    assert len(result.items) > 0
    assert result.filtered_count == 0
    for item in result.items:
        assert item["retrieval_mode"] == "exact"
        assert item["citation"]["url"]


def test_exact_miss_on_a_mutation_not_in_the_graph(graph):
    result = retrieve_exact("TP53", "NP_000537.3:p.NotARealMutation999", graph=graph)
    assert result.items == []


def test_exact_by_variant_name_reaches_a_fusion(graph):
    """The gap retrieve_exact(hgvs_p=...) alone cannot close: fusions have
    no hgvs_p at all. LMNA::NTRK1 is loaded, level-C+ CIViC evidence."""
    result = retrieve_exact("LMNA::NTRK1", variant_name="Fusion", graph=graph)

    assert len(result.items) > 0
    assert all(item["drug"] for item in result.items)


def test_relaxed_same_gene_finds_a_different_variant_of_tp53(graph):
    result = retrieve_relaxed("TP53", "NP_000000.0:p.Bogus999Bogus", graph=graph)

    assert len(result.items) > 0
    assert all(item["relaxation_type"] == "same_gene_different_variant" for item in result.items)
    assert all(item["retrieval_mode"] == "relaxed" for item in result.items)


def test_relaxed_drug_class_reaches_evidence_from_a_different_gene_entirely(graph):
    """The scenario path (b) exists for: a patient's specific fusion
    (TPR::NTRK1) has zero direct CIViC evidence and the gene itself isn't
    in the graph, so Mode 1 and path (a) both correctly return nothing --
    but four OTHER NTRK-family fusions have level A/C evidence for
    Larotrectinib/Entrectinib in the same disease, and path (b) must
    surface that as a class-matched (not gene-matched) relaxation."""
    exact = retrieve_exact("TPR::NTRK1", variant_name="Fusion", graph=graph)
    assert exact.items == [], "this fusion should have no direct evidence in the loaded graph"

    same_gene = retrieve_relaxed("TPR::NTRK1", variant_name="Fusion", graph=graph)
    assert same_gene.items == [], "TPR::NTRK1 is not a gene in the graph at all"

    relaxed = retrieve_relaxed(
        "TPR::NTRK1",
        variant_name="Fusion",
        disease="Congenital Fibrosarcoma",
        drug_class="Neurotrophic tyrosine kinase receptor inhibitor",
        graph=graph,
    )
    assert len(relaxed.items) > 0
    assert {item["drug"] for item in relaxed.items} >= {"Larotrectinib"}
    for item in relaxed.items:
        assert item["relaxation_type"] == "same_disease_drug_class"
        assert item["retrieval_mode"] == "relaxed"
        assert item["citation"]["url"]


def test_no_hit_anywhere_returns_empty_not_an_error(graph):
    result = retrieve_relaxed(
        "COMPLETELY_FAKE_GENE_XYZ",
        variant_name="NotReal",
        disease="Not A Real Disease",
        drug_class="Not A Real Class",
        graph=graph,
    )
    assert result.items == []
    assert result.filtered_count == 0


def test_strong_hit_and_no_hit_paths_end_to_end(graph):
    """The two shapes orchestration relies on most, walked together
    against the real graph and real query path: a known-good exact hit
    with match_key actually asserted (not just assumed correct because a
    unit test checked it once), and a gene+mutation that exists nowhere in
    the graph at all -- confirming BOTH retrieve_exact and retrieve_relaxed
    return empty cleanly for it, not just that neither raises in
    isolation."""
    hit = retrieve_exact("FGFR1", "NP_075598.2:p.Asn546Lys", graph=graph)
    assert len(hit.items) > 0
    for item in hit.items:
        assert item["type"] == "documented"
        assert item["citation"]["url"]
        assert item["retrieval_mode"] == "exact"
        assert item["match_key"] == "hgvs_p"
        assert "relaxation_type" not in item

    fake_gene = "TOTALLY_FAKE_GENE_NOT_IN_GRAPH"
    fake_hgvs = "NP_000000.0:p.Fake1Fake"

    no_hit_exact = retrieve_exact(fake_gene, fake_hgvs, graph=graph)
    assert no_hit_exact.items == []

    no_hit_relaxed = retrieve_relaxed(fake_gene, fake_hgvs, graph=graph)
    assert no_hit_relaxed.items == []
    assert no_hit_relaxed.filtered_count == 0


def test_malformed_item_missing_citation_url_never_surfaces():
    """Writes a throwaway EvidenceItem directly via raw Cypher -- bypassing
    civic_loader.py entirely -- with citation_url left unset, confirming
    the hard "no citation.url, no item" rule holds against a real Cypher
    query path (not just a FakeGraph's scripted rows). Uses its own
    disposable scratch graph, deleted in `finally`, so a malformed node is
    never even briefly visible in the shared demo graph."""
    falkordb = pytest.importorskip("falkordb")
    db = falkordb.FalkorDB(host="localhost", port=6379)
    graph = db.select_graph(f"tier1_malformed_item_test_{uuid.uuid4().hex[:10]}")
    try:
        graph.query(
            "CREATE (g:Gene {symbol: 'ZZZ_TEST_GENE'}) "
            "CREATE (v:Variant {civic_variant_id: -1, name: 'Fusion'}) "
            "CREATE (e:EvidenceItem {civic_id: -1, evidence_level: 'A', "
            "summary: 'malformed test item, no citation_url'}) "
            "CREATE (d:Drug {name: 'ZZZ_TEST_DRUG'}) "
            "CREATE (g)-[:HAS_VARIANT]->(v) "
            "CREATE (e)-[:SUPPORTS]->(v) "
            "CREATE (v)-[:PREDICTS_RESPONSE_TO {direction: 'sensitive'}]->(d)"
        )

        result = retrieve_exact("ZZZ_TEST_GENE", variant_name="Fusion", graph=graph)

        assert result.items == [], "an item with no citation_url must never be returned"
        assert result.filtered_count == 1, "it must be counted as filtered, not silently vanish"
    finally:
        try:
            graph.delete()
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown only
            print(f"teardown: could not delete test graph: {type(exc).__name__}: {exc}")
