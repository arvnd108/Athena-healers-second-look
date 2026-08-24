"""Read-only facade over a CaseStore. Write method names do not exist on this type."""

from __future__ import annotations

from typing import Any


class ReadOnlyCaseView:
    """Exposes only the store reads MCP tools are allowed to call.

    Bound methods still have `__self__` pointing at the underlying store
    (a Python limitation). Enforcement is the type boundary plus the AST
    scan under `mcp_server/`.
    """

    def __init__(self, store: Any) -> None:
        self._get_case = store.get_case
        self._list_events = store.list_events
        self._derive_state = store.derive_state
        self._list_questions = store.list_questions
        self._list_active_findings = store.list_active_findings

    def get_case(self, case_id):
        return self._get_case(case_id)

    def list_events(self, case_id):
        return self._list_events(case_id)

    def derive_state(self, case_id):
        return self._derive_state(case_id)

    def list_questions(self, case_id):
        return self._list_questions(case_id)

    def list_active_findings(self, case_id):
        return self._list_active_findings(case_id)
