"""Live shape verification for the CIViC GraphQL API.

RareCure rule #1 (docs/rarecure-build-reference.md SS2): RareCure's diagnostic
checked `status_code == 200` only, and reported `[ OK ]` for a DGIdb endpoint
that was returning `200 text/html` and contributing zero results. A status
code is not a shape check.

This module makes real calls against CIViC and asserts on the *parsed*
response structure -- the exact fields and enum values civic_loader.py will
depend on -- so a silent upstream schema change fails loudly here rather
than producing an empty or wrong graph load.

Run standalone:
    python -m secondlook.tier1.civic_verify
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

CIVIC_GRAPHQL_URL = "https://civicdb.org/api/graphql"
REQUEST_TIMEOUT_SECONDS = 60.0


class CivicApiError(RuntimeError):
    """Raised when CIViC is unreachable, returns a non-JSON body, reports
    GraphQL errors, or returns a payload whose shape does not match what
    this codebase parses. Caught at the call site -- never a bare except."""


def civic_post(query: str, variables: dict | None = None, *, client: httpx.Client) -> dict:
    """POST a GraphQL query to CIViC and return `data`, or raise CivicApiError.

    Validates, in order: transport, HTTP status, content-type, JSON
    decodability, absence of GraphQL `errors`, and presence of `data`.
    Every one of those is a distinct way a "200 OK" can still be unusable.
    """
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        response = client.post(
            CIVIC_GRAPHQL_URL, json=payload, headers={"Content-Type": "application/json"}
        )
    except httpx.RequestError as exc:
        raise CivicApiError(f"CIViC request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise CivicApiError(f"CIViC returned HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        # This is the exact RareCure DGIdb failure: 200 + text/html.
        raise CivicApiError(
            f"CIViC returned content-type {content_type!r}, expected application/json "
            f"(body starts: {response.text[:120]!r})"
        )

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise CivicApiError(f"CIViC returned undecodable JSON: {exc}") from exc

    if "errors" in body:
        raise CivicApiError(f"CIViC GraphQL errors: {json.dumps(body['errors'])[:400]}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise CivicApiError(f"CIViC response has no 'data' object (keys: {sorted(body)})")

    return data


# --- The exact enum values civic_loader.py's mapping logic depends on -------
#
# Verified live 2026-08-21. If CIViC adds/renames a value here, the loader's
# direction mapping must be re-reviewed before it is trusted again -- which
# is precisely why these are asserted rather than assumed.
EXPECTED_EVIDENCE_DIRECTIONS = {"SUPPORTS", "DOES_NOT_SUPPORT", "NA"}
EXPECTED_EVIDENCE_LEVELS = {"A", "B", "C", "D", "E"}
REQUIRED_SIGNIFICANCE_VALUES = {"SENSITIVITYRESPONSE", "RESISTANCE"}

_ENUM_QUERY = '{ __type(name: "%s") { enumValues { name } } }'

_EVIDENCE_PROBE_QUERY = """
query($first: Int!) {
  evidenceItems(evidenceType: PREDICTIVE, status: ACCEPTED, first: $first) {
    totalCount
    nodes {
      id name link evidenceLevel evidenceType evidenceDirection significance status
      disease { id name doid }
      therapies { id name ncitId }
      source { id citationId sourceType sourceUrl title publicationYear }
      molecularProfile { id name variants { id name
        ... on GeneVariant { feature { name } hgvsDescriptions
          variantTypes { name soid } } } }
    }
  }
}
"""


def _enum_values(type_name: str, *, client: httpx.Client) -> set[str]:
    data = civic_post(_ENUM_QUERY % type_name, client=client)
    type_info = data.get("__type")
    if not isinstance(type_info, dict) or not isinstance(type_info.get("enumValues"), list):
        raise CivicApiError(f"Could not introspect enum {type_name!r}: got {type_info!r}")
    return {value["name"] for value in type_info["enumValues"]}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CivicApiError(message)


def verify_civic_shape(*, client: httpx.Client | None = None) -> dict:
    """Assert CIViC's live response shape matches what the loader parses.

    Returns a dict summarising what was found. Raises CivicApiError on any
    mismatch -- a caller that gets a return value can trust the shape.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        directions = _enum_values("EvidenceDirection", client=client)
        levels = _enum_values("EvidenceLevel", client=client)
        significances = _enum_values("EvidenceSignificance", client=client)

        _require(
            directions == EXPECTED_EVIDENCE_DIRECTIONS,
            f"EvidenceDirection changed: expected {sorted(EXPECTED_EVIDENCE_DIRECTIONS)}, "
            f"got {sorted(directions)}",
        )
        _require(
            levels == EXPECTED_EVIDENCE_LEVELS,
            f"EvidenceLevel changed: expected {sorted(EXPECTED_EVIDENCE_LEVELS)}, "
            f"got {sorted(levels)}",
        )
        missing_significance = REQUIRED_SIGNIFICANCE_VALUES - significances
        _require(
            not missing_significance,
            f"EvidenceSignificance is missing required values {sorted(missing_significance)}; "
            "the sensitive/resistant mapping in civic_loader.py depends on them",
        )

        data = civic_post(_EVIDENCE_PROBE_QUERY, {"first": 5}, client=client)
        evidence = data.get("evidenceItems")
        _require(isinstance(evidence, dict), "evidenceItems is not an object")
        nodes = evidence.get("nodes")
        _require(
            isinstance(nodes, list) and bool(nodes),
            "evidenceItems.nodes is empty -- cannot verify record shape",
        )

        sample = nodes[0]
        for field in (
            "id",
            "evidenceLevel",
            "evidenceType",
            "evidenceDirection",
            "significance",
            "status",
            "molecularProfile",
        ):
            _require(field in sample, f"evidence item is missing field {field!r}")

        profile = sample["molecularProfile"]
        _require(
            isinstance(profile, dict) and isinstance(profile.get("variants"), list),
            "molecularProfile.variants is not a list",
        )
        source = sample.get("source")
        _require(
            isinstance(source, dict) and "sourceUrl" in source and "citationId" in source,
            "evidence source is missing sourceUrl/citationId -- citation enforcement "
            "depends on these",
        )

        return {
            "evidence_directions": sorted(directions),
            "evidence_levels": sorted(levels),
            "significance_count": len(significances),
            "predictive_accepted_total": evidence.get("totalCount"),
            "sample_evidence_id": sample["id"],
            "sample_significance": sample["significance"],
            "sample_direction": sample["evidenceDirection"],
        }
    finally:
        if owns_client:
            client.close()


def main() -> int:
    print("=== CIViC live shape verification ===")
    print(f"endpoint: {CIVIC_GRAPHQL_URL}")
    try:
        found = verify_civic_shape()
    except CivicApiError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print("[ OK ] shape matches what civic_loader.py parses")
    for key, value in found.items():
        print(f"       {key}: {value}")
    print(
        "\nNote: `evidenceDirection` is SUPPORTS/DOES_NOT_SUPPORT/NA -- it is NOT\n"
        "sensitive/resistant. The graph's PREDICTS_RESPONSE_TO.direction comes from\n"
        "`significance` (SENSITIVITYRESPONSE/RESISTANCE), gated on direction=SUPPORTS.\n"
        "See civic_loader.map_direction()."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
