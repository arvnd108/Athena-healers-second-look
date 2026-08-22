"""Ligand identity by InChIKey, replacing name-to-HET-code string matching.

A PDB chemical-component code is an opaque three-character identifier: `KY9`
carries no information about which drug it is. The previous resolver asked
whether the code appeared as a substring of the drug name:

    if code.upper() in needle or needle.endswith(code.upper()):

Against three-character codes and arbitrary drug names that is close to a coin
flip in both directions. It can match an unrelated ligand (any drug name
containing those three letters) and miss the correct one (`KY9` appears nowhere
in `OSIMERTINIB`). Both failures are silent, and the first one is worse: it hands
mCSM-lig a structure bound to the wrong molecule and the result looks fine.

InChIKey comparison is identity rather than resemblance. Matching is two-tier:

* **Exact** — all 27 characters. Same molecule including stereochemistry and
  protonation state.
* **Connectivity** — the first 14 characters, which encode the molecular skeleton
  only. Catches the same drug deposited as a different salt, tautomer, or
  stereoisomer. Reported with an explicit flag, never silently treated as exact,
  because stereochemistry can matter for binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MatchKind = Literal["exact", "connectivity", "none"]


@dataclass(frozen=True)
class LigandMatch:
    kind: MatchKind
    het_code: str | None
    candidate_inchikey: str | None
    ligand_inchikey: str | None
    #: True only for a full 27-character match. A connectivity match means the
    #: skeleton agrees but stereochemistry, charge, or salt form may not.
    is_exact: bool

    @property
    def matched(self) -> bool:
        return self.kind != "none"

    @property
    def note(self) -> str | None:
        if self.kind == "exact":
            return None
        if self.kind == "connectivity":
            return (
                f"Ligand {self.het_code} matches this drug's molecular skeleton but not its "
                "full structure; stereochemistry, charge, or salt form may differ."
            )
        return None


def inchikey_from_smiles(smiles: str) -> str | None:
    """InChIKey for a SMILES string, or None when it cannot be derived.

    Returns None rather than raising when RDKit is missing or the SMILES is
    unparseable — the caller degrades to "no match", which is the safe direction.
    """
    try:
        from rdkit import Chem
    except ImportError:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        key = Chem.MolToInchiKey(mol)
    except Exception:  # noqa: BLE001 - RDKit raises bare exceptions from the InChI layer
        return None
    return key or None


def connectivity_layer(inchikey: str) -> str:
    """The skeleton-only prefix of an InChIKey."""
    return inchikey.split("-")[0] if "-" in inchikey else inchikey[:14]


def compare_inchikeys(candidate: str | None, ligand: str | None) -> MatchKind:
    if not candidate or not ligand:
        return "none"
    if candidate.upper() == ligand.upper():
        return "exact"
    if connectivity_layer(candidate.upper()) == connectivity_layer(ligand.upper()):
        return "connectivity"
    return "none"


def match_ligand(
    candidate_smiles: str | None,
    het_inchikeys: dict[str, str | None],
) -> LigandMatch:
    """Find which HET code, if any, is the candidate drug.

    `het_inchikeys` maps HET code to its InChIKey. Exact matches win over
    connectivity matches regardless of iteration order.
    """
    candidate_key = inchikey_from_smiles(candidate_smiles) if candidate_smiles else None
    if candidate_key is None:
        return LigandMatch("none", None, None, None, False)

    fallback: LigandMatch | None = None
    for het_code, ligand_key in het_inchikeys.items():
        kind = compare_inchikeys(candidate_key, ligand_key)
        if kind == "exact":
            return LigandMatch("exact", het_code, candidate_key, ligand_key, True)
        if kind == "connectivity" and fallback is None:
            fallback = LigandMatch("connectivity", het_code, candidate_key, ligand_key, False)
    return fallback or LigandMatch("none", None, candidate_key, None, False)
