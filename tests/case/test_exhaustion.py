"""Offline tests for guideline-grounded exhaustion assessment.

Fixtures use the synthetic cancer type and regimen names only. Nothing here
is a real-sounding clinical claim.
"""

from __future__ import annotations

from secondlook.case.exhaustion import (
    ALL_TRIED_REASON,
    NO_APPLICABLE_REASON,
    NO_DATA_REASON,
    STAGE_UNKNOWN_REASON,
    assess_exhaustion,
)
from secondlook.case.state import Alteration, CaseState, TreatmentEntry
from secondlook.tier1.guideline_kb_loader import (
    SYNTHETIC_PLACEHOLDER,
    GuidelineKnowledgeBase,
    GuidelineRecommendation,
)

CANCER = "TEST_SARCOMA_SYNTHETIC"
INSTRUMENT = "[SYNTHETIC EXAMPLE — NOT REAL GUIDANCE] Example Oncology Society Guidelines v0"
SOURCE_URL = "https://example.invalid/synthetic-guideline"


def _rec(
    regimen: str,
    *,
    line: int = 1,
    molecular: tuple[str, str] | None = None,
    stages: tuple[str, ...] = (),
) -> GuidelineRecommendation:
    return GuidelineRecommendation(
        regimen=regimen,
        line=line,
        molecular_requirement=molecular,
        applicable_stages=stages,
        instrument=INSTRUMENT,
        source_url=SOURCE_URL,
        cancer_type=CANCER,
        review_status=SYNTHETIC_PLACEHOLDER,
    )


def _kb(*recs: GuidelineRecommendation) -> GuidelineKnowledgeBase:
    grouped: dict[str, list[GuidelineRecommendation]] = {}
    for rec in recs:
        grouped.setdefault(rec.cancer_type, []).append(rec)
    return GuidelineKnowledgeBase(
        by_cancer_type={key: tuple(value) for key, value in grouped.items()}
    )


def _treatment(regimen: str, event_id: str = "tx-1") -> TreatmentEntry:
    return TreatmentEntry(regimen=regimen, line=1, action="started", reason=None, event_id=event_id)


def _alteration(gene: str, variant: str) -> Alteration:
    return Alteration(
        gene=gene,
        variant=variant,
        variant_type=None,
        assay=None,
        tested_on=None,
        event_id="alt-1",
    )


def _case(
    *,
    treatments: tuple[TreatmentEntry, ...] = (),
    alterations: tuple[Alteration, ...] = (),
) -> CaseState:
    return CaseState(case_id="case-1", treatments=treatments, alterations=alterations)


def test_untried_standard_regimen_is_not_exhausted():
    kb = _kb(_rec("synthetic-agent-alpha"))
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.status == "not_exhausted"
    assert result.untried_standard_option is not None
    assert result.untried_standard_option.regimen == "synthetic-agent-alpha"
    assert result.citation == INSTRUMENT
    assert "synthetic-agent-alpha" in result.reason


def test_all_standard_regimens_tried_is_exhausted():
    kb = _kb(_rec("synthetic-agent-alpha"), _rec("synthetic-agent-beta", line=2))
    case = _case(
        treatments=(
            _treatment("synthetic-agent-alpha", "tx-1"),
            _treatment("synthetic-agent-beta", "tx-2"),
        )
    )
    result = assess_exhaustion(case, CANCER, kb)
    assert result.status == "exhausted"
    assert result.untried_standard_option is None
    assert result.citation is None
    assert result.reason == ALL_TRIED_REASON


def test_stage_gated_unknown_beats_a_false_exhausted():
    """A stage-gated rec cannot be evaluated (#51). If a different,
    stage-agnostic rec was clearly tried, the result is still unknown —
    never a guessed 'exhausted'.
    """
    kb = _kb(
        _rec("synthetic-agent-alpha"),
        _rec("synthetic-agent-delta", stages=("II", "III")),
    )
    case = _case(treatments=(_treatment("synthetic-agent-alpha"),))
    result = assess_exhaustion(case, CANCER, kb)
    assert result.status == "unknown"
    assert result.reason == STAGE_UNKNOWN_REASON
    assert result.untried_standard_option is None


def test_stage_gated_alone_is_unknown():
    kb = _kb(_rec("synthetic-agent-delta", stages=("II",)))
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.status == "unknown"
    assert result.reason == STAGE_UNKNOWN_REASON


