"""Question-status counts shared by case summary and the research-queue view."""

from __future__ import annotations

from collections import Counter

from secondlook.case.models import QUESTION_STATUSES


def question_status_counts(questions) -> dict[str, int]:
    counts = Counter(q.status for q in questions)
    return {status: int(counts.get(status, 0)) for status in sorted(QUESTION_STATUSES)}
