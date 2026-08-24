"""Research-queue view: questions and status counts, no event-log fold."""

from __future__ import annotations

from secondlook.query.contracts import QuestionView, QueueView
from secondlook.query.question_counts import question_status_counts
from secondlook.query.result import QueryResult


def get_queue(store, case_id) -> QueryResult[QueueView]:
    questions = store.list_questions(case_id)
    view = QueueView(
        case_id=str(case_id),
        questions=[
            QuestionView(id=str(q.id), text=q.text, status=q.status, priority=q.priority)
            for q in questions
        ],
        question_counts=question_status_counts(questions),
        empty_reason=None if questions else "no questions recorded for this case",
    )
    return QueryResult(items=(view,))
