"""Tests for chat model registry (Phase 2)."""

import pytest

from secondlook.chat.models import (
    MockOutlineClient,
    MockTerseClient,
    build_client,
    get_model_spec,
    list_models,
)
from secondlook.synthesis.llm_client import LLMClientError


def test_list_models_contains_expected_models():
    models = list_models()
    model_ids = {m.id for m in models}
    assert "mock-outline" in model_ids
    assert "mock-terse" in model_ids
    assert "anthropic" in model_ids
    assert "openai-compatible" in model_ids

    outline = next(m for m in models if m.id == "mock-outline")
    assert outline.available is True
    assert outline.provider == "mock"


def test_get_model_spec():
    spec = get_model_spec("mock-outline")
    assert spec is not None
    assert spec.id == "mock-outline"
    assert spec.provider == "mock"

    missing = get_model_spec("nonexistent-model")
    assert missing is None


def test_mock_models_produce_visibly_different_output():
    prompt = "What are the resistance mutations for EGFR?"

    outline_client = MockOutlineClient()
    terse_client = MockTerseClient()

    outline_res = outline_client.complete(prompt)
    terse_res = terse_client.complete(prompt)

    # Outline has markdown headings
    assert "## On:" in outline_res
    assert "### Caveats" in outline_res

    # Terse is concise prose
    assert "No grounded answer available" in terse_res
    assert "##" not in terse_res

    # Both are non-empty and visibly distinct
    assert outline_res != terse_res


def test_build_client_offline_mocks():
    client1 = build_client("mock-outline")
    assert isinstance(client1, MockOutlineClient)

    client2 = build_client("mock-terse")
    assert isinstance(client2, MockTerseClient)


def test_build_client_unknown_raises():
    with pytest.raises(LLMClientError, match="unknown model"):
        build_client("unknown-xyz")


def test_build_client_unavailable_raises():
    # anthropic is unavailable when ANTHROPIC_API_KEY is not set
    spec = get_model_spec("anthropic")
    if not spec.available:
        with pytest.raises(LLMClientError, match="is not configured"):
            build_client("anthropic")
