"""Offline unit tests for the inverted findings-by-evidence-key index."""

from __future__ import annotations

from secondlook.case.assumptions import DrugNotYetTried, Finding, NoAlterationIn
from secondlook.case.finding_index import FindingsByEvidenceKey


def test_lookup_returns_frozenset_not_set():
    index = FindingsByEvidenceKey()
    index.add_finding("f1", frozenset({("EGFR", "T790M")}))
    result = index.lookup("EGFR", "T790M")
    assert type(result) is frozenset
    assert result == frozenset({"f1"})


def test_exact_key_lookup():
    index = FindingsByEvidenceKey()
    index.add_finding("f1", frozenset({("EGFR", "T790M")}))
    index.add_finding("f2", frozenset({("KRAS", "G12C")}))
    assert index.lookup("EGFR", "T790M") == frozenset({"f1"})
    assert index.lookup("KRAS", "G12C") == frozenset({"f2"})
    assert index.lookup("ALK", "fusion") == frozenset()


def test_gene_level_wildcard_is_found_by_a_specific_variant_lookup():
    """A finding indexed under `(gene, "")` (from NoAlterationIn) is found
    by a lookup for `(gene, "SPECIFIC_VARIANT")`.
    """
    index = FindingsByEvidenceKey()
    finding = Finding(id="f-egfr", case_id="c1", assumptions=(NoAlterationIn("EGFR"),))
    index.add_finding(finding.id, finding.collected_evidence_keys())
    assert finding.collected_evidence_keys() == frozenset({("EGFR", "")})
    assert "f-egfr" in index.lookup("EGFR", "T790M")
    assert "f-egfr" in index.lookup("EGFR", "L858R")
    assert "f-egfr" in index.lookup("EGFR", "")
    assert "f-egfr" not in index.lookup("KRAS", "G12C")


def test_no_gene_wildcard_is_found_by_a_drug_lookup_regardless_of_gene():
    index = FindingsByEvidenceKey()
    finding = Finding(id="f-osi", case_id="c1", assumptions=(DrugNotYetTried("osimertinib"),))
    index.add_finding(finding.id, finding.collected_evidence_keys())
    assert finding.collected_evidence_keys() == frozenset({("", "osimertinib")})
    assert "f-osi" in index.lookup("EGFR", "osimertinib")
    assert "f-osi" in index.lookup("", "osimertinib")
    assert "f-osi" not in index.lookup("EGFR", "T790M")


def test_lookup_unions_exact_and_both_wildcards():
    index = FindingsByEvidenceKey()
    index.add_finding("exact", frozenset({("EGFR", "T790M")}))
    index.add_finding("gene-wild", frozenset({("EGFR", "")}))
    index.add_finding("drug-wild", frozenset({("", "T790M")}))
    index.add_finding("other", frozenset({("KRAS", "G12C")}))
    assert index.lookup("EGFR", "T790M") == frozenset({"exact", "gene-wild", "drug-wild"})


def test_remove_finding_drops_every_key_it_occupied():
    index = FindingsByEvidenceKey()
    index.add_finding("f1", frozenset({("EGFR", ""), ("KRAS", "G12C")}))
    index.add_finding("f2", frozenset({("EGFR", "")}))
    index.remove_finding("f1")
    assert "f1" not in index.lookup("EGFR", "T790M")
    assert "f1" not in index.lookup("KRAS", "G12C")
    assert index.lookup("EGFR", "T790M") == frozenset({"f2"})
    assert index.lookup("KRAS", "G12C") == frozenset()


def test_remove_unknown_finding_is_idempotent():
    index = FindingsByEvidenceKey()
    index.remove_finding("missing")
    index.add_finding("f1", frozenset({("EGFR", "T790M")}))
    index.remove_finding("missing")
    assert index.lookup("EGFR", "T790M") == frozenset({"f1"})


def test_readding_a_finding_replaces_its_keys():
    index = FindingsByEvidenceKey()
    index.add_finding("f1", frozenset({("EGFR", "T790M")}))
    index.add_finding("f1", frozenset({("KRAS", "G12C")}))
    assert index.lookup("EGFR", "T790M") == frozenset()
    assert index.lookup("KRAS", "G12C") == frozenset({"f1"})


def test_empty_keys_are_recorded_but_never_returned_by_lookup():
    index = FindingsByEvidenceKey()
    index.add_finding("f-empty", frozenset())
    assert index.lookup("EGFR", "T790M") == frozenset()
    index.remove_finding("f-empty")  # must not raise


def test_index_has_no_drift_after_1000_adds_and_50_removes():
    index = FindingsByEvidenceKey()
    n = 1000
    removed_count = 50
    for i in range(n):
        index.add_finding(f"f-{i:04d}", frozenset({(f"GENE{i}", f"VAR{i}")}))

    for i in range(removed_count):
        index.remove_finding(f"f-{i:04d}")

    for i in range(removed_count):
        assert index.lookup(f"GENE{i}", f"VAR{i}") == frozenset()

    for i in range(removed_count, n):
        fid = f"f-{i:04d}"
        assert index.lookup(f"GENE{i}", f"VAR{i}") == frozenset({fid})

    remaining = {f"f-{i:04d}" for i in range(removed_count, n)}
    indexed_ids: set[str] = set()
    for bucket in index._index.values():
        indexed_ids.update(bucket)
    assert indexed_ids == remaining
    assert set(index._finding_keys) == remaining
    assert all(index._index.values())  # no empty buckets left under stale keys
