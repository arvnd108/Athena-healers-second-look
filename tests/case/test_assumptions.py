"""Direct tests for typed assumption predicates, including `triggering_event`."""

from __future__ import annotations

from secondlook.case.assumptions import (
    BiomarkerBelow,
    DiseaseNotProgressing,
    DrugNotYetTried,
    NoAlterationIn,
)
from secondlook.case.state import (
    Alteration,
    BiomarkerValue,
    CaseState,
    DiseaseAssessment,
    TreatmentEntry,
)


def test_no_alteration_in_triggering_event_is_the_last_matching_alteration():
    case = CaseState(
        case_id="case-1",
        alterations=(
            Alteration(
                gene="EGFR",
                variant="L858R",
                variant_type=None,
                assay="NGS",
                tested_on=None,
                event_id="e1",
            ),
            Alteration(
                gene="KRAS",
                variant="G12C",
                variant_type=None,
                assay="PCR",
                tested_on=None,
                event_id="e2",
            ),
            Alteration(
                gene="EGFR",
                variant="T790M",
                variant_type=None,
                assay="NGS",
                tested_on=None,
                event_id="e3",
            ),
        ),
    )
    assert NoAlterationIn(gene="EGFR").triggering_event(case) == "e3"


def test_no_alteration_in_triggering_event_is_empty_when_gene_absent():
    case = CaseState(
        case_id="case-1",
        alterations=(
            Alteration(
                gene="KRAS",
                variant="G12C",
                variant_type=None,
                assay="PCR",
                tested_on=None,
                event_id="e2",
            ),
        ),
    )
    assert NoAlterationIn(gene="EGFR").triggering_event(case) == ""


def test_biomarker_below_triggering_event_is_the_reading_event():
    case = CaseState(
        case_id="case-1",
        biomarkers={
            "TMB": BiomarkerValue(
                name="TMB", value=16.0, unit=None, measured_on=None, event_id="b2"
            )
        },
    )
    assert BiomarkerBelow(name="TMB", threshold=10.0).triggering_event(case) == "b2"


def test_biomarker_below_triggering_event_is_empty_when_reading_absent():
    assert (
        BiomarkerBelow(name="TMB", threshold=10.0).triggering_event(CaseState(case_id="case-1"))
        == ""
    )


def test_drug_not_yet_tried_triggering_event_is_the_last_matching_treatment():
    case = CaseState(
        case_id="case-1",
        treatments=(
            TreatmentEntry(
                regimen="osimertinib",
                line=None,
                action="started",
                reason=None,
                event_id="t1",
            ),
            TreatmentEntry(
                regimen="Osimertinib",
                line=None,
                action="stopped",
                reason=None,
                event_id="t2",
            ),
        ),
    )
    assert DrugNotYetTried(drug="osimertinib").triggering_event(case) == "t2"


def test_drug_not_yet_tried_triggering_event_is_empty_when_drug_absent():
    case = CaseState(
        case_id="case-1",
        treatments=(
            TreatmentEntry(
                regimen="pembrolizumab",
                line=None,
                action="started",
                reason=None,
                event_id="t3",
            ),
        ),
    )
    assert DrugNotYetTried(drug="osimertinib").triggering_event(case) == ""


def test_disease_not_progressing_triggering_event_is_the_assessment_event():
    case = CaseState(
        case_id="case-1",
        assessments=(
            DiseaseAssessment(status="progression", sites=None, assessed_on=None, event_id="d2"),
        ),
    )
    assert DiseaseNotProgressing().triggering_event(case) == "d2"


def test_disease_not_progressing_triggering_event_is_empty_when_no_assessment():
    assert DiseaseNotProgressing().triggering_event(CaseState(case_id="case-1")) == ""
