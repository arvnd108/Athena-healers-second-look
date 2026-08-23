"""Binding-site proximity — the fallback signal Tier 2 actually defends."""

import pytest

from secondlook.proximity import (
    CONTACT_ANGSTROM,
    POCKET_ADJACENT_ANGSTROM,
    build_proximity_signal,
    classify_proximity,
    describe_proximity,
)


@pytest.mark.parametrize(
    "distance,expected",
    [
        (0.0, "in_contact"),
        (3.5, "in_contact"),  # EGFR T790M, measured
        (5.0, "in_contact"),  # boundary is inclusive
        (5.1, "pocket_adjacent"),
        (8.0, "pocket_adjacent"),
        (8.1, "distant"),
        (10.1, "distant"),  # EGFR C797S, measured
        (12.2, "distant"),  # BRAF V600E, measured
        (16.6, "distant"),  # KIT D816V, measured
        (None, "unknown"),
    ],
)
def test_bands_match_measured_gold_standard_distances(distance, expected):
    assert classify_proximity(distance) == expected


def test_bands_are_structural_conventions_not_fitted_cutoffs():
    """5 A is the standard van der Waals contact convention, not tuned to outcomes."""
    assert CONTACT_ANGSTROM == 5.0
    assert POCKET_ADJACENT_ANGSTROM == 8.0


def test_contact_mechanism_availability_is_the_actual_claim():
    assert build_proximity_signal(
        3.5, structure_id="8A27", structure_source="PDB"
    ).contact_mechanism_available
    assert build_proximity_signal(
        7.0, structure_id="8A27", structure_source="PDB"
    ).contact_mechanism_available
    assert not build_proximity_signal(
        16.6, structure_id="8PQD", structure_source="PDB"
    ).contact_mechanism_available
    assert not build_proximity_signal(
        None, structure_id=None, structure_source=None
    ).contact_mechanism_available


def test_distant_description_names_the_mechanisms_it_cannot_assess():
    """The honest part: says what it cannot rule out, not just what it found.

    Phrased as "drugs occupying this pocket" rather than "this drug": the distance
    is measured to the co-crystallized ligand, so a drug-specific claim would
    overstate what was computed.
    """
    text = describe_proximity("distant", 16.6, "STI")
    assert "16.6" in text
    assert "allosteric" in text
    assert "bypass" in text
    assert "cannot affect drugs occupying this pocket by direct contact" in text
    assert "STI" in text


def test_no_band_claims_resistance_or_sensitivity():
    """Proximity is geometry, not a treatment-response prediction."""
    forbidden = ("resistant", "resistance", "sensitive", "will respond", "recommend")
    for band, distance in (
        ("in_contact", 3.0),
        ("pocket_adjacent", 6.0),
        ("distant", 20.0),
        ("unknown", None),
    ):
        text = describe_proximity(band, distance).lower()
        for word in forbidden:
            assert word not in text, f"{band} description claims {word!r}"


def test_unknown_band_says_why_rather_than_guessing():
    text = describe_proximity("unknown", None)
    assert "could not be measured" in text
    assert "nan" not in text.lower()


def test_signal_records_which_structure_it_measured_from():
    signal = build_proximity_signal(3.5, structure_id="8A27", structure_source="PDB")
    assert signal.structure_id == "8A27"
    assert signal.structure_source == "PDB"
    assert signal.from_experimental_structure is True


def test_unmeasurable_signal_is_not_marked_experimental():
    signal = build_proximity_signal(None, structure_id=None, structure_source=None)
    assert signal.from_experimental_structure is False


# --- The distance is to the co-crystallized ligand, not the candidate drug ----
# EGFR 8A27 holds ligand KY9. Both gefitinib and osimertinib measured 3.5 A from
# it — identical, because neither number is about those drugs. Describing that as
# "distance to this drug" reports a fact that was not computed.


def test_description_names_the_ligand_actually_measured():
    signal = build_proximity_signal(
        3.5, structure_id="8A27", structure_source="PDB", measured_to_ligand="KY9"
    )
    assert "KY9" in signal.description
    assert signal.measured_to_ligand == "KY9"


def test_description_does_not_claim_the_distance_is_to_the_candidate_drug():
    for distance in (3.5, 6.0, 16.6):
        text = build_proximity_signal(
            distance, structure_id="8A27", structure_source="PDB", measured_to_ligand="KY9"
        ).description
        assert "from the bound drug" not in text
        assert "contact with the bound drug" not in text
        assert "pocket" in text


