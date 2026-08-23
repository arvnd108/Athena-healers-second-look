"""Interoperability export tests. Pure, offline -- no database needed."""

from secondlook.case.state import Alteration, BiomarkerValue, CaseState, TreatmentEntry
from secondlook.interop.mcode_export import (
    FINDING_CLAIM_EXTENSION_URL,
    FINDING_EVIDENCE_CLASS_EXTENSION_URL,
    ExportableFinding,
    export_mcode,
)


def _empty_state(case_id: str = "abc123") -> CaseState:
    return CaseState(case_id=case_id)


def _resources_by_type(bundle: dict, resource_type: str) -> list[dict]:
    return [
        e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == resource_type
    ]


# --- Bundle envelope ----------------------------------------------------------


def test_bundle_is_well_formed():
    bundle = export_mcode(case_id="abc123", cancer_type="Synovial sarcoma", state=_empty_state())
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    assert isinstance(bundle["entry"], list)
    assert all("resource" in e for e in bundle["entry"])


def test_minimal_export_has_exactly_patient_and_condition():
    bundle = export_mcode(case_id="abc123", cancer_type="Synovial sarcoma", state=_empty_state())
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert types == ["Patient", "Condition"]


def test_no_resource_ever_carries_a_fabricated_coding_array():
    """The one invariant this whole module exists to protect: never a
    SNOMED/LOINC/RxNorm code this system cannot verify is correct. Checked
    structurally across every resource, not just spot-checked.
    """
    bundle = export_mcode(
        case_id="abc123",
        cancer_type="Synovial sarcoma",
        primary_site="soft tissue",
        histology="synovial sarcoma, biphasic",
        state=CaseState(
            case_id="abc123",
            alterations=(
                Alteration(
                    gene="EGFR",
                    variant="T790M",
                    variant_type="missense",
                    assay="NGS panel",
                    tested_on="2026-02-14",
                    event_id="e1",
                ),
            ),
            biomarkers={"PD-L1": BiomarkerValue("PD-L1", 62.0, "%", "2026-01-05", "e2")},
            treatments=(TreatmentEntry("imatinib", 1, "stopped", "progression", "e3"),),
        ),
        findings=(ExportableFinding("claim text", "documented", "active"),),
    )

    def _walk(obj):
        if isinstance(obj, dict):
            assert "coding" not in obj, f"Found a coding array in: {obj}"
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(bundle)


# --- Patient --------------------------------------------------------------


def test_patient_resource_carries_no_identifying_fields():
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=_empty_state())
    patient = _resources_by_type(bundle, "Patient")[0]
    forbidden = {"name", "birthDate", "identifier", "address", "telecom"}
    assert not (
        forbidden & set(patient)
    ), f"Patient leaked identifying fields: {set(patient) & forbidden}"


def test_patient_id_derived_from_case_id():
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=_empty_state())
    patient = _resources_by_type(bundle, "Patient")[0]
    assert patient["id"] == "case-abc123"


def test_patient_id_not_double_prefixed_if_already_prefixed():
    bundle = export_mcode(case_id="case-abc123", cancer_type="X", state=_empty_state())
    patient = _resources_by_type(bundle, "Patient")[0]
    assert patient["id"] == "case-abc123"


def test_patient_id_truncated_to_fhir_max_length():
    long_id = "x" * 100
    bundle = export_mcode(case_id=long_id, cancer_type="X", state=_empty_state())
    patient = _resources_by_type(bundle, "Patient")[0]
    assert len(patient["id"]) <= 64


# --- Condition --------------------------------------------------------------


def test_condition_carries_cancer_type_as_text_only():
    bundle = export_mcode(case_id="abc123", cancer_type="Synovial sarcoma", state=_empty_state())
    condition = _resources_by_type(bundle, "Condition")[0]
    assert condition["code"] == {"text": "Synovial sarcoma"}
    assert "clinicalStatus" not in condition  # never guessed


def test_condition_includes_body_site_when_given():
    bundle = export_mcode(
        case_id="abc123", cancer_type="X", primary_site="soft tissue", state=_empty_state()
    )
    condition = _resources_by_type(bundle, "Condition")[0]
    assert condition["bodySite"] == [{"text": "soft tissue"}]


def test_condition_omits_body_site_when_not_given():
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=_empty_state())
    condition = _resources_by_type(bundle, "Condition")[0]
    assert "bodySite" not in condition


def test_condition_references_the_patient():
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=_empty_state())
    condition = _resources_by_type(bundle, "Condition")[0]
    assert condition["subject"] == {"reference": "Patient/case-abc123"}


# --- Genomic variant observations -------------------------------------------


