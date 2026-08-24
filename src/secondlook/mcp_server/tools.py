"""Thin MCP tool functions. No business logic -- all work lives in `query/`."""

from __future__ import annotations

from datetime import datetime

from secondlook.mcp_server.view import ReadOnlyCaseView
from secondlook.query.case_summary import get_case_summary
from secondlook.query.evidence_search import search_evidence
from secondlook.query.recent_changes import get_recent_changes
from secondlook.query.result import QueryResult
from secondlook.query.trial_access import match_trials_for_case
from secondlook.tier1.access_pathway_query import query_access_pathways


def _dump(result: QueryResult) -> dict:
    return result.to_dict()


def get_case_summary_tool(view: ReadOnlyCaseView, case_id: str) -> dict:
    return _dump(get_case_summary(view, case_id))


def get_recent_changes_tool(
    view: ReadOnlyCaseView,
    case_id: str,
    since: datetime | None = None,
) -> dict:
    return _dump(get_recent_changes(view, case_id, since=since))


def search_evidence_tool(
    view: ReadOnlyCaseView,
    gene: str,
    variant: str | None = None,
    cancer_type: str | None = None,
    *,
    graph=None,
) -> dict:
    del view  # evidence search is graph-backed; the view is required by the type boundary
    return _dump(search_evidence(gene, variant, cancer_type, graph=graph))


def match_trials_tool(view: ReadOnlyCaseView, case_id: str, *, graph=None, extractor=None) -> dict:
    return _dump(match_trials_for_case(view, case_id, graph=graph, extractor=extractor))


def get_access_pathways_tool(
    view: ReadOnlyCaseView,
    drug: str,
    country: str,
    *,
    graph=None,
) -> dict:
    del view
    return _dump(query_access_pathways(drug, country, graph=graph))
