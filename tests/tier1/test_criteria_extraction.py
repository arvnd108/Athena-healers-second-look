"""Criteria extraction: what it structures, and what it refuses to guess."""

import json

import pytest

from secondlook.tier1.criteria_extraction import (
    LlmAssistedExtractor,
    Predicate,
    RuleBasedExtractor,
    criteria_fingerprint,
    split_criteria,
)


@pytest.fixture
def extractor():
    return RuleBasedExtractor()


class TestSplitting:
    def test_headers_switch_section(self):
        pairs = split_criteria("Inclusion Criteria:\n* A\nExclusion Criteria:\n* B\n")
        assert pairs == [("inclusion", "A"), ("exclusion", "B")]

    def test_qualified_headers_are_recognised(self):
        """Sponsors qualify these freely. Requiring the line to end after
        'Criteria' left whole sections marked `unknown`, losing the sense that
        inverts every criterion's meaning."""
        pairs = split_criteria(
            "Inclusion Criteria for All Patients\n* A\nExclusion Criteria (Cohort B):\n* B\n"
        )
        assert [section for section, _ in pairs] == ["inclusion", "exclusion"]

    def test_text_before_any_header_is_unknown_not_assumed_inclusion(self):
        """Guessing here would invert every criterion from a sponsor who omits
        the headers."""
        assert split_criteria("* Something\n")[0][0] == "unknown"

    def test_registry_backslash_escapes_are_undone(self):
        """ClinicalTrials.gov escapes its own comparison operators; left in
        place they defeat every numeric pattern."""
        assert split_criteria(r"* Bilirubin \> 3.0")[0][1] == "Bilirubin > 3.0"

    @pytest.mark.parametrize("bullet", ["*", "-", "•", "1.", "2)"])
    def test_bullet_styles(self, bullet):
        assert split_criteria(f"{bullet} Age 18 years or older")[0][1] == "Age 18 years or older"


class TestAgeAndEcog:
    @pytest.mark.parametrize(
        "line",
        [
            "ECOG performance status 0-2",
            "ECOG <= 2",
            "ECOG performance status of 2",
            "ECOG 2",
        ],
    )
    def test_ecog_cap_variants(self, extractor, line):
        predicate = extractor._line_to_predicate("inclusion", line)
        assert predicate.type == "ECOG_MAX"
        assert predicate.value == 2.0

    def test_ecog_range_takes_the_upper_bound(self, extractor):
        """'0-2' also contains a bare '0'. Taking the lower bound as the cap
        would exclude exactly the patients the trial is most open to."""
        assert extractor._line_to_predicate("inclusion", "ECOG 0-2").value == 2.0

    def test_age_lower_bound(self, extractor):
        predicate = extractor._line_to_predicate("inclusion", "Age 18 years or older")
        assert predicate.type == "AGE_RANGE"
        assert predicate.value == "18-"


class TestRefusesToGuess:
    def test_biomarker_without_a_nameable_marker_is_unparseable(self, extractor):
        """Measured C1 failure: 'Subjects testing positive for HIV' in a
        transplant workup is not a tumour-biomarker criterion."""
        predicate = extractor._line_to_predicate(
            "exclusion", "Subjects testing positive for HIV may be rejected"
        )
        assert predicate.type == "UNPARSEABLE"

    def test_prior_therapy_without_a_nameable_agent_is_unparseable(self, extractor):
        predicate = extractor._line_to_predicate(
            "exclusion", "Received prior systemic therapy of any kind"
        )
        assert predicate.type == "UNPARSEABLE"
        assert "no agent could be named" in predicate.reason

    def test_named_agent_is_extracted(self, extractor):
        predicate = extractor._line_to_predicate(
            "exclusion", "Prior treatment with a PARP inhibitor"
        )
        assert predicate.type == "PRIOR_THERAPY_EXCLUDES"
        assert predicate.subject == "PARP inhibitor"
        assert predicate.comparison == "absent"

    def test_named_marker_is_extracted(self, extractor):
        predicate = extractor._line_to_predicate(
            "inclusion", "Tumor must show loss of INI1 by immunohistochemistry"
        )
        assert predicate.type == "BIOMARKER_REQUIRES"
        assert predicate.subject == "INI1"

    @pytest.mark.parametrize("token", ["HIV", "ECOG", "DLCO", "HLA", "MRI"])
    def test_non_gene_uppercase_tokens_are_not_treated_as_markers(self, extractor, token):
        predicate = extractor._line_to_predicate(
            "inclusion", f"Documented {token} mutation status required"
        )
        assert predicate.subject != token


class TestNothingIsDropped:
    def test_every_line_produces_exactly_one_predicate(self, extractor):
        """A criterion silently discarded reads downstream as one the patient
        satisfies."""
        text = "Inclusion Criteria:\n* A thing\n* Another thing\nExclusion Criteria:\n* A third\n"
        result = extractor.extract("NCT1", text)
        assert result.lines_seen == 3
        assert len(result.predicates) == 3

    def test_unrecognised_lines_become_unparseable_not_nothing(self, extractor):
        result = extractor.extract("NCT1", "* Entirely idiosyncratic sponsor prose\n")
        assert [p.type for p in result.predicates] == ["UNPARSEABLE"]


