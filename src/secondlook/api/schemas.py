"""Request/response models this issue adds on top of query/contracts.py."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from secondlook.case.models import DECISION_ACTIONS, EVENT_TYPES


class CreateCaseRequest(BaseModel):
    """Mirrors CaseStore.create_case kwargs exactly. No extra P0 event fields."""

    model_config = ConfigDict(extra="forbid")

    label: str
    cancer_type: str
    age_years: int | None = None
    primary_site: str | None = None
    histology: str | None = None
    stage: str | None = None
    doid: str | None = None


class AppendEventRequest(BaseModel):
    event_type: str
    payload: dict
    occurred_at: datetime
    source_document: str | None = None
    recorded_by: str | None = None

    @field_validator("event_type")
    @classmethod
    def event_type_must_be_known(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(EVENT_TYPES)}")
        return value


class RecordDecisionRequest(BaseModel):
    action: str
    reason: str
    decided_by: str

    @field_validator("action")
    @classmethod
    def action_must_be_known(cls, value: str) -> str:
        if value not in DECISION_ACTIONS:
            raise ValueError(f"action must be one of {sorted(DECISION_ACTIONS)}")
        return value


class ResearchFindingView(BaseModel):
    id: str
    claim: str
    question_id: str


class ResearchResponse(BaseModel):
    questions_created: int
    findings_created: int
    findings: list[ResearchFindingView] = Field(default_factory=list)
