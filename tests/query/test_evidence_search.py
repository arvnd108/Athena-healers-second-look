from __future__ import annotations

from unittest.mock import patch

from secondlook.query.evidence_search import search_evidence
from secondlook.tier1.retrieval import RetrievalResult


def test_missing_variant_is_empty_with_reason():
    result = search_evidence("EGFR")
    assert result.items == ()
    assert "variant" in result.empty_reason


def test_exact_hit_does_not_call_relaxed():
    exact = RetrievalResult(items=[{"retrieval_mode": "exact", "drug": "osimertinib"}])
    with (
        patch("secondlook.query.evidence_search.retrieve_exact", return_value=exact) as exact_fn,
        patch("secondlook.query.evidence_search.retrieve_relaxed") as relaxed_fn,
    ):
        result = search_evidence("EGFR", "T790M")
    exact_fn.assert_called_once()
    relaxed_fn.assert_not_called()
    assert result.items[0].retrieval_mode == "exact"
    assert result.empty_reason is None


def test_relaxed_runs_when_exact_is_empty():
    empty = RetrievalResult(items=[])
    relaxed = RetrievalResult(items=[{"retrieval_mode": "relaxed", "drug": "gefitinib"}])
    with (
        patch("secondlook.query.evidence_search.retrieve_exact", return_value=empty),
        patch("secondlook.query.evidence_search.retrieve_relaxed", return_value=relaxed),
    ):
        result = search_evidence("EGFR", "p.Thr790Met", cancer_type="NSCLC")
    assert result.items[0].retrieval_mode == "relaxed"


def test_both_empty_is_empty_with_reason():
    empty = RetrievalResult(items=[])
    with (
        patch("secondlook.query.evidence_search.retrieve_exact", return_value=empty),
        patch("secondlook.query.evidence_search.retrieve_relaxed", return_value=empty),
        patch("secondlook.query.evidence_search.semantic_retrieval", create=True) as semantic,
    ):
        result = search_evidence("EGFR", "T790M")
    semantic.assert_not_called()
    assert result.items == ()
    assert "no evidence" in result.empty_reason
