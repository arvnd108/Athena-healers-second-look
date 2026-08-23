"""Offline unit tests for the patient-side diff engine.

No `@pytest.mark.integration`. Minimum surface from IMPLEMENTATION_PLAN.md
§3.4, plus first-assumption-wins and dict-order determinism.
"""

from __future__ import annotations

from dataclasses import dataclass

from secondlook.case.assumptions import (
    BiomarkerBelow,
    DiseaseNotProgressing,
    DrugNotYetTried,
    Finding,
    NoAlterationIn,
)
from secondlook.case.diff import (
    UNCHANGED_REASON,
    ChangeKind,
    ChangeSet,
    compute_diff,
)
from secondlook.case.evidence_state import EvidenceSnapshot
from secondlook.case.state import (
    Alteration,
    BiomarkerValue,
    CaseState,
    DiseaseAssessment,
    TreatmentEntry,
)

EGFR_T790M = Alteration(
    gene="EGFR",
    variant="T790M",
    variant_type=None,
    assay="NGS",
    tested_on=None,
    event_id="evt-alt-1",
)
EGFR_L858R = Alteration(
    gene="EGFR",
    variant="L858R",
    variant_type=None,
    assay="NGS",
    tested_on=None,
    event_id="evt-alt-2",
)
KRAS_G12C = Alteration(
    gene="KRAS",
    variant="G12C",
    variant_type=None,
    assay="PCR",
    tested_on=None,
    event_id="evt-alt-3",
)

TMB_LOW = BiomarkerValue(
    name="TMB", value=8.0, unit="mut/Mb", measured_on=None, event_id="evt-bio-1"
)
TMB_HIGH = BiomarkerValue(
    name="TMB", value=16.0, unit="mut/Mb", measured_on=None, event_id="evt-bio-2"
)
TMB_MID = BiomarkerValue(
    name="TMB", value=9.5, unit="mut/Mb", measured_on=None, event_id="evt-bio-3"
)

OSI_STARTED = TreatmentEntry(
    regimen="osimertinib", line=1, action="started", reason=None, event_id="evt-tx-1"
)
OSI_STOPPED = TreatmentEntry(
    regimen="osimertinib", line=1, action="stopped", reason=None, event_id="evt-tx-2"
)
PEMBRO = TreatmentEntry(
    regimen="pembrolizumab", line=2, action="started", reason=None, event_id="evt-tx-3"
)

STABLE = DiseaseAssessment(status="stable", sites=["lung"], assessed_on=None, event_id="evt-dx-1")
PROGRESSION = DiseaseAssessment(
    status="progression", sites=["lung", "liver"], assessed_on=None, event_id="evt-dx-2"
)
RESPONSE = DiseaseAssessment(
    status="response", sites=["lung"], assessed_on=None, event_id="evt-dx-3"
)

TMB_THRESHOLDS = {"TMB": 10.0}


def _state(
    *,
    case_id: str = "case-1",
    alterations: tuple[Alteration, ...] = (),
    biomarkers: dict[str, BiomarkerValue] | None = None,
    treatments: tuple[TreatmentEntry, ...] = (),
    assessment: DiseaseAssessment | None = None,
) -> CaseState:
    return CaseState(
        case_id=case_id,
        alterations=alterations,
        biomarkers=biomarkers if biomarkers is not None else {},
        treatments=treatments,
        assessments=(assessment,) if assessment is not None else (),
    )


def _finding(
    finding_id: str = "f1",
    *,
    case_id: str = "case-1",
    assumptions: tuple = (),
) -> Finding:
    return Finding(id=finding_id, case_id=case_id, assumptions=assumptions)


def _diff(
    previous: CaseState,
    current: CaseState,
    findings: tuple[Finding, ...] = (),
    *,
    thresholds: dict[str, float] | None = None,
    evidence: EvidenceSnapshot | None = None,
) -> ChangeSet:
    kwargs: dict = {"biomarker_thresholds": thresholds if thresholds is not None else {}}
    if evidence is not None:
        kwargs["evidence"] = evidence
    return compute_diff(previous, current, findings, **kwargs)


# ---------------------------------------------------------------------------
# New alterations
# ---------------------------------------------------------------------------


def test_new_alteration_detected():
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)))
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind is ChangeKind.NEW_ALTERATION
    assert change.summary == "EGFR T790M newly observed"
    assert change.detail == {"gene": "EGFR", "variant": "T790M", "assay": "NGS"}
    assert change.triggering_event_id == "evt-alt-1"
    assert result.unchanged_reason is None
    assert result.is_empty is False


