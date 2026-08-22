"""Live integration tests for the CIViC loader.

Deselected by default; run explicitly with:
    pytest -m integration tests/secondlook/tier1/test_civic_loader_integration.py

Requires a local FalkorDB (docker compose up -d) and network access to CIViC.
Each test writes to its own throwaway graph and deletes it afterwards, so it
never touches the real `secondlook_tier1` graph.
"""

from __future__ import annotations

import os
import uuid

import pytest

from secondlook.tier1.civic_loader import run_load
from secondlook.tier1.civic_verify import CivicApiError, verify_civic_shape

pytestmark = pytest.mark.integration


@pytest.fixture
def throwaway_graph():
    """A uniquely-named graph, deleted on teardown."""
    falkordb = pytest.importorskip("falkordb")

    db = falkordb.FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
    )
    graph = db.select_graph(f"tier1_itest_{uuid.uuid4().hex[:10]}")
    try:
        yield graph
    finally:
        try:
            graph.delete()
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown only
            print(f"teardown: could not delete test graph: {type(exc).__name__}: {exc}")


# One narrow, stable disease: Dermatofibrosarcoma Protuberans (DOID:3507).
# Small enough to load in seconds, and its canonical COL1A1-PDGFB biology
# means it reliably carries PREDICTIVE evidence.
NARROW_CONFIG = {
    "config_version": "itest-narrow",
    "diseases": [{"doid": "3507", "name": "Dermatofibrosarcoma Protuberans"}],
}


def test_civic_live_shape_still_matches():
    """The Phase 2 gate: if CIViC's shape drifts, this fails before any
    loader test can produce a misleadingly-empty result."""
    found = verify_civic_shape()
    assert found["evidence_levels"] == ["A", "B", "C", "D", "E"]
    assert set(found["evidence_directions"]) == {"SUPPORTS", "DOES_NOT_SUPPORT", "NA"}
    assert found["predictive_accepted_total"] > 0


def test_narrow_load_writes_expected_graph(throwaway_graph):
    summary = run_load(throwaway_graph, NARROW_CONFIG)

    assert summary.evidence_items_fetched > 0, "expected real CIViC evidence for DOID:3507"
    assert summary.disease_errors == {}

    written = sum(summary.nodes_written.values())
    assert written > 0, f"nothing written; skips were {summary.skipped}"

    def count(cypher: str) -> int:
        return throwaway_graph.query(cypher).result_set[0][0]

    assert count("MATCH (n:Gene) RETURN count(n)") > 0
    assert count("MATCH (n:Variant) RETURN count(n)") > 0
    assert count("MATCH (n:Drug) RETURN count(n)") > 0
    assert count("MATCH (n:EvidenceItem) RETURN count(n)") > 0
    assert count("MATCH ()-[r:PREDICTS_RESPONSE_TO]->() RETURN count(r)") > 0


def test_every_written_node_has_provenance(throwaway_graph):
    run_load(throwaway_graph, NARROW_CONFIG)

    missing_source = throwaway_graph.query(
        "MATCH (n) WHERE n.source IS NULL RETURN count(n)"
    ).result_set[0][0]
    missing_time = throwaway_graph.query(
        "MATCH (n) WHERE n.retrieved_at IS NULL RETURN count(n)"
    ).result_set[0][0]

    assert missing_source == 0, "every node must carry source (tier1 spec SS3.1)"
    assert missing_time == 0, "every node must carry retrieved_at"


def test_no_evidence_item_lacks_a_citation_url(throwaway_graph):
    """The hard rule from api-contracts.md, checked against real loaded data."""
    run_load(throwaway_graph, NARROW_CONFIG)

    uncited = throwaway_graph.query(
        "MATCH (e:EvidenceItem) WHERE e.citation_url IS NULL OR e.citation_url = '' "
        "RETURN count(e)"
    ).result_set[0][0]
    assert uncited == 0


def test_every_response_edge_has_a_valid_direction(throwaway_graph):
    run_load(throwaway_graph, NARROW_CONFIG)

    rows = throwaway_graph.query(
        "MATCH ()-[r:PREDICTS_RESPONSE_TO]->() RETURN DISTINCT r.direction"
    ).result_set
    directions = {row[0] for row in rows}
    assert directions, "expected at least one response edge"
    assert directions <= {"sensitive", "resistant"}, f"unexpected direction(s): {directions}"


def test_reload_is_idempotent(throwaway_graph):
    """Running the loader twice must not duplicate nodes or edges."""

    def snapshot() -> dict[str, int]:
        counts = {}
        for label in ("Gene", "Variant", "Disease", "Drug", "EvidenceItem", "Publication"):
            counts[label] = throwaway_graph.query(f"MATCH (n:{label}) RETURN count(n)").result_set[
                0
            ][0]
        for rel in ("HAS_VARIANT", "OBSERVED_IN", "PREDICTS_RESPONSE_TO", "SUPPORTS", "CITES"):
            counts[rel] = throwaway_graph.query(
                f"MATCH ()-[r:{rel}]->() RETURN count(r)"
            ).result_set[0][0]
        return counts

    run_load(throwaway_graph, NARROW_CONFIG)
    after_first = snapshot()

    run_load(throwaway_graph, NARROW_CONFIG)
    after_second = snapshot()

    assert after_first == after_second, (
        "re-running the loader changed graph counts, so MERGE is not keyed "
        f"correctly somewhere: {after_first} -> {after_second}"
    )


def test_out_of_scope_disease_is_excluded_from_the_graph(throwaway_graph):
    """Scope config must actually gate what lands in the graph -- the
    auditability point behind keeping scope in versioned config."""
    run_load(throwaway_graph, NARROW_CONFIG)

    rows = throwaway_graph.query("MATCH (d:Disease) RETURN DISTINCT d.doid").result_set
    doids = {row[0] for row in rows}
    assert doids <= {"3507"}, f"graph contains out-of-scope diseases: {doids - {'3507'}}"


def test_unreachable_civic_raises_civic_api_error(monkeypatch):
    """A dead endpoint must surface as CivicApiError, not a silent empty load."""
    import secondlook.tier1.civic_verify as civic_verify

    monkeypatch.setattr(civic_verify, "CIVIC_GRAPHQL_URL", "https://civicdb.invalid/api/graphql")
    with pytest.raises(CivicApiError):
        verify_civic_shape()
