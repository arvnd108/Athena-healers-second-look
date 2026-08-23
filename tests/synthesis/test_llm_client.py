"""Offline tests for synthesis/llm_client.py -- no live LLM, no network."""

from __future__ import annotations

import sys

import pytest

from secondlook.synthesis.llm_client import (
    LLM_PROVIDERS,
    AnthropicClient,
    OpenAICompatibleClient,
    get_llm_client,
    llm_enabled,
)
from secondlook.tier1.graph_schema import assert_valid


class TestLlmEnabled:
    def test_default_is_true(self, monkeypatch):
        monkeypatch.delenv("ATHENA_LLM_ENABLED", raising=False)
        assert llm_enabled() is True

    def test_false_string_is_false(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "false")
        assert llm_enabled() is False

    def test_zero_is_false(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "0")
        assert llm_enabled() is False

    def test_false_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "FALSE")
        assert llm_enabled() is False


class TestGetLlmClient:
    def test_disabled_returns_none_without_constructing_a_client(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "false")

        def boom(*_a, **_k):
            raise AssertionError("client constructor must not run when disabled")

        monkeypatch.setattr("secondlook.synthesis.llm_client.AnthropicClient", boom)
        monkeypatch.setattr("secondlook.synthesis.llm_client.OpenAICompatibleClient", boom)
        assert get_llm_client() is None

    def test_disabled_does_not_import_anthropic(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "false")
        sys.modules.pop("anthropic", None)
        get_llm_client()
        assert "anthropic" not in sys.modules

    def test_invalid_provider_raises_via_assert_valid(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_ENABLED", "true")
        monkeypatch.setenv("ATHENA_LLM_PROVIDER", "magic-8-ball")
        with pytest.raises(ValueError, match="ATHENA_LLM_PROVIDER"):
            get_llm_client()

    def test_providers_allowed_set_is_closed(self):
        assert_valid("anthropic", LLM_PROVIDERS, "ATHENA_LLM_PROVIDER")
        assert_valid("openai_compatible", LLM_PROVIDERS, "ATHENA_LLM_PROVIDER")
        with pytest.raises(ValueError):
            assert_valid("something_else", LLM_PROVIDERS, "ATHENA_LLM_PROVIDER")


class TestAnthropicLazyImport:
    def test_construction_does_not_import_anthropic(self, monkeypatch):
        sys.modules.pop("anthropic", None)

        class _Blocker:
            def find_module(self, name, path=None):  # noqa: ARG002 -- import hook
                if name == "anthropic" or name.startswith("anthropic."):
                    raise ImportError("anthropic must not be imported at construction")
                return None

            def find_spec(self, name, path=None, target=None):  # noqa: ARG002
                if name == "anthropic" or name.startswith("anthropic."):
                    raise ImportError("anthropic must not be imported at construction")
                return None

        blocker = _Blocker()
        monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
        client = AnthropicClient(model="claude-sonnet-4-5")
        assert client.model == "claude-sonnet-4-5"
        assert "anthropic" not in sys.modules

    def test_default_model_is_a_documented_constant(self, monkeypatch):
        monkeypatch.delenv("ATHENA_LLM_MODEL", raising=False)
        client = AnthropicClient()
        assert client.model
        assert isinstance(client.model, str)


class TestOpenAICompatibleConstruction:
    def test_reads_base_url_and_model_from_env(self, monkeypatch):
        monkeypatch.setenv("ATHENA_LLM_BASE_URL", "http://localhost:8000/v1")
        monkeypatch.setenv("ATHENA_LLM_MODEL", "candidate-model")
        client = OpenAICompatibleClient()
        assert client.base_url == "http://localhost:8000/v1"
        assert client.model == "candidate-model"

    def test_no_model_is_hardcoded(self, monkeypatch):
        monkeypatch.delenv("ATHENA_LLM_MODEL", raising=False)
        monkeypatch.delenv("ATHENA_LLM_BASE_URL", raising=False)
        with pytest.raises(Exception, match="ATHENA_LLM_"):
            OpenAICompatibleClient()
