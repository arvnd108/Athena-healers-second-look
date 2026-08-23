"""The allowlist and k-anonymity gate governing export from Case Memory
toward the Structured Case Index.

Everything here is deterministic, pure (no I/O, no database, no LLM), and
deliberately decoupled from `case/models.py`'s SQLAlchemy types -- callers
pass plain values in, following the same DI pattern as `case/state.py`.

Two invariants, not just documented:

1. **Allowlist, not blocklist.** `ALLOWLISTED_EXPORT_FIELDS` is the complete
   set of fields `CaseSummaryExport` may ever carry. Adding a field to what
   this module exports means adding it here first -- a deliberate
   governance decision, not a convenience edit.
2. **Fails closed.** `k_anonymity_gate()` returns `False` (blocking export)
   whenever the corpus is too small to protect, which at hackathon/early
   deployment volumes (per `CONCEPT_EVALUATION.md` SS5) is essentially
   always. That is the correct, honest behavior -- not a bug to route
   around by lowering the threshold to make an export "work."

Source: `CONCEPT_EVALUATION.md` SS5 (the "Can move" / "can never move" table
and the `case_summary_export()` design), `patient-schema-mvp.md` SS7.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from secondlook.case.state import Alteration, CaseState, TreatmentEntry

# ---------------------------------------------------------------------------
# Allowlist -- CONCEPT_EVALUATION.md SS5's "Can move (aggregated/structural
# only)" column, verbatim. Nothing outside this set leaves Case Memory.
# ---------------------------------------------------------------------------
ALLOWLISTED_EXPORT_FIELDS: frozenset[str] = frozenset(
    {
        "case_ref",
        "age_decade",
        "cancer_type",
        "disease_stage_bucket",
        "gene_variant_types",
        "treatment_outcome_categories",
        "relevant_evidence_ids",
    }
)

# Named explicitly so a reviewer can check this module never touches them --
# CONCEPT_EVALUATION.md SS5's "Can never move" column. Not an enforcement
# mechanism by itself (this module simply never reads these), just a
# checklist for review.
NEVER_EXPORT_FIELDS: frozenset[str] = frozenset(
    {
        "case_id",
        "label",
        "exact_age",
        "raw_variant",
        "regimen_name",
        "source_document",
        "occurred_at",
        "recorded_at",
        "claim",
        "superseded_note",
    }
)

K_ANONYMITY_THRESHOLD_DEFAULT = 5
# CONCEPT_EVALUATION.md SS5 gives no specific number -- it states only that
# "anything below a k-anonymity threshold" must not export, and that at
# hackathon N=2-3, "the honest answer is: nothing moves yet." 5 is a
# conservative, commonly-cited k-anonymity floor, used here as an explicit,
# documented placeholder. It is NOT a validated clinical-privacy threshold;
# a maintainer should revisit it once real deployment volume and real
# governance review exist, per that same section's closing note.


def k_anonymity_gate(corpus_size: int, *, threshold: int = K_ANONYMITY_THRESHOLD_DEFAULT) -> bool:
    """True only if export is currently permitted. Fails closed: any
    corpus_size below threshold returns False, always -- including at
    corpus_size == 0.
    """
    if threshold <= 0:
        raise ValueError(f"k-anonymity threshold must be positive, got {threshold}")
    if corpus_size < 0:
        raise ValueError(f"corpus_size must be non-negative, got {corpus_size}")
    return corpus_size >= threshold


def age_to_decade_bucket(age_years: int | None) -> str | None:
    """Exact age never exports -- only a decade bucket, per the "Can never
    move" column ("Age (exact), only a decade bucket").
    """
    if age_years is None:
        return None
    if age_years < 0:
        raise ValueError(f"age_years must be non-negative, got {age_years}")
    decade_start = (age_years // 10) * 10
    return f"{decade_start}-{decade_start + 9}"


def gene_variant_types(alterations: tuple[Alteration, ...]) -> tuple[dict, ...]:
    """Gene + variant TYPE only -- never the raw HGVS/variant string.
    CONCEPT_EVALUATION.md SS5: "not raw HGVS string, to reduce
    re-identifiability at low N."
    """
    return tuple(
        {"gene": a.gene, "variant_type": a.variant_type or "unspecified"} for a in alterations
    )


_TOXICITY_MARKERS = ("toxicity", "intoleran", "adverse")
_PROGRESSION_MARKERS = ("progress",)


def treatment_outcome_categories(treatments: tuple[TreatmentEntry, ...]) -> tuple[str, ...]:
    """Outcome category only -- never the regimen name or exact dates, per
    "Treatment-line outcome category (responded/progressed/
    stopped-for-toxicity)". A `started` entry with no matching `stopped`
    entry yet carries no outcome and is omitted, not guessed at.
    """
    categories: list[str] = []
    for t in treatments:
        if t.action != "stopped":
            continue
        reason = (t.reason or "").lower()
        if any(marker in reason for marker in _TOXICITY_MARKERS):
            categories.append("stopped_for_toxicity")
        elif any(marker in reason for marker in _PROGRESSION_MARKERS):
            categories.append("progressed")
        else:
            categories.append("stopped_other")
    return tuple(categories)


_EVIDENCE_ID_KEYS = ("civic_id", "pmid", "nct_id")


def relevant_evidence_ids(evidence_refs: tuple[dict, ...]) -> tuple[str, ...]:
    """Extracts only the citation identifier -- never the full
    `evidence_ref` dict, which may carry a URL or other field not on the
    allowlist.
    """
    ids: list[str] = []
    for ref in evidence_refs:
        for key in _EVIDENCE_ID_KEYS:
            value = ref.get(key)
            if value:
                ids.append(f"{key}:{value}")
    return tuple(ids)


def _opaque_case_ref(case_id: str) -> str:
    """A reference usable for de-duplication/accounting in the export
    corpus WITHOUT being traceable back to the originating case.
    CONCEPT_EVALUATION.md SS5: "Case label, any identifier traceable to the
    originating case" never moves -- so this is a one-way hash, not the raw
    `case_id`, and deliberately not reversible by this module or any
    caller of it.
    """
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CaseSummaryExport:
    """The only shape this module ever produces. Every field here must be
    in `ALLOWLISTED_EXPORT_FIELDS` -- `tests/index/test_export.py` asserts
    this by introspecting the dataclass directly, so the two can't drift.
    """

    case_ref: str
    age_decade: str | None
    cancer_type: str
    disease_stage_bucket: str | None
    gene_variant_types: tuple[dict, ...]
    treatment_outcome_categories: tuple[str, ...]
    relevant_evidence_ids: tuple[str, ...]


def case_summary_export(
    *,
    case_id: str,
    age_years: int | None,
    cancer_type: str,
    state: CaseState,
    evidence_refs: tuple[dict, ...] = (),
    disease_stage: str | None = None,
    corpus_size: int,
    k_threshold: int = K_ANONYMITY_THRESHOLD_DEFAULT,
) -> CaseSummaryExport | None:
    """Returns an allowlisted, de-identified summary, or `None` if the
    k-anonymity gate fails. Returning `None` (never raising) is deliberate:
    "export not currently permitted" is an expected, routine outcome at low
    corpus volume, not an error condition -- callers should treat `None` as
    "nothing to show yet," not as a failure to handle.

    `disease_stage` is accepted as a parameter, not read from
    `case/models.py`'s `Case` table, because that table -- built exactly to
    `IMPLEMENTATION_PLAN.md` SS2.2 -- has no `stage` column yet, even though
    `patient-schema-mvp.md` SS1 lists disease stage as a P0 field. This is a
    known gap between the target patient schema and the currently
    implemented Case Memory Store schema. It's flagged here rather than
    worked around silently, since adding a column is subsystem C's call,
    not this module's -- callers without a stage source simply pass `None`,
    and `disease_stage_bucket` comes back `None` too.
    """
    if not k_anonymity_gate(corpus_size, threshold=k_threshold):
        return None

    return CaseSummaryExport(
        case_ref=_opaque_case_ref(case_id),
        age_decade=age_to_decade_bucket(age_years),
        cancer_type=cancer_type,
        disease_stage_bucket=disease_stage,
        gene_variant_types=gene_variant_types(state.alterations),
        treatment_outcome_categories=treatment_outcome_categories(state.treatments),
        relevant_evidence_ids=relevant_evidence_ids(evidence_refs),
    )