def test_ligand_is_flagged_as_the_candidate_only_on_an_exact_match():
    same = build_proximity_signal(
        3.5,
        structure_id="8A27",
        structure_source="PDB",
        measured_to_ligand="KY9",
        candidate_drug_het_code="ky9",
    )
    different = build_proximity_signal(
        3.5,
        structure_id="8A27",
        structure_source="PDB",
        measured_to_ligand="KY9",
        candidate_drug_het_code="IRE",
    )
    unknown = build_proximity_signal(
        3.5, structure_id="8A27", structure_source="PDB", measured_to_ligand="KY9"
    )
    assert same.ligand_is_candidate_drug is True
    assert different.ligand_is_candidate_drug is False
    assert unknown.ligand_is_candidate_drug is False


def test_unidentified_ligand_is_stated_not_silently_omitted():
    text = build_proximity_signal(
        3.5, structure_id="X", structure_source="PDB", measured_to_ligand=None
    ).description
    assert "unidentified" in text


def test_two_different_drugs_on_one_structure_share_the_pocket_measurement():
    """The identical numbers are correct — the framing just must not imply otherwise."""
    a = build_proximity_signal(
        3.5,
        structure_id="8A27",
        structure_source="PDB",
        measured_to_ligand="KY9",
        candidate_drug_het_code="GEF",
    )
    b = build_proximity_signal(
        3.5,
        structure_id="8A27",
        structure_source="PDB",
        measured_to_ligand="KY9",
        candidate_drug_het_code="OSI",
    )
    assert a.distance_angstrom == b.distance_angstrom
    assert a.description == b.description
    assert not a.ligand_is_candidate_drug and not b.ligand_is_candidate_drug


# --- homodimer protomers ------------------------------------------------------
#
# A crystal often holds several copies of the protein and they do not always
# agree. 8C7X puts BRAF V600 12.2 A from the ligand in chain A and 7.2 A in
# chain B -- which straddles the 8 A band boundary, so which copy was measured
# decided whether the mutation read as `pocket_adjacent` or `distant`.

from secondlook.proximity import build_proximity_signal  # noqa: E402


def _signal(protomers, **kw):
    return build_proximity_signal(
        None, structure_id="8C7X", structure_source="PDB",
        measured_to_ligand="TXV", protomer_distances=protomers, **kw,
    )


def test_closest_protomer_is_reported():
    """The permissive reading: the copy where a contact mechanism is most
    plausible. Reporting the furthest would rule out a mechanism the structure
    shows is available."""
    signal = _signal({"A": 12.2452, "B": 7.2077})
    assert signal.distance_angstrom == pytest.approx(7.2077)
    assert signal.band == "pocket_adjacent"


def test_per_chain_values_win_over_a_single_passed_in_distance():
    """One number cannot represent a multimer."""
    signal = build_proximity_signal(
        99.0, structure_id="8C7X", structure_source="PDB",
        protomer_distances={"A": 12.2, "B": 7.2},
    )
    assert signal.distance_angstrom == pytest.approx(7.2)


def test_disagreement_is_stated_in_the_description():
    """A band that depends on which copy you measured is not a settled fact and
    must not read as one."""
    description = _signal({"A": 12.2452, "B": 7.2077}).description
    assert "do not agree" in description
    assert "chain A 12.2" in description
    assert "chain B 7.2" in description


def test_disagreement_is_band_based_not_a_numeric_threshold():
    """A 1 A spread inside one band changes no conclusion; a 0.2 A spread across
    a boundary changes whether a docking delta is even attempted."""
    wide_same_band = _signal({"A": 2.0, "B": 3.5})       # both in_contact
    narrow_across = _signal({"A": 7.9, "B": 8.1})        # straddles 8.0
    assert not wide_same_band.protomers_disagree
    assert narrow_across.protomers_disagree
    assert "do not agree" not in wide_same_band.description


def test_single_protomer_has_no_range_and_no_disagreement():
    signal = _signal({"A": 7.2})
    assert signal.distance_range_angstrom is None
    assert not signal.protomers_disagree


def test_range_reports_closest_and_furthest():
    assert _signal({"A": 12.2, "B": 7.2, "C": 9.0}).distance_range_angstrom == (7.2, 12.2)


def test_no_protomer_data_behaves_as_before():
    signal = build_proximity_signal(
        3.5, structure_id="8A27", structure_source="PDB", measured_to_ligand="KY9"
    )
    assert signal.distance_angstrom == pytest.approx(3.5)
    assert signal.protomer_distances == {}
    assert not signal.protomers_disagree
