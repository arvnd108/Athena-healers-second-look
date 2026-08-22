"""Unit tests for retrieval.py Modes 1/2 -- no network, no live FalkorDB.

Mocks at the FalkorDB client call boundary (graph.query) and asserts the
mock is what actually got invoked -- not something upstream of it
(RareCure rule #9).
"""

from __future__ import annotations

import pytest

from secondlook.tier1.retrieval import (
    RetrievalResult,
    retrieve_exact,
    retrieve_relaxed,
)


class FakeResult:
    """Mimics the falkordb client's query result object: `.result_set`."""

    def __init__(self, rows: list[tuple]):
        self.result_set = rows


class FakeGraph:
    """Records every Cypher call, and returns a scripted row set per call.

    `responses` is a list consumed in order -- the Nth call to `.query()`
    returns `responses[N]`. This lets a test script "exact query returns
    nothing, relaxed query returns something" without caring which literal
    Cypher string maps to which call, while still recording everything for
    the injection/parameterization assertions.
    """

    def __init__(self, responses: list[list[tuple]]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params or {}))
        rows = self._responses.pop(0) if self._responses else []
        return FakeResult(rows)


# A well-formed row as the Cypher RETURN clause actually shapes it:
# d.name, r.direction, e.evidence_level, e.citation_url, e.summary, e.civic_id
GOOD_ROW = (
    "Larotrectinib",
    "sensitive",
    "A",
    "https://civicdb.org/evidence/238",
    "ETV6::NTRK3 confers sensitivity to larotrectinib.",
    238,
)


class TestRetrieveExact:
    def test_match_found_and_correctly_shaped(self):
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_exact("NTRK3", "NP_001007792.1:p.Xxx", graph=graph)

        assert isinstance(result, RetrievalResult)
        assert result.filtered_count == 0
        assert len(result.items) == 1
        item = result.items[0]
        assert item == {
            "type": "documented",
            "source": "CIViC",
            "evidence_level": "A",
            "citation": {"id": "238", "url": "https://civicdb.org/evidence/238"},
            "summary": "ETV6::NTRK3 confers sensitivity to larotrectinib.",
            "drug": "Larotrectinib",
            "trial_status": None,
            "retrieval_mode": "exact",
            "match_key": "hgvs_p",
        }

    def test_no_match_returns_empty_result_not_an_error(self):
        graph = FakeGraph(responses=[[]])
        result = retrieve_exact("BRAF", "NP_004324.2:p.Val600Glu", graph=graph)

        assert result.items == []
        assert result.filtered_count == 0

    def test_row_missing_citation_url_is_filtered_and_counted(self):
        uncited_row = ("Larotrectinib", "sensitive", "A", None, "summary", 238)
        graph = FakeGraph(responses=[[uncited_row]])
        result = retrieve_exact("NTRK3", "x", graph=graph)

        assert result.items == [], "an uncited item must never reach the caller"
        assert result.filtered_count == 1

    def test_row_with_empty_string_citation_url_is_also_filtered(self):
        row = ("Larotrectinib", "sensitive", "A", "", "summary", 238)
        graph = FakeGraph(responses=[[row]])
        result = retrieve_exact("NTRK3", "x", graph=graph)

        assert result.items == []
        assert result.filtered_count == 1

    def test_corrupted_evidence_level_raises_rather_than_passing_through(self):
        bad_row = ("Larotrectinib", "sensitive", "Z", "https://civicdb.org/e/1", "s", 1)
        graph = FakeGraph(responses=[[bad_row]])

        with pytest.raises(ValueError, match="evidence_level"):
            retrieve_exact("NTRK3", "x", graph=graph)

    def test_literature_evidence_level_still_rejected_on_a_real_evidence_item(self):
        """Mode 1 reads EvidenceItem.evidence_level off the graph and must
        keep validating against EVIDENCE_LEVELS (CIViC's A-E scale) even
        though "literature" is now a valid *output item* evidence_level
        for Mode 3's PubMed-sourced hits (RESULT_EVIDENCE_LEVELS) -- an
        EvidenceItem node should never carry that value, so it must still
        raise here."""
        literature_row = (
            "Larotrectinib",
            "sensitive",
            "literature",
            "https://civicdb.org/e/1",
            "s",
            1,
        )
        graph = FakeGraph(responses=[[literature_row]])

        with pytest.raises(ValueError, match="evidence_level"):
            retrieve_exact("NTRK3", "x", graph=graph)

    def test_corrupted_direction_raises(self):
        bad_row = ("Larotrectinib", "unclear", "A", "https://civicdb.org/e/1", "s", 1)
        graph = FakeGraph(responses=[[bad_row]])

        with pytest.raises(ValueError, match="direction"):
            retrieve_exact("NTRK3", "x", graph=graph)

    def test_uses_parameter_binding_not_string_interpolation(self):
        """The gene/hgvs_p values must appear only in the params dict, never
        formatted into the Cypher string itself -- Cypher injection surface
        otherwise."""
        graph = FakeGraph(responses=[[]])
        malicious_gene = "TP53' OR '1'='1"

        retrieve_exact(malicious_gene, "x", graph=graph)

        assert len(graph.calls) == 1
        cypher, params = graph.calls[0]
        assert malicious_gene not in cypher, "raw value leaked into the Cypher string"
        assert params["gene"] == malicious_gene
        assert "$gene" in cypher and "$hgvs_p" in cypher

    def test_multiple_rows_all_shaped_and_none_dropped(self):
        row_b = ("Sunitinib", "sensitive", "B", "https://civicdb.org/e/2", "s2", 2)
        graph = FakeGraph(responses=[[GOOD_ROW, row_b]])
        result = retrieve_exact("NTRK3", "x", graph=graph)

        assert len(result.items) == 2
        assert {i["drug"] for i in result.items} == {"Larotrectinib", "Sunitinib"}


