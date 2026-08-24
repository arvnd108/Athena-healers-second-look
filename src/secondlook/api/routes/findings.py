"""Finding routes. Thin: parse, call query/store, map to HTTP."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from secondlook.api.auth import require_api_key
from secondlook.api.deps import get_existing_finding, get_store
from secondlook.api.responses import first_item
from secondlook.api.schemas import RecordDecisionRequest
from secondlook.case.store import CaseStore
from secondlook.query.contracts import DecisionView, FindingDetail
from secondlook.query.finding_detail import get_finding_detail

router = APIRouter(prefix="/api/findings", tags=["findings"])


@router.get("/{finding_id}", response_model=FindingDetail)
def read_finding(
    finding=Depends(get_existing_finding),
    store: CaseStore = Depends(get_store),
) -> FindingDetail:
    return first_item(get_finding_detail(store, finding.id))


@router.post("/{finding_id}/decision", response_model=DecisionView)
def record_decision(
    body: RecordDecisionRequest,
    finding=Depends(get_existing_finding),
    store: CaseStore = Depends(get_store),
    _: None = Depends(require_api_key),
) -> DecisionView:
    decision = store.create_decision(
        finding.id,
        action=body.action,
        reason=body.reason,
        decided_by=body.decided_by,
    )
    return DecisionView(
        id=str(decision.id),
        action=decision.action,
        reason=decision.reason,
        decided_by=decision.decided_by,
        decided_at=decision.decided_at,
    )