def test_variant_observation_carries_the_real_variant_string():
    """Unlike index/export.py's de-identified summaries, this clinical
    export must carry the real variant -- that's the entire point.
    """
    state = CaseState(
        case_id="abc123",
        alterations=(Alteration("EGFR", "T790M", "missense", "NGS panel", "2026-02-14", "e1"),),
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    observations = _resources_by_type(bundle, "Observation")
    variant_obs = [o for o in observations if o["category"] == [{"text": "genomics"}]]
    assert len(variant_obs) == 1
    assert variant_obs[0]["valueCodeableConcept"]["text"] == "EGFR T790M (missense)"
    assert variant_obs[0]["method"] == {"text": "NGS panel"}
    assert variant_obs[0]["effectiveDateTime"] == "2026-02-14"


def test_variant_observation_status_is_final():
    state = CaseState(
        case_id="abc123", alterations=(Alteration("KIT", "D816V", None, None, None, "e1"),)
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    observations = [o for o in _resources_by_type(bundle, "Observation")]
    assert observations[0]["status"] == "final"


def test_multiple_alterations_produce_multiple_observations():
    state = CaseState(
        case_id="abc123",
        alterations=(
            Alteration("EGFR", "T790M", "missense", None, None, "e1"),
            Alteration("KIT", "D816V", "missense", None, None, "e2"),
        ),
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    genomic = [
        o
        for o in _resources_by_type(bundle, "Observation")
        if o["category"] == [{"text": "genomics"}]
    ]
    assert len(genomic) == 2


# --- Biomarker observations --------------------------------------------------


def test_biomarker_observation_uses_value_quantity():
    state = CaseState(
        case_id="abc123",
        biomarkers={"PD-L1": BiomarkerValue("PD-L1", 62.0, "%", "2026-01-05", "e1")},
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    lab = [
        o
        for o in _resources_by_type(bundle, "Observation")
        if o["category"] == [{"text": "laboratory"}]
    ]
    assert len(lab) == 1
    assert lab[0]["code"] == {"text": "PD-L1"}
    assert lab[0]["valueQuantity"] == {"value": 62.0, "unit": "%"}
    assert lab[0]["effectiveDateTime"] == "2026-01-05"


# --- Medication statements ---------------------------------------------------


def test_medication_statement_maps_started_to_active():
    state = CaseState(
        case_id="abc123", treatments=(TreatmentEntry("imatinib", 1, "started", None, "e1"),)
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    med = _resources_by_type(bundle, "MedicationStatement")[0]
    assert med["status"] == "active"
    assert med["medicationCodeableConcept"] == {"text": "imatinib"}
    assert "reasonCode" not in med


def test_medication_statement_maps_stopped_to_stopped_with_reason():
    state = CaseState(
        case_id="abc123",
        treatments=(TreatmentEntry("imatinib", 1, "stopped", "progression", "e1"),),
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    med = _resources_by_type(bundle, "MedicationStatement")[0]
    assert med["status"] == "stopped"
    assert med["reasonCode"] == [{"text": "progression"}]


def test_medication_statement_unknown_action_maps_to_unknown_status():
    """Defensive -- the taxonomy only allows started/stopped, but this
    guards against a future third action silently mis-mapping to "active".
    """
    state = CaseState(
        case_id="abc123", treatments=(TreatmentEntry("imatinib", 1, "paused", None, "e1"),)
    )
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=state)
    med = _resources_by_type(bundle, "MedicationStatement")[0]
    assert med["status"] == "unknown"


# --- Findings -----------------------------------------------------------------


def test_only_active_findings_are_exported():
    findings = (
        ExportableFinding("this is still true", "documented", "active"),
        ExportableFinding("this was superseded", "documented", "superseded"),
    )
    bundle = export_mcode(
        case_id="abc123", cancer_type="X", state=_empty_state(), findings=findings
    )
    basics = _resources_by_type(bundle, "Basic")
    assert len(basics) == 1
    claim_values = [
        ext["valueString"]
        for ext in basics[0]["extension"]
        if ext["url"] == FINDING_CLAIM_EXTENSION_URL
    ]
    assert claim_values == ["this is still true"]


def test_finding_basic_resource_carries_evidence_class():
    findings = (ExportableFinding("claim", "computed", "active"),)
    bundle = export_mcode(
        case_id="abc123", cancer_type="X", state=_empty_state(), findings=findings
    )
    basic = _resources_by_type(bundle, "Basic")[0]
    evidence_values = [
        ext["valueString"]
        for ext in basic["extension"]
        if ext["url"] == FINDING_EVIDENCE_CLASS_EXTENSION_URL
    ]
    assert evidence_values == ["computed"]


def test_no_findings_produces_no_basic_resources():
    bundle = export_mcode(case_id="abc123", cancer_type="X", state=_empty_state())
    assert _resources_by_type(bundle, "Basic") == []


def test_finding_extension_urls_are_urns_not_fabricated_https_urls():
    """These extension URLs are deliberately unregistered -- a bare
    https:// URL would misrepresent them as resolvable/registered.
    """
    assert FINDING_CLAIM_EXTENSION_URL.startswith("urn:")
    assert FINDING_EVIDENCE_CLASS_EXTENSION_URL.startswith("urn:")
