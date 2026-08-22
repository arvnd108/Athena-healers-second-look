"""Tier 1 retrieval Modes 1 and 2 -- exact structured match and structured
relaxation, per tier1-implementation-spec.md SS4.

Every result item is shaped per the api-contracts.md Tier 1 result item
schema, with `retrieval_mode` (and, for Mode 2, `relaxation_type`) added so
the caller can never mistake a relaxed hit for an exact one.

HARD RULE, unconditional: an item with no `citation.url` is never
constructed or returned, at either mode. Every filtered item is counted and
returned to the caller as `filtered_count` -- never only a log line
(RareCure rule #4).

Exact-match key: hgvs_p OR variant_name, not hgvs_p alone. Only 4 of 37
variants in the loaded graph carry hgvs_p (missense only) -- fusions,
amplifications, and expression changes are named ("Fusion",
"Amplification", "OVEREXPRESSION") with no protein-level HGVS notation at
all, because they aren't point mutations. Restricting Mode 1 to hgvs_p
would make it structurally unable to reach ~89% of loaded evidence,
including the level-A ETV6::NTRK3 -> Larotrectinib finding. `variant_name`
is CIViC's own per-gene variant name, scoped by gene in the same way CIViC
itself scopes it (not globally unique, but unique within a gene, the same
way "T790M" is unique within EGFR).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import (
    EVIDENCE_LEVELS,
    RESPONSE_DIRECTIONS,
    RETRIEVAL_MODES,
    assert_valid,
)

logger = logging.getLogger(__name__)

RELAXATION_TYPES = frozenset({"same_gene_different_variant", "same_disease_drug_class"})


@dataclass
class RetrievalResult:
    """Items plus a structured count of what was filtered out, so a caller
    can assert on the count directly rather than scrape it from a log."""

    items: list[dict] = field(default_factory=list)
    filtered_count: int = 0


def _require_exactly_one_variant_key(hgvs_p: str | None, variant_name: str | None) -> None:
    if bool(hgvs_p) == bool(variant_name):
        raise ValueError(
            "exactly one of hgvs_p or variant_name is required "
            f"(got hgvs_p={hgvs_p!r}, variant_name={variant_name!r})"
        )


MATCH_KEYS = frozenset({"hgvs_p", "variant_name"})


def _row_to_item(
    *,
    drug: str | None,
    direction: str,
    evidence_level: str,
    citation_url: str | None,
    summary: str | None,
    civic_id,
    retrieval_mode: str,
    relaxation_type: str | None = None,
    match_key: str | None = None,
) -> dict | None:
    """Build one Tier 1 result item, or return None if it must be filtered.

    Validates every bounded field against graph_schema.py's allowed sets
    before returning -- a corrupted graph value (a stale write, a manual
    Cypher edit, a future loader bug) must raise here, not pass through to
    the caller silently (RareCure rule #5).
    """
    if not citation_url:
        return None

    assert_valid(evidence_level, EVIDENCE_LEVELS, "evidence_level")
    assert_valid(direction, RESPONSE_DIRECTIONS, "direction")
    assert_valid(retrieval_mode, RETRIEVAL_MODES, "retrieval_mode")

    item = {
        "type": "documented",
        "source": "CIViC",
        "evidence_level": evidence_level,
        "citation": {"id": str(civic_id), "url": citation_url},
        "summary": summary,
        "drug": drug,
        "trial_status": None,
        "retrieval_mode": retrieval_mode,
    }
    if relaxation_type is not None:
        assert_valid(relaxation_type, RELAXATION_TYPES, "relaxation_type")
        item["relaxation_type"] = relaxation_type
    if match_key is not None:
        # Same principle as relaxation_type: a result should always say WHY
        # it matched, not just that it did. Mode 1 only -- Mode 2's path (a)
        # matches on gene alone (any variant except the one just excluded),
        # so hgvs_p/variant_name isn't "the key that matched" there the same
        # way; path (b) doesn't use either at all.
        assert_valid(match_key, MATCH_KEYS, "match_key")
        item["match_key"] = match_key
    return item


def _rows_to_result(
    rows,
    *,
    retrieval_mode: str,
    relaxation_type: str | None,
    match_key: str | None,
    log_ctx: str,
) -> RetrievalResult:
    items: list[dict] = []
    filtered_count = 0
    for row in rows:
        drug_name, direction, evidence_level, citation_url, summary, civic_id = row
        item = _row_to_item(
            drug=drug_name,
            direction=direction,
            evidence_level=evidence_level,
            citation_url=citation_url,
            summary=summary,
            civic_id=civic_id,
            retrieval_mode=retrieval_mode,
            relaxation_type=relaxation_type,
            match_key=match_key,
        )
        if item is None:
            filtered_count += 1
            logger.warning("%s: dropped evidence item %s -- no citation_url", log_ctx, civic_id)
            continue
        items.append(item)
    return RetrievalResult(items=items, filtered_count=filtered_count)


# ---------------------------------------------------------------------------
# Mode 1 -- exact structured match
# ---------------------------------------------------------------------------

_RETURN_CLAUSE = (
    "RETURN d.name, r.direction, e.evidence_level, e.citation_url, e.summary, e.civic_id "
    "ORDER BY e.evidence_level"
)

_EXACT_BY_HGVS_QUERY = f"""
MATCH (g:Gene {{symbol: $gene}})-[:HAS_VARIANT]->(v:Variant {{hgvs_p: $hgvs_p}})
MATCH (v)-[r:PREDICTS_RESPONSE_TO]->(d:Drug)
MATCH (e:EvidenceItem)-[:SUPPORTS]->(v)
WHERE e.evidence_level IN ['A','B','C','D','E']
{_RETURN_CLAUSE}
"""

_EXACT_BY_NAME_QUERY = f"""
MATCH (g:Gene {{symbol: $gene}})-[:HAS_VARIANT]->(v:Variant {{name: $variant_name}})
MATCH (v)-[r:PREDICTS_RESPONSE_TO]->(d:Drug)
MATCH (e:EvidenceItem)-[:SUPPORTS]->(v)
WHERE e.evidence_level IN ['A','B','C','D','E']
{_RETURN_CLAUSE}
"""


def retrieve_exact(
    gene: str,
    hgvs_p: str | None = None,
    *,
    variant_name: str | None = None,
    graph=None,
) -> RetrievalResult:
    """Mode 1: gene + exact variant -> drugs + evidence.

    Exactly one of `hgvs_p` (protein-level HGVS, for missense variants) or
    `variant_name` (CIViC's own variant name, for everything else -- fusion
    labels, "Amplification", "Overexpression", ...) is required. Passing
    both or neither raises ValueError rather than guessing which one the
    caller meant. Every returned item carries `match_key` recording which
    of the two actually produced it, so a caller (or reviewer) can always
    tell why a result matched, not just that it did.

    `civic_variant_id` is deliberately NOT a third match key. Checked
    against the loaded graph rather than assumed: zero (gene, variant_name)
    pairs collide across all 37 loaded Gene-HAS_VARIANT-Variant edges, so
    `(gene, variant_name)` is already a unique key in the data as loaded
    today -- adding civic_variant_id would not close a real collision risk
    right now. This is a property of the current 17-disease-scope load, not
    a guarantee CIViC upholds in general; re-check this exact query
    (MATCH (g:Gene)-[:HAS_VARIANT]->(v:Variant) WITH g.symbol, v.name,
    count(v) AS n WHERE n>1 ...) if the loaded scope grows and add
    civic_variant_id as a key then if it ever returns a non-empty result.

    Uses FalkorDB's parameterized query support -- every value is a bound
    parameter, never interpolated into the Cypher string, so a
    caller-supplied gene/variant value cannot inject Cypher.
    """
    _require_exactly_one_variant_key(hgvs_p, variant_name)
    graph = graph if graph is not None else connect_graph()

    if hgvs_p:
        result = graph.query(_EXACT_BY_HGVS_QUERY, params={"gene": gene, "hgvs_p": hgvs_p})
        match_key = "hgvs_p"
        log_ctx = f"retrieve_exact(gene={gene}, hgvs_p={hgvs_p})"
    else:
        result = graph.query(
            _EXACT_BY_NAME_QUERY, params={"gene": gene, "variant_name": variant_name}
        )
        match_key = "variant_name"
        log_ctx = f"retrieve_exact(gene={gene}, variant_name={variant_name})"

    return _rows_to_result(
        result.result_set,
        retrieval_mode="exact",
        relaxation_type=None,
        match_key=match_key,
        log_ctx=log_ctx,
    )


# ---------------------------------------------------------------------------
# Mode 2 -- structured relaxation
# ---------------------------------------------------------------------------

# Path (a): same gene, any other variant with CIViC evidence. Two variants
# of the exclusion clause depending on which key Mode 1 was called with --
# excluding by the wrong key (e.g. excluding by hgvs_p when the miss was a
# variant_name lookup) would silently fail to exclude the variant that was
# just searched, since fusions/amplifications have hgvs_p = NULL and
# `NULL <> $hgvs_p` is NULL (falsy), not true, in Cypher's three-valued
# logic -- get this wrong and path (a) re-returns the exact variant Mode 1
# already found nothing citable for, mislabeled as a "relaxed" hit.
_SAME_GENE_EXCLUDING_HGVS_QUERY = f"""
MATCH (g:Gene {{symbol: $gene}})-[:HAS_VARIANT]->(v:Variant)
WHERE v.hgvs_p IS NULL OR v.hgvs_p <> $hgvs_p
MATCH (v)-[r:PREDICTS_RESPONSE_TO]->(d:Drug)
MATCH (e:EvidenceItem)-[:SUPPORTS]->(v)
WHERE e.evidence_level IN ['A','B','C','D','E']
{_RETURN_CLAUSE}
"""

_SAME_GENE_EXCLUDING_NAME_QUERY = f"""
MATCH (g:Gene {{symbol: $gene}})-[:HAS_VARIANT]->(v:Variant)
WHERE v.name IS NULL OR v.name <> $variant_name
MATCH (v)-[r:PREDICTS_RESPONSE_TO]->(d:Drug)
MATCH (e:EvidenceItem)-[:SUPPORTS]->(v)
WHERE e.evidence_level IN ['A','B','C','D','E']
{_RETURN_CLAUSE}
"""

# Path (b): same disease + same drug class.
_SAME_DISEASE_DRUG_CLASS_QUERY = f"""
MATCH (dis:Disease {{name: $disease}})<-[:OBSERVED_IN]-(v:Variant)
MATCH (v)-[r:PREDICTS_RESPONSE_TO]->(d:Drug)
MATCH (e:EvidenceItem)-[:SUPPORTS]->(v)
WHERE e.evidence_level IN ['A','B','C','D','E'] AND d.drug_class = $drug_class
{_RETURN_CLAUSE}
"""


def _try_same_gene(
    gene: str, hgvs_p: str | None, variant_name: str | None, graph
) -> RetrievalResult:
    if hgvs_p:
        result = graph.query(
            _SAME_GENE_EXCLUDING_HGVS_QUERY, params={"gene": gene, "hgvs_p": hgvs_p}
        )
    else:
        result = graph.query(
            _SAME_GENE_EXCLUDING_NAME_QUERY, params={"gene": gene, "variant_name": variant_name}
        )
    return _rows_to_result(
        result.result_set,
        retrieval_mode="relaxed",
        relaxation_type="same_gene_different_variant",
        match_key=None,
        log_ctx=f"retrieve_relaxed same-gene (gene={gene})",
    )


def _try_same_disease_drug_class(
    disease: str | None, drug_class: str | None, graph
) -> RetrievalResult:
    if not disease or not drug_class:
        return RetrievalResult()
    result = graph.query(
        _SAME_DISEASE_DRUG_CLASS_QUERY, params={"disease": disease, "drug_class": drug_class}
    )
    return _rows_to_result(
        result.result_set,
        retrieval_mode="relaxed",
        relaxation_type="same_disease_drug_class",
        match_key=None,
        log_ctx=f"retrieve_relaxed same-disease-drug-class (disease={disease})",
    )


def retrieve_relaxed(
    gene: str,
    hgvs_p: str | None = None,
    disease: str | None = None,
    *,
    variant_name: str | None = None,
    drug_class: str | None = None,
    graph=None,
) -> RetrievalResult:
    """Mode 2: structured relaxation. Only meaningful to call when
    retrieve_exact() returned no items -- pass it the same
    hgvs_p/variant_name key so path (a) excludes the right variant.

    Tries, in order, stopping at the first non-empty result:
      (a) same gene, any other variant with CIViC evidence.
      (b) same disease + same drug class. Requires both `disease` and
          `drug_class` -- when either is omitted this path is skipped
          rather than run with a missing constraint (which would just be
          "any drug for this disease", not a relaxation of the original
          drug-class intent). `drug_class` comes from ChEMBL mechanism data
          populated onto Drug.drug_class by the loader; a Drug written
          before that enrichment ran has no drug_class and will not match.

    Every item carries `relaxation_type` so the caller can never mistake
    this for an exact-variant match. `filtered_count` accumulates across
    whichever path actually ran and returned non-empty.
    """
    _require_exactly_one_variant_key(hgvs_p, variant_name)
    graph = graph if graph is not None else connect_graph()

    same_gene = _try_same_gene(gene, hgvs_p, variant_name, graph)
    if same_gene.items:
        return same_gene

    same_disease = _try_same_disease_drug_class(disease, drug_class, graph)
    if same_disease.items:
        return same_disease

    return RetrievalResult(
        items=[], filtered_count=same_gene.filtered_count + same_disease.filtered_count
    )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def main() -> int:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Query Tier 1 retrieval Modes 1/2")
    parser.add_argument("--gene", required=True)
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--hgvs-p")
    key.add_argument("--variant-name")
    parser.add_argument("--disease")
    parser.add_argument("--drug-class")
    args = parser.parse_args()

    exact = retrieve_exact(args.gene, args.hgvs_p, variant_name=args.variant_name)
    if exact.items:
        print(f"Mode 1 (exact): {len(exact.items)} items, {exact.filtered_count} filtered")
        print(json.dumps(exact.items, indent=2))
        return 0

    relaxed = retrieve_relaxed(
        args.gene,
        args.hgvs_p,
        args.disease,
        variant_name=args.variant_name,
        drug_class=args.drug_class,
    )
    print(
        f"Mode 1: 0 items. Mode 2 (relaxed): {len(relaxed.items)} items, "
        f"{relaxed.filtered_count} filtered"
    )
    print(json.dumps(relaxed.items, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
