"""Binding-site proximity signal — the claim Tier 2 can actually defend.

The gold-standard run (`validation/results.md`) showed the binding-affinity delta
does not survive contact with the validation set: 4 of 9 cases sit beyond the
range docking can assess at all, and the 2 that scored landed `uncertain` with
deltas near noise. `validation-plan.md` pre-committed the fallback for exactly
this outcome — "mutation is in/near the known binding pocket" plus the
AlphaMissense flag, with the delta dropped.

This module is that fallback, and it is a real signal rather than a consolation
prize. The distance from a mutated residue to the co-crystallized ligand is
measured directly from experimental coordinates: no model, no threshold fitted to
outcomes, no accuracy claim to defend. It answers a question a clinician can act
on — *could this substitution plausibly affect this drug by direct contact at
all?* — and it answered it correctly across every gold-standard case where a
dockable structure was found:

| Case | Distance | Known mechanism | Band |
|---|---|---|---|
| EGFR T790M | 3.5 A | gatekeeper, direct contact | in_contact |
| EGFR C797S | 10.1 A | covalent-site loss | distant |
| BRAF V600E | 7.2 A | conformational activation | pocket_adjacent |
| KIT D816V | 16.6 A | activation-loop, allosteric | distant |

**BRAF V600E was previously recorded here as 12.2 A / `distant`, and that was
wrong.** 8C7X is a homodimer whose two copies disagree — 12.2 A in chain A,
7.2 A in chain B — and the pipeline was reporting whichever copy a chain-ordering
rule written for docking happened to return. The closest approach is 7.2 A, which
is `pocket_adjacent`, not `distant`.

That correction weakens a claim this docstring used to make. It is not true that
this signal cleanly places every mechanism docking cannot represent out of contact
range: BRAF V600E acts conformationally and yet sits 7.2 A from the ligand, inside
the range where a direct-contact effect is geometrically available. KIT D816V at
16.6 A still is correctly placed out of range. The honest version of the claim is
narrower — proximity says whether direct contact is *geometrically possible*, and
a mutation can be close and still act by another mechanism entirely.

**What this does not claim.** Proximity is not a prediction of resistance or
sensitivity. A mutation in the pocket may leave binding untouched; a distant one
may abolish it through allostery. The signal states whether a *direct-contact*
mechanism is geometrically available, and nothing further.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ProximityBand = Literal["in_contact", "pocket_adjacent", "distant", "unknown"]

#: Direct van der Waals contact between the residue and the ligand. Standard
#: structural-biology convention for "contacting" — not fitted to any outcome.
CONTACT_ANGSTROM = 5.0

#: Second shell: close enough that a substitution can reshape the pocket without
#: touching the drug. Matches `vina_dock.MUTATION_CONTACT_MAX_ANGSTROM`, the range
#: within which a docking delta is even attempted.
POCKET_ADJACENT_ANGSTROM = 8.0

#: Every description names the ligand actually measured against. This is not a
#: detail: the distance is to whatever molecule was co-crystallized in the chosen
#: structure, which is usually **not** the candidate drug being evaluated. EGFR
#: 8A27 holds ligand KY9, so both gefitinib and osimertinib measure 3.5 A from it
#: — identical, because neither number is about those drugs specifically. Writing
#: "distance to this drug" would be reporting a fact we did not compute.
#:
#: The measurement is still meaningful, and more general than it first appears:
#: it locates the mutation relative to the *occupied binding pocket*. For kinase
#: inhibitors competing for the same ATP site, that is the relevant geometry. But
#: the claim is pocket-level, and the text says so.
_BAND_DESCRIPTIONS: dict[ProximityBand, str] = {
    "in_contact": (
        "The mutated residue is in direct contact with the ligand-binding pocket "
        "of this structure (within {contact:.0f} A of bound ligand {ligand}). A "
        "substitution here can alter binding of drugs occupying this pocket by "
        "changing the contact surface itself."
    ),
    "pocket_adjacent": (
        "The mutated residue lines the ligand-binding pocket of this structure "
        "without directly contacting the bound ligand ({distance:.1f} A from "
        "{ligand}). A substitution here can reshape the pocket and affect binding "
        "indirectly."
    ),
    "distant": (
        "The mutated residue is {distance:.1f} A from the ligand-binding pocket of "
        "this structure (bound ligand {ligand}), outside the pocket. It cannot "
        "affect drugs occupying this pocket by direct contact. Any effect on "
        "treatment response would act through another mechanism — allosteric or "
        "conformational change, altered expression, or a bypass pathway — none of "
        "which structural proximity can assess."
    ),
    "unknown": (
        "Binding-site proximity could not be measured: no structure with both this "
        "residue and a bound ligand was available."
    ),
}


@dataclass(frozen=True)
class ProximitySignal:
    """Where a mutation sits relative to a drug's binding site."""

    band: ProximityBand
    distance_angstrom: float | None
    description: str
    structure_id: str | None
    structure_source: str | None
    #: HET code of the ligand the distance was measured to. Usually NOT the
    #: candidate drug — see `_BAND_DESCRIPTIONS`. Surfaced so a reader can check
    #: what the number actually refers to.
    measured_to_ligand: str | None
    #: True only when the co-crystallized ligand is the candidate drug itself, in
    #: which case the distance is drug-specific rather than pocket-level.
    ligand_is_candidate_drug: bool
    #: True only when measured from experimental coordinates. A predicted model
    #: has no co-crystallized ligand, so proximity is not measurable from one —
    #: this is always True when `band` is not "unknown".
    from_experimental_structure: bool
    #: Per-chain distance for every protomer holding both the residue and a
    #: ligand. A crystal often contains several copies of the same protein, and
    #: they do not always agree: 8C7X puts BRAF V600 12.2 A from the ligand in
    #: chain A and 7.2 A in chain B. Reporting one copy's number as "the"
    #: distance hides that, and which copy you get depends on chain-ordering
    #: rules written for a different purpose.
    protomer_distances: dict[str, float] = field(default_factory=dict)

    @property
    def contact_mechanism_available(self) -> bool:
        """Whether a direct-contact effect on this drug is geometrically possible."""
        return self.band in ("in_contact", "pocket_adjacent")

    @property
    def protomers_disagree(self) -> bool:
        """True when the copies fall in different bands.

        Deliberately band-based rather than a numeric threshold: a 1 A spread
        entirely inside `in_contact` changes no conclusion, while a 0.2 A spread
        straddling 8.0 A changes `pocket_adjacent` into `distant` and with it
        whether a docking delta is even attempted.
        """
        bands = {classify_proximity(d) for d in self.protomer_distances.values()}
        return len(bands) > 1

    @property
    def distance_range_angstrom(self) -> tuple[float, float] | None:
        """(closest, furthest) across protomers, or None if fewer than two."""
        if len(self.protomer_distances) < 2:
            return None
        values = self.protomer_distances.values()
        return (min(values), max(values))


