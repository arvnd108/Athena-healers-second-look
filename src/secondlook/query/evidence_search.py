"""Structured evidence search for MCP / future REST.

Chains `retrieve_exact` then `retrieve_relaxed` from `tier1/retrieval.py`.
Does **not** fall through to `semantic_retrieval.py`: that module pulls
torch (~2 GB) via the optional `semantic` extra, and a gene/variant lookup
from a chat client must stay on the structured graph path. Empty-with-reason
is the honest answer when structured retrieval finds nothing.
"""

from __future__ import annotations

from secondlook.query.contracts import EvidenceItem
from secondlook.query.result import QueryResult
from secondlook.tier1.retrieval import retrieve_exact, retrieve_relaxed


def _variant_keys(variant: str) -> tuple[str | None, str | None]:
    if variant.startswith("p.") or ":p." in variant:
        return variant, None
    return None, variant


def search_evidence(
    gene: str,
    variant: str | None = None,
    cancer_type: str | None = None,
    *,
    graph=None,
) -> QueryResult[EvidenceItem]:
    if not gene:
        return QueryResult(empty_reason="gene is required for structured evidence search")
    if not variant:
        return QueryResult(
            empty_reason=(
                "variant is required for structured evidence search "
                "(retrieve_exact needs exactly one of hgvs_p or variant_name)"
            )
        )

    hgvs_p, variant_name = _variant_keys(variant)
    exact = retrieve_exact(gene, hgvs_p, variant_name=variant_name, graph=graph)
    if exact.items:
        items = tuple(
            EvidenceItem(
                gene=gene,
                variant=variant,
                cancer_type=cancer_type,
                retrieval_mode="exact",
                item=row,
            )
            for row in exact.items
        )
        return QueryResult(items=items)

    relaxed = retrieve_relaxed(
        gene, hgvs_p, disease=cancer_type, variant_name=variant_name, graph=graph
    )
    if relaxed.items:
        items = tuple(
            EvidenceItem(
                gene=gene,
                variant=variant,
                cancer_type=cancer_type,
                retrieval_mode="relaxed",
                item=row,
            )
            for row in relaxed.items
        )
        return QueryResult(items=items)

    return QueryResult(empty_reason=f"no evidence items for gene={gene} variant={variant}")
