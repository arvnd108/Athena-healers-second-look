"""Minimal Question value for the signal-generator interface.

Issue #6's generators take `(ChangeSet, Question) -> list[Signal]`. The
full template-rendering machinery in IMPLEMENTATION_PLAN.md §6
(`QUESTION_TEMPLATES`, LLM polish, `ATHENA_LLM_ENABLED`) is issue #10
and is deliberately not implemented here. This module only holds the
in-memory shape a generator needs to type-check and test against --
`kind`, `text`, `detail`, `priority`, `triggering_change` -- imported
from the real `case/diff.py` types (issue #5), not redefined.

This is a different type from the SQLAlchemy `Question` row in
`case/models.py`. That one is the persisted case-memory record; this
one is the diff-engine value produced from a `Change` before
persistence.
"""

from __future__ import annotations

from dataclasses import dataclass

from secondlook.case.diff import Change, ChangeKind


@dataclass(frozen=True)
class Question:
    kind: ChangeKind
    text: str
    detail: dict
    priority: int
    triggering_change: Change
