"""Structured Case Index tests. Pure, offline -- no database needed."""

import dataclasses

import pytest

from secondlook.index.export import CaseSummaryExport
from secondlook.index.similarity import (
    DEFAULT_SIMILARITY_WEIGHTS,
    SIMILARITY_FIELDS,
    SimilarCase,
    find_similar_cases,
)


def _summary(
    case_ref: str,
    *,
    cancer_type: str = "Synovial sarcoma",
    age_decade: str | None = "30-39",
    disease_stage_bucket: str | None = None,
    gene_variant_types: tuple[dict, ...] = (),
    treatment_outcome_categories: tuple[str, ...] = (),
    relevant_evidence_ids: tuple[str, ...] = (),
) -> CaseSummaryExport:
    return CaseSummaryExport(
        case_ref=case_ref,
        age_decade=age_decade,
        cancer_type=cancer_type,
        disease_stage_bucket=disease_stage_bucket,
        gene_variant_types=gene_variant_types,
        treatment_outcome_categories=treatment_outcome_categories,
        relevant_evidence_ids=relevant_evidence_ids,
    )


# --- weight validation -------------------------------------------------------


def test_unknown_weight_field_rejected():
    query = _summary("q")
    with pytest.raises(ValueError, match="Unknown similarity field"):
        find_similar_cases(query, [_summary("c1")], weights={"not_a_real_field": 1.0})


def test_negative_weight_rejected():
    query = _summary("q")
    with pytest.raises(ValueError, match="must be non-negative"):
        find_similar_cases(query, [_summary("c1")], weights={"cancer_type": -1.0})


def test_default_weights_cover_every_similarity_field():
    assert set(DEFAULT_SIMILARITY_WEIGHTS) == SIMILARITY_FIELDS


def test_relevant_evidence_ids_is_deliberately_not_a_similarity_field():
    """Which evidence a case's research turned up is not a structural
    property of the patient -- see similarity.py's module docstring.
    """
    assert "relevant_evidence_ids" not in SIMILARITY_FIELDS


# --- no self-matching, no zero-overlap results ------------------------------


def test_a_case_never_matches_itself():
    query = _summary("q", cancer_type="Synovial sarcoma")
    corpus = [query]  # the query itself, present in the corpus
    result = find_similar_cases(query, corpus)
    assert result == []


def test_zero_overlap_candidate_is_excluded():
    query = _summary("q", cancer_type="Synovial sarcoma", age_decade="30-39")
    candidate = _summary("c1", cancer_type="NSCLC", age_decade="60-69")
    result = find_similar_cases(query, [candidate])
    assert result == []


# --- individual field matching ----------------------------------------------


def test_cancer_type_match_scores_and_is_named():
    query = _summary("q", cancer_type="Synovial sarcoma", age_decade=None)
    candidate = _summary("c1", cancer_type="Synovial sarcoma", age_decade=None)
    result = find_similar_cases(query, [candidate])
    assert len(result) == 1
    assert result[0].matched_fields == ("cancer_type",)
    assert result[0].score == DEFAULT_SIMILARITY_WEIGHTS["cancer_type"]


def test_age_decade_none_never_matches():
    """Missing age on either side must not count as a match -- None == None
    would otherwise falsely score a match between two cases with unknown age.
    """
    query = _summary("q", cancer_type="X", age_decade=None)
    candidate = _summary("c1", cancer_type="Y", age_decade=None)
    result = find_similar_cases(query, [candidate])
    assert result == []  # cancer_type differs too, so nothing at all matches


def test_disease_stage_bucket_none_never_matches():
    query = _summary("q", cancer_type="X", age_decade=None, disease_stage_bucket=None)
    candidate = _summary("c1", cancer_type="X", age_decade=None, disease_stage_bucket=None)
    result = find_similar_cases(query, [candidate])
    assert result[0].matched_fields == ("cancer_type",)  # not disease_stage_bucket


def test_gene_variant_type_overlap_lists_each_pair_individually():
    query = _summary(
        "q",
        cancer_type="X",
        age_decade=None,
        gene_variant_types=(
            {"gene": "EGFR", "variant_type": "missense"},
            {"gene": "KIT", "variant_type": "missense"},
        ),
    )
    candidate = _summary(
        "c1",
        cancer_type="Y",
        age_decade=None,
        gene_variant_types=(
            {"gene": "EGFR", "variant_type": "missense"},
            {"gene": "KIT", "variant_type": "missense"},
            {"gene": "BRAF", "variant_type": "missense"},  # not shared
        ),
    )
    result = find_similar_cases(query, [candidate])
    assert result[0].matched_fields == (
        "gene_variant_type:EGFR/missense",
        "gene_variant_type:KIT/missense",
    )
    assert result[0].score == 2 * DEFAULT_SIMILARITY_WEIGHTS["gene_variant_type"]


