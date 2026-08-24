"""Recompute a display-shaped ChangeSet for MCP / future GET /api/cases/{id}/changes.

`since=None` picks the boundary event by `recorded_at` (insertion time). The
fold itself still runs in `occurred_at` order. An explicit `since` is clinical
time: previous = events with `occurred_at` <= since.

`biomarker_thresholds` defaults to `{}`. No production thresholds exist anywhere
in this repo, so biomarker-shift detection is inert until a caller with clinical
authority supplies real values. That is an honest gap, not silently working.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet, compute_diff
from secondlook.query.contracts import (
    ChangeDescription,
    ChangeSetForApi,
    SupersessionDescription,
)
from secondlook.query.fold import fold_store_events
from secondlook.query.result import QueryResult


def compute_recent_diff(
    store,
    case_id,
    *,
    since: datetime | None = None,
    biomarker_thresholds: dict[str, float] | None = None,
) -> tuple[ChangeSet | None, str | None]:
    """Domain ChangeSet using the same boundary rules as `get_recent_changes`.

    Returns `(None, None)` when the case has no events. `since=None` picks the
    boundary by `recorded_at`; an explicit `since` is clinical `occurred_at`.
    """
    thresholds = {} if biomarker_thresholds is None else biomarker_thresholds
    events = list(store.list_events(case_id))
    if not events:
        return None, None

    if since is None:
        boundary = max(events, key=lambda e: e.recorded_at)
        previous_events = [e for e in events if e.id != boundary.id]
        boundary_id = str(boundary.id)
    else:
        previous_events = [e for e in events if e.occurred_at <= since]
        boundary_id = None

    previous = fold_store_events(case_id, previous_events)
    current = fold_store_events(case_id, events)
    findings = store.list_active_findings(case_id)
    change_set = compute_diff(previous, current, findings, biomarker_thresholds=thresholds)
    return change_set, boundary_id


def get_recent_changes(
    store,
    case_id,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    biomarker_thresholds: dict[str, float] | None = None,
) -> QueryResult[ChangeSetForApi]:
    """See module docstring for timestamp rules and the thresholds gap."""
    change_set, boundary_id = compute_recent_diff(
        store, case_id, since=since, biomarker_thresholds=biomarker_thresholds
    )
    if change_set is None:
        return QueryResult(empty_reason="no events recorded for this case")

    changes = [
        ChangeDescription(
            kind=c.kind.value,
            summary=c.summary,
            triggering_event_id=c.triggering_event_id,
        )
        for c in change_set.changes
    ]
    if limit is not None:
        changes = changes[:limit]

    supersessions = [
        SupersessionDescription(
            finding_id=s.finding_id,
            broken_assumption=s.broken_assumption,
            note=s.note,
            triggering_event_id=s.triggering_event_id,
        )
        for s in change_set.supersessions
    ]

    empty_reason = None
    if not changes and not supersessions:
        empty_reason = change_set.unchanged_reason or "no tracked field changed"

    payload = ChangeSetForApi(
        changes=changes,
        supersessions=supersessions,
        boundary_event_id=boundary_id,
        empty_reason=empty_reason if not changes and not supersessions else None,
    )
    return QueryResult(items=(payload,))