def test_identical_states_produce_empty_changeset_with_reason():
    state = _state(alterations=(EGFR_T790M,), biomarkers={"TMB": TMB_LOW})
    result = _diff(state, state)
    assert result.changes == ()
    assert result.supersessions == ()
    assert result.is_empty is True
    assert result.unchanged_reason == UNCHANGED_REASON


def test_empty_states_produce_empty_changeset_with_reason():
    result = _diff(_state(), _state())
    assert result.is_empty is True
    assert result.unchanged_reason == UNCHANGED_REASON


def test_readding_existing_alteration_is_not_a_change():
    prev = _state(alterations=(EGFR_T790M,))
    current = _state(alterations=(EGFR_T790M,))
    result = _diff(prev, current)
    assert result.is_empty is True
    assert result.unchanged_reason == UNCHANGED_REASON


def test_same_gene_variant_different_assay_is_not_a_change():
    prev = _state(alterations=(EGFR_T790M,))
    current = _state(
        alterations=(
            Alteration(
                gene="EGFR",
                variant="T790M",
                variant_type=None,
                assay="ddPCR",
                tested_on=None,
                event_id="evt-alt-9",
            ),
        )
    )
    result = _diff(prev, current)
    assert result.is_empty is True


def test_multiple_new_alterations_both_detected_in_input_order():
    prev = _state()
    current = _state(alterations=(EGFR_T790M, KRAS_G12C))
    result = _diff(prev, current)
    assert [c.summary for c in result.changes] == [
        "EGFR T790M newly observed",
        "KRAS G12C newly observed",
    ]


def test_only_the_new_alteration_is_emitted_when_one_already_existed():
    prev = _state(alterations=(EGFR_L858R,))
    current = _state(alterations=(EGFR_L858R, EGFR_T790M))
    result = _diff(prev, current)
    assert len(result.changes) == 1
    assert result.changes[0].detail["variant"] == "T790M"


# ---------------------------------------------------------------------------
# Biomarkers
# ---------------------------------------------------------------------------


def test_biomarker_moving_within_band_is_not_a_change():
    prev = _state(biomarkers={"TMB": TMB_LOW})
    current = _state(biomarkers={"TMB": TMB_MID})
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert result.is_empty is True
    assert result.unchanged_reason == UNCHANGED_REASON


def test_biomarker_crossing_upward_is_a_change():
    prev = _state(biomarkers={"TMB": TMB_LOW})
    current = _state(biomarkers={"TMB": TMB_HIGH})
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind is ChangeKind.BIOMARKER_SHIFT
    assert change.summary == "TMB crossed 10.0 (8.0 -> 16.0)"
    assert change.detail == {"name": "TMB", "from": 8.0, "to": 16.0, "threshold": 10.0}
    assert change.triggering_event_id == "evt-bio-2"


def test_biomarker_crossing_downward_is_a_change():
    prev = _state(biomarkers={"TMB": TMB_HIGH})
    current = _state(biomarkers={"TMB": TMB_LOW})
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert len(result.changes) == 1
    assert result.changes[0].kind is ChangeKind.BIOMARKER_SHIFT
    assert result.changes[0].detail["from"] == 16.0
    assert result.changes[0].detail["to"] == 8.0


def test_threshold_absent_emits_no_biomarker_change():
    prev = _state(biomarkers={"TMB": TMB_LOW})
    current = _state(biomarkers={"TMB": TMB_HIGH})
    result = _diff(prev, current, thresholds={})
    assert result.is_empty is True
    assert result.unchanged_reason == UNCHANGED_REASON


def test_first_appearance_of_biomarker_is_not_a_crossing():
    prev = _state()
    current = _state(biomarkers={"TMB": TMB_HIGH})
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert result.is_empty is True


def test_value_landing_exactly_on_threshold_is_a_crossing_from_below():
    prev = _state(
        biomarkers={
            "TMB": BiomarkerValue(name="TMB", value=9.0, unit=None, measured_on=None, event_id="b1")
        }
    )
    current = _state(
        biomarkers={
            "TMB": BiomarkerValue(
                name="TMB", value=10.0, unit=None, measured_on=None, event_id="b2"
            )
        }
    )
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert len(result.changes) == 1
    assert result.changes[0].kind is ChangeKind.BIOMARKER_SHIFT