class TestLlmAssistedCaching:
    def test_cache_key_is_the_text_not_the_registry_id(self):
        """Registries edit records in place. Keying on the id would serve a
        cached parse of text that no longer exists."""
        assert criteria_fingerprint("a") != criteria_fingerprint("b")

    def test_model_is_called_once_then_served_from_cache(self, tmp_path):
        calls = []

        def model(text):
            calls.append(text)
            return [{"type": "ECOG_MAX", "source_text": text, "value": 1.0}]

        extractor = LlmAssistedExtractor(model, cache_dir=tmp_path)
        first = extractor.extract("NCT1", "ECOG <= 1")
        second = extractor.extract("NCT1", "ECOG <= 1")
        assert len(calls) == 1
        assert extractor.cache_hits == 1
        assert first.predicates[0].value == second.predicates[0].value == 1.0

    def test_a_model_returning_the_wrong_shape_falls_back_not_crashes(self, tmp_path):
        """A trial with rule-based predicates is worth more than no trial."""

        def broken(text):
            return [{"unexpected": "shape"}]

        extractor = LlmAssistedExtractor(broken, cache_dir=tmp_path)
        result = extractor.extract("NCT1", "* ECOG <= 1\n")
        assert extractor.fallbacks == 1
        assert result.extractor == "rule_based"
        assert result.predicates[0].type == "ECOG_MAX"

    def test_unreadable_cache_is_ignored_not_fatal(self, tmp_path):
        (tmp_path / f"{criteria_fingerprint('x')}.json").write_text("{not json", encoding="utf-8")
        extractor = LlmAssistedExtractor(
            lambda t: [{"type": "UNPARSEABLE", "source_text": t}], cache_dir=tmp_path
        )
        assert extractor.extract("NCT1", "x").predicates


class TestPredicateRoundTrip:
    def test_to_dict_from_dict(self):
        predicate = Predicate(
            type="ECOG_MAX",
            source_text="ECOG <= 2",
            section="inclusion",
            subject="ECOG",
            comparison="at_most",
            value=2.0,
        )
        assert Predicate.from_dict(json.loads(json.dumps(predicate.to_dict()))) == predicate


class TestDefectsFoundByExpandingTheCorpus:
    """Every case here is real registry text that produced a *confidently wrong*
    predicate -- the one outcome docs/trial-extraction-validation-plan.md gives
    zero tolerance, because a wrong predicate buckets a patient with no signal
    that anything went awry."""

    def test_non_inclusion_criteria_is_an_exclusion_header(self, extractor):
        """NCT04055220 heads its exclusions "NON-INCLUSION CRITERIA :". That
        matched no header at all, so all 60-odd of them stayed attributed to the
        inclusion header above -- inverting every one."""
        pairs = split_criteria(
            "INCLUSION CRITERIA :\n* I1. Age >= 12 years\n"
            "NON-INCLUSION CRITERIA :\n* E1. Prior treatment with any VEGFR inhibitor\n"
        )
        assert [section for section, _ in pairs] == ["inclusion", "exclusion"]

    def test_non_inclusion_does_not_also_read_as_inclusion(self):
        assert split_criteria("NON-INCLUSION CRITERIA :\n* A\n")[0][0] == "exclusion"

    @pytest.mark.parametrize("header", ["Ineligibility Criteria:", "Non Inclusion Criteria:"])
    def test_other_exclusion_spellings(self, header):
        assert split_criteria(f"{header}\n* A\n")[0][0] == "exclusion"

    def test_a_decimal_elsewhere_on_the_line_is_not_a_performance_score(self, extractor):
        """Measured on NCT00004157, a legacy record whose whole patient section is
        one line: the bare-number rule matched the "2" of a bilirubin limit of
        "2.5" and capped ECOG at 2."""
        line = (
            "PATIENT CHARACTERISTICS: Age: 70 and under Performance status: Not specified "
            "Hepatic: Bilirubin no greater than 2.5 times upper limit of normal"
        )
        assert extractor._line_to_predicate("unknown", line).type != "ECOG_MAX"

    def test_performance_status_with_no_score_near_it_is_not_extracted(self, extractor):
        predicate = extractor._line_to_predicate("inclusion", "Performance status: Not specified")
        assert predicate.type == "UNPARSEABLE"

    @pytest.mark.parametrize(
        "line",
        [
            "Age: >= 16 years; Male: 1.7 mg/dL; Female: 1.4 mg/dL",
            "Age 2 to < 6 years: maximum serum creatinine 0.8 mg/dL for male and 0.8 for female",
        ],
    )
    def test_an_age_keying_a_lab_limit_is_not_an_eligibility_bound(self, extractor, line):
        """NCT03320330 and NCT03213652 tabulate creatinine limits by age band.
        Read as "must be 16 or older" this excludes younger patients the trial
        accepts, and the trial says nothing of the kind."""
        predicate = extractor._line_to_predicate("inclusion", line)
        assert predicate.type == "UNPARSEABLE"
        assert "laboratory limit" in predicate.reason

    def test_an_age_defining_menopausal_status_is_not_an_eligibility_bound(self, extractor):
        """NCT01692496: the age says when menopause may be assumed, not who may
        enrol."""
        line = (
            "Female subjects not using hormone replacement therapy must have experienced "
            "total cessation of menses for >= 1 year and be greater than 45 years in age"
        )
        assert extractor._line_to_predicate("inclusion", line).type == "UNPARSEABLE"

    def test_a_plain_age_bound_still_extracts(self, extractor):
        """The guards above must not cost the ordinary case."""
        predicate = extractor._line_to_predicate(
            "inclusion", "I1. Age >= 12 years at the day of consenting to the study;"
        )
        assert predicate.type == "AGE_RANGE"
        assert predicate.value == "12-"

    def test_a_plain_ecog_cap_still_extracts(self, extractor):
        predicate = extractor._line_to_predicate(
            "inclusion", "Eastern Cooperative Oncology Group (ECOG) Performance Status (PS) 0-2"
        )
        assert predicate.type == "ECOG_MAX"
        assert predicate.value == 2.0
