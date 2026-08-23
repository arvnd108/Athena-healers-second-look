"""Question phrasing from ChangeSet templates.

HARD RULE: this module is 100% deterministic. No LLM import, no network
call, anywhere in this file. The ChangeSet decides *what* to ask about;
templates only phrase it. IMPLEMENTATION_PLAN.md §6's aside about "LLM
polish" for readability is out of scope for issue #10 -- a future issue
can compose with `synthesis/llm_client.py`. The one LLM call site in this
issue is `synthesis/generate.py`.

`cancer_type` is a keyword argument because it lives on the persisted
`Case` row (`case/models.py`), not on `CaseState` and not on
`Change.detail`. `compute_diff()` does not put it on any Change -- do not
"fix" that by smuggling it onto `Change.detail`. `last_regimen` for
`DISEASE_PROGRESSION` is derived from `CaseState.treatments` (most recent
entry with `action == "started"`). If there is none, the fallback string
`MISSING_REGIMEN_FALLBACK` ("prior therapy") is used rather than crashing:
a case can reach progression with an incomplete treatment history.

This `Question` dataclass is a different type from the SQLAlchemy
`Question` row in `case/models.py`. Wiring rendered questions into
persistence is not this module's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from secondlook.case.diff import Change, ChangeKind, ChangeSet
from secondlook.case.state import CaseState

MISSING_REGIMEN_FALLBACK = "prior therapy"

# Verbatim from IMPLEMENTATION_PLAN.md §6, as tuples (immutable, matching
# ChangeSet.changes). One Question is rendered per (Change, template) pair
# so each angle (evidence / trials / resistance) dispatches independently
# through signals/registry.py.
QUESTION_TEMPLATES: dict[ChangeKind, tuple[str, ...]] = {
    ChangeKind.NEW_ALTERATION: (
        "What documented evidence exists for {gene} {variant} in {cancer_type}?",
        "Are there recruiting trials matching {gene} {variant}?",
        "Does {gene} {variant} confer resistance to any component of the current regimen?",
    ),
    ChangeKind.BIOMARKER_SHIFT: (
        "What does {name} crossing {threshold} imply for treatment options in {cancer_type}?",
    ),
    ChangeKind.DISEASE_PROGRESSION: (
        "What resistance mechanisms are documented for {last_regimen} in {cancer_type}?",
    ),
    ChangeKind.TREATMENT_LINE_CHANGE: (
        "What options are documented after {line} line {regimen} in {cancer_type}?",
    ),
}

# Higher int = more urgent. A disease-progression event changes what is
# actionable *now*; a new alteration is the next reason to re-query
# evidence; a treatment-line change is a plan update whose options matter
# after the fact; a biomarker crossing a threshold is information that
# has not yet, by itself, changed the plan. All templates for one Change
# share that Change's priority -- there is no spec basis for ranking
# within a single change's templates.
PRIORITY_BY_CHANGE_KIND: dict[ChangeKind, int] = {
    ChangeKind.DISEASE_PROGRESSION: 4,
    ChangeKind.NEW_ALTERATION: 3,
    ChangeKind.TREATMENT_LINE_CHANGE: 2,
    ChangeKind.BIOMARKER_SHIFT: 1,
}


class TemplateFillError(ValueError):
    """A template placeholder could not be filled from any available source.

    Raised instead of a raw KeyError so the missing name is an explicit,
    named failure (no silent gap), not a leaked format-string KeyError.
    """


@dataclass(frozen=True)
class Question:
    kind: ChangeKind
    text: str
    detail: dict
    priority: int
    triggering_change: Change


def _placeholder_names(template: str) -> tuple[str, ...]:
    return tuple(name for _, name, _, _ in Formatter().parse(template) if name)


def _last_regimen(case_state: CaseState) -> str:
    started = [t.regimen for t in case_state.treatments if t.action == "started"]
    if not started:
        return MISSING_REGIMEN_FALLBACK
    return started[-1]


def _fill(template: str, values: dict) -> str:
    names = _placeholder_names(template)
    missing = [name for name in names if values.get(name) is None or values.get(name) == ""]
    if missing:
        raise TemplateFillError(
            "cannot fill template placeholder(s) "
            + ", ".join(missing)
            + " from Change.detail, cancer_type, or last_regimen"
        )
    return template.format(**{name: values[name] for name in names})


def render_questions(
    change_set: ChangeSet,
    case_state: CaseState,
    *,
    cancer_type: str,
) -> list[Question]:
    """Pure. Deterministic. Same inputs -> byte-identical output, always.

    Iterates `change_set.changes` in order, then each template for that
    kind in order. Does not iterate `supersessions` -- those have no
    ChangeKind and QUESTION_TEMPLATES is not keyed on them.
    """
    questions: list[Question] = []
    for change in change_set.changes:
        templates = QUESTION_TEMPLATES[change.kind]
        values: dict = {
            **change.detail,
            "cancer_type": cancer_type,
            "last_regimen": _last_regimen(case_state),
        }
        for template in templates:
            text = _fill(template, values)
            questions.append(
                Question(
                    kind=change.kind,
                    text=text,
                    detail=dict(values),
                    priority=PRIORITY_BY_CHANGE_KIND[change.kind],
                    triggering_change=change,
                )
            )
    return questions