def test_both_sides_at_or_above_threshold_is_not_a_crossing():
    prev = _state(
        biomarkers={
            "TMB": BiomarkerValue(
                name="TMB", value=10.0, unit=None, measured_on=None, event_id="b1"
            )
        }
    )
    current = _state(
        biomarkers={
            "TMB": BiomarkerValue(
                name="TMB", value=12.0, unit=None, measured_on=None, event_id="b2"
            )
        }
    )
    result = _diff(prev, current, thresholds=TMB_THRESHOLDS)
    assert result.is_empty is True


def test_biomarker_dict_insertion_order_does_not_affect_output():
    tmb = BiomarkerValue(name="TMB", value=16.0, unit=None, measured_on=None, event_id="b-tmb")
    msi = BiomarkerValue(name="MSI", value=40.0, unit=None, measured_on=None, event_id="b-msi")
    prev_low = {
        "TMB": BiomarkerValue(name="TMB", value=8.0, unit=None, measured_on=None, event_id="p-tmb"),
        "MSI": BiomarkerValue(name="MSI", value=5.0, unit=None, measured_on=None, event_id="p-msi"),
    }
    thresholds = {"TMB": 10.0, "MSI": 20.0}
    forward = _diff(
        _state(biomarkers=prev_low),
        _state(biomarkers={"TMB": tmb, "MSI": msi}),
        thresholds=thresholds,
    )
    reverse = _diff(
        _state(biomarkers={"MSI": prev_low["MSI"], "TMB": prev_low["TMB"]}),
        _state(biomarkers={"MSI": msi, "TMB": tmb}),
        thresholds=thresholds,
    )
    assert forward == reverse
    assert [c.detail["name"] for c in forward.changes] == ["MSI", "TMB"]


# ---------------------------------------------------------------------------
# Treatments
# ---------------------------------------------------------------------------


def test_treatment_started_is_a_change():
    result = _diff(_state(), _state(treatments=(OSI_STARTED,)))
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind is ChangeKind.TREATMENT_LINE_CHANGE
    assert change.summary == "osimertinib started"
    assert change.detail == {"regimen": "osimertinib", "action": "started", "line": 1}
    assert change.triggering_event_id == "evt-tx-1"


def test_treatment_stopped_is_a_change():
    prev = _state(treatments=(OSI_STARTED,))
    current = _state(treatments=(OSI_STARTED, OSI_STOPPED))
    result = _diff(prev, current)
    assert len(result.changes) == 1
    assert result.changes[0].summary == "osimertinib stopped"
    assert result.changes[0].detail["action"] == "stopped"


def test_readding_existing_treatment_is_not_a_change():
    prev = _state(treatments=(OSI_STARTED,))
    current = _state(treatments=(OSI_STARTED,))
    result = _diff(prev, current)
    assert result.is_empty is True


def test_treatment_identity_is_case_insensitive_on_regimen():
    prev = _state(treatments=(OSI_STARTED,))
    current = _state(
        treatments=(
            TreatmentEntry(
                regimen="Osimertinib",
                line=1,
                action="started",
                reason=None,
                event_id="evt-tx-9",
            ),
        )
    )
    result = _diff(prev, current)
    assert result.is_empty is True


def test_second_line_of_a_new_drug_is_a_change():
    prev = _state(treatments=(OSI_STARTED,))
    current = _state(treatments=(OSI_STARTED, PEMBRO))
    result = _diff(prev, current)
    assert len(result.changes) == 1
    assert result.changes[0].detail["regimen"] == "pembrolizumab"


# ---------------------------------------------------------------------------
# Disease assessment
# ---------------------------------------------------------------------------


def test_disease_progression_recorded():
    prev = _state(assessment=STABLE)
    current = _state(assessment=PROGRESSION)
    result = _diff(prev, current)
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind is ChangeKind.DISEASE_PROGRESSION
    assert change.summary == "disease progression recorded"
    assert change.detail["status"] == "progression"
    assert change.detail["from"] == "stable"
    assert change.triggering_event_id == "evt-dx-2"


def test_first_assessment_as_stable_is_a_change():
    result = _diff(_state(), _state(assessment=STABLE))
    assert len(result.changes) == 1
    assert result.changes[0].summary == "disease recorded as stable"


def test_unchanged_assessment_is_not_a_change():
    result = _diff(_state(assessment=STABLE), _state(assessment=STABLE))
    assert result.is_empty is True


