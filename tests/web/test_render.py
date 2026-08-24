"""Offline tests for Subsystem M's server-rendered views.

No browser, no network, no fixtures beyond the committed JSON set. The two
tests that matter most here are structural rather than cosmetic:

* `TestComputedCardHasNoPlaceForACitation` asserts the *signature* of
  `_computed_card`, not just its output. IMPLEMENTATION_PLAN.md SS9.2 says
  the computed card must have no place for a citation, "not an empty one" --
  an output-only assertion would still pass after someone added an optional
  `citation_url=None` parameter, which is exactly the regression worth
  catching.
* `TestPerformanceBudget` asserts the gzipped byte budget from
  docs/performance-budget.md, so the budget fails CI when it is exceeded
  instead of aging into a document nobody re-measures.
"""

from __future__ import annotations

import gzip
import inspect
import re

import pytest

from secondlook.web import render
from secondlook.web.fixtures import load_all

TRIAL_EXISTS = "f0000000-0000-4000-8000-000000000031"
TRIAL_ELIGIBILITY = "f0000000-0000-4000-8000-000000000032"
SUPERSEDED = "f0000000-0000-4000-8000-000000000007"
REGULATORY = "f0000000-0000-4000-8000-000000000051"
CONTEXTUAL = "f0000000-0000-4000-8000-000000000061"

#: The first TCP congestion window is ~14.6 KB. A response that fits inside
#: it arrives in one round trip; one that does not costs an extra RTT, which
#: on the 400 ms link in the budget is a visible stall.
SSR_GZIP_BUDGET_BYTES = 14_000


@pytest.fixture(scope="module")
def data():
    return load_all()


def _gz(html: str) -> int:
    return len(gzip.compress(html.encode("utf-8"), 9))


class TestComputedCardHasNoPlaceForACitation:
    def test_signature_accepts_no_citation_parameter(self):
        params = set(inspect.signature(render._computed_card).parameters)
        forbidden = {"citation_url", "citation_id", "url", "citation", "source_name"}
        assert not (params & forbidden), (
            "IMPLEMENTATION_PLAN.md SS9.2: the computed card must have no place "
            f"for a citation, not an empty one. Found {sorted(params & forbidden)}."
        )

    def test_passing_a_citation_is_a_typeerror(self):
        with pytest.raises(TypeError):
            render._computed_card("x", method="m", version="v", citation_url="https://example.org")

    def test_a_smuggled_citation_on_the_source_never_reaches_the_page(self, data):
        """Defence in depth: even if upstream data is wrong, the page is not."""
        finding = dict(data["findings"][TRIAL_ELIGIBILITY])
        finding["source"] = {
            **finding["source"],
            "citation_url": "https://clinicaltrials.gov/study/NCT03778229",
        }
        html = render.render_card(finding)
        assert "clinicaltrials.gov" not in html
        assert "href" not in html

    def test_computed_card_states_method_and_version(self, data):
        html = render.render_card(data["findings"][TRIAL_ELIGIBILITY])
        assert "signals.trial_matching.match_trial" in html
        assert "signals.trial_matching/1" in html

    def test_computed_card_is_dashed_not_solid(self, data):
        html = render.render_card(data["findings"][TRIAL_ELIGIBILITY])
        assert "card-computed" in html


class TestDocumentedCard:
    def test_citation_is_required_by_the_signature(self):
        sig = inspect.signature(render._documented_card)
        assert sig.parameters["citation_url"].default is inspect.Parameter.empty

    def test_citation_renders_clickable(self, data):
        html = render.render_card(data["findings"][TRIAL_EXISTS])
        assert 'href="https://clinicaltrials.gov/study/NCT03778229"' in html
        assert "card-documented" in html

    def test_a_documented_finding_with_no_citation_url_raises(self, data):
        finding = dict(data["findings"][TRIAL_EXISTS])
        finding["source"] = {"kind": "documented", "name": "CIViC"}
        with pytest.raises(KeyError):
            render.render_card(finding)


class TestTheTrialPairRendersSideBySide:
    """Issue #46 emits two signals per matched trial. Reconciling them is M's job."""

    def test_both_cards_render_with_different_treatments(self, data):
        exists = render.render_card(data["findings"][TRIAL_EXISTS])
        bucket = render.render_card(data["findings"][TRIAL_ELIGIBILITY])
        assert "card-documented" in exists and "card-computed" not in exists
        assert "card-computed" in bucket and "card-documented" not in bucket
        # The documented half carries the registry link; the computed half
        # carries none at all. Same trial, two warrants, visibly different.
        assert "clinicaltrials.gov" in exists
        assert "clinicaltrials.gov" not in bucket

    def test_the_bucket_carries_its_pre_screen_caveat(self, data):
        html = render.render_card(data["findings"][TRIAL_ELIGIBILITY])
        assert "not an eligibility determination" in html


class TestRegulatoryAndContextual:
    def test_regulatory_cites_the_instrument(self, data):
        html = render.render_card(data["findings"][REGULATORY])
        assert "card-regulatory" in html
        assert "CDSCO" in html

    def test_contextual_says_it_is_not_about_this_patient(self, data):
        html = render.render_card(data["findings"][CONTEXTUAL])
        assert "card-contextual" in html
        assert "not a finding about this patient" in html

    def test_an_unknown_evidence_class_raises_rather_than_rendering_generically(self):
        with pytest.raises(ValueError, match="unknown evidence class"):
            render.render_card({"evidence_class": "vibes", "claim": "x", "source": {}})


