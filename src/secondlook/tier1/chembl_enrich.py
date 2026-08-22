"""Enrich Drug nodes with a `drug_class` from ChEMBL's mechanism-of-action
data, for retrieval.py Mode 2 path (b) (same disease + same drug class).

Deliberately a separate pass from civic_loader.py, not inline during the
CIViC load: ChEMBL is a different source with its own failure domain, and
a ChEMBL outage should never block or slow down the CIViC evidence load.
Idempotent and incremental -- only Drug nodes with no drug_class are
resolved, so re-running after adding new drugs or after a prior partial
failure is cheap and correct; a node already enriched is left alone.

Run:
    python -m secondlook.tier1.chembl_enrich
    python -m secondlook.tier1.chembl_enrich --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import with_enrichment_provenance

logger = logging.getLogger(__name__)

CHEMBL_MOLECULE_URL = "https://www.ebi.ac.uk/chembl/api/data/molecule"
CHEMBL_MECHANISM_URL = "https://www.ebi.ac.uk/chembl/api/data/mechanism"
CHEMBL_STATUS_URL = "https://www.ebi.ac.uk/chembl/api/data/status"
REQUEST_TIMEOUT_SECONDS = 30.0
SOURCE_NAME = "ChEMBL"


class ChemblApiError(RuntimeError):
    """Raised on transport failure, non-200, wrong content-type, or a
    payload shape ChEMBL's own docs don't promise. Caught at the call site
    -- never a bare except."""


@dataclass
class EnrichmentSummary:
    drugs_considered: int = 0
    already_enriched: int = 0
    resolved: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # Distinguishes "ChEMBL's /status endpoint errored" from "the version
    # just wasn't available" -- both leave every drug_class_source_version
    # written this run as None, so without this flag a caller has no way
    # to tell those two very different situations apart from the summary
    # alone (RareCure rule #4: every skip/filter is a structured, visible
    # field, never only a log line).
    db_version_fetch_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "drugs_considered": self.drugs_considered,
            "already_enriched": self.already_enriched,
            "resolved_count": len(self.resolved),
            "resolved": self.resolved,
            "unresolved_count": len(self.unresolved),
            "unresolved": self.unresolved,
            "error_count": len(self.errors),
            "errors": self.errors,
            "db_version_fetch_failed": self.db_version_fetch_failed,
        }


def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    """Shape-checked GET -- a 200 with the wrong content-type is not
    success (RareCure rule #1)."""
    try:
        response = client.get(url, params=params)
    except httpx.RequestError as exc:
        raise ChemblApiError(f"ChEMBL request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise ChemblApiError(f"ChEMBL returned HTTP {response.status_code} for {url}")
    if "application/json" not in response.headers.get("content-type", ""):
        raise ChemblApiError(
            f"ChEMBL returned content-type {response.headers.get('content-type')!r}, "
            f"expected application/json (body starts: {response.text[:120]!r})"
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise ChemblApiError(f"ChEMBL returned undecodable JSON: {exc}") from exc


def resolve_drug_class(drug_name: str, *, client: httpx.Client) -> str | None:
    """Resolve a drug name to its ChEMBL mechanism_of_action, or None.

    Uses EXACT preferred-name matching (`pref_name__iexact`), not the fuzzy
    `/molecule/search` endpoint -- confirmed live these return different
    ChEMBL IDs for the same query (Larotrectinib: fuzzy search resolves to
    a salt-form entry, CHEMBL4856292, with zero curated mechanism records;
    exact pref_name resolves to the parent entry, CHEMBL3889654, which has
    the real "Neurotrophic tyrosine kinase receptor inhibitor" record).
    Fuzzy search would silently produce a much lower hit rate.

    None is a legitimate, expected result -- many CIViC therapy names are
    generic descriptors ("BET Inhibitor", "IGF1R Monoclonal Antibody") that
    were never registered in ChEMBL under that name at all.
    """
    body = _get_json(
        client, CHEMBL_MOLECULE_URL, {"pref_name__iexact": drug_name, "format": "json"}
    )
    molecules = body.get("molecules")
    if not isinstance(molecules, list):
        raise ChemblApiError(f"molecule search response missing 'molecules' list: {body!r}")
    if not molecules:
        return None

    chembl_id = molecules[0].get("molecule_chembl_id")
    if not chembl_id:
        raise ChemblApiError(f"molecule record missing molecule_chembl_id: {molecules[0]!r}")

    mech_body = _get_json(
        client, CHEMBL_MECHANISM_URL, {"molecule_chembl_id": chembl_id, "format": "json"}
    )
    mechanisms = mech_body.get("mechanisms")
    if not isinstance(mechanisms, list):
        raise ChemblApiError(f"mechanism response missing 'mechanisms' list: {mech_body!r}")
    if not mechanisms:
        return None

    return mechanisms[0].get("mechanism_of_action") or None


def chembl_db_version(*, client: httpx.Client) -> str | None:
    """The ChEMBL release this run's data came from (e.g. "ChEMBL_37"), from
    the /status endpoint -- confirmed live this exists and is a real,
    meaningful version string, not a guess. One call per enrichment run
    (a database-wide release, not per-molecule), used as source_version on
    every drug_class written in that run.
    """
    body = _get_json(client, CHEMBL_STATUS_URL, {"format": "json"})
    return body.get("chembl_db_version")


def enrich_drug_classes(
    graph, *, client: httpx.Client | None = None, dry_run: bool = False
) -> EnrichmentSummary:
    """Resolve and write drug_class for every Drug node that doesn't have
    one yet. Returns a structured summary -- every unresolved/errored drug
    is a counted, named entry, never only a log line (RareCure rule #4)."""
    owns_client = client is None
    client = client or httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": "SecondLook-Tier1/1.0"}
    )
    summary = EnrichmentSummary()

    try:
        try:
            db_version = chembl_db_version(client=client)
        except ChemblApiError as exc:
            logger.warning("Could not resolve ChEMBL db version, proceeding without it: %s", exc)
            db_version = None
            summary.db_version_fetch_failed = True

        rows = graph.query("MATCH (d:Drug) WHERE d.drug_class IS NULL RETURN d.name").result_set
        already = graph.query(
            "MATCH (d:Drug) WHERE d.drug_class IS NOT NULL RETURN count(d)"
        ).result_set[0][0]
        summary.already_enriched = already
        drug_names = [row[0] for row in rows]
        summary.drugs_considered = len(drug_names)

        for name in drug_names:
            try:
                drug_class = resolve_drug_class(name, client=client)
            except ChemblApiError as exc:
                logger.warning("ChEMBL resolution failed for %r: %s", name, exc)
                summary.errors[name] = str(exc)
                continue

            if drug_class is None:
                summary.unresolved.append(name)
                continue

            summary.resolved[name] = drug_class
            if not dry_run:
                # with_enrichment_provenance(), not with_provenance(): this
                # node was created by civic_loader.py (source="CIViC"). A
                # plain with_provenance() here would blanket-overwrite that
                # node-level source/retrieved_at with "ChEMBL" via `SET d +=
                # $props` -- confirmed live, this is exactly what the
                # previous version of this function did. Per-property
                # (drug_class_source / drug_class_retrieved_at /
                # drug_class_source_version) keeps both sources' provenance
                # on the node instead of one collapsing the other.
                graph.query(
                    "MATCH (d:Drug {name: $name}) SET d += $props",
                    params={
                        "name": name,
                        "props": with_enrichment_provenance(
                            {"drug_class": drug_class},
                            source=SOURCE_NAME,
                            retrieved_at=datetime.now(UTC).isoformat(),
                            source_version=db_version,
                        ),
                    },
                )
    finally:
        if owns_client:
            client.close()

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich Drug nodes with ChEMBL drug_class")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    graph = connect_graph()
    summary = enrich_drug_classes(graph, dry_run=args.dry_run)
    print(json.dumps(summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