def test_response_after_progression_is_still_emitted():
    result = _diff(_state(assessment=PROGRESSION), _state(assessment=RESPONSE))
    assert len(result.changes) == 1
    assert result.changes[0].detail["status"] == "response"
    assert result.changes[0].detail["from"] == "progression"


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


def test_supersession_fires_when_no_alteration_in_breaks():
    finding = _finding(assumptions=(NoAlterationIn(gene="EGFR"),))
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)), (finding,))
    assert len(result.supersessions) == 1
    sup = result.supersessions[0]
    assert sup.finding_id == "f1"
    assert sup.broken_assumption == "no known alteration in EGFR"
    assert sup.triggering_event_id == "evt-alt-1"
    assert sup.note == ("This finding assumed no known alteration in EGFR. That is no longer true.")


def test_supersession_does_not_fire_for_an_unrelated_finding():
    finding = _finding(assumptions=(NoAlterationIn(gene="ALK"),))
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)), (finding,))
    assert result.supersessions == ()
    assert len(result.changes) == 1


def test_finding_with_zero_assumptions_is_never_superseded():
    finding = _finding(assumptions=())
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)), (finding,))
    assert result.supersessions == ()
    assert len(result.changes) == 1


def test_multiple_broken_assumptions_produce_exactly_one_supersession():
    finding = _finding(
        assumptions=(
            NoAlterationIn(gene="EGFR"),
            DrugNotYetTried(drug="osimertinib"),
        )
    )
    current = _state(alterations=(EGFR_T790M,), treatments=(OSI_STARTED,))
    result = _diff(_state(), current, (finding,))
    assert len(result.supersessions) == 1


def test_first_assumption_in_iteration_order_wins_when_several_break():
    finding = _finding(
        assumptions=(
            NoAlterationIn(gene="EGFR"),
            DrugNotYetTried(drug="osimertinib"),
            DiseaseNotProgressing(),
        )
    )
    current = _state(
        alterations=(EGFR_T790M,),
        treatments=(OSI_STARTED,),
        assessment=PROGRESSION,
    )
    result = _diff(_state(), current, (finding,))
    assert len(result.supersessions) == 1
    assert result.supersessions[0].broken_assumption == "no known alteration in EGFR"


def test_second_assumption_wins_when_the_first_still_holds():
    finding = _finding(
        assumptions=(
            NoAlterationIn(gene="ALK"),
            DrugNotYetTried(drug="osimertinib"),
        )
    )
    current = _state(alterations=(EGFR_T790M,), treatments=(OSI_STARTED,))
    result = _diff(_state(), current, (finding,))
    assert len(result.supersessions) == 1
    assert result.supersessions[0].broken_assumption == ("osimertinib not previously administered")


def test_biomarker_below_breaks_when_value_crosses_threshold():
    finding = _finding(assumptions=(BiomarkerBelow(name="TMB", threshold=10.0),))
    prev = _state(biomarkers={"TMB": TMB_LOW})
    current = _state(biomarkers={"TMB": TMB_HIGH})
    result = _diff(prev, current, (finding,), thresholds=TMB_THRESHOLDS)
    assert len(result.supersessions) == 1
    assert result.supersessions[0].broken_assumption == "TMB below 10.0"
    assert result.supersessions[0].triggering_event_id == "evt-bio-2"


def test_biomarker_below_holds_when_biomarker_is_absent():
    finding = _finding(assumptions=(BiomarkerBelow(name="TMB", threshold=10.0),))
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)), (finding,))
    assert result.supersessions == ()


def test_drug_not_yet_tried_breaks_case_insensitively():
    finding = _finding(assumptions=(DrugNotYetTried(drug="Osimertinib"),))
    current = _state(treatments=(OSI_STARTED,))
    result = _diff(_state(), current, (finding,))
    assert len(result.supersessions) == 1
    assert result.supersessions[0].triggering_event_id == "evt-tx-1"


def test_disease_not_progressing_breaks_on_progression():
    finding = _finding(assumptions=(DiseaseNotProgressing(),))
    result = _diff(_state(assessment=STABLE), _state(assessment=PROGRESSION), (finding,))
    assert len(result.supersessions) == 1
    assert result.supersessions[0].broken_assumption == ("disease not recorded as progressing")
    assert result.supersessions[0].triggering_event_id == "evt-dx-2"