class TestRetrieveExactByVariantName:
    """Mode 1's non-HGVS fallback -- fusions, amplifications, expression
    changes have no hgvs_p at all and are only reachable this way."""

    FUSION_ROW = (
        "Larotrectinib",
        "sensitive",
        "A",
        "https://civicdb.org/evidence/999",
        "ETV6::NTRK3 confers sensitivity to larotrectinib.",
        999,
    )

    def test_variant_name_match_found_and_correctly_shaped(self):
        graph = FakeGraph(responses=[[self.FUSION_ROW]])
        result = retrieve_exact("NTRK3", variant_name="Fusion", graph=graph)

        assert len(result.items) == 1
        assert result.items[0]["retrieval_mode"] == "exact"
        assert result.items[0]["drug"] == "Larotrectinib"
        assert result.items[0]["match_key"] == "variant_name"

    def test_neither_key_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            retrieve_exact("NTRK3", graph=FakeGraph(responses=[]))

    def test_both_keys_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            retrieve_exact(
                "NTRK3", "NP_1.1:p.X", variant_name="Fusion", graph=FakeGraph(responses=[])
            )

    def test_variant_name_query_uses_parameter_binding(self):
        graph = FakeGraph(responses=[[]])
        malicious = "Fusion' OR '1'='1"

        retrieve_exact("NTRK3", variant_name=malicious, graph=graph)

        cypher, params = graph.calls[0]
        assert malicious not in cypher
        assert params["variant_name"] == malicious
        assert "$variant_name" in cypher

    def test_variant_name_row_missing_citation_is_filtered(self):
        uncited = ("Larotrectinib", "sensitive", "A", None, "s", 999)
        graph = FakeGraph(responses=[[uncited]])
        result = retrieve_exact("NTRK3", variant_name="Fusion", graph=graph)

        assert result.items == []
        assert result.filtered_count == 1


