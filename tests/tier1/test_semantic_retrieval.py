"""Unit tests for semantic_retrieval.py -- no network, no live FalkorDB,
no real model load (embed_text is monkeypatched to a deterministic stub).
"""

from __future__ import annotations

import pytest

from secondlook.tier1 import semantic_retrieval
from secondlook.tier1.graph_schema import EVIDENCE_LEVELS, RESULT_EVIDENCE_LEVELS
from secondlook.tier1.semantic_retrieval import (
    BackfillSummary,
    RetrievalResult,
    backfill_evidence_embeddings,
    ensure_vector_indexes,
    retrieve_semantic,
    retrieve_semantic_for_gene,
)


class FakeGraph:
    """Records every Cypher call, returns a scripted row set per call
    (matched by a substring of the query, not call order -- semantic
    queries interleave in a fixed but easy-to-get-wrong sequence)."""

    def __init__(self, responses: dict[str, list[tuple]]):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []
        self.index_calls: list[tuple[str, str]] = []

    def query(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params or {}))
        for needle, rows in self._responses.items():
            if needle in cypher:
                return _Result(rows)
        return _Result([])

    def create_node_vector_index(self, label, prop, *, dim, similarity_function):
        self.index_calls.append((label, prop))


class _Result:
    def __init__(self, rows):
        self.result_set = rows


# EvidenceItem query row shape: civic_id, evidence_level, citation_url, summary, score, drug_name
GOOD_EVIDENCE_ROW = (
    238,
    "A",
    "https://civicdb.org/evidence/238",
    "NTRK fusion evidence.",
    0.05,
    "Larotrectinib",
)


@pytest.fixture(autouse=True)
def stub_embed_text(monkeypatch):
    """Deterministic, instant stand-in for the real model -- unit tests
    must never load sentence-transformers."""
    monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text: [0.0] * 384)


class TestRetrieveSemantic:
    def test_match_found_and_correctly_shaped(self):
        graph = FakeGraph({"EvidenceItem'": [GOOD_EVIDENCE_ROW]})
        result = retrieve_semantic("NTRK fusion", graph=graph)

        assert isinstance(result, RetrievalResult)
        assert result.filtered_count == 0
        assert len(result.items) == 1
        item = result.items[0]
        assert item["type"] == "documented"
        assert item["source"] == "CIViC"
        assert item["evidence_level"] == "A"
        assert item["citation"] == {"id": "238", "url": "https://civicdb.org/evidence/238"}
        assert item["drug"] == "Larotrectinib"
        assert item["retrieval_mode"] == "semantic"

    def test_no_match_returns_empty_not_an_error(self):
        graph = FakeGraph({})
        result = retrieve_semantic("nothing relevant", graph=graph)
        assert result.items == []
        assert result.filtered_count == 0

    def test_row_missing_citation_url_is_filtered_and_counted(self):
        uncited = (238, "A", None, "summary text", 0.05, "Larotrectinib")
        graph = FakeGraph({"EvidenceItem'": [uncited]})
        result = retrieve_semantic("query", graph=graph)

        assert result.items == [], "an uncited item must never reach the caller"
        assert result.filtered_count == 1

    def test_corrupted_evidence_level_raises_rather_than_passing_through(self):
        bad = (238, "Z", "https://civicdb.org/e/238", "s", 0.05, "X")
        graph = FakeGraph({"EvidenceItem'": [bad]})
        with pytest.raises(ValueError, match="evidence_level"):
            retrieve_semantic("query", graph=graph)

    def test_drug_is_none_when_evidence_has_no_response_edge(self):
        row = (100, "D", "https://civicdb.org/e/100", "prognostic only", 0.1, None)
        graph = FakeGraph({"EvidenceItem'": [row]})
        result = retrieve_semantic("query", graph=graph)
        assert result.items[0]["drug"] is None

    def test_results_sorted_by_score_ascending(self):
        """Lower score = closer match (cosine distance), confirmed live in
        Phase 0/4 -- a farther row scripted first must still sort last."""
        far = (1, "D", "https://civicdb.org/e/1", "s1", 0.9, "DrugA")
        near = (2, "D", "https://civicdb.org/e/2", "s2", 0.1, "DrugB")
        graph = FakeGraph({"EvidenceItem'": [far, near]})

        result = retrieve_semantic("query", graph=graph)

        assert [item["citation"]["id"] for item in result.items] == ["2", "1"]

    def test_top_k_is_passed_through_and_slices_final_results(self):
        graph = FakeGraph({"EvidenceItem'": [GOOD_EVIDENCE_ROW]})
        retrieve_semantic("query", top_k=3, graph=graph)

        evidence_call = next(c for c in graph.calls if "EvidenceItem'" in c[0])
        assert evidence_call[1]["top_k"] == 3

    def test_embeds_query_text_exactly_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            semantic_retrieval, "embed_text", lambda text: calls.append(text) or [0.0] * 384
        )
        graph = FakeGraph({})

        retrieve_semantic("CDK4 amplification", graph=graph)

        assert calls == ["CDK4 amplification"]

    def test_query_vector_passed_as_parameter_not_interpolated(self):
        """Same injection discipline as Modes 1/2 -- the embedding vector
        (and by extension query_text) never appears in the Cypher string."""
        graph = FakeGraph({})
        retrieve_semantic("'; MATCH (n) DETACH DELETE n; //", graph=graph)

        for cypher, params in graph.calls:
            assert "DETACH DELETE" not in cypher
            assert "query_vec" in params


