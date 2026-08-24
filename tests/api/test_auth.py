from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from secondlook.api.app import create_app
from secondlook.api.deps import get_store

from .fakes import InMemoryStore

API_KEY = "test-key-not-for-production"


@pytest.fixture
def keyed_client(monkeypatch):
    monkeypatch.delenv("ATHENA_API_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("ATHENA_API_KEY", API_KEY)
    store = InMemoryStore()
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app), store


def test_post_without_key_is_401(keyed_client):
    client, _store = keyed_client
    response = client.post("/api/cases", json={"label": "C1", "cancer_type": "NSCLC"})
    assert response.status_code == 401


def test_post_with_correct_key_succeeds(keyed_client):
    client, store = keyed_client
    response = client.post(
        "/api/cases",
        json={"label": "C1", "cancer_type": "NSCLC"},
        headers={"X-Athena-Api-Key": API_KEY},
    )
    assert response.status_code == 200
    assert store.get_case(uuid.UUID(response.json()["id"])) is not None


def test_get_does_not_require_api_key(keyed_client):
    client, store = keyed_client
    case = store.create_case(label="C1", cancer_type="NSCLC")
    response = client.get(f"/api/cases/{case.id}")
    assert response.status_code == 200


def test_create_app_refuses_to_start_without_key_or_opt_out(monkeypatch):
    monkeypatch.delenv("ATHENA_API_KEY", raising=False)
    monkeypatch.delenv("ATHENA_API_AUTH_DISABLED", raising=False)
    with pytest.raises(RuntimeError, match="ATHENA_API_KEY"):
        create_app()


def test_wrong_key_is_401(keyed_client):
    client, _store = keyed_client
    response = client.post(
        "/api/cases",
        json={"label": "C1", "cancer_type": "NSCLC"},
        headers={"X-Athena-Api-Key": "nope"},
    )
    assert response.status_code == 401


def test_write_routes_other_than_create_case_also_require_the_key(keyed_client):
    client, store = keyed_client
    case = store.create_case(label="C1", cancer_type="NSCLC")
    events = client.post(
        f"/api/cases/{case.id}/events",
        json={
            "event_type": "ALTERATION_OBSERVED",
            "payload": {"gene": "EGFR", "variant": "T790M"},
            "occurred_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert events.status_code == 401
    research = client.post(f"/api/cases/{case.id}/research")
    assert research.status_code == 401
    question = store.create_question(case.id, text="q", priority=1)
    finding = store.create_finding(
        question.id,
        claim="c",
        evidence_class="computed",
        evidence_ref={"source": "synthesis", "cited_ids": []},
        assumptions=[],
    )
    decision = client.post(
        f"/api/findings/{finding.id}/decision",
        json={"action": "investigating", "reason": "x", "decided_by": "dr"},
    )
    assert decision.status_code == 401