def test_disease_not_progressing_holds_on_stable_and_response_and_none():
    finding = _finding(assumptions=(DiseaseNotProgressing(),))
    for assessment in (None, STABLE, RESPONSE):
        result = _diff(_state(), _state(assessment=assessment), (finding,))
        assert result.supersessions == ()


def test_new_alteration_and_supersession_together():
    finding = _finding(assumptions=(NoAlterationIn(gene="EGFR"),))
    result = _diff(_state(), _state(alterations=(EGFR_T790M,)), (finding,))
    assert len(result.changes) == 1
    assert len(result.supersessions) == 1
    assert result.unchanged_reason is None


def test_identical_states_still_supersede_already_invalid_findings():
    finding = _finding(assumptions=(NoAlterationIn(gene="EGFR"),))
    state = _state(alterations=(EGFR_T790M,))
    result = _diff(state, state, (finding,))
    assert result.changes == ()
    assert len(result.supersessions) == 1
    assert result.unchanged_reason is None


def test_assumption_describe_strings_match_the_spec():
    assert NoAlterationIn(gene="EGFR").describe() == "no known alteration in EGFR"
    assert BiomarkerBelow(name="TMB", threshold=10.0).describe() == "TMB below 10.0"
    assert DrugNotYetTried(drug="osimertinib").describe() == (
        "osimertinib not previously administered"
    )
    assert DiseaseNotProgressing().describe() == "disease not recorded as progressing"


def test_no_alteration_in_holds_when_gene_is_absent():
    snapshot = EvidenceSnapshot()
    assert NoAlterationIn(gene="EGFR").holds(_state(alterations=(KRAS_G12C,)), snapshot)


def test_evidence_keys_match_the_wildcard_convention():
    assert NoAlterationIn(gene="EGFR").evidence_keys() == frozenset({("EGFR", "")})
    assert DrugNotYetTried(drug="osimertinib").evidence_keys() == frozenset({("", "osimertinib")})
    assert BiomarkerBelow(name="TMB", threshold=10.0).evidence_keys() == frozenset()
    assert DiseaseNotProgressing().evidence_keys() == frozenset()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_compute_diff_is_byte_identical_across_100_runs():
    finding_a = _finding(
        "f-a",
        assumptions=(NoAlterationIn(gene="EGFR"), DrugNotYetTried(drug="osimertinib")),
    )
    finding_b = _finding("f-b", assumptions=(BiomarkerBelow(name="TMB", threshold=10.0),))
    previous = _state(
        alterations=(EGFR_L858R,),
        biomarkers={
            "MSI": BiomarkerValue(
                name="MSI", value=5.0, unit=None, measured_on=None, event_id="p-msi"
            ),
            "TMB": TMB_LOW,
        },
        treatments=(),
        assessment=STABLE,
    )
    current = _state(
        alterations=(EGFR_L858R, EGFR_T790M),
        biomarkers={
            "TMB": TMB_HIGH,
            "MSI": BiomarkerValue(
                name="MSI", value=40.0, unit=None, measured_on=None, event_id="c-msi"
            ),
        },
        treatments=(OSI_STARTED,),
        assessment=PROGRESSION,
    )
    first = _diff(
        previous,
        current,
        (finding_a, finding_b),
        thresholds={"TMB": 10.0, "MSI": 20.0},
    )
    for _ in range(100):
        again = _diff(
            previous,
            current,
            (finding_a, finding_b),
            thresholds={"TMB": 10.0, "MSI": 20.0},
        )
        assert again == first
        assert repr(again) == repr(first)


@dataclass(frozen=True)
class _AlwaysBroken:
    """Non-built-in Assumption: regression for the old isinstance dispatcher."""

    event_id: str

    def holds(self, case: CaseState, evidence: EvidenceSnapshot) -> bool:
        return False

    def describe(self) -> str:
        return "custom always-broken"

    def evidence_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset()

    def triggering_event(self, case: CaseState) -> str:
        return self.event_id


def test_custom_assumption_supersedes_without_diff_knowing_its_type():
    finding = _finding(assumptions=(_AlwaysBroken(event_id="evt-custom"),))
    result = _diff(_state(), _state(), (finding,))
    assert len(result.supersessions) == 1
    sup = result.supersessions[0]
    assert sup.finding_id == "f1"
    assert sup.broken_assumption == "custom always-broken"
    assert sup.triggering_event_id == "evt-custom"
    assert sup.note == ("This finding assumed custom always-broken. That is no longer true.")
