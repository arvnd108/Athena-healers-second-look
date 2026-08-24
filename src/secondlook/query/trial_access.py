"""Compose candidate lookup + criteria extraction + trial matching for a case."""

from __future__ import annotations

from secondlook.query.contracts import TrialMatchResult
from secondlook.query.fold import fold_store_events
from secondlook.query.result import QueryResult
from secondlook.signals.trial_matching import match_trials
from secondlook.signals.trials import lookup_candidate_trials
from secondlook.tier1.criteria_extraction import RuleBasedExtractor


def match_trials_for_case(
    store,
    case_id,
    *,
    graph=None,
    extractor=None,
    match_fn=match_trials,
) -> QueryResult[TrialMatchResult]:
    """Three-step pipeline: candidates, extract-or-fetch, match_trials.

    `extractor` and `match_fn` are injectable so tests never need a live graph
    or a cached extraction corpus. Default extractor is `RuleBasedExtractor`
    (offline, no API key).
    """
    case = store.get_case(case_id)
    if case is None:
        return QueryResult(empty_reason=f"no case with id {case_id}")

    events = store.list_events(case_id)
    state = fold_store_events(case_id, events)
    genes = sorted({a.gene for a in state.alterations})
    if not genes:
        return QueryResult(
            empty_reason="no gene in case state to match Trial-[:TARGETS_BIOMARKER]->Gene"
        )

    extractor = extractor or RuleBasedExtractor()
    if graph is None:
        from secondlook.tier1.graph_connection import connect_graph

        graph = connect_graph()
    candidates_by_id: dict[str, tuple] = {}
    for gene in genes:
        rows = lookup_candidate_trials(gene, disease=case.cancer_type, graph=graph)
        for row in rows:
            registry_id = row[0]
            if registry_id:
                candidates_by_id.setdefault(str(registry_id), row)

    if not candidates_by_id:
        return QueryResult(
            empty_reason=("no Trial rows for this case's genes after actionable-status filter")
        )

    extractions: dict[str, list] = {}
    for registry_id, row in candidates_by_id.items():
        criteria_text = row[6] if len(row) > 6 else None
        if not criteria_text:
            extractions[registry_id] = []
            continue
        extracted = extractor.extract(registry_id, criteria_text)
        extractions[registry_id] = list(extracted.predicates)

    matches = match_fn(extractions, state)
    items = []
    for match in matches:
        row = candidates_by_id.get(match.trial_id)
        items.append(
            TrialMatchResult(
                trial_id=match.trial_id,
                bucket=match.bucket,
                brief_title=row[4] if row else None,
                status=row[2] if row else None,
                phase=row[3] if row else None,
                eligibility_url=row[5] if row else None,
                matched_criteria=list(match.matched_criteria),
                unresolved_criteria=list(match.unresolved_criteria),
                violated_criteria=list(match.violated_criteria),
            )
        )
    if not items:
        return QueryResult(empty_reason="trial matching returned no results")
    return QueryResult(items=tuple(items))