class TestCaveatsAreNeverDropped:
    def test_every_caveat_in_the_data_appears_on_the_page(self, data):
        finding = data["findings"][TRIAL_ELIGIBILITY]
        html = render.render_card(finding)
        for caveat in finding["caveats"]:
            # Escaped comparison: the em-dash and quotes survive escaping.
            assert render._e(caveat) in html


class TestChangeBanner:
    def test_every_change_is_rendered_not_a_sample(self, data):
        html = render.render_change_banner(data["changes"])
        for change in data["changes"]["changes"]:
            assert render._e(change["summary"]) in html

    def test_supersessions_use_literal_line_through(self, data):
        html = render.render_change_banner(data["changes"])
        assert "superseded-claim" in html
        css = render.load_stylesheet()
        assert re.search(r"\.superseded-claim\s*\{[^}]*text-decoration:\s*line-through", css), (
            "SS9.1 requires a literal text-decoration: line-through, not opacity "
            "or a colour change"
        )

    def test_supersession_note_and_trigger_are_both_shown(self, data):
        html = render.render_change_banner(data["changes"])
        assert "That is no longer true." in html
        assert "14 Feb sequencing" in html

    def test_an_empty_change_set_states_its_reason_rather_than_rendering_nothing(self):
        html = render.render_change_banner(
            {"changes": [], "supersessions": [], "unchanged_reason": "No tracked field changed."}
        )
        assert "No tracked field changed." in html


class TestFindingDetail:
    def test_provenance_chain_is_clickable_through_to_the_source(self, data):
        html = render.render_finding_detail(
            data["findings"]["f0000000-0000-4000-8000-000000000021"]
        )
        assert "civicdb.org/evidence/1409" in html
        assert "pubmed.ncbi.nlm.nih.gov" in html

    def test_review_buttons_work_without_javascript(self, data):
        html = render.render_finding_detail(data["findings"][TRIAL_EXISTS])
        assert 'method="post"' in html
        assert 'value="investigating"' in html and 'value="rejected"' in html

    def test_a_superseded_finding_is_struck_through_not_hidden(self, data):
        html = render.render_finding_detail(data["findings"][SUPERSEDED])
        assert "superseded-claim" in html
        assert "osimertinib-naive, EGFR-TKI candidate" in html

    def test_no_javascript_is_served(self, data):
        html = render.render_finding_detail(data["findings"][TRIAL_EXISTS])
        assert "<script" not in html.lower()
        assert "onclick" not in html.lower()

    def test_no_subresource_requests(self, data):
        """Zero external requests: no <link>, no <img>, no webfont."""
        html = render.render_finding_detail(data["findings"][TRIAL_EXISTS])
        assert "<link" not in html.lower()
        assert "<img" not in html.lower()
        assert "fonts.googleapis" not in html and "@import" not in html


class TestBrief:
    def test_suppressed_count_is_visible(self, data):
        html = render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        assert "2 suppressed as already answered" in html

    def test_superseded_findings_are_printed_struck_through_not_omitted(self, data):
        html = render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        assert "Superseded findings" in html
        assert "PD-L1 low; checkpoint monotherapy not indicated" in html

    def test_all_four_evidence_classes_render_distinctly(self, data):
        html = render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        for klass in ("documented", "computed", "regulatory", "contextual"):
            assert f"card-{klass}" in html

    def test_print_stylesheet_expands_links_for_paper(self):
        css = render.load_stylesheet()
        assert "@media print" in css
        assert "a[href]::after" in css

    def test_open_questions_are_ordered_by_priority(self, data):
        html = render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        progression = html.index("What resistance mechanisms are documented")
        biomarker = html.index("What does PD-L1 crossing")
        assert progression < biomarker, "priority 4 must precede priority 1"


class TestEscaping:
    def test_claims_are_escaped(self):
        html = render.render_card(
            {
                "evidence_class": "computed",
                "claim": "<script>alert(1)</script>",
                "source": {"method": "m", "version": "v"},
            }
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_none_renders_empty_not_the_word_none(self):
        assert render._e(None) == ""


class TestPerformanceBudget:
    """docs/performance-budget.md, asserted rather than aspired to."""

    def test_finding_detail_fits_one_congestion_window(self, data):
        size = _gz(render.render_finding_detail(data["findings"][TRIAL_ELIGIBILITY]))
        assert size <= SSR_GZIP_BUDGET_BYTES, f"{size} B gzipped exceeds the budget"

    def test_brief_fits_one_congestion_window(self, data):
        size = _gz(
            render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        )
        assert size <= SSR_GZIP_BUDGET_BYTES, f"{size} B gzipped exceeds the budget"

    def test_the_budget_is_measured_on_a_real_page_not_an_empty_one(self, data):
        """Guards the guard: a budget that passes because the page is blank is not a budget."""
        html = render.render_brief(data["case"], data["changes"], data["queue"], data["findings"])
        assert len(html) > 8000
        assert html.count("card-") >= 6


class TestFixtures:
    def test_no_computed_fixture_carries_a_citation_field(self, data):
        for finding_id, finding in data["findings"].items():
            if finding["evidence_class"] != "computed":
                continue
            source = finding["source"]
            assert "citation_url" not in source, finding_id
            assert "citation_id" not in source, finding_id

    def test_a_missing_fixture_raises_rather_than_returning_empty(self, tmp_path):
        from secondlook.web.fixtures import _read

        with pytest.raises(FileNotFoundError, match="not found"):
            _read("case.json", tmp_path)
