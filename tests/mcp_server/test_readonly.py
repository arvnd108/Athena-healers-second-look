from __future__ import annotations

import ast
import inspect
import warnings
from pathlib import Path
from typing import get_type_hints

import pytest

from secondlook.mcp_server import tools
from secondlook.mcp_server.server import DEFAULT_BIND_HOST, resolve_bind_host
from secondlook.mcp_server.view import ReadOnlyCaseView

WRITE_NAMES = {
    "create_case",
    "append_event",
    "create_decision",
    "create_question",
    "create_finding",
}

MCP_ROOT = Path(__file__).resolve().parents[2] / "src" / "secondlook" / "mcp_server"

TOOL_FUNCS = (
    tools.get_case_summary_tool,
    tools.get_recent_changes_tool,
    tools.search_evidence_tool,
    tools.match_trials_tool,
    tools.get_access_pathways_tool,
)


def test_ast_scan_rejects_write_method_names_under_mcp_server():
    hits = []
    for path in MCP_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in WRITE_NAMES:
                hits.append(f"{path.name}:{node.lineno}:{node.id}")
            if isinstance(node, ast.Attribute) and node.attr in WRITE_NAMES:
                hits.append(f"{path.name}:{node.lineno}:{node.attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in WRITE_NAMES:
                    if name in node.value:
                        hits.append(f"{path.name}:{node.lineno}:str:{name}")
    assert hits == [], f"write names leaked into mcp_server: {hits}"


def test_every_tool_annotates_readonly_view_as_first_parameter():
    for fn in TOOL_FUNCS:
        hints = get_type_hints(fn)
        first = next(iter(inspect.signature(fn).parameters))
        assert hints[first] is ReadOnlyCaseView, fn.__name__


def test_default_bind_host_is_loopback():
    assert DEFAULT_BIND_HOST == "127.0.0.1"
    assert resolve_bind_host() == "127.0.0.1"
    assert resolve_bind_host(allow_remote=False) == "127.0.0.1"


def test_remote_bind_requires_the_flag_and_warns_naming_issue_60():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        host = resolve_bind_host(allow_remote=True)
    assert host == "0.0.0.0"
    assert any("60" in str(w.message) for w in caught)


class _FakeMcp:
    def __init__(self):
        self.names: list[str] = []

    def tool(self):
        def decorator(fn):
            self.names.append(fn.__name__)
            return fn

        return decorator


class _MinimalStore:
    get_case = list_events = derive_state = list_questions = list_active_findings = None


def test_register_tools_exposes_the_five_issue_tool_names():
    from secondlook.mcp_server.server import register_tools

    mcp = _FakeMcp()
    register_tools(mcp, ReadOnlyCaseView(_MinimalStore()))
    assert mcp.names == [
        "get_case_summary",
        "get_recent_changes",
        "search_evidence",
        "match_trials",
        "get_access_pathways",
    ]


def test_cli_help_does_not_open_a_store():
    from secondlook.mcp_server.server import main

    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
