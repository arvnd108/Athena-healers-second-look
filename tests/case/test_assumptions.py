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
    BiomarkerReading,
    CaseState,
    DiseaseAssessment,
    Treatment,
)


def test_no_alteration_in_triggering_event_is_the_last_matching_alteration():
    case = CaseState(
        alterations=(
            Alteration(gene="EGFR", variant="L858R", assay="NGS", event_id="e1"),
            Alteration(gene="KRAS", variant="G12C", assay="PCR", event_id="e2"),
            Alteration(gene="EGFR", variant="T790M", assay="NGS", event_id="e3"),
        )
    )
    assert NoAlterationIn(gene="EGFR").triggering_event(case) == "e3"


def test_no_alteration_in_triggering_event_is_empty_when_gene_absent():
    case = CaseState(
        alterations=(Alteration(gene="KRAS", variant="G12C", assay="PCR", event_id="e2"),)
    )
    assert NoAlterationIn(gene="EGFR").triggering_event(case) == ""


def test_biomarker_below_triggering_event_is_the_reading_event():
    case = CaseState(biomarkers={"TMB": BiomarkerReading(value=16.0, event_id="b2")})
    assert BiomarkerBelow(name="TMB", threshold=10.0).triggering_event(case) == "b2"


def test_biomarker_below_triggering_event_is_empty_when_reading_absent():
    assert BiomarkerBelow(name="TMB", threshold=10.0).triggering_event(CaseState()) == ""


def test_drug_not_yet_tried_triggering_event_is_the_last_matching_treatment():
    case = CaseState(
        treatments=(
            Treatment(regimen="osimertinib", event_id="t1", action="started"),
            Treatment(regimen="Osimertinib", event_id="t2", action="stopped"),
        )
    )
    assert DrugNotYetTried(drug="osimertinib").triggering_event(case) == "t2"


def test_drug_not_yet_tried_triggering_event_is_empty_when_drug_absent():
    case = CaseState(
        treatments=(Treatment(regimen="pembrolizumab", event_id="t3", action="started"),)
    )
    assert DrugNotYetTried(drug="osimertinib").triggering_event(case) == ""


def test_disease_not_progressing_triggering_event_is_the_assessment_event():
    case = CaseState(latest_assessment=DiseaseAssessment(status="progression", event_id="d2"))
    assert DiseaseNotProgressing().triggering_event(case) == "d2"


def test_disease_not_progressing_triggering_event_is_empty_when_no_assessment():
    assert DiseaseNotProgressing().triggering_event(CaseState()) == ""
