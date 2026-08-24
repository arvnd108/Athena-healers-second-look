from __future__ import annotations

from secondlook.tier1.access_pathway_query import query_access_pathways

CHEMBL = "CHEMBL1789941"
OSIMERTINIB = ("osimertinib", CHEMBL, None)
TAGRISSO = ("Tagrisso", CHEMBL, None)
US_PATHWAY = (
    "US:expanded_access_individual:FDA Form 3926",
    "US",
    "expanded_access_individual",
    "FDA Form 3926",
    "Individual patient expanded access",
    "https://www.fda.gov/",
    "FDA",
    "theoretical",
    "unreviewed",
)
IN_PATHWAY = (
    "IN:expanded_access:CDSCO",
    "IN",
    "expanded_access",
    "CDSCO Form",
    "India expanded access",
    "https://cdsco.gov.in/",
    "CDSCO",
    "theoretical",
    "unreviewed",
)


class RoutingGraph:
    """Dispatch by whether the Cypher mentions Drug or AccessPathway."""

    def __init__(self, drugs, pathways):
        self.drugs = drugs
        self.pathways = pathways
        self.calls = []

    def query(self, cypher, params=None):
        self.calls.append((cypher, params or {}))
        q = (params or {}).get("q", "")
        if "Drug" in cypher and "toLower" in cypher:
            rows = [row for row in self.drugs if row[0].lower() == str(q).lower() or row[1] == q]
        elif "Drug" in cypher:
            rows = list(self.drugs)
        else:
            rows = list(self.pathways)

        class _R:
            def __init__(self, result_set):
                self.result_set = result_set

        return _R(rows)


def test_alias_names_with_the_same_chembl_id_resolve_to_the_same_pathways():
    graph = RoutingGraph(drugs=[OSIMERTINIB, TAGRISSO], pathways=[US_PATHWAY])
    by_generic = query_access_pathways("osimertinib", "US", graph=graph)
    by_brand = query_access_pathways("Tagrisso", "US", graph=graph)
    assert by_generic.empty_reason is None
    assert by_brand.empty_reason is None
    assert by_generic.items[0].resolved_chembl_id == CHEMBL
    assert by_brand.items[0].resolved_chembl_id == CHEMBL
    assert by_generic.items[0].pathway_id == by_brand.items[0].pathway_id


def test_no_pathway_for_country_is_distinct_from_none_at_all():
    country_gap = query_access_pathways(
        "osimertinib",
        "JP",
        graph=RoutingGraph(drugs=[OSIMERTINIB], pathways=[US_PATHWAY, IN_PATHWAY]),
    )
    assert country_gap.items == ()
    assert "country" in country_gap.empty_reason

    none_at_all = query_access_pathways(
        "osimertinib", "US", graph=RoutingGraph(drugs=[OSIMERTINIB], pathways=[])
    )
    assert none_at_all.items == ()
    assert "at all" in none_at_all.empty_reason
    assert country_gap.empty_reason != none_at_all.empty_reason


def test_unresolved_drug_does_not_return_pathways():
    result = query_access_pathways(
        "not-a-real-drug", "US", graph=RoutingGraph(drugs=[OSIMERTINIB], pathways=[US_PATHWAY])
    )
    assert result.items == ()
    assert "resolved" in result.empty_reason
