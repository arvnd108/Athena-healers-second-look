"""`export_mcode()` -- maps a case's derived state onto FHIR R4 resources,
shaped after mCODE (Minimal Common Oncology Data Elements, an HL7 FHIR
implementation guide for oncology).

**Read this before treating the output as interop-ready.** This function
produces structurally valid FHIR R4 (correct resource types, correct
required fields, a valid `Bundle`) organized the way mCODE organizes
oncology data (a Primary Cancer Condition, genomic variant observations,
treatment history as medication statements). It does **not** claim full
mCODE profile conformance, for one concrete, honest reason: true mCODE
conformance requires bound clinical terminology -- SNOMED CT codes for
conditions, LOINC codes and structured HGVS expressions for genomic
observations, RxNorm for medications. This system's underlying data
(`case/models.py`'s `cancer_type`, `gene`, `variant`, `regimen` fields) is
free text, not coded against those terminologies anywhere upstream of this
module. Fabricating a plausible-looking SNOMED or LOINC code here -- one
this module cannot verify is actually correct -- would be worse than
omitting it: a wrong code in a medical interop export is a real hazard,
not a cosmetic gap. Every `CodeableConcept` below therefore carries `text`
only, never a `coding` array, until real terminology binding is added by
someone who can validate it against the actual code systems.

This is a **read-only export**. It never imports or writes case state from
an external FHIR source -- that's a separate, larger integration decision,
explicitly out of scope here, same as the source issue specifies.

Only **active** findings are exported (`status == "active"`) -- a
superseded finding is, by definition, no longer believed true, and an
interop snapshot must not present it as current fact.

**This is a deliberate exception to "patient data stays local by
default"** (`Concept.md` SS8.5, `POLICY.md`), not a violation of it: unlike
the Structured Case Index's de-identified summaries (`index/export.py`),
this export carries real clinical detail -- the whole point is that it
travels with a referred patient or feeds a receiving institution's EHR.
Calling this function is a deliberate, clinician-initiated action; nothing
in this module calls itself automatically or on a schedule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from secondlook.case.state import Alteration, BiomarkerValue, CaseState, TreatmentEntry

FHIRResource = dict[str, Any]
FHIRBundle = dict[str, Any]

# Local, non-registered extension namespace for the one thing base FHIR R4
# and mCODE have no resource for: an evidence-graph finding. `Basic` is the
# real, correct FHIR R4 mechanism for exactly this situation -- a generic
# resource for a concept with no defined profile -- not an invented
# resource type. The extension URL below is a URN, not a resolvable HTTPS
# URI, precisely because it has not been registered anywhere; using a bare
# made-up https:// URL would misrepresent it as a real, dereferenceable
# extension definition.
FINDING_CLAIM_EXTENSION_URL = "urn:secondlook:finding-claim"
FINDING_EVIDENCE_CLASS_EXTENSION_URL = "urn:secondlook:finding-evidence-class"


@dataclass(frozen=True)
class ExportableFinding:
    """The minimal shape `export_mcode` needs from a `Finding` row --
    decoupled from `case/models.py`'s SQLAlchemy type, matching this
    project's DI convention (`case/state.py`'s `RawEvent` is the same
    pattern).
    """

    claim: str
    evidence_class: str
    status: str  # "active" | "superseded" -- only "active" is exported


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _patient_resource(patient_id: str) -> FHIRResource:
    """Deliberately minimal -- no name, no birthDate, no identifier beyond
    the opaque `patient_id` the caller supplies. Case Memory Store carries
    no PHI (POLICY.md SS5.4); this export must not introduce any by
    fabricating a birthDate from `age_years` or similar. `birthDate` is
    optional in base FHIR R4 Patient, so omitting it produces a valid,
    merely less complete, resource.
    """
    return {"resourceType": "Patient", "id": patient_id}


def _condition_resource(
    *,
    patient_id: str,
    cancer_type: str,
    primary_site: str | None,
    histology: str | None,
) -> FHIRResource:
    """Shaped after mCODE's Primary Cancer Condition -- `code` carries the
    cancer type, `bodySite` the primary site, both as `text`-only
    CodeableConcepts (see module docstring). `clinicalStatus` is
    deliberately omitted rather than guessed -- this system does not track
    remission/relapse/resolution as a distinct condition-level state.
    """
    resource: FHIRResource = {
        "resourceType": "Condition",
        "id": _new_id(),
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {"text": cancer_type},
    }
    if primary_site:
        resource["bodySite"] = [{"text": primary_site}]
    if histology:
        # No dedicated mCODE element carries histology as free text on
        # Condition without a coded morphology -- represented as a note
        # rather than invented as a structured field.
        resource["note"] = [{"text": f"Histology: {histology}"}]
    return resource


def _variant_observation(patient_id: str, alteration: Alteration) -> FHIRResource:
    """Shaped after mCODE's genomic variant reporting pattern (gene +
    variant as an Observation), without the LOINC-coded components real
    mCODE genomic profiles use -- see module docstring. The raw variant
    string is included here deliberately: unlike `index/export.py`'s
    de-identified summaries, this is a clinical export meant for a
    receiving clinician/EHR, where the specific variant is exactly the
    information that must travel, not information to strip.
    """
    value_text = f"{alteration.gene} {alteration.variant}"
    if alteration.variant_type:
        value_text += f" ({alteration.variant_type})"
    resource: FHIRResource = {
        "resourceType": "Observation",
        "id": _new_id(),
        "status": "final",
        "category": [{"text": "genomics"}],
        "code": {"text": "Genomic variant"},
        "subject": {"reference": f"Patient/{patient_id}"},
        "valueCodeableConcept": {"text": value_text},
    }
    if alteration.assay:
        resource["method"] = {"text": alteration.assay}
    if alteration.tested_on:
        resource["effectiveDateTime"] = alteration.tested_on
    return resource


def _biomarker_observation(patient_id: str, biomarker: BiomarkerValue) -> FHIRResource:
    resource: FHIRResource = {
        "resourceType": "Observation",
        "id": _new_id(),
        "status": "final",
        "category": [{"text": "laboratory"}],
        "code": {"text": biomarker.name},
        "subject": {"reference": f"Patient/{patient_id}"},
        "valueQuantity": {"value": biomarker.value, "unit": biomarker.unit or ""},
    }
    if biomarker.measured_on:
        resource["effectiveDateTime"] = biomarker.measured_on
    return resource


# FHIR R4's real, valid MedicationStatement.status codes. Mapping our
# two-action taxonomy (started/stopped) onto this real value set, not an
# invented one.
_MEDICATION_STATUS_BY_ACTION = {"started": "active", "stopped": "stopped"}


def _medication_statement(patient_id: str, treatment: TreatmentEntry) -> FHIRResource:
    resource: FHIRResource = {
        "resourceType": "MedicationStatement",
        "id": _new_id(),
        "status": _MEDICATION_STATUS_BY_ACTION.get(treatment.action, "unknown"),
        "medicationCodeableConcept": {"text": treatment.regimen},
        "subject": {"reference": f"Patient/{patient_id}"},
    }
    if treatment.reason:
        resource["reasonCode"] = [{"text": treatment.reason}]
    return resource


def _finding_basic_resource(patient_id: str, finding: ExportableFinding) -> FHIRResource:
    """`Basic` is FHIR R4's real, correct resource type for a concept with
    no defined profile -- not an invented one. See module-level constants'
    docstrings for why the extension URLs are URNs, not HTTPS URLs.
    """
    return {
        "resourceType": "Basic",
        "id": _new_id(),
        "code": {"text": "evidence-graph-finding"},
        "subject": {"reference": f"Patient/{patient_id}"},
        "extension": [
            {"url": FINDING_CLAIM_EXTENSION_URL, "valueString": finding.claim},
            {
                "url": FINDING_EVIDENCE_CLASS_EXTENSION_URL,
                "valueString": finding.evidence_class,
            },
        ],
    }


def export_mcode(
    *,
    case_id: str,
    cancer_type: str,
    state: CaseState,
    primary_site: str | None = None,
    histology: str | None = None,
    findings: tuple[ExportableFinding, ...] = (),
) -> FHIRBundle:
    """Returns a FHIR R4 `Bundle` (`type: "collection"`) containing:
    one `Patient`, one `Condition`, one `Observation` per alteration and
    per biomarker, one `MedicationStatement` per treatment entry, and one
    `Basic` resource per **active** finding.

    Read-only: never imports or writes case state from an external source.
    """
    patient_id = f"case-{case_id}" if not case_id.startswith("case-") else case_id
    patient_id = patient_id[:64]  # FHIR `id` type: max 64 chars

    resources: list[FHIRResource] = [_patient_resource(patient_id)]
    resources.append(
        _condition_resource(
            patient_id=patient_id,
            cancer_type=cancer_type,
            primary_site=primary_site,
            histology=histology,
        )
    )
    resources.extend(_variant_observation(patient_id, a) for a in state.alterations)
    resources.extend(_biomarker_observation(patient_id, b) for b in state.biomarkers.values())
    resources.extend(_medication_statement(patient_id, t) for t in state.treatments)
    resources.extend(
        _finding_basic_resource(patient_id, f) for f in findings if f.status == "active"
    )

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }
