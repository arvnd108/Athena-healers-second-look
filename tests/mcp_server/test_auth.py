from __future__ import annotations

import asyncio
import warnings

import pytest

from secondlook.mcp_server.auth import StaticTokenVerifier, configure_remote_auth
from secondlook.mcp_server.server import build_server
from secondlook.mcp_server.view import ReadOnlyCaseView

API_KEY = "test-mcp-key-not-for-production"


class _MinimalStore:
    get_case = list_events = derive_state = list_questions = list_active_findings = None


def test_configure_remote_auth_refuses_to_start_without_key(monkeypatch):
    monkeypatch.delenv("ATHENA_MCP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ATHENA_MCP_API_KEY"):
        configure_remote_auth()


def test_build_server_allow_remote_without_key_raises(monkeypatch):
    monkeypatch.delenv("ATHENA_MCP_API_KEY", raising=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(RuntimeError, match="ATHENA_MCP_API_KEY"):
            build_server(ReadOnlyCaseView(_MinimalStore()), allow_remote=True)


def test_build_server_allow_remote_with_key_sets_token_verifier(monkeypatch):
    monkeypatch.setenv("ATHENA_MCP_API_KEY", API_KEY)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mcp = build_server(ReadOnlyCaseView(_MinimalStore()), allow_remote=True)
    assert mcp._token_verifier is not None
    assert mcp.settings.auth is not None


def test_loopback_build_does_not_require_the_mcp_key(monkeypatch):
    monkeypatch.delenv("ATHENA_MCP_API_KEY", raising=False)
    mcp = build_server(ReadOnlyCaseView(_MinimalStore()), allow_remote=False)
    assert mcp._token_verifier is None
    assert mcp.settings.auth is None


def test_static_token_verifier_accepts_matching_token_only():
    verifier = StaticTokenVerifier(API_KEY)
    ok = asyncio.run(verifier.verify_token(API_KEY))
    assert ok is not None
    assert ok.token == API_KEY
    assert ok.client_id == "mcp-static"
    assert ok.scopes == ["mcp"]
    assert asyncio.run(verifier.verify_token("nope")) is None
