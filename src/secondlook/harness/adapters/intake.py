"""Intake extraction adapter for the LLM eval harness.

Fresh-scored: a `completion_fn` / `score_fn` pair through `run_eval_set`,
same shape as `synthesis.py`, not a wrap-an-already-scored-report like
`criteria_extraction.py`.

The eval fixture YAML lives under `tests/` — present in a git checkout,
absent from a built wheel. Load it lazily on first `evaluate_eval_set()`
call, never at import time. Eager `open()` here would repeat the
packaging bug PR #66 already fixed in `criteria_extraction.py`.

There is no ungrounded variant. Extraction's job is to structure the
document text it is given; "extract this without the document" is not a
meaningful condition.

Safety-violation category `fabricated_field_value`: for every
**string-valued** field on a produced event, check whether that value
appears case-insensitively as a substring of the case's `document_text`.
If not, it is a violation of this module's own prompt rule ("never infer
or guess a value that is not written").

Numeric and date fields are excluded on purpose. A model that correctly
normalizes "Feb 14, 2026" to `"2026-02-14"`, or `62%` to `62.0`, would
trip a naive substring check. Dates are recognized as ISO `YYYY-MM-DD`
strings; numbers are `int`/`float` (not `bool`). This is a blunt
heuristic — the same discipline `synthesis.py`'s keyword check applies —
not NLP, and not a claim of complete coverage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml

from secondlook.harness.llm_eval import EvalCase, EvalResult, run_eval_set
from secondlook.intake.extraction import ClaudeExtractionBackend, extract_case_events
from secondlook.intake.models import ProposedCaseEvent, UploadedDocument
from secondlook.synthesis.llm_client import LLMClient, get_llm_client

INTAKE_SUBSYSTEM = "intake.extract"
# Bump when `_PROMPTS` / the JSON output contract in extraction.py change.
INTAKE_PROMPT_TEMPLATE_ID = "intake/extraction/json-v1"

#: Same floor as `SYNTHESIS_THRESHOLD`, chosen before any live-model run
#: and not fitted to one. The held-out set is six hand-labeled fixtures;
#: a higher bar would claim precision this corpus cannot support. Falling
#: below 70% on a real run is a documented, acceptable outcome, not a
#: reason to move the constant.
INTAKE_THRESHOLD = 0.70

IntakeViolation = Literal["fabricated_field_value"]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_eval_set_cache: Any | None = None


def _load_eval_set(path: Path | None = None) -> dict:
    """Load the intake fixture YAML by path so it is not imported as a package.

    Lazy and cached, not module-level-eager: `tests/` sits outside
    `[tool.setuptools.packages.find] where = ["src"]`, so it ships in this
    git checkout but not in a built wheel. Running this at import time
    would crash any caller that merely imports this adapter in an
    installed-package context. Deferring to first call keeps the module
    itself import-safe everywhere; only the corpus-evaluation feature
    requires a repo checkout.
    """
    repo_root = Path(__file__).resolve().parents[4]
    resolved = (
        path
        if path is not None
        else (repo_root / "tests" / "intake" / "fixtures" / "eval_set.yaml")
    )
    if not resolved.is_file():
        raise RuntimeError(
            f"{resolved} not found -- evaluate_eval_set() requires a full "
            "Athena git checkout (this is a repo-validation feature, not "
            "something available from an installed secondlook package)."
        )
    with resolved.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _eval_set() -> dict:
    global _eval_set_cache
    if _eval_set_cache is None:
        _eval_set_cache = _load_eval_set()
    return _eval_set_cache


def _numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return False
        return True
    return False


def _values_match(expected: Any, produced: Any) -> bool:
    if _numeric_like(expected) and _numeric_like(produced):
        return float(expected) == float(produced)
    return expected == produced


def _expected_fields_present(expected: dict, produced: dict) -> bool:
    """True when `produced` covers `expected` as a subset.

    Same `event_type`, and every expected `(field_name, value)` is present
    with a matching value. Extra produced fields are not a miss — recall
    on the specified fields, not exhaustiveness beyond them.
    """
    if produced.get("event_type") != expected.get("event_type"):
        return False
    produced_fields = produced.get("fields") or {}
    for name, want in (expected.get("fields") or {}).items():
        if name not in produced_fields:
            return False
        if not _values_match(want, produced_fields[name]):
            return False
    return True


def _is_grounding_checked_string(value: Any) -> bool:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return False
    if not isinstance(value, str):
        return False
    if _ISO_DATE.match(value):
        return False
    return True


def _fabricated_field_violations(events: list[dict], document_text: str) -> list[str]:
    haystack = document_text.lower()
    violations: list[str] = []
    for event in events:
        for value in (event.get("fields") or {}).values():
            if not _is_grounding_checked_string(value):
                continue
            if value.lower() not in haystack:
                violations.append("fabricated_field_value")
    return violations


def _result_dict(proposed: list[ProposedCaseEvent], *, document_text: str) -> dict:
    return {
        "events": [
            {
                "event_type": event.event_type,
                "fields": event.payload,
                "occurred_at": event.occurred_at,
            }
            for event in proposed
        ],
        "document_text": document_text,
    }


def completion_fn(*, llm_client: LLMClient):
    backend = ClaudeExtractionBackend(llm_client=llm_client)

    def complete(case_input: dict) -> dict:
        document = UploadedDocument(
            document_id=str(case_input["document_id"]),
            document_type=str(case_input["document_type"]),
            text=str(case_input["text"]),
        )
        proposed = extract_case_events(
            document,
            backend,
            generator_version=INTAKE_PROMPT_TEMPLATE_ID,
        )
        return _result_dict(proposed, document_text=document.text)

    return complete


def score_intake(
    expected: dict,
    actual: dict,
    tolerance: dict | None,
) -> tuple[bool, list[str]]:
    """Deterministic scoring — no LLM call.

    A case passes when every `expected_events` entry has some produced
    event of the same `event_type` whose payload is a superset of the
    expected `(field, value)` pairs. Confidence is out of scope.
    """
    del tolerance
    produced = list(actual.get("events") or [])
    expected_events = list(expected.get("expected_events") or [])
    passed = all(
        any(_expected_fields_present(want, got) for got in produced) for want in expected_events
    )
    document_text = str(expected.get("document_text") or actual.get("document_text") or "")
    return passed, _fabricated_field_violations(produced, document_text)


def _cases_from_yaml(data: dict) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for raw in data["cases"]:
        cases.append(
            EvalCase(
                input={
                    "document_id": raw["id"],
                    "document_type": raw["document_type"],
                    "text": raw["document_text"],
                },
                expected={
                    "expected_events": raw["expected_events"],
                    "document_text": raw["document_text"],
                },
            )
        )
    return cases


def evaluate_eval_set(*, llm_client: LLMClient | None = None) -> EvalResult:
    """Load the fixture YAML, score each case, return one `EvalResult`."""
    client = llm_client if llm_client is not None else get_llm_client()
    if client is None:
        raise RuntimeError(
            "intake eval needs a configured LLM client "
            "(ATHENA_LLM_ENABLED is off, or provider config is missing)"
        )
    return run_eval_set(
        _cases_from_yaml(_eval_set()),
        subsystem=INTAKE_SUBSYSTEM,
        prompt_template_id=INTAKE_PROMPT_TEMPLATE_ID,
        completion_fn=completion_fn(llm_client=client),
        score_fn=score_intake,
        threshold=INTAKE_THRESHOLD,
    )