def test_stage_gated_rec_with_matching_stage_is_evaluated_normally():
    """#57: once `stage` is given, a stage-gated rec is no longer
    unresolvable -- it's checked against treatment history like any other
    applicable rec."""
    kb = _kb(_rec("synthetic-agent-delta", stages=("II", "III")))
    result = assess_exhaustion(_case(), CANCER, kb, stage="III")
    assert result.status == "not_exhausted"
    assert result.untried_standard_option is not None
    assert result.untried_standard_option.regimen == "synthetic-agent-delta"


def test_stage_gated_rec_with_non_matching_stage_is_inapplicable_not_unknown():
    """A case's stage that isn't in applicable_stages excludes the rec from
    consideration entirely -- same as a molecular-requirement mismatch --
    rather than making the result unknown."""
    kb = _kb(_rec("synthetic-agent-delta", stages=("II", "III")))
    result = assess_exhaustion(_case(), CANCER, kb, stage="IV")
    assert result.status == "unknown"
    assert result.reason == NO_APPLICABLE_REASON


def test_stage_none_default_keeps_prior_unknown_behavior():
    """Backward compatibility: an omitted `stage` behaves exactly as it did
    before #57 -- unresolvable, not a guessed match or mismatch."""
    kb = _kb(_rec("synthetic-agent-delta", stages=("II",)))
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.status == "unknown"
    assert result.reason == STAGE_UNKNOWN_REASON


def test_untried_stage_agnostic_still_surfaces_when_a_stage_gated_rec_exists():
    kb = _kb(
        _rec("synthetic-agent-alpha"),
        _rec("synthetic-agent-delta", stages=("II",)),
    )
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.status == "not_exhausted"
    assert result.untried_standard_option is not None
    assert result.untried_standard_option.regimen == "synthetic-agent-alpha"


def test_molecular_requirement_without_matching_alteration_is_inapplicable():
    kb = _kb(_rec("synthetic-agent-gamma", molecular=("TESTGENE", "TESTVAR")))
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.status == "unknown"
    assert result.reason == NO_APPLICABLE_REASON
    assert result.untried_standard_option is None


def test_molecular_requirement_matches_gene_and_variant():
    kb = _kb(_rec("synthetic-agent-gamma", molecular=("TESTGENE", "TESTVAR")))
    case = _case(alterations=(_alteration("TESTGENE", "TESTVAR"),))
    result = assess_exhaustion(case, CANCER, kb)
    assert result.status == "not_exhausted"
    assert result.untried_standard_option is not None
    assert result.untried_standard_option.regimen == "synthetic-agent-gamma"


def test_unknown_cancer_type_distinguishes_no_data_from_exhausted():
    kb = _kb(_rec("synthetic-agent-alpha"))
    result = assess_exhaustion(_case(), "SOME_OTHER_SYNTHETIC_TYPE", kb)
    assert result.status == "unknown"
    assert result.reason == NO_DATA_REASON
    assert result.reason != ALL_TRIED_REASON


def test_regimen_matching_is_case_insensitive():
    kb = _kb(_rec("Synthetic-Agent-Alpha"))
    case = _case(treatments=(_treatment("synthetic-agent-alpha"),))
    result = assess_exhaustion(case, CANCER, kb)
    assert result.status == "exhausted"


def test_first_untried_in_kb_order_wins():
    kb = _kb(_rec("synthetic-agent-alpha"), _rec("synthetic-agent-beta", line=2))
    result = assess_exhaustion(_case(), CANCER, kb)
    assert result.untried_standard_option is not None
    assert result.untried_standard_option.regimen == "synthetic-agent-alpha"


def test_assess_exhaustion_is_byte_identical_across_100_runs():
    kb = _kb(
        _rec("synthetic-agent-alpha"),
        _rec("synthetic-agent-gamma", molecular=("TESTGENE", "TESTVAR")),
        _rec("synthetic-agent-delta", stages=("II", "III")),
    )
    case = _case(
        treatments=(_treatment("synthetic-agent-alpha"),),
        alterations=(_alteration("TESTGENE", "OTHERVAR"),),
    )
    first = assess_exhaustion(case, CANCER, kb)
    for _ in range(100):
        again = assess_exhaustion(case, CANCER, kb)
        assert again == first
        assert repr(again) == repr(first)
