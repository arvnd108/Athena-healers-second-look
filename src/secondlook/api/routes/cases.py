"""Case routes. Thin: parse, call query/store, map to HTTP."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from secondlook.api.auth import require_api_key
from secondlook.api.brief import render_brief
from secondlook.api.deps import (
    get_dispatch,
    get_existing_case,
    get_graph,
    get_llm_client,
    get_store,
)
from secondlook.api.responses import changeset_for_response, first_item
from secondlook.api.schemas import (
    AppendEventRequest,
    CreateCaseRequest,
    ResearchFindingView,
    ResearchResponse,
)
from secondlook.case.store import CaseStore
from secondlook.query.case_summary import get_case_summary
from secondlook.query.contracts import CaseSummary, CaseView, ChangeSetForApi, QueueView
from secondlook.query.finding_detail import get_finding_detail
from secondlook.query.queue import get_queue
from secondlook.query.recent_changes import get_recent_changes
from secondlook.query.research import run_research

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _case_view(case) -> CaseView:
    return CaseView(
        id=str(case.id),
        label=case.label,
        cancer_type=case.cancer_type,
        age_years=case.age_years,
        primary_site=case.primary_site,
        histology=case.histology,
        stage=case.stage,
        doid=case.doid,
        created_at=getattr(case, "created_at", None),
    )


@router.post("", response_model=CaseView)
def create_case(
    body: CreateCaseRequest,
    store: CaseStore = Depends(get_store),
    _: None = Depends(require_api_key),
) -> CaseView:
    case = store.create_case(**body.model_dump())
    return _case_view(case)


@router.get("/{case_id}", response_model=CaseSummary)
def read_case(
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
) -> CaseSummary:
    return first_item(get_case_summary(store, case.id))


@router.post("/{case_id}/events", response_model=ChangeSetForApi)
def append_event(
    body: AppendEventRequest,
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
    _: None = Depends(require_api_key),
) -> ChangeSetForApi:
    """Append one event, then reuse `get_recent_changes(since=None)`.

    The event just written is, at that instant, the one with the latest
    `recorded_at`, so the existing boundary logic returns its delta.

    Known limitation: under concurrent writers, a second request's
    `since=None` read could race and pick a different boundary than this
    writer expects. Accepted for a single-writer clinical-entry flow.
    """
    store.append_event(
        case.id,
        event_type=body.event_type,
        payload=body.payload,
        occurred_at=body.occurred_at,
        source_document=body.source_document,
        recorded_by=body.recorded_by,
    )
    return changeset_for_response(get_recent_changes(store, case.id, since=None))


@router.get("/{case_id}/changes", response_model=ChangeSetForApi)
def read_changes(
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
    since: datetime | None = Query(default=None),
) -> ChangeSetForApi:
    return changeset_for_response(get_recent_changes(store, case.id, since=since))


@router.post("/{case_id}/research", response_model=ResearchResponse)
def run_case_research(
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
    _: None = Depends(require_api_key),
    graph=Depends(get_graph),
    llm_client=Depends(get_llm_client),
    dispatch_fn=Depends(get_dispatch),
) -> ResearchResponse:
    """ChangeSet → questions → dispatch → synthesis → findings.

    Not idempotent. `case/memory.py` `suppress_duplicates` is unbuilt
    (flagged when issue #10 was scoped). Calling this twice against the
    same ChangeSet creates duplicate Question/Finding rows.
    """
    outcome = run_research(
        store,
        case.id,
        graph=graph,
        llm_client=llm_client,
        dispatch_fn=dispatch_fn,
    )
    return ResearchResponse(
        questions_created=outcome.questions_created,
        findings_created=outcome.findings_created,
        findings=[
            ResearchFindingView(id=item.id, claim=item.claim, question_id=item.question_id)
            for item in outcome.findings
        ],
    )


@router.get("/{case_id}/queue", response_model=QueueView)
def read_queue(
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
) -> QueueView:
    return first_item(get_queue(store, case.id))


@router.get("/{case_id}/brief", response_class=HTMLResponse)
def read_brief(
    case=Depends(get_existing_case),
    store: CaseStore = Depends(get_store),
) -> str:
    summary = first_item(get_case_summary(store, case.id))
    findings = []
    for domain in store.list_active_findings(case.id):
        detail = first_item(get_finding_detail(store, UUID(domain.id)))
        findings.append(detail)
    return render_brief(summary, findings)
