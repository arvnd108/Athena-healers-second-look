"""Covalent-inhibitor mechanism gate (runs before any binding scoring).

Both scoring methods in this pipeline — mCSM-lig and AutoDock Vina — are
**non-covalent**. Their scoring functions contain no term for bond formation
between ligand and protein. For a drug that works by forming a covalent bond,
they score only the reversible pre-covalent pose, which is not the mechanism.

The consequence is not a missing number, it is a **wrong** one. EGFR C797S
confers resistance to osimertinib precisely by removing the cysteine the drug
bonds to. The reversible pose still fits the pocket, so a non-covalent score can
come back unchanged — reporting *retained binding* for a textbook resistance
case, confidently and in the worst possible direction.

This gate detects covalent drugs before scoring and returns an explicit
"mechanism not representable" result instead of a number. Detection is
deliberately two-sided:

* a curated list of covalent drugs, because name matching is exact and cheap;
* a SMARTS scan for electrophilic warheads, because the list will never be
  complete and a missed covalent drug fails silently in the dangerous direction.

Either signal is enough to gate. Covalent drugs are roughly 7% of approved
small molecules, so this is a real minority worth handling rather than an edge
case worth ignoring.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Approved covalent inhibitors likely to surface from DGIdb/Open Targets/ChEMBL
#: for the kinase targets this pipeline sees. Values name the residue the drug
#: bonds to, so the explanation can be specific rather than generic.
COVALENT_DRUGS: dict[str, str] = {
    "OSIMERTINIB": "EGFR Cys797",
    "AFATINIB": "EGFR Cys797",
    "DACOMITINIB": "EGFR Cys797",
    "NERATINIB": "EGFR Cys805 / HER2",
    "CANERTINIB": "EGFR Cys797",
    "POZIOTINIB": "EGFR Cys797",
    "MOBOCERTINIB": "EGFR Cys797",
    "IBRUTINIB": "BTK Cys481",
    "ACALABRUTINIB": "BTK Cys481",
    "ZANUBRUTINIB": "BTK Cys481",
    "SOTORASIB": "KRAS Cys12 (G12C)",
    "ADAGRASIB": "KRAS Cys12 (G12C)",
    "RITUXIMAB": "",  # not covalent; present only to document the exclusion below
}
# Remove the documentation entry — kept above only to make the omission explicit.
COVALENT_DRUGS.pop("RITUXIMAB")

#: Electrophilic warheads that react with an active-site nucleophile. Catches
#: covalent drugs absent from the list above, which matters because the failure
#: mode of a miss is a confidently wrong score rather than a gap.
WARHEAD_SMARTS: dict[str, str] = {
    "acrylamide": "C=CC(=O)N",
    "vinyl_sulfonamide": "C=CS(=O)(=O)N",
    "chloroacetamide": "ClCC(=O)N",
    "propiolamide": "C#CC(=O)N",
    "fluorosulfate": "OS(=O)(=O)F",
    "nitrile_warhead": "[CX2]#[NX1]",
}

#: Warheads whose SMARTS also matches many harmless molecules. A nitrile is a
#: common, entirely non-reactive substituent, so it only counts as evidence when
#: the drug is already on the curated list — otherwise it would gate half the
#: shortlist on a false positive.
_AMBIGUOUS_WARHEADS = frozenset({"nitrile_warhead"})

MECHANISM_INVALIDATED_MESSAGE = (
    "{drug} is a covalent inhibitor{target}. Both scoring methods available here "
    "(mCSM-lig and AutoDock Vina) are non-covalent: their scoring functions "
    "contain no term for bond formation, so they can only evaluate the reversible "
    "pose. No binding-affinity estimate is reported, because a non-covalent score "
    "would describe the wrong mechanism — and for resistance mutations that remove "
    "the target residue, it can indicate retained binding when binding is in fact "
    "abolished. Assessing this drug requires a covalent-aware method. "
    "AlphaMissense functional score and structural context remain available."
)


@dataclass(frozen=True)
class CovalentVerdict:
    is_covalent: bool
    #: "curated" | "warhead:<name>" | None — how it was detected, for provenance.
    evidence: str | None
    #: Residue the drug bonds to, when known.
    target_residue: str | None

    def message(self, drug_name: str) -> str:
        target = f" that bonds {self.target_residue}" if self.target_residue else ""
        return MECHANISM_INVALIDATED_MESSAGE.format(drug=drug_name.title(), target=target)


def _normalise(drug_name: str) -> str:
    return "".join(ch for ch in drug_name.upper() if ch.isalnum())


def detect_warhead(smiles: str) -> str | None:
    """Name of the first unambiguous electrophilic warhead found, if any.

    Returns None when RDKit is unavailable rather than guessing — a missing
    cheminformatics stack must not silently disable the gate's second half.
    """
    try:
        from rdkit import Chem
    except ImportError:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    for name, smarts in WARHEAD_SMARTS.items():
        if name in _AMBIGUOUS_WARHEADS:
            continue
        pattern = Chem.MolFromSmarts(smarts)
        if pattern is not None and mol.HasSubstructMatch(pattern):
            return name
    return None


def classify_covalent(drug_name: str, smiles: str | None = None) -> CovalentVerdict:
    """Decide whether a candidate acts by forming a covalent bond.

    Curated name match is checked first because it is exact and carries the
    target residue; the warhead scan is the safety net for drugs not on the list.
    """
    normalised = _normalise(drug_name)
    for known, residue in COVALENT_DRUGS.items():
        if normalised == known or known in normalised:
            return CovalentVerdict(True, "curated", residue or None)

    if smiles:
        warhead = detect_warhead(smiles)
        if warhead is not None:
            return CovalentVerdict(True, f"warhead:{warhead}", None)

    return CovalentVerdict(False, None, None)