def classify_proximity(distance_angstrom: float | None) -> ProximityBand:
    """Bucket a measured distance. Bands are structural conventions, not fitted cutoffs."""
    if distance_angstrom is None:
        return "unknown"
    if distance_angstrom <= CONTACT_ANGSTROM:
        return "in_contact"
    if distance_angstrom <= POCKET_ADJACENT_ANGSTROM:
        return "pocket_adjacent"
    return "distant"


def describe_proximity(
    band: ProximityBand,
    distance_angstrom: float | None,
    ligand: str | None = None,
) -> str:
    return _BAND_DESCRIPTIONS[band].format(
        contact=CONTACT_ANGSTROM,
        adjacent=POCKET_ADJACENT_ANGSTROM,
        distance=distance_angstrom if distance_angstrom is not None else float("nan"),
        ligand=ligand or "unidentified",
    )


def build_proximity_signal(
    distance_angstrom: float | None,
    *,
    structure_id: str | None,
    structure_source: str | None,
    measured_to_ligand: str | None = None,
    candidate_drug_het_code: str | None = None,
    protomer_distances: dict[str, float] | None = None,
) -> ProximitySignal:
    protomer_distances = dict(protomer_distances or {})
    # Per-chain measurements win over a single passed-in value: one number
    # cannot represent a multimer, and the caller that supplied per-chain data
    # measured them all rather than picking one.
    #
    # The closest approach is reported because it is the permissive reading —
    # the copy in which a direct-contact mechanism is most plausible. Reporting
    # the furthest, or an arbitrary copy, would let the pipeline rule out a
    # mechanism that the structure shows is available. This is also the rule
    # `vina_dock.min_residue_ligand_distance` uses, so the reported distance and
    # the docking contact gate cannot disagree.
    if protomer_distances:
        distance_angstrom = min(protomer_distances.values())

    band = classify_proximity(distance_angstrom)
    is_candidate = bool(
        measured_to_ligand
        and candidate_drug_het_code
        and measured_to_ligand.upper() == candidate_drug_het_code.upper()
    )
    description = describe_proximity(band, distance_angstrom, measured_to_ligand)
    if len({classify_proximity(d) for d in protomer_distances.values()}) > 1:
        # Stated in the description, not left for a reader to infer from a field
        # they may never look at. A band that depends on which copy you measured
        # is not a settled fact and must not read as one.
        detail = ", ".join(
            f"chain {chain} {value:.1f} A"
            for chain, value in sorted(protomer_distances.items())
        )
        description += (
            f" This structure holds {len(protomer_distances)} copies of the "
            f"protein and they do not agree ({detail}); the closest approach is "
            "reported, and the band should be treated as the most permissive "
            "reading rather than a settled fact."
        )
    return ProximitySignal(
        band=band,
        distance_angstrom=distance_angstrom,
        description=description,
        structure_id=structure_id,
        structure_source=structure_source,
        measured_to_ligand=measured_to_ligand,
        ligand_is_candidate_drug=is_candidate,
        from_experimental_structure=band != "unknown",
        protomer_distances=protomer_distances,
    )