class TestMatchKey:
    """match_key records WHICH key produced a Mode 1 match -- same principle
    as relaxation_type: a result must always say why it matched."""

    def test_hgvs_p_match_carries_match_key_hgvs_p(self):
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_exact("NTRK3", "NP_1.1:p.X", graph=graph)
        assert result.items[0]["match_key"] == "hgvs_p"

    def test_variant_name_match_carries_match_key_variant_name(self):
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_exact("NTRK3", variant_name="Fusion", graph=graph)
        assert result.items[0]["match_key"] == "variant_name"

    def test_mode_2_same_gene_items_do_not_carry_match_key(self):
        """Path (a) matches on gene alone (any other variant) -- hgvs_p/
        variant_name is only used to EXCLUDE the variant already tried, not
        to produce the match, so attaching match_key here would misstate
        why the result matched."""
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_relaxed("NTRK3", "NP_9999.1:p.Xxx", graph=graph)
        assert "match_key" not in result.items[0]

    def test_mode_2_drug_class_items_do_not_carry_match_key(self):
        graph = FakeGraph(responses=[[], [GOOD_ROW]])
        result = retrieve_relaxed(
            "NTRK3", variant_name="Fusion", disease="Sarcoma", drug_class="X", graph=graph
        )
        assert "match_key" not in result.items[0]


class TestRetrieveRelaxed:
    def test_same_gene_relaxation_found_with_correct_relaxation_type(self):
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_relaxed("NTRK3", "NP_9999.1:p.Xxx", graph=graph)

        assert len(result.items) == 1
        item = result.items[0]
        assert item["retrieval_mode"] == "relaxed"
        assert item["relaxation_type"] == "same_gene_different_variant"
        assert item["drug"] == "Larotrectinib"

    def test_relaxed_item_can_never_be_mistaken_for_exact(self):
        graph = FakeGraph(responses=[[GOOD_ROW]])
        result = retrieve_relaxed("NTRK3", "x", graph=graph)

        assert result.items[0]["retrieval_mode"] != "exact"
        assert "relaxation_type" in result.items[0]

    def test_no_relaxed_match_returns_empty_not_an_error(self):
        graph = FakeGraph(responses=[[]])
        result = retrieve_relaxed("XYZGENE", "x", graph=graph)

        assert result.items == []
        assert result.filtered_count == 0

    def test_uncited_relaxed_row_is_filtered_and_counted(self):
        uncited = ("Larotrectinib", "sensitive", "B", None, "s", 5)
        graph = FakeGraph(responses=[[uncited]])
        result = retrieve_relaxed("NTRK3", "x", graph=graph)

        assert result.items == []
        assert result.filtered_count == 1

    def test_corrupted_evidence_level_raises(self):
        bad = ("Larotrectinib", "sensitive", "Q", "https://civicdb.org/e/9", "s", 9)
        graph = FakeGraph(responses=[[bad]])

        with pytest.raises(ValueError):
            retrieve_relaxed("NTRK3", "x", graph=graph)

    def test_disease_parameter_accepted_but_drug_class_path_not_run(self):
        """Path (b), same-disease-drug-class, is documented as unimplemented
        -- the graph has no drug-class data. Passing `disease` must not
        raise or silently invent a second query."""
        graph = FakeGraph(responses=[[]])
        result = retrieve_relaxed("XYZGENE", "x", disease="Sarcoma", graph=graph)

        assert result.items == []
        # Exactly one query ran (path a); no second call for path b.
        assert len(graph.calls) == 1

    def test_uses_parameter_binding_not_string_interpolation(self):
        graph = FakeGraph(responses=[[]])
        malicious_gene = "'; MATCH (n) DETACH DELETE n; //"

        retrieve_relaxed(malicious_gene, "x", graph=graph)

        cypher, params = graph.calls[0]
        assert malicious_gene not in cypher
        assert params["gene"] == malicious_gene

    def test_excludes_the_exact_variant_being_relaxed_from(self):
        """Path (a) must not just re-return the same variant that Mode 1
        already searched and found nothing citable for."""
        graph = FakeGraph(responses=[[]])
        retrieve_relaxed("NTRK3", "NP_001007792.1:p.Xxx", graph=graph)

        cypher, params = graph.calls[0]
        assert params["hgvs_p"] == "NP_001007792.1:p.Xxx"
        assert "hgvs_p <> $hgvs_p" in cypher or "hgvs_p" in cypher

    def test_excludes_by_variant_name_when_that_was_the_original_key(self):
        """The exclusion must key off whichever field Mode 1 actually
        searched on -- excluding by hgvs_p when the original lookup was by
        variant_name would fail to exclude anything, since fusions have
        hgvs_p = NULL and `NULL <> $hgvs_p` is never true in Cypher."""
        graph = FakeGraph(responses=[[]])
        retrieve_relaxed("NTRK3", variant_name="Fusion", graph=graph)

        cypher, params = graph.calls[0]
        assert params["variant_name"] == "Fusion"
        assert "hgvs_p" not in params, "must not exclude by a key that wasn't the search key"
        assert "v.name <> $variant_name" in cypher


