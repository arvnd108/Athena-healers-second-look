"""Live integration test for chembl_enrich.py against the real ChEMBL API
and the real loaded graph. Deselected by default:
    pytest -m integration tests/secondlook/tier1/test_chembl_enrich_integration.py
"""

from __future__ import annotations

import pytest

from secondlook.tier1.chembl_enrich import enrich_drug_classes, resolve_drug_class
from secondlook.tier1.graph_connection import connect_graph

pytestmark = pytest.mark.integration


def test_resolves_a_known_drug_live():
    import httpx

    with httpx.Client(timeout=30) as client:
        result = resolve_drug_class("Larotrectinib", client=client)
    assert result == "Neurotrophic tyrosine kinase receptor inhibitor"


def test_generic_civic_placeholder_name_resolves_to_none_not_an_error():
    import httpx

    with httpx.Client(timeout=30) as client:
        result = resolve_drug_class("BET Inhibitor", client=client)
    assert result is None


def test_rerun_against_the_real_graph_is_idempotent():
    """Every drug already enriched must be left alone -- a second full pass
    changes nothing already resolved."""
    graph = connect_graph()

    before = graph.query(
        "MATCH (d:Drug) WHERE d.drug_class IS NOT NULL RETURN d.name, d.drug_class"
    ).result_set
    assert before, "expected the live graph to already have enriched drugs from Phase 3"

    summary = enrich_drug_classes(graph)

    after = graph.query(
        "MATCH (d:Drug) WHERE d.drug_class IS NOT NULL RETURN d.name, d.drug_class"
    ).result_set

    assert sorted(before) == sorted(after)
    assert summary.already_enriched == len(before)
