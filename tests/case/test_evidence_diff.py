"""Offline unit tests for the evidence-side reverse diff."""

from __future__ import annotations

from dataclasses import dataclass

from secondlook.case.assumptions import Finding, NoAlterationIn
from secondlook.case.evidence_diff import (
    NO_AFFECTED_REASON,
    UNRESOLVED_REASON,
    EvidenceChangeEvent,
    compute_affected_findings,
)
from secondlook.case.evidence_state import EvidenceSnapshot
from secondlook.case.finding_index import FindingsByEvidenceKey
from secondlook.case.state import CaseState


@dataclass
class SpyAssumption:
    """Records every `holds()` call so tests can assert what was re-evaluated."""

    gene: str
    log: list[str]
    variant_or_drug: str = ""
    should_hold: bool = True

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        self.log.append(self.gene)
        return self.should_hold

    def describe(self) -> str:
        return f"spy on {self.gene}"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset({(self.gene, self.variant_or_drug)})

    def triggering_event(self, case: CaseState) -> str:
        return ""


@dataclass(frozen=True)
class EvidenceAbsent:
    """Holds only while the snapshot has no item at this key — test double
    for a future evidence-aware assumption (the four built-in types ignore
    the snapshot).
    """

    gene: str
    variant_or_drug: str

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        return (self.gene, self.variant_or_drug) not in evidence

    def describe(self) -> str:
        return f"no evidence item for {self.gene} {self.variant_or_drug}"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset({(self.gene, self.variant_or_drug)})

    def triggering_event(self, case: CaseState) -> str:
        return ""


def _event(
    *,
    gene: str,
    variant_or_drug: str,
    node_id: str = "node-1",
    node_type: str = "EvidenceItem",
) -> EvidenceChangeEvent:
    return EvidenceChangeEvent(
        node_type=node_type,
        node_id=node_id,
        old_props={},
        new_props={"level": "A"},
        source="CIViC",
        retrieved_at="2026-08-23T12:00:00Z",
        gene=gene,
        variant_or_drug=variant_or_drug,
    )


def _index_findings(*findings: Finding) -> FindingsByEvidenceKey:
    index = FindingsByEvidenceKey()
    for finding in findings:
        index.add_finding(finding.id, finding.collected_evidence_keys())
    return index


def test_new_evidence_for_gene_x_does_not_reevaluate_a_finding_indexed_under_gene_y():
    log: list[str] = []
    finding_x = Finding(
        id="fx",
        case_id="case-x",
        assumptions=(SpyAssumption(gene="EGFR", log=log, should_hold=False),),
    )
    finding_y = Finding(
        id="fy",
        case_id="case-y",
        assumptions=(SpyAssumption(gene="KRAS", log=log, should_hold=False),),
    )
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        _index_findings(finding_x, finding_y),
        {"fx": finding_x, "fy": finding_y},
        EvidenceSnapshot(),
    )
    assert log == ["EGFR"]
    assert report.finding_ids == ("fx",)
    assert "fy" not in report.finding_ids
    assert report.affected_case_ids == ("case-x",)


def test_only_matching_assumptions_on_a_finding_are_reevaluated():
    log: list[str] = []
    finding = Finding(
        id="f-mixed",
        case_id="c1",
        assumptions=(
            SpyAssumption(gene="EGFR", log=log, should_hold=True),
            SpyAssumption(gene="KRAS", log=log, should_hold=False),
        ),
    )
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        _index_findings(finding),
        {"f-mixed": finding},
        EvidenceSnapshot(),
    )
    assert log == ["EGFR"]
    assert report.finding_ids == ()
    assert report.empty_reason == NO_AFFECTED_REASON


def test_finding_with_zero_assumptions_is_never_superseded_on_the_evidence_path():
    finding = Finding(id="f-empty", case_id="c1", assumptions=())
    index = FindingsByEvidenceKey()
    # Force-index it under a key so lookup would find it if assumptions ran.
    index.add_finding(finding.id, frozenset({("EGFR", "T790M")}))
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        index,
        {"f-empty": finding},
        EvidenceSnapshot(items={("EGFR", "T790M"): {"level": "A"}}),
    )
    assert report.finding_ids == ()
    assert report.empty_reason == NO_AFFECTED_REASON