class TestPublicationLiteratureItems:
    """A PubMed-sourced hit isn't a CIViC-graded evidence item, so its
    evidence_level must validate against RESULT_EVIDENCE_LEVELS (which adds
    "literature" on top of CIViC's A-E scale), not EVIDENCE_LEVELS itself --
    see graph_schema.RESULT_EVIDENCE_LEVELS docstring."""

    # Publication query row shape: pmid, title, score
    GOOD_PUBLICATION_ROW = (12345678, "NTRK fusions across solid tumors.", 0.08)

    def test_literature_item_has_evidence_level_literature(self):
        graph = FakeGraph({"Publication'": [self.GOOD_PUBLICATION_ROW]})
        result = retrieve_semantic("NTRK fusion", graph=graph)

        assert len(result.items) == 1
        item = result.items[0]
        assert item["evidence_level"] == "literature"
        assert item["evidence_level"] in RESULT_EVIDENCE_LEVELS
        assert item["evidence_level"] not in EVIDENCE_LEVELS
        assert item["source"] == "PubMed"
        assert item["retrieval_mode"] == "semantic"
        assert item["citation"] == {
            "id": "12345678",
            "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        }

    def test_literature_row_missing_pmid_is_filtered_and_counted(self):
        no_pmid = (None, "Some title with no PMID.", 0.08)
        graph = FakeGraph({"Publication'": [no_pmid]})
        result = retrieve_semantic("query", graph=graph)

        assert result.items == []
        assert result.filtered_count == 1

    def test_evidence_and_literature_items_coexist_with_distinct_evidence_level_axes(self):
        """The same retrieve_semantic() call can return both a CIViC-graded
        item (evidence_level in EVIDENCE_LEVELS) and a literature item
        (evidence_level == "literature") without either constant loosening
        the other."""
        graph = FakeGraph(
            {
                "EvidenceItem'": [GOOD_EVIDENCE_ROW],
                "Publication'": [self.GOOD_PUBLICATION_ROW],
            }
        )
        result = retrieve_semantic("query", graph=graph)

        levels = {item["evidence_level"] for item in result.items}
        assert levels == {"A", "literature"}


class TestRetrieveSemanticForGene:
    def test_scoped_query_includes_gene_and_returns_shaped_items(self):
        graph = FakeGraph({"MATCH (gene:Gene": [GOOD_EVIDENCE_ROW]})
        result = retrieve_semantic_for_gene("NTRK1", "kinase inhibitor", graph=graph)

        assert len(result.items) == 1
        assert result.items[0]["retrieval_mode"] == "semantic"
        cypher, params = graph.calls[0]
        assert params["gene"] == "NTRK1"
        assert "$gene" in cypher

    def test_uncited_row_is_filtered(self):
        uncited = (238, "A", None, "s", 0.05, "X")
        graph = FakeGraph({"MATCH (gene:Gene": [uncited]})
        result = retrieve_semantic_for_gene("NTRK1", "query", graph=graph)
        assert result.items == []
        assert result.filtered_count == 1

    def test_no_match_for_gene_returns_empty_not_an_error(self):
        graph = FakeGraph({})
        result = retrieve_semantic_for_gene("NOTAREALGENE", "query", graph=graph)
        assert result.items == []


class TestEnsureVectorIndexes:
    def test_creates_both_indexes(self):
        graph = FakeGraph({})
        ensure_vector_indexes(graph)
        assert set(graph.index_calls) == {
            ("EvidenceItem", "summary_embedding"),
            ("Publication", "abstract_embedding"),
        }

    def test_already_indexed_error_is_swallowed(self):
        class AlreadyIndexedGraph(FakeGraph):
            def create_node_vector_index(self, label, prop, **kw):
                raise RuntimeError("errMsg: Attribute 'x' is already indexed")

        ensure_vector_indexes(AlreadyIndexedGraph({}))  # must not raise

    def test_other_errors_are_not_swallowed(self):
        class BrokenGraph(FakeGraph):
            def create_node_vector_index(self, label, prop, **kw):
                raise RuntimeError("connection refused")

        with pytest.raises(RuntimeError, match="connection refused"):
            ensure_vector_indexes(BrokenGraph({}))


class TestBackfillEvidenceEmbeddings:
    def test_embeds_and_writes_with_per_property_provenance(self, monkeypatch):
        class EmbedGraph(FakeGraph):
            def __init__(self):
                super().__init__({})
                self.writes = []

            def query(self, cypher, params=None):
                params = params or {}
                self.calls.append((cypher, params))
                if "WHERE e.summary_embedding IS NOT NULL RETURN count" in cypher:
                    return _Result([(0,)])
                if "WHERE e.summary_embedding IS NULL" in cypher:
                    return _Result([(238, "NTRK fusion confers sensitivity.")])
                if cypher.strip().startswith("MATCH (e:EvidenceItem {civic_id:"):
                    self.writes.append(params)
                    return _Result([])
                raise AssertionError(f"unexpected query: {cypher}")

        class FakeModel:
            def encode(self, texts):
                class _Arr(list):
                    def tolist(self):
                        return [[0.1] * 384 for _ in self]

                return _Arr(texts)

        monkeypatch.setattr(semantic_retrieval, "_get_model", lambda: FakeModel())

        graph = EmbedGraph()
        summary = backfill_evidence_embeddings(graph)

        assert summary.considered == 1
        assert summary.embedded == 1
        assert len(graph.writes) == 1
        props = graph.writes[0]["props"]
        assert props["summary_embedding_source"] == semantic_retrieval.EMBEDDING_MODEL_NAME
        assert "summary_embedding_retrieved_at" in props
        assert "summary_embedding" not in props, (
            "the placeholder key must be dropped -- otherwise `e += $props` "
            "would overwrite the vecf32-typed vector with a plain null"
        )

    def test_already_embedded_are_not_reconsidered(self):
        class EmbedGraph(FakeGraph):
            def query(self, cypher, params=None):
                self.calls.append((cypher, params or {}))
                if "WHERE e.summary_embedding IS NOT NULL RETURN count" in cypher:
                    return _Result([(46,)])
                if "WHERE e.summary_embedding IS NULL" in cypher:
                    return _Result([])
                raise AssertionError(f"unexpected query: {cypher}")

        summary = backfill_evidence_embeddings(EmbedGraph({}))
        assert summary.already_embedded == 46
        assert summary.considered == 0
        assert summary.embedded == 0

    def test_summary_to_dict_is_json_serializable(self):
        import json

        json.dumps(BackfillSummary(considered=1, already_embedded=2, embedded=1).to_dict())
