"""Governance / Privacy Gate tests. Pure, offline -- no database needed."""

import dataclasses

import pytest

from secondlook.case.models import NO_PHI_COLUMN_PATTERN
from secondlook.case.state import Alteration, CaseState, TreatmentEntry
from secondlook.index.export import (
    ALLOWLISTED_EXPORT_FIELDS,
    K_ANONYMITY_THRESHOLD_DEFAULT,
    CaseSummaryExport,
    age_to_decade_bucket,
    case_summary_export,
    gene_variant_types,
    k_anonymity_gate,
    relevant_evidence_ids,
    treatment_outcome_categories,
)

# --- Schema-level PHI check, reusing subsystem C's pattern ------------------
# This is the "schema-level CI check that rejects any new column matching a
# disallowed-PHI pattern" deliverable, applied to this subsystem's own
# export surface rather than duplicating case/models.py's mechanism.


def test_case_summary_export_has_no_phi_shaped_field():
    offending = [
        f.name
        for f in dataclasses.fields(CaseSummaryExport)
        if NO_PHI_COLUMN_PATTERN.search(f.name)
    ]
    assert not offending, f"PHI-shaped field name(s) in CaseSummaryExport: {offending}"


def test_dataclass_fields_exactly_match_the_allowlist():
    """The allowlist and the dataclass cannot drift -- if someone adds a
    field to one without the other, this fails.
    """
    field_names = {f.name for f in dataclasses.fields(CaseSummaryExport)}
    assert field_names == ALLOWLISTED_EXPORT_FIELDS


# --- k-anonymity gate: fails closed -----------------------------------------


def test_k_anonymity_gate_blocks_below_threshold():
    assert k_anonymity_gate(4, threshold=5) is False


def test_k_anonymity_gate_allows_at_threshold():
    assert k_anonymity_gate(5, threshold=5) is True


def test_k_anonymity_gate_blocks_at_hackathon_scale_with_default_threshold():
    """CONCEPT_EVALUATION.md SS5: at hackathon N=2-3, nothing moves yet."""
    for corpus_size in (0, 1, 2, 3):
        assert k_anonymity_gate(corpus_size) is False


def test_k_anonymity_gate_blocks_zero_corpus():
    assert k_anonymity_gate(0) is False


def test_k_anonymity_gate_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="threshold must be positive"):
        k_anonymity_gate(10, threshold=0)


def test_k_anonymity_gate_rejects_negative_corpus_size():
    with pytest.raises(ValueError, match="corpus_size must be non-negative"):
        k_anonymity_gate(-1)


# --- age_to_decade_bucket: exact age never exports --------------------------


def test_age_bucket_none_passes_through():
    assert age_to_decade_bucket(None) is None


@pytest.mark.parametrize(
    "age,expected",
    [(0, "0-9"), (5, "0-9"), (34, "30-39"), (79, "70-79"), (99, "90-99")],
)
def test_age_bucket_maps_to_decade(age, expected):
    assert age_to_decade_bucket(age) == expected


def test_age_bucket_rejects_negative_age():
    with pytest.raises(ValueError, match="age_years must be non-negative"):
        age_to_decade_bucket(-1)


def test_age_bucket_never_returns_the_exact_age():
    """The one property this function exists to guarantee."""
    for age in range(0, 100):
        bucket = age_to_decade_bucket(age)
        assert str(age) != bucket
        assert "-" in bucket  # always a range, never a point value


# --- gene_variant_types: never the raw HGVS string --------------------------


def test_gene_variant_types_strips_the_raw_variant_string():
    alterations = (
        Alteration(
            gene="EGFR",
            variant="T790M",
            variant_type="missense",
            assay="NGS panel",
            tested_on="2026-02-14",
            event_id="e1",
        ),
    )
    result = gene_variant_types(alterations)
    assert result == ({"gene": "EGFR", "variant_type": "missense"},)
    # The raw variant string must not appear anywhere in the output.
    assert "T790M" not in str(result)


def test_gene_variant_types_defaults_unknown_type_to_unspecified():
    alterations = (
        Alteration(
            gene="KIT",
            variant="D816V",
            variant_type=None,
            assay=None,
            tested_on=None,
            event_id="e1",
        ),
    )
    result = gene_variant_types(alterations)
    assert result == ({"gene": "KIT", "variant_type": "unspecified"},)


# --- treatment_outcome_categories: category only, never the regimen --------