def test_wildcard_no_alteration_in_is_found_for_a_specific_variant_event():
    log: list[str] = []
    finding = Finding(
        id="f-wild",
        case_id="c1",
        assumptions=(
            NoAlterationIn(gene="EGFR"),
            SpyAssumption(gene="EGFR", log=log, variant_or_drug="", should_hold=True),
        ),
    )
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        _index_findings(finding),
        {"f-wild": finding},
        EvidenceSnapshot(),
    )
    assert "EGFR" in log
    # NoAlterationIn looks at case state, which is empty, so it still holds.
    assert report.finding_ids == ()


def test_evidence_aware_assumption_breaks_when_snapshot_contains_the_item():
    finding = Finding(
        id="f-ev",
        case_id="case-47",
        assumptions=(EvidenceAbsent(gene="EGFR", variant_or_drug="T790M"),),
    )
    snapshot = EvidenceSnapshot(items={("EGFR", "T790M"): {"civic_id": "EID999"}})
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        _index_findings(finding),
        {"f-ev": finding},
        snapshot,
    )
    assert report.finding_ids == ("f-ev",)
    assert report.affected_case_ids == ("case-47",)
    assert report.empty_reason is None


def test_results_are_grouped_by_case_and_sorted():
    findings = []
    for fid, case_id in (
        ("f-c", "case-b"),
        ("f-a", "case-a"),
        ("f-b", "case-a"),
        ("f-d", "case-c"),
    ):
        findings.append(
            Finding(
                id=fid,
                case_id=case_id,
                assumptions=(EvidenceAbsent(gene="EGFR", variant_or_drug="T790M"),),
            )
        )
    by_id = {f.id: f for f in findings}
    snapshot = EvidenceSnapshot(items={("EGFR", "T790M"): {}})
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        _index_findings(*findings),
        by_id,
        snapshot,
    )
    assert report.finding_ids == ("f-a", "f-b", "f-c", "f-d")
    assert report.affected_case_ids == ("case-a", "case-b", "case-c")


def test_unresolved_indexed_ids_are_reported_not_silently_dropped():
    finding = Finding(
        id="f-known",
        case_id="c1",
        assumptions=(EvidenceAbsent(gene="EGFR", variant_or_drug="T790M"),),
    )
    index = _index_findings(finding)
    index.add_finding("f-ghost", frozenset({("EGFR", "T790M")}))
    snapshot = EvidenceSnapshot(items={("EGFR", "T790M"): {}})
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        index,
        {"f-known": finding},
        snapshot,
    )
    assert report.finding_ids == ("f-known",)
    assert report.unresolved_finding_ids == ("f-ghost",)
    assert report.unresolved_reason == UNRESOLVED_REASON


def test_empty_report_carries_a_reason():
    report = compute_affected_findings(
        _event(gene="EGFR", variant_or_drug="T790M"),
        FindingsByEvidenceKey(),
        {},
        EvidenceSnapshot(),
    )
    assert report.finding_ids == ()
    assert report.affected_case_ids == ()
    assert report.empty_reason == NO_AFFECTED_REASON


def test_compute_affected_findings_is_byte_identical_across_100_runs():
    findings = (
        Finding(
            id="f-2",
            case_id="case-z",
            assumptions=(EvidenceAbsent(gene="EGFR", variant_or_drug="T790M"),),
        ),
        Finding(
            id="f-1",
            case_id="case-a",
            assumptions=(EvidenceAbsent(gene="EGFR", variant_or_drug="T790M"),),
        ),
        Finding(
            id="f-hold",
            case_id="case-a",
            assumptions=(EvidenceAbsent(gene="KRAS", variant_or_drug="G12C"),),
        ),
    )
    index = _index_findings(*findings)
    by_id = {f.id: f for f in findings}
    event = _event(gene="EGFR", variant_or_drug="T790M")
    snapshot = EvidenceSnapshot(items={("EGFR", "T790M"): {"level": "A"}})
    first = compute_affected_findings(event, index, by_id, snapshot)
    for _ in range(100):
        again = compute_affected_findings(event, index, by_id, snapshot)
        assert again == first
        assert repr(again) == repr(first)
    assert first.finding_ids == ("f-1", "f-2")
    assert first.affected_case_ids == ("case-a", "case-z")
