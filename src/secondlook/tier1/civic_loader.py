"""Load CIViC evidence into the Tier 1 FalkorDB knowledge graph.

Scope is config-driven (config/civic_scope.yaml), writes are idempotent
(MERGE, never CREATE), and every dropped record is counted by reason and
returned in a structured LoadSummary rather than only logged.

Run:
    python -m secondlook.tier1.civic_loader
    python -m secondlook.tier1.civic_loader --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from secondlook.tier1.civic_verify import CivicApiError
from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import (
    CITES,
    EVIDENCE_LEVELS,
    HAS_VARIANT,
    OBSERVED_IN,
    PREDICTS_RESPONSE_TO,
    RESPONSE_DIRECTIONS,
    SUPPORTS,
    VARIANT_TYPES,
    assert_valid,
    predicts_response_to_properties,
    with_provenance,
)

logger = logging.getLogger(__name__)

CIVIC_GRAPHQL_URL = "https://civicdb.org/api/graphql"
CONFIG_PATH = Path(__file__).parent / "config" / "civic_scope.yaml"
SOURCE_NAME = "CIViC"
PAGE_SIZE = 50
MAX_CONCURRENT_REQUESTS = 5
REQUEST_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Direction mapping -- the load-bearing part of this module
# ---------------------------------------------------------------------------
#
# CIViC has TWO separate fields, and conflating them is a clinical-severity
# bug rather than a cosmetic one (verified live, see civic_verify.py):
#
#   evidenceDirection : SUPPORTS | DOES_NOT_SUPPORT | NA
#       -- does this evidence support or refute the claim?
#   significance      : SENSITIVITYRESPONSE | RESISTANCE | 22 other values
#       -- what IS the claim?
#
# Our graph's PREDICTS_RESPONSE_TO.direction (sensitive|resistant) is a
# statement about drug response, so it derives from `significance`, gated on
# the evidence actually SUPPORTing that claim.
#
# The subtle case this exists to get right: significance=SENSITIVITYRESPONSE
# with evidenceDirection=DOES_NOT_SUPPORT means "this study found the drug did
# NOT confer sensitivity". That is emphatically NOT the same claim as
# "resistant" -- inverting it would fabricate a resistance finding CIViC never
# made. Anything that does not map cleanly is SKIPPED and counted, never
# guessed (tier1-implementation-spec.md SS7 Deliverable 2).
_DIRECTION_MAP: dict[tuple[str, str], str] = {
    ("SENSITIVITYRESPONSE", "SUPPORTS"): "sensitive",
    ("RESISTANCE", "SUPPORTS"): "resistant",
}


def map_direction(significance: str | None, evidence_direction: str | None) -> str | None:
    """Map a CIViC (significance, evidenceDirection) pair to sensitive/resistant.

    Returns None when the pair does not map cleanly -- the caller must skip
    and count the record, never substitute a default. REDUCED_SENSITIVITY is
    deliberately unmapped: it is a graded claim, not a binary resistance one.
    """
    if not significance or not evidence_direction:
        return None
    direction = _DIRECTION_MAP.get((significance.upper(), evidence_direction.upper()))
    if direction is not None:
        assert_valid(direction, RESPONSE_DIRECTIONS, "direction")
    return direction


# Sequence Ontology ID -> our bounded variant_type set. Matching on SO IDs
# rather than on the free-text SO term name is RareCure rule #8 again: an
# ontology identifier is exact, a name substring is not.
_SO_ID_TO_VARIANT_TYPE: dict[str, str] = {
    "SO:0001583": "missense",  # missense_variant
    "SO:0001606": "missense",  # amino_acid_substitution
    "SO:0000667": "indel",  # insertion
    "SO:0000159": "indel",  # deletion
    "SO:1000032": "indel",  # delins
    "SO:0001589": "indel",  # frameshift_variant
    "SO:0001909": "indel",  # frameshift_elongation
    "SO:0001910": "indel",  # frameshift_truncation
    "SO:0001565": "fusion",  # gene_fusion
    "SO:0001882": "fusion",  # feature_fusion
    "SO:0001886": "fusion",  # transcript_fusion (what CIViC tags fusions with)
    "SO:0001574": "splice",  # splice_acceptor_variant
    "SO:0001575": "splice",  # splice_donor_variant
    "SO:0001630": "splice",  # splice_region_variant
}


def map_variant_type(variant_types: list[dict] | None) -> str | None:
    """Map CIViC variantTypes[].soid to our bounded variant_type, or None.

    None means "CIViC's SO term is outside our four-value vocabulary" -- the
    variant is still loaded (the evidence is clinically real), the field is
    just honestly left unset rather than forced into a bucket it doesn't
    belong in. Counted as `unmapped_variant_type` in the summary.
    """
    for variant_type in variant_types or []:
        mapped = _SO_ID_TO_VARIANT_TYPE.get((variant_type or {}).get("soid", ""))
        if mapped:
            assert_valid(mapped, VARIANT_TYPES, "variant_type")
            return mapped
    return None


def extract_hgvs_p(hgvs_descriptions: list[str] | None) -> str | None:
    """Pull the protein-level HGVS string (`...:p.Xxx###Yyy`) if present."""
    for description in hgvs_descriptions or []:
        if ":p." in description:
            return description
    return None


def extract_hgvs_c(hgvs_descriptions: list[str] | None) -> str | None:
    """Pull a transcript-level HGVS string, preferring a RefSeq NM_ accession."""
    candidates = [d for d in (hgvs_descriptions or []) if ":c." in d]
    for candidate in candidates:
        if candidate.startswith("NM_"):
            return candidate
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Load summary -- structured, not log-only
# ---------------------------------------------------------------------------
#
# RareCure rule #4: its silent fallback (260/261 patients quietly given a
# generic gene panel) was detectable only by grepping warning strings across
# two separate analysis scripts. Every drop here is a counted field on a
# returned object, so a caller can assert on it.
SKIP_REASONS = (
    "bad_direction",
    "missing_citation",
    "out_of_scope_disease",
    "multi_variant_profile",
    "missing_gene_or_variant",
    "no_therapy",
    "bad_evidence_level",
)


@dataclass
class LoadSummary:
    config_version: str
    retrieved_at: str
    diseases_in_scope: int = 0
    evidence_items_fetched: int = 0
    nodes_written: dict[str, int] = field(default_factory=dict)
    edges_written: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SKIP_REASONS, 0))
    unmapped_variant_type: int = 0
    disease_errors: dict[str, str] = field(default_factory=dict)

    def count_node(self, label: str) -> None:
        self.nodes_written[label] = self.nodes_written.get(label, 0) + 1

    def count_edge(self, rel_type: str) -> None:
        self.edges_written[rel_type] = self.edges_written.get(rel_type, 0) + 1

    def skip(self, reason: str) -> None:
        if reason not in self.skipped:
            raise ValueError(f"unknown skip reason {reason!r}; add it to SKIP_REASONS")
        self.skipped[reason] += 1

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())

    def to_dict(self) -> dict:
        return {
            "config_version": self.config_version,
            "retrieved_at": self.retrieved_at,
            "diseases_in_scope": self.diseases_in_scope,
            "evidence_items_fetched": self.evidence_items_fetched,
            "nodes_written": dict(sorted(self.nodes_written.items())),
            "edges_written": dict(sorted(self.edges_written.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "total_skipped": self.total_skipped,
            "unmapped_variant_type": self.unmapped_variant_type,
            "disease_errors": self.disease_errors,
        }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_scope_config(path: Path = CONFIG_PATH) -> dict:
    """Read and validate the versioned scope config."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CivicApiError(f"scope config not found at {path}") from exc
    except yaml.YAMLError as exc:
        raise CivicApiError(f"scope config at {path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise CivicApiError(f"scope config at {path} did not parse to a mapping")
    if not raw.get("config_version"):
        raise CivicApiError("scope config is missing required key 'config_version'")

    diseases = raw.get("diseases")
    if not isinstance(diseases, list) or not diseases:
        raise CivicApiError("scope config must list at least one disease")
    for entry in diseases:
        if not isinstance(entry, dict) or not entry.get("doid"):
            raise CivicApiError(f"scope config disease entry missing 'doid': {entry!r}")

    return raw


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

_EVIDENCE_QUERY = """
query($diseaseId: Int!, $first: Int!, $after: String) {
  evidenceItems(diseaseId: $diseaseId, evidenceType: PREDICTIVE,
                status: ACCEPTED, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name link evidenceLevel evidenceType evidenceDirection significance status
      description
      disease { id name doid }
      therapies { id name ncitId }
      source { id citationId sourceType sourceUrl title publicationYear }
      molecularProfile { id name variants { id name __typename
        ... on GeneVariant { feature { name } hgvsDescriptions
          variantTypes { name soid } }
        ... on FusionVariant { feature { name } viccCompliantName
          variantTypes { name soid } } } }
    }
  }
}
"""

_DISEASE_BY_DOID_QUERY = """
query($doid: String!) {
  browseDiseases(doid: $doid, first: 5) { nodes { id name doid } }
}
"""


async def _post(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    """POST one GraphQL query, validating shape (not just status code)."""
    try:
        response = await client.post(
            CIVIC_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json"},
        )
    except httpx.RequestError as exc:
        raise CivicApiError(f"CIViC request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise CivicApiError(f"CIViC returned HTTP {response.status_code}")
    if "application/json" not in response.headers.get("content-type", ""):
        raise CivicApiError(
            f"CIViC returned content-type {response.headers.get('content-type')!r}, "
            "expected application/json"
        )
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise CivicApiError(f"CIViC returned undecodable JSON: {exc}") from exc
    if "errors" in body:
        raise CivicApiError(f"CIViC GraphQL errors: {json.dumps(body['errors'])[:300]}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise CivicApiError("CIViC response has no 'data' object")
    return data


async def resolve_disease_id(client: httpx.AsyncClient, doid: str) -> int | None:
    """Resolve a DOID to CIViC's internal disease id, or None if absent."""
    data = await _post(client, _DISEASE_BY_DOID_QUERY, {"doid": doid})
    nodes = ((data.get("browseDiseases") or {}).get("nodes")) or []
    for node in nodes:
        if str(node.get("doid")) == str(doid):
            return int(node["id"])
    return None


async def fetch_evidence_for_disease(
    client: httpx.AsyncClient, disease_id: int, semaphore: asyncio.Semaphore
) -> list[dict]:
    """Fetch all PREDICTIVE/ACCEPTED evidence items for one CIViC disease id."""
    items: list[dict] = []
    cursor: str | None = None
    while True:
        async with semaphore:
            data = await _post(
                client,
                _EVIDENCE_QUERY,
                {"diseaseId": disease_id, "first": PAGE_SIZE, "after": cursor},
            )
        evidence = data.get("evidenceItems") or {}
        items.extend(evidence.get("nodes") or [])
        page_info = evidence.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return items
        cursor = page_info.get("endCursor")
        if not cursor:
            return items


# ---------------------------------------------------------------------------
# Graph writes -- all MERGE, all provenance-wrapped
# ---------------------------------------------------------------------------


def _merge_node(graph, label: str, key_prop: str, key_value, properties: dict) -> None:
    """MERGE a node on its natural key, then SET the remaining properties.

    MERGE (never CREATE) is what makes re-running the loader idempotent.
    """
    updatable = {k: v for k, v in properties.items() if k != key_prop}
    query = f"MERGE (n:{label} {{{key_prop}: $key}}) " f"SET n += $props " f"RETURN n.{key_prop}"
    graph.query(query, params={"key": key_value, "props": updatable})


def _merge_edge(
    graph,
    from_label: str,
    from_key: str,
    from_value,
    rel_type: str,
    to_label: str,
    to_key: str,
    to_value,
    rel_props: dict | None = None,
) -> None:
    """MERGE a relationship, including any properties in the MERGE key itself.

    Passing rel_props means the MERGE pattern is
    `-[r:REL {prop: $v, ...}]->`, so two edges that differ only by a property
    stay distinct. For PREDICTS_RESPONSE_TO this is what keys the edge on
    (variant, drug, direction) instead of on the drug alone -- RareCure rule
    #3, whose name-only dedup produced `"Pazopanib" | gene: "TP53"`.
    """
    rel_props = rel_props or {}
    if rel_props:
        rel_pattern = ", ".join(f"{k}: $rel_{k}" for k in sorted(rel_props))
        rel_clause = f"[r:{rel_type} {{{rel_pattern}}}]"
    else:
        rel_clause = f"[r:{rel_type}]"

    params = {"from_value": from_value, "to_value": to_value}
    params.update({f"rel_{k}": v for k, v in rel_props.items()})

    query = (
        f"MATCH (a:{from_label} {{{from_key}: $from_value}}) "
        f"MATCH (b:{to_label} {{{to_key}: $to_value}}) "
        f"MERGE (a)-{rel_clause}->(b) "
        f"RETURN type(r)"
    )
    graph.query(query, params=params)


def citation_url_for(item: dict) -> str | None:
    """Resolvable citation URL, or None if the item has no usable citation.

    Prefers the underlying publication URL; falls back to the CIViC evidence
    record. Returning None means the caller must drop the item -- the hard
    rule from api-contracts.md is that an item with no citation.url is never
    constructed at all.
    """
    source = item.get("source") or {}
    source_url = (source.get("sourceUrl") or "").strip()
    if source_url:
        return source_url
    citation_id = (source.get("citationId") or "").strip()
    if citation_id and (source.get("sourceType") or "").upper() == "PUBMED":
        return f"https://pubmed.ncbi.nlm.nih.gov/{citation_id}/"
    link = (item.get("link") or "").strip()
    if link:
        return f"https://civicdb.org{link}"
    return None


def load_evidence_item(graph, item: dict, summary: LoadSummary, in_scope_doids: set[str]) -> bool:
    """Write one CIViC evidence item into the graph. Returns True if written.

    Every early return increments a counted skip reason on `summary`.
    """
    profile = item.get("molecularProfile") or {}
    variants = profile.get("variants") or []

    # V1 models (Gene)-[:HAS_VARIANT]->(Variant); a multi-variant molecular
    # profile ("BRAF V600E AND NRAS Q61") is a compound claim that this shape
    # cannot represent without flattening it into two claims CIViC never made.
    if len(variants) != 1:
        summary.skip("multi_variant_profile")
        return False

    variant = variants[0] or {}

    # Fusions ARE loaded, as a single Gene node carrying CIViC's fusion label
    # verbatim (e.g. Gene.symbol == "TPM3::NTRK1"). Fusions are the defining
    # actionable alteration in several in-scope sarcomas -- NTRK fusions
    # (larotrectinib/entrectinib), SS18::SSX in synovial sarcoma,
    # COL1A1::PDGFB in DFSP -- so dropping them would lose roughly 40% of the
    # in-scope sarcoma evidence.
    #
    # Known trade-off, recorded here deliberately rather than discovered later:
    # Gene.symbol is therefore NOT always an HGNC symbol. A Phase 3
    # retrieve_exact(gene="NTRK1") will not match "TPM3::NTRK1" on an equality
    # predicate. Retrieval must handle fusion partners explicitly -- and must
    # do so without a substring/CONTAINS match on the symbol, which would
    # reintroduce exactly the free-text-matching failure mode that RareCure
    # rule #8 exists to prevent. `is_fusion` is set on the Gene node so
    # retrieval can branch on a flag rather than on string shape.
    is_fusion = variant.get("__typename") == "FusionVariant"

    gene_symbol = ((variant.get("feature") or {}).get("name") or "").strip()
    civic_variant_id = variant.get("id")
    if not gene_symbol or civic_variant_id is None:
        summary.skip("missing_gene_or_variant")
        return False

    disease = item.get("disease") or {}
    doid = str(disease.get("doid") or "").strip()
    if doid not in in_scope_doids:
        summary.skip("out_of_scope_disease")
        return False

    citation_url = citation_url_for(item)
    if not citation_url:
        summary.skip("missing_citation")
        return False

    evidence_level = (item.get("evidenceLevel") or "").strip().upper()
    if evidence_level not in EVIDENCE_LEVELS:
        summary.skip("bad_evidence_level")
        return False

    direction = map_direction(item.get("significance"), item.get("evidenceDirection"))
    if direction is None:
        summary.skip("bad_direction")
        return False

    therapies = [t for t in (item.get("therapies") or []) if (t or {}).get("name")]
    if not therapies:
        summary.skip("no_therapy")
        return False

    retrieved_at = summary.retrieved_at
    version = summary.config_version

    def provenance(props: dict) -> dict:
        return with_provenance(
            props, source=SOURCE_NAME, retrieved_at=retrieved_at, source_version=version
        )

    # --- Gene ---
    _merge_node(
        graph,
        "Gene",
        "symbol",
        gene_symbol,
        provenance({"symbol": gene_symbol, "is_fusion": is_fusion}),
    )
    summary.count_node("Gene")

    # --- Variant ---
    hgvs_list = variant.get("hgvsDescriptions") or []
    variant_type = map_variant_type(variant.get("variantTypes"))
    if variant_type is None:
        summary.unmapped_variant_type += 1
    _merge_node(
        graph,
        "Variant",
        "civic_variant_id",
        civic_variant_id,
        provenance(
            {
                "civic_variant_id": civic_variant_id,
                "name": variant.get("name"),
                "hgvs_p": extract_hgvs_p(hgvs_list),
                "hgvs_c": extract_hgvs_c(hgvs_list),
                "variant_type": variant_type,
            }
        ),
    )
    summary.count_node("Variant")
    _merge_edge(
        graph,
        "Gene",
        "symbol",
        gene_symbol,
        HAS_VARIANT,
        "Variant",
        "civic_variant_id",
        civic_variant_id,
    )
    summary.count_edge(HAS_VARIANT)

    # --- Disease ---
    _merge_node(
        graph,
        "Disease",
        "doid",
        doid,
        provenance({"doid": doid, "name": disease.get("name"), "is_in_scope_cancer_type": True}),
    )
    summary.count_node("Disease")
    _merge_edge(
        graph,
        "Variant",
        "civic_variant_id",
        civic_variant_id,
        OBSERVED_IN,
        "Disease",
        "doid",
        doid,
    )
    summary.count_edge(OBSERVED_IN)

    # --- EvidenceItem ---
    civic_id = item["id"]
    _merge_node(
        graph,
        "EvidenceItem",
        "civic_id",
        civic_id,
        provenance(
            {
                "civic_id": civic_id,
                "evidence_level": evidence_level,
                "evidence_type": item.get("evidenceType"),
                "clinical_significance": item.get("significance"),
                "direction": direction,
                "summary": item.get("description"),
                "citation_url": citation_url,
            }
        ),
    )
    summary.count_node("EvidenceItem")
    _merge_edge(
        graph,
        "EvidenceItem",
        "civic_id",
        civic_id,
        SUPPORTS,
        "Variant",
        "civic_variant_id",
        civic_variant_id,
    )
    summary.count_edge(SUPPORTS)

    # --- Publication (only when the source is a real PubMed record) ---
    source = item.get("source") or {}
    if (source.get("sourceType") or "").upper() == "PUBMED" and source.get("citationId"):
        pmid = str(source["citationId"])
        _merge_node(
            graph,
            "Publication",
            "pmid",
            pmid,
            provenance(
                {
                    "pmid": pmid,
                    "title": source.get("title"),
                    "year": source.get("publicationYear"),
                }
            ),
        )
        summary.count_node("Publication")
        _merge_edge(graph, "EvidenceItem", "civic_id", civic_id, CITES, "Publication", "pmid", pmid)
        summary.count_edge(CITES)

    # --- Drugs + the direction-bearing response edge ---
    for therapy in therapies:
        drug_name = therapy["name"].strip()
        _merge_node(graph, "Drug", "name", drug_name, provenance({"name": drug_name}))
        summary.count_node("Drug")
        _merge_edge(
            graph,
            "Variant",
            "civic_variant_id",
            civic_variant_id,
            PREDICTS_RESPONSE_TO,
            "Drug",
            "name",
            drug_name,
            # predicts_response_to_properties() re-validates direction; there
            # is no path that writes this edge without a checked direction.
            rel_props=predicts_response_to_properties(direction),
        )
        summary.count_edge(PREDICTS_RESPONSE_TO)

    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _fetch_all(config: dict, summary: LoadSummary) -> list[dict]:
    """Fetch evidence for every in-scope disease, concurrently.

    The semaphore is created HERE, inside the coroutine that uses it -- never
    at module scope. RareCure rule #7: its module-level asyncio.Semaphore
    bound itself to whichever event loop first awaited it, so every
    asyncio.run() after the first silently lost most of its concurrency, and
    the failures were invisible because they were caught and merely logged.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    all_items: list[dict] = []

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": "SecondLook-Tier1/1.0"},
    ) as client:
        for entry in config["diseases"]:
            doid = str(entry["doid"])
            try:
                disease_id = await resolve_disease_id(client, doid)
                if disease_id is None:
                    summary.disease_errors[doid] = "not found in CIViC"
                    continue
                items = await fetch_evidence_for_disease(client, disease_id, semaphore)
                all_items.extend(items)
            except CivicApiError as exc:
                # One disease failing must not abort the whole load
                # (tier1-retrieval.md, Failure handling), but it must be
                # visible in the summary rather than swallowed.
                logger.warning("CIViC fetch failed for DOID %s: %s", doid, exc)
                summary.disease_errors[doid] = str(exc)

    return all_items


def run_load(graph, config: dict | None = None, *, dry_run: bool = False) -> LoadSummary:
    """Load all in-scope CIViC evidence into `graph`. Returns a LoadSummary."""
    config = config or load_scope_config()
    summary = LoadSummary(
        config_version=str(config["config_version"]),
        retrieved_at=datetime.now(UTC).isoformat(),
    )
    summary.diseases_in_scope = len(config["diseases"])
    in_scope_doids = {str(d["doid"]) for d in config["diseases"]}

    items = asyncio.run(_fetch_all(config, summary))
    summary.evidence_items_fetched = len(items)
    logger.info("fetched %d CIViC evidence items", len(items))

    if dry_run:
        logger.info("dry run: skipping all graph writes")
        return summary

    seen_ids: set[Any] = set()
    for item in items:
        # The same evidence item can be returned under more than one
        # in-scope disease query; write it once.
        if item.get("id") in seen_ids:
            continue
        seen_ids.add(item.get("id"))
        load_evidence_item(graph, item, summary, in_scope_doids)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load CIViC evidence into FalkorDB")
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch and report, but write nothing"
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    try:
        config = load_scope_config(args.config)
    except CivicApiError as exc:
        print(f"[FAIL] {exc}")
        return 1

    graph = None if args.dry_run else connect_graph()

    try:
        summary = run_load(graph, config, dry_run=args.dry_run)
    except CivicApiError as exc:
        print(f"[FAIL] CIViC load failed: {exc}")
        return 1

    print(json.dumps(summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
