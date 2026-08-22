"""Unit tests for the eval harness itself (run_eval.py) -- NOT a test of
real retrieval quality. Retrieval functions are mocked; the point here is
to confirm the harness's own logic: per-bucket breakdown counts, and the
mislabeled-vs-not-found distinction, are computed correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from secondlook.tier1.eval import run_eval as run_eval_module
from secondlook.tier1.eval.run_eval import GroundTruthError, load_ground_truth, run_eval


class _Result:
    """Shape of what a retrieval function (retrieve_exact/_relaxed/
    _semantic) returns -- `.items`."""

    def __init__(self, items):
        self.items = items


class _QueryResult:
    """Shape of what graph.query() itself returns -- `.result_set`. A
    different shape from _Result on purpose: conflating the two silently
    breaks run_eval.py's own direct graph.query() call
    (_mode3_data_is_loaded), which is a genuinely different boundary from
    the mocked retrieval functions."""

    def __init__(self, result_set):
        self.result_set = result_set


class FakeGraph:
    """Only answers the one query run_eval.py issues directly
    (_mode3_data_is_loaded); every real retrieval call is mocked out
    separately, so this never needs to look like a real graph."""

    def __init__(self, mode3_loaded: bool = True):
        self._mode3_loaded = mode3_loaded

    def query(self, cypher: str, params: dict | None = None):
        if "Publication" in cypher:
            return _QueryResult([(1 if self._mode3_loaded else 0,)])
        raise AssertionError(f"unexpected query on FakeGraph: {cypher}")


def _write_ground_truth(tmp_path: Path, pairs: list[dict]) -> Path:
    path = tmp_path / "ground_truth.yaml"
    path.write_text(yaml.safe_dump({"config_version": 1, "pairs": pairs}), encoding="utf-8")
    return path


BASE_PAIR = {
    "gene": None,
    "key_type": None,
    "key_value": None,
    "disease": None,
    "drug_class": None,
    "query_text": None,
    "expected_relaxation_type": None,
    "expected_citation_id": None,
}


class TestLoadGroundTruth:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GroundTruthError, match="not found"):
            load_ground_truth(tmp_path / "nope.yaml")

    def test_missing_config_version_raises(self, tmp_path):
        path = tmp_path / "gt.yaml"
        path.write_text(yaml.safe_dump({"pairs": []}), encoding="utf-8")
        with pytest.raises(GroundTruthError, match="config_version"):
            load_ground_truth(path)

    def test_empty_pairs_raises(self, tmp_path):
        path = tmp_path / "gt.yaml"
        path.write_text(yaml.safe_dump({"config_version": 1, "pairs": []}), encoding="utf-8")
        with pytest.raises(GroundTruthError, match="at least one pair"):
            load_ground_truth(path)

    def test_pair_missing_required_key_raises(self, tmp_path):
        bad_pair = {**BASE_PAIR, "id": "x", "bucket": "mode1_exact", "expected_mode": "exact"}
        del bad_pair["expected_citation_id"]
        path = _write_ground_truth(tmp_path, [bad_pair])
        with pytest.raises(GroundTruthError, match="missing keys"):
            load_ground_truth(path)

    def test_unknown_bucket_raises(self, tmp_path):
        pair = {
            **BASE_PAIR,
            "id": "x",
            "bucket": "not_a_real_bucket",
            "expected_mode": "exact",
            "expected_citation_id": "1",
        }
        path = _write_ground_truth(tmp_path, [pair])
        with pytest.raises(GroundTruthError, match="unknown bucket"):
            load_ground_truth(path)


class TestRunEval:
    @pytest.fixture(autouse=True)
    def stub_retrieval(self, monkeypatch):
        self.exact_calls = []
        self.relaxed_calls = []
        self.semantic_calls = []

        def fake_exact(gene, hgvs_p, *, variant_name=None, graph=None):
            self.exact_calls.append((gene, hgvs_p, variant_name))
            if gene == "GENE_PASS":
                return _Result(
                    [
                        {
                            "citation": {"id": "100"},
                            "retrieval_mode": "exact",
                            "relaxation_type": None,
                            "match_key": "hgvs_p",
                        }
                    ]
                )
            if gene == "GENE_NEGATIVE_HIT":
                # A negative-expectation pair that unexpectedly finds something.
                return _Result(
                    [
                        {
                            "citation": {"id": "999"},
                            "retrieval_mode": "exact",
                            "relaxation_type": None,
                            "match_key": "hgvs_p",
                        }
                    ]
                )
            if gene == "GENE_WRONG_MODE":
                # Right citation, but retrieval_mode says "relaxed" on an
                # item that came back from retrieve_exact -- e.g. a future
                # bug where _row_to_item's retrieval_mode argument gets
                # passed through wrong.
                return _Result(
                    [
                        {
                            "citation": {"id": "300"},
                            "retrieval_mode": "relaxed",
                            "relaxation_type": None,
                            "match_key": "hgvs_p",
                        }
                    ]
                )
            if gene == "GENE_WRONG_MATCH_KEY":
                # Right citation, right retrieval_mode, but match_key says
                # "variant_name" when the pair queried by hgvs_p.
                return _Result(
                    [
                        {
                            "citation": {"id": "400"},
                            "retrieval_mode": "exact",
                            "relaxation_type": None,
                            "match_key": "variant_name",
                        }
                    ]
                )
            return _Result([])

        def fake_relaxed(
            gene, hgvs_p, disease=None, *, variant_name=None, drug_class=None, graph=None
        ):
            self.relaxed_calls.append((gene, hgvs_p, disease, variant_name, drug_class))
            if gene == "GENE_MISLABELED":
                # Right citation, but relaxation_type doesn't match what's expected.
                return _Result(
                    [
                        {
                            "citation": {"id": "200"},
                            "retrieval_mode": "relaxed",
                            "relaxation_type": "same_disease_drug_class",
                            "match_key": None,
                        }
                    ]
                )
            return _Result([])

        def fake_semantic(query_text, top_k=10, *, graph=None):
            self.semantic_calls.append(query_text)
            if query_text == "MISLABELED_EVIDENCE_LEVEL_QUERY":
                # Right citation, right retrieval_mode, but evidence_level
                # is a CIViC-style grade instead of "literature".
                return _Result(
                    [
                        {
                            "citation": {"id": "500"},
                            "retrieval_mode": "semantic",
                            "relaxation_type": None,
                            "match_key": None,
                            "evidence_level": "A",
                        }
                    ]
                )
            return _Result([])  # always "not_found" otherwise

        monkeypatch.setattr(run_eval_module, "retrieve_exact", fake_exact)
        monkeypatch.setattr(run_eval_module, "retrieve_relaxed", fake_relaxed)
        monkeypatch.setattr(run_eval_module, "retrieve_semantic", fake_semantic)

    def test_pass_not_found_mislabeled_and_unexpected_hit_are_distinguished(self, tmp_path):
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode1_exact",
                "gene": "GENE_PASS",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "exact",
                "expected_citation_id": "100",
            },
            {
                **BASE_PAIR,
                "id": "p2",
                "bucket": "mode1_exact_negative",
                "gene": "GENE_NEGATIVE_HIT",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "none",
                "expected_citation_id": None,
            },
            {
                **BASE_PAIR,
                "id": "p3",
                "bucket": "mode2_path_a",
                "gene": "GENE_MISLABELED",
                "key_type": "variant_name",
                "key_value": "x",
                "expected_mode": "relaxed",
                "expected_relaxation_type": "same_gene_different_variant",
                "expected_citation_id": "200",
            },
            {
                **BASE_PAIR,
                "id": "p4",
                "bucket": "mode3_semantic",
                "query_text": "some query",
                "expected_mode": "semantic",
                "expected_citation_id": "300",
            },
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))

        outcomes = {r.id: r.outcome for r in summary.results}
        assert outcomes["p1"] == "pass"
        assert outcomes["p2"] == "unexpected_hit"
        assert outcomes["p3"] == "mislabeled", "right citation via the wrong relaxation_type"
        assert outcomes["p4"] == "not_found"

    def test_semantic_pair_with_wrong_evidence_level_is_mislabeled_not_a_pass(self, tmp_path):
        """The gap a reviewer flagged: finding the right PMID with
        retrieval_mode=='semantic' is not sufficient -- evidence_level
        must actually be 'literature', not silently assumed correct."""
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode3_semantic",
                "query_text": "MISLABELED_EVIDENCE_LEVEL_QUERY",
                "expected_mode": "semantic",
                "expected_citation_id": "500",
            }
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))

        assert summary.results[0].outcome == "mislabeled"
        assert "evidence_level" in summary.results[0].detail

    def test_exact_pair_with_wrong_retrieval_mode_is_mislabeled_not_a_pass(self, tmp_path):
        """The eval harness judges everything else's correctness -- its own
        mislabeling checks need the same coverage as the ones it checks
        for. Right citation, but retrieval_mode=='relaxed' on what came
        back from retrieve_exact must not read as a pass."""
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode1_exact",
                "gene": "GENE_WRONG_MODE",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "exact",
                "expected_citation_id": "300",
            }
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))

        assert summary.results[0].outcome == "mislabeled"
        assert "retrieval_mode" in summary.results[0].detail

    def test_exact_pair_with_wrong_match_key_is_mislabeled_not_a_pass(self, tmp_path):
        """Right citation, right retrieval_mode, but match_key says
        'variant_name' when the pair queried by hgvs_p -- must not read as
        a pass either."""
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode1_exact",
                "gene": "GENE_WRONG_MATCH_KEY",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "exact",
                "expected_citation_id": "400",
            }
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))

        assert summary.results[0].outcome == "mislabeled"
        assert "match_key" in summary.results[0].detail

    def test_per_display_group_counts_are_correct(self, tmp_path):
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode1_exact",
                "gene": "GENE_PASS",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "exact",
                "expected_citation_id": "100",
            },
            {
                **BASE_PAIR,
                "id": "p2",
                "bucket": "mode1_exact_negative",
                "gene": "GENE_NOWHERE",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "none",
                "expected_citation_id": None,
            },
            {
                **BASE_PAIR,
                "id": "p3",
                "bucket": "mode2_path_b",
                "gene": "GENE_NOWHERE",
                "key_type": "variant_name",
                "key_value": "x",
                "disease": "D",
                "drug_class": "C",
                "expected_mode": "relaxed",
                "expected_relaxation_type": "same_disease_drug_class",
                "expected_citation_id": "400",
            },
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))
        by_group = summary.counts_by_display_group()

        # p1 (GENE_PASS, found) and p2 (GENE_NOWHERE, correctly empty
        # negative) are both "pass", just for different reasons.
        assert by_group["exact"]["pass"] == 2
        assert by_group["exact"]["unexpected_hit"] == 0
        assert by_group["relaxed_drug_class"]["not_found"] == 1
        assert by_group["relaxed_same_gene"] == dict.fromkeys(run_eval_module.OUTCOMES, 0)
        assert by_group["semantic"] == dict.fromkeys(run_eval_module.OUTCOMES, 0)

    def test_mode3_bucket_is_skipped_and_reported_when_no_publication_data_exists(self, tmp_path):
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode3_semantic",
                "query_text": "some query",
                "expected_mode": "semantic",
                "expected_citation_id": "300",
            }
        ]
        path = _write_ground_truth(tmp_path, pairs)

        summary = run_eval(path, graph=FakeGraph(mode3_loaded=False))

        assert summary.results == [], "the skipped pair must not be scored at all"
        assert "mode3_semantic" in summary.skipped_buckets
        assert self.semantic_calls == [], "a skipped bucket must never call retrieve_semantic"

    def test_dispatches_to_the_correct_function_per_bucket(self, tmp_path):
        """An exact-bucket pair must call retrieve_exact, not "try
        everything" -- confirmed by checking which fake got called."""
        pairs = [
            {
                **BASE_PAIR,
                "id": "p1",
                "bucket": "mode1_exact",
                "gene": "G",
                "key_type": "hgvs_p",
                "key_value": "x",
                "expected_mode": "exact",
                "expected_citation_id": None,
            },
            {
                **BASE_PAIR,
                "id": "p2",
                "bucket": "mode2_path_a",
                "gene": "G",
                "key_type": "variant_name",
                "key_value": "y",
                "expected_mode": "relaxed",
                "expected_citation_id": None,
            },
        ]
        path = _write_ground_truth(tmp_path, pairs)

        run_eval(path, graph=FakeGraph(mode3_loaded=True))

        assert len(self.exact_calls) == 1
        assert len(self.relaxed_calls) == 1
        assert len(self.semantic_calls) == 0


def test_summary_to_dict_is_json_serializable(tmp_path, monkeypatch):
    import json

    pairs = [
        {
            **BASE_PAIR,
            "id": "p1",
            "bucket": "mode1_exact",
            "gene": "GENE_PASS",
            "key_type": "hgvs_p",
            "key_value": "x",
            "expected_mode": "exact",
            "expected_citation_id": None,
        }
    ]
    path = _write_ground_truth(tmp_path, pairs)

    def fake_exact(gene, hgvs_p, *, variant_name=None, graph=None):
        return _Result([])

    monkeypatch.setattr(run_eval_module, "retrieve_exact", fake_exact)
    summary = run_eval(path, graph=FakeGraph(mode3_loaded=True))

    json.dumps(summary.to_dict())
