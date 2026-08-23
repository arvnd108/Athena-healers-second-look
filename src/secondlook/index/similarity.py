"""Structured Case Index -- deterministic structural-similarity lookup
across cases run through *this specific deployment*. Not a cross-institution
or global system, and not a learned-pattern claim.

`find_similar_cases()` is the entire surface of this module. It operates
only on `CaseSummaryExport` objects (subsystem N's output) -- callers must
have already run each candidate through `export.case_summary_export()` and
gotten a non-`None` result back (i.e. the k-anonymity gate already passed
for that corpus). This module does not re-run or bypass that gate; it has
no opinion on de-identification at all, only on comparing already
de-identified summaries.

**Explicitly not built here, per `CONCEPT_EVALUATION.md` SS5:**

- No embedding-based "whole case similarity" -- every match is a named,
  structured field overlap a reviewer can point at, never a vector
  distance nobody can explain.
- No outcome-correlation claim, ever, at this corpus scale. This module
  reports which fields two case summaries share; it says nothing about
  what happened to either patient, and nothing about what similar cases
  "usually" do.
- Every `SimilarCase` carries `corpus_size` and `matched_fields` so a
  caller cannot render a result without also rendering the N it's drawn
  from and exactly what matched -- per SS5's explicit rendering example:
  "1 structurally similar case in this corpus -- matches on gene, variant
  class, and prior treatment; outcome not shown because N=1 is not a
  pattern."
"""

from __future__ import annotations

from dataclasses import dataclass

from secondlook.index.export import CaseSummaryExport

# ---------------------------------------------------------------------------
# Weights -- visible and adjustable, per ARCHITECTURE.md SS9's scorecard
# rule (weights a clinician can see and change, never a hidden model
# parameter). CONCEPT_EVALUATION.md SS5 does not specify numeric weights --
# these are a documented starting point, not a validated scoring model.
# A field absent from a caller-supplied `weights` dict simply contributes 0,
# which is why every key below exists explicitly rather than being implied.
# ---------------------------------------------------------------------------
SIMILARITY_FIELDS: frozenset[str] = frozenset(
    {
        "cancer_type",
        "gene_variant_type",
        "treatment_outcome_category",
        "disease_stage_bucket",
        "age_decade",
    }
)

DEFAULT_SIMILARITY_WEIGHTS: dict[str, float] = {
    "cancer_type": 1.0,
    "gene_variant_type": 2.0,
    "treatment_outcome_category": 1.0,
    "disease_stage_bucket": 0.5,
    "age_decade": 0.25,
}
# gene_variant_type weighted highest -- a shared molecular target is the
# single most clinically load-bearing overlap this index can report.
# age_decade weighted lowest -- the weakest, most coincidental signal of
# the five. Deliberately excluded: `relevant_evidence_ids` -- which CIViC/
# PubMed items a case's research turned up is a property of what was
# queried, not a structural property of the patient, and including it here
# would conflate "we looked up the same drug" with "these patients are
# alike."


@dataclass(frozen=True)
class SimilarCase:
    """One entry in a `find_similar_cases()` result. `matched_fields` is
    never empty -- a case with zero overlap is not returned at all.
    """

    case_ref: str
    score: float
    matched_fields: tuple[str, ...]
    corpus_size: int


def _validate_weights(weights: dict[str, float]) -> None:
    unknown = set(weights) - SIMILARITY_FIELDS
    if unknown:
        raise ValueError(f"Unknown similarity field(s) in weights: {sorted(unknown)}")
    negative = {k: v for k, v in weights.items() if v < 0}
    if negative:
        raise ValueError(f"Similarity weights must be non-negative, got: {negative}")


def _match_cancer_type(query: CaseSummaryExport, candidate: CaseSummaryExport) -> tuple[str, ...]:
    if query.cancer_type == candidate.cancer_type:
        return ("cancer_type",)
    return ()


def _match_disease_stage_bucket(
    query: CaseSummaryExport, candidate: CaseSummaryExport
) -> tuple[str, ...]:
    if (
        query.disease_stage_bucket is not None
        and query.disease_stage_bucket == candidate.disease_stage_bucket
    ):
        return ("disease_stage_bucket",)
    return ()


