from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from secondlook.api.app import create_app
from secondlook.api.deps import get_dispatch, get_graph, get_llm_client, get_store

from .fakes import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("ATHENA_API_AUTH_DISABLED", "true")
    monkeypatch.delenv("ATHENA_API_KEY", raising=False)
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_graph] = lambda: None
    app.dependency_overrides[get_llm_client] = lambda: None
    app.dependency_overrides[get_dispatch] = lambda: (lambda *a, **k: [])
    return TestClient(app)


def test_get_case_unknown_id_is_404(client):
    response = client.get(f"/api/cases/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_case_existing_returns_case_summary(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC", age_years=54)
    response = client.get(f"/api/cases/{case.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "C1"
    assert body["cancer_type"] == "NSCLC"
    assert body["case_id"] == str(case.id)
    assert "question_counts" in body
    assert "items" not in body


def test_get_changes_unknown_id_is_404_not_empty_reason(client):
    """Non-negotiable finding #2: missing case is 404, not 200-with-empty."""
    response = client.get(f"/api/cases/{uuid.uuid4()}/changes")
    assert response.status_code == 404


def test_get_changes_existing_case_with_no_events_is_200_explained_empty(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    response = client.get(f"/api/cases/{case.id}/changes")
    assert response.status_code == 200
    body = response.json()
    assert body["changes"] == []
    assert body["empty_reason"]


def test_post_event_invalid_type_is_422(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    response = client.post(
        f"/api/cases/{case.id}/events",
        json={
            "event_type": "NOT_A_REAL_EVENT",
            "payload": {},
            "occurred_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert response.status_code == 422


def test_post_event_returns_changeset_for_the_just_appended_event(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    store.append_event(
        case.id,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    # Backdate clinical time; recorded_at of this POST is latest, so since=None
    # must pick this event (same rule as tests/query/test_recent_changes.py).
    response = client.post(
        f"/api/cases/{case.id}/events",
        json={
            "event_type": "ALTERATION_OBSERVED",
            "payload": {"gene": "KRAS", "variant": "G12C"},
            "occurred_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert any("KRAS" in c["summary"] for c in body["changes"])
    assert not any("EGFR" in c["summary"] for c in body["changes"])


def test_research_creates_findings_with_disease_not_progressing(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    store.append_event(
        case.id,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    response = client.post(f"/api/cases/{case.id}/research")
    assert response.status_code == 200
    body = response.json()
    assert body["questions_created"] == 3
    assert body["findings_created"] == 3
    for row in store.findings:
        assert row.assumptions == [{"type": "DiseaseNotProgressing"}]


def test_research_duplicates_on_second_call(client, store):
    # case/memory.py suppress_duplicates is unbuilt (issue #10). Assert the gap.
    case = store.create_case(label="C1", cancer_type="NSCLC")
    store.append_event(
        case.id,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    first = client.post(f"/api/cases/{case.id}/research")
    second = client.post(f"/api/cases/{case.id}/research")
    assert first.json()["findings_created"] == 3
    assert second.json()["findings_created"] == 3
    assert len(store.findings) == 6


def test_get_finding_unknown_is_404(client):
    assert client.get(f"/api/findings/{uuid.uuid4()}").status_code == 404


def test_get_finding_includes_provenance(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    store.append_event(
        case.id,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    client.post(f"/api/cases/{case.id}/research")
    finding = store.findings[0]
    response = client.get(f"/api/findings/{finding.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_ref"]["source"] == "synthesis"
    assert body["question_text"]
    assert body["case_id"] == str(case.id)
    assert body["decisions"] == []
    assert "items" not in body


def test_post_decision_then_visible_on_get(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    store.append_event(
        case.id,
        event_type="ALTERATION_OBSERVED",
        payload={"gene": "EGFR", "variant": "T790M"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    client.post(f"/api/cases/{case.id}/research")
    finding = store.findings[0]
    posted = client.post(
        f"/api/findings/{finding.id}/decision",
        json={"action": "investigating", "reason": "need labs", "decided_by": "dr-a"},
    )
    assert posted.status_code == 200
    assert posted.json()["action"] == "investigating"
    fetched = client.get(f"/api/findings/{finding.id}")
    assert fetched.status_code == 200
    assert fetched.json()["decisions"][0]["decided_by"] == "dr-a"


def test_get_queue_and_brief_on_existing_case(client, store):
    case = store.create_case(label="C1", cancer_type="NSCLC")
    queue = client.get(f"/api/cases/{case.id}/queue")
    assert queue.status_code == 200
    assert "question_counts" in queue.json()
    brief = client.get(f"/api/cases/{case.id}/brief")
    assert brief.status_code == 200
    assert "text/html" in brief.headers["content-type"]
    assert "C1" in brief.text


def test_get_queue_unknown_case_is_404(client):
    assert client.get(f"/api/cases/{uuid.uuid4()}/queue").status_code == 404


def test_post_case_returns_case_view_not_summary(client, store):
    response = client.post("/api/cases", json={"label": "C1", "cancer_type": "NSCLC"})
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "C1"
    assert "id" in body
    assert "question_counts" not in body
    assert "alterations" not in body
    assert store.get_case(uuid.UUID(body["id"])) is not None