def test_gene_variant_type_requires_both_gene_and_type_to_match():
    """Same gene, different variant type -- not a match. This is
    deliberately stricter than gene-only overlap.
    """
    query = _summary(
        "q",
        cancer_type="X",
        age_decade=None,
        gene_variant_types=({"gene": "EGFR", "variant_type": "missense"},),
    )
    candidate = _summary(
        "c1",
        cancer_type="Y",
        age_decade=None,
        gene_variant_types=({"gene": "EGFR", "variant_type": "fusion"},),
    )
    result = find_similar_cases(query, [candidate])
    assert result == []


def test_treatment_outcome_category_overlap():
    query = _summary(
        "q",
        cancer_type="X",
        age_decade=None,
        treatment_outcome_categories=("progressed", "stopped_for_toxicity"),
    )
    candidate = _summary(
        "c1", cancer_type="Y", age_decade=None, treatment_outcome_categories=("progressed",)
    )
    result = find_similar_cases(query, [candidate])
    assert result[0].matched_fields == ("treatment_outcome_category:progressed",)


# --- ranking and determinism -------------------------------------------------


def test_results_ranked_by_score_descending():
    query = _summary(
        "q",
        cancer_type="Synovial sarcoma",
        age_decade="30-39",
        gene_variant_types=({"gene": "EGFR", "variant_type": "missense"},),
    )
    strong_match = _summary(
        "strong",
        cancer_type="Synovial sarcoma",
        age_decade="30-39",
        gene_variant_types=({"gene": "EGFR", "variant_type": "missense"},),
    )
    weak_match = _summary("weak", cancer_type="Synovial sarcoma", age_decade="60-69")

    result = find_similar_cases(query, [weak_match, strong_match])
    assert [r.case_ref for r in result] == ["strong", "weak"]
    assert result[0].score > result[1].score


def test_tied_scores_broken_by_case_ref_ascending_for_determinism():
    query = _summary("q", cancer_type="X", age_decade=None)
    candidate_b = _summary("b", cancer_type="X", age_decade=None)
    candidate_a = _summary("a", cancer_type="X", age_decade=None)

    result = find_similar_cases(query, [candidate_b, candidate_a])
    assert [r.case_ref for r in result] == ["a", "b"]


def test_determinism_same_input_same_output_100_runs():
    query = _summary(
        "q",
        cancer_type="Synovial sarcoma",
        age_decade="30-39",
        gene_variant_types=({"gene": "EGFR", "variant_type": "missense"},),
        treatment_outcome_categories=("progressed",),
    )
    corpus = [
        _summary(
            f"c{i}",
            cancer_type="Synovial sarcoma",
            age_decade="30-39",
            gene_variant_types=({"gene": "EGFR", "variant_type": "missense"},),
        )
        for i in range(10)
    ]
    results = [find_similar_cases(query, corpus) for _ in range(100)]
    assert all(r == results[0] for r in results)


# --- corpus_size always carried on every result ------------------------------


def test_every_result_carries_the_corpus_size_it_was_drawn_from():
    """CONCEPT_EVALUATION.md SS5: 'Every result must show exactly which
    fields matched and the N it's drawn from, on screen, not just in
    documentation.' This is the field a UI reads to render that N.
    """
    query = _summary("q", cancer_type="X", age_decade=None)
    corpus = [
        _summary("c1", cancer_type="X", age_decade=None),
        _summary("c2", cancer_type="Y", age_decade=None),  # no overlap, excluded from results
        _summary("c3", cancer_type="X", age_decade=None),
    ]
    result = find_similar_cases(query, corpus)
    assert len(result) == 2  # c2 excluded (zero overlap), but still counted in corpus_size
    assert all(r.corpus_size == 3 for r in result)


def test_empty_corpus_returns_empty_list():
    query = _summary("q")
    assert find_similar_cases(query, []) == []


def test_custom_weights_override_defaults_and_zero_out_omitted_fields():
    query = _summary("q", cancer_type="X", age_decade="30-39")
    candidate = _summary("c1", cancer_type="X", age_decade="30-39")
    # Only cancer_type weighted; age_decade omitted -> contributes 0 even
    # though it also matches.
    result = find_similar_cases(query, [candidate], weights={"cancer_type": 3.0})
    assert result[0].score == 3.0
    assert set(result[0].matched_fields) == {"cancer_type", "age_decade"}


def test_similar_case_is_frozen_and_hashable_shape():
    sc = SimilarCase(case_ref="x", score=1.0, matched_fields=("cancer_type",), corpus_size=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sc.score = 2.0