def test_treatment_outcome_categorizes_toxicity():
    treatments = (
        TreatmentEntry(
            regimen="imatinib", line=1, action="stopped", reason="toxicity", event_id="e1"
        ),
    )
    result = treatment_outcome_categories(treatments)
    assert result == ("stopped_for_toxicity",)
    assert "imatinib" not in str(result)


def test_treatment_outcome_categorizes_progression():
    treatments = (
        TreatmentEntry(
            regimen="osimertinib",
            line=2,
            action="stopped",
            reason="disease progression",
            event_id="e1",
        ),
    )
    assert treatment_outcome_categories(treatments) == ("progressed",)


def test_treatment_outcome_started_only_produces_no_category():
    """A treatment that's started but not yet stopped has no outcome yet --
    omitted, not guessed at.
    """
    treatments = (
        TreatmentEntry(regimen="imatinib", line=1, action="started", reason=None, event_id="e1"),
    )
    assert treatment_outcome_categories(treatments) == ()


def test_treatment_outcome_unrecognized_stop_reason_falls_back_to_other():
    treatments = (
        TreatmentEntry(
            regimen="imatinib", line=1, action="stopped", reason="patient preference", event_id="e1"
        ),
    )
    assert treatment_outcome_categories(treatments) == ("stopped_other",)


# --- relevant_evidence_ids ---------------------------------------------------


def test_relevant_evidence_ids_extracts_only_the_identifier():
    refs = (
        {"source": "CIViC", "civic_id": "123", "url": "https://civicdb.org/123"},
        {"source": "PubMed", "pmid": "456", "url": "https://pubmed.ncbi.nlm.nih.gov/456"},
    )
    result = relevant_evidence_ids(refs)
    assert result == ("civic_id:123", "pmid:456")
    # The URL -- not on the allowlist -- must never appear in the output.
    assert "civicdb.org" not in str(result)


def test_relevant_evidence_ids_skips_refs_with_no_recognized_id():
    refs = ({"source": "internal note", "summary": "not citable"},)
    assert relevant_evidence_ids(refs) == ()


# --- case_summary_export: the full integration of the above -----------------


def _sample_state() -> CaseState:
    return CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
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
        treatments=(
            TreatmentEntry(
                regimen="osimertinib", line=1, action="stopped", reason="progression", event_id="e2"
            ),
        ),
    )


def test_case_summary_export_blocked_below_k_anonymity_threshold():
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=2,
    )
    assert result is None


def test_case_summary_export_succeeds_at_or_above_threshold():
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        evidence_refs=({"source": "CIViC", "civic_id": "999"},),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    assert result is not None
    assert result.age_decade == "30-39"
    assert result.cancer_type == "Synovial sarcoma"
    assert result.gene_variant_types == ({"gene": "EGFR", "variant_type": "missense"},)
    assert result.treatment_outcome_categories == ("progressed",)
    assert result.relevant_evidence_ids == ("civic_id:999",)


def test_case_summary_export_never_leaks_the_raw_case_id():
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    assert result.case_ref != "case-123"
    assert "case-123" not in result.case_ref


def test_case_summary_export_never_leaks_the_raw_variant_or_regimen():
    """End-to-end: even though the input CaseState carries the raw variant
    string and regimen name, neither appears anywhere in the export.
    """
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    exported_text = str(dataclasses.asdict(result))
    assert "T790M" not in exported_text
    assert "osimertinib" not in exported_text


def test_case_summary_export_opaque_ref_is_deterministic_but_not_reversible():
    """Same case_id -> same case_ref (needed for corpus de-duplication), but
    it must not be trivially reversible to the original id.
    """
    result_1 = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    result_2 = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    assert result_1.case_ref == result_2.case_ref
    assert len(result_1.case_ref) == 16  # truncated hash, not the full case_id


def test_case_summary_export_disease_stage_passthrough_when_provided():
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        disease_stage="metastatic",
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    assert result.disease_stage_bucket == "metastatic"


def test_case_summary_export_disease_stage_none_when_not_provided():
    """Case Memory Store's `cases` table has no stage column yet -- see
    export.py's docstring. Callers without a stage source get None back,
    not a fabricated value.
    """
    result = case_summary_export(
        case_id="case-123",
        age_years=34,
        cancer_type="Synovial sarcoma",
        state=_sample_state(),
        corpus_size=K_ANONYMITY_THRESHOLD_DEFAULT,
    )
    assert result.disease_stage_bucket is None
