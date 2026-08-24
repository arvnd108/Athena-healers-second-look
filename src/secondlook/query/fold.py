"""Shared event-to-state folding so a request path never folds twice by accident."""

from __future__ import annotations

from secondlook.case.state import CaseState, RawEvent, fold_events


def raw_events_from_store(events) -> list[RawEvent]:
    return [
        RawEvent(
            id=str(e.id),
            event_type=e.event_type,
            payload=e.payload,
            occurred_at=e.occurred_at,
        )
        for e in events
    ]


def fold_store_events(case_id, events) -> CaseState:
    return fold_events(str(case_id), raw_events_from_store(events))