class TestRetrieveRelaxedDrugClass:
    """Mode 2 path (b): same disease + same drug class."""

    CLASS_ROW = (
        "Entrectinib",
        "sensitive",
        "C",
        "https://civicdb.org/evidence/500",
        "Same-class NTRK inhibitor activity in a different sarcoma cohort.",
        500,
    )

    def test_runs_only_when_both_disease_and_drug_class_given(self):
        """Omitting either must skip path (b) entirely, not run a query
        with a missing constraint (which would mean "any drug for this
        disease", not a class-relaxed match)."""
        graph = FakeGraph(responses=[[]])
        result = retrieve_relaxed("NTRK3", variant_name="Fusion", graph=graph)

        assert result.items == []
        assert len(graph.calls) == 1, "only path (a) should have run"

    def test_falls_through_to_drug_class_when_same_gene_is_empty(self):
        graph = FakeGraph(responses=[[], [self.CLASS_ROW]])
        result = retrieve_relaxed(
            "NTRK3",
            variant_name="Fusion",
            disease="Sarcoma",
            drug_class="Neurotrophic tyrosine kinase receptor inhibitor",
            graph=graph,
        )

        assert len(result.items) == 1
        item = result.items[0]
        assert item["relaxation_type"] == "same_disease_drug_class"
        assert item["retrieval_mode"] == "relaxed"
        assert item["drug"] == "Entrectinib"
        assert len(graph.calls) == 2

    def test_same_gene_hit_short_circuits_before_drug_class_runs(self):
        same_gene_row = ("Crizotinib", "sensitive", "B", "https://civicdb.org/e/1", "s", 1)
        graph = FakeGraph(responses=[[same_gene_row]])
        result = retrieve_relaxed(
            "NTRK3", variant_name="Fusion", disease="Sarcoma", drug_class="X", graph=graph
        )

        assert len(result.items) == 1
        assert result.items[0]["relaxation_type"] == "same_gene_different_variant"
        assert len(graph.calls) == 1, "path (b) must not run once path (a) found something"

    def test_drug_class_query_uses_parameter_binding(self):
        graph = FakeGraph(responses=[[], []])
        malicious_class = "X' OR '1'='1"

        retrieve_relaxed(
            "NTRK3",
            variant_name="Fusion",
            disease="Sarcoma",
            drug_class=malicious_class,
            graph=graph,
        )

        _cypher, params = graph.calls[1]
        assert params["drug_class"] == malicious_class

    def test_uncited_drug_class_row_is_filtered_and_counted(self):
        uncited = ("Entrectinib", "sensitive", "C", None, "s", 500)
        graph = FakeGraph(responses=[[], [uncited]])
        result = retrieve_relaxed(
            "NTRK3", variant_name="Fusion", disease="Sarcoma", drug_class="X", graph=graph
        )

        assert result.items == []
        assert result.filtered_count == 1

    def test_both_paths_empty_returns_empty_not_an_error(self):
        graph = FakeGraph(responses=[[], []])
        result = retrieve_relaxed(
            "NTRK3", variant_name="Fusion", disease="Sarcoma", drug_class="X", graph=graph
        )

        assert result.items == []
        assert result.filtered_count == 0
