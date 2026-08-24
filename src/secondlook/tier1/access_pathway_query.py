"""Read AccessPathway nodes after resolving a drug to a graph Drug identity.

AccessPathway seeds are country-level expanded-access routes (US/India YAML).
They have no drug property and no Drug relationship. Identity is still gated
first: an unresolved name must not silently receive another country's generic
pathways. `ligand_identity.py` is InChIKey/HET matching, not RxNorm; this
module resolves Drug nodes by case-insensitive `name` or `chembl_id`, and
optionally by InChIKey derived from `smiles`.
"""

from __future__ import annotations

from secondlook.ligand_identity import compare_inchikeys, inchikey_from_smiles
from secondlook.query.contracts import AccessPathway
from secondlook.query.result import QueryResult
from secondlook.tier1.graph_connection import connect_graph

_DRUG_QUERY = """
MATCH (d:Drug)
WHERE toLower(d.name) = toLower($q) OR d.chembl_id = $q
RETURN d.name, d.chembl_id, d.smiles
"""

_DRUG_ALL_QUERY = """
MATCH (d:Drug)
RETURN d.name, d.chembl_id, d.smiles
"""

_PATHWAYS_QUERY = """
MATCH (p:AccessPathway)
RETURN p.pathway_id, p.country, p.pathway_type, p.instrument, p.description,
       p.source_url, p.regulator, p.precedent_strength, p.review_status
"""


def _pathway_id(country: str, pathway_type: str, instrument: str) -> str:
    return f"{country}:{pathway_type}:{instrument}"


def _looks_like_inchikey(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 3 and len(parts[0]) == 14 and len(value) >= 25


def resolve_drug(graph, drug: str) -> tuple[str, str | None] | None:
    """Return (canonical_name, chembl_id) or None if identity cannot be resolved."""
    result = graph.query(_DRUG_QUERY, params={"q": drug})
    rows = list(result.result_set)
    if len(rows) == 1:
        name, chembl_id, _smiles = rows[0]
        return str(name), str(chembl_id) if chembl_id else None
    if len(rows) > 1:
        chembl_ids = {r[1] for r in rows if r[1]}
        if len(chembl_ids) == 1:
            chembl_id = next(iter(chembl_ids))
            name = next(r[0] for r in rows if r[1] == chembl_id)
            return str(name), str(chembl_id)
        return None

    if not _looks_like_inchikey(drug):
        return None

    all_drugs = graph.query(_DRUG_ALL_QUERY, params={})
    matches = []
    for name, chembl_id, smiles in all_drugs.result_set:
        if not smiles:
            continue
        key = inchikey_from_smiles(smiles)
        if not key:
            continue
        kind = compare_inchikeys(drug, key)
        if kind in {"exact", "connectivity"}:
            matches.append((str(name), str(chembl_id) if chembl_id else None, kind))
    exact = [m for m in matches if m[2] == "exact"]
    chosen = exact or matches
    if len({(m[0], m[1]) for m in chosen}) == 1:
        return chosen[0][0], chosen[0][1]
    return None


def query_access_pathways(
    drug: str,
    country: str,
    *,
    graph=None,
) -> QueryResult[AccessPathway]:
    graph = graph if graph is not None else connect_graph()
    resolved = resolve_drug(graph, drug)
    if resolved is None:
        return QueryResult(empty_reason="drug identity could not be resolved")
    resolved_name, chembl_id = resolved

    result = graph.query(_PATHWAYS_QUERY, params={})
    rows = list(result.result_set)
    if not rows:
        return QueryResult(empty_reason="no pathway exists for this drug at all")

    matching = [r for r in rows if str(r[1]).upper() == country.upper()]
    if not matching:
        return QueryResult(empty_reason="no pathway recorded for this country")

    items = []
    for row in matching:
        (
            pid,
            row_country,
            pathway_type,
            instrument,
            description,
            source_url,
            regulator,
            strength,
            review,
        ) = row
        items.append(
            AccessPathway(
                pathway_id=str(pid or _pathway_id(row_country, pathway_type, instrument)),
                country=str(row_country),
                pathway_type=str(pathway_type),
                instrument=str(instrument),
                description=str(description),
                source_url=str(source_url),
                regulator=str(regulator) if regulator else None,
                precedent_strength=str(strength) if strength else None,
                review_status=str(review) if review else None,
                resolved_drug_name=resolved_name,
                resolved_chembl_id=chembl_id,
            )
        )
    return QueryResult(items=tuple(items))