def _match_age_decade(query: CaseSummaryExport, candidate: CaseSummaryExport) -> tuple[str, ...]:
    if query.age_decade is not None and query.age_decade == candidate.age_decade:
        return ("age_decade",)
    return ()


def _match_gene_variant_types(
    query: CaseSummaryExport, candidate: CaseSummaryExport
) -> tuple[str, ...]:
    """Overlap on (gene, variant_type) pairs -- 'same gene+variant class',
    per CONCEPT_EVALUATION.md SS5. Each overlapping pair is reported
    individually so 'exactly which fields matched' is never a lie of
    omission -- two cases sharing EGFR/missense AND KIT/missense show both,
    not just a generic 'gene_variant_type' flag.
    """
    query_pairs = {(g["gene"], g["variant_type"]) for g in query.gene_variant_types}
    candidate_pairs = {(g["gene"], g["variant_type"]) for g in candidate.gene_variant_types}
    overlap = sorted(query_pairs & candidate_pairs)
    return tuple(f"gene_variant_type:{gene}/{variant_type}" for gene, variant_type in overlap)


def _match_treatment_outcome_categories(
    query: CaseSummaryExport, candidate: CaseSummaryExport
) -> tuple[str, ...]:
    """Overlap on outcome categories -- 'overlapping treatment history',
    per CONCEPT_EVALUATION.md SS5. This is categories only
    (responded/progressed/stopped_for_toxicity/stopped_other), never the
    regimen name -- export.py already stripped that.
    """
    overlap = sorted(
        set(query.treatment_outcome_categories) & set(candidate.treatment_outcome_categories)
    )
    return tuple(f"treatment_outcome_category:{category}" for category in overlap)


def _score_candidate(
    query: CaseSummaryExport, candidate: CaseSummaryExport, weights: dict[str, float]
) -> tuple[float, tuple[str, ...]]:
    matched: list[str] = []
    score = 0.0

    for field_name in _match_cancer_type(query, candidate):
        matched.append(field_name)
        score += weights.get("cancer_type", 0.0)

    for field_name in _match_disease_stage_bucket(query, candidate):
        matched.append(field_name)
        score += weights.get("disease_stage_bucket", 0.0)

    for field_name in _match_age_decade(query, candidate):
        matched.append(field_name)
        score += weights.get("age_decade", 0.0)

    gene_matches = _match_gene_variant_types(query, candidate)
    matched.extend(gene_matches)
    score += weights.get("gene_variant_type", 0.0) * len(gene_matches)

    treatment_matches = _match_treatment_outcome_categories(query, candidate)
    matched.extend(treatment_matches)
    score += weights.get("treatment_outcome_category", 0.0) * len(treatment_matches)

    return score, tuple(matched)


def find_similar_cases(
    query: CaseSummaryExport,
    corpus: list[CaseSummaryExport],
    *,
    weights: dict[str, float] | None = None,
) -> list[SimilarCase]:
    """Structured field overlap between `query` and every case in `corpus`
    -- a ranked list, each result showing exactly which fields matched.

    A candidate with `case_ref == query.case_ref` is skipped (a case never
    matches itself), and a candidate with zero overlap is never included --
    "similar" means at least one shared structured field, not merely
    "present in the corpus."

    Deterministic: ties in `score` are broken by `case_ref` ascending, so
    the same query against the same corpus always returns the same order.

    `weights` defaults to `DEFAULT_SIMILARITY_WEIGHTS` if not given. A field
    omitted from a caller-supplied `weights` dict contributes 0 to the
    score (it's still eligible to appear in `matched_fields` if you pass an
    explicit 0.0, but omitting a key silently zeroes it out -- pass an
    explicit weights dict if that distinction matters to your caller).
    """
    effective_weights = DEFAULT_SIMILARITY_WEIGHTS if weights is None else weights
    _validate_weights(effective_weights)

    corpus_size = len(corpus)
    results: list[SimilarCase] = []

    for candidate in corpus:
        if candidate.case_ref == query.case_ref:
            continue
        score, matched_fields = _score_candidate(query, candidate, effective_weights)
        if not matched_fields:
            continue
        results.append(
            SimilarCase(
                case_ref=candidate.case_ref,
                score=score,
                matched_fields=matched_fields,
                corpus_size=corpus_size,
            )
        )

    results.sort(key=lambda r: (-r.score, r.case_ref))
    return results
