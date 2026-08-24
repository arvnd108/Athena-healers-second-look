"""`extract_case_events()` -- LLM-assisted structured extraction, always
human-confirmed before anything reaches `case/store.py`.

The actual model call is behind `ExtractionBackend`, an injected protocol,
for two reasons: it's what keeps `extract_case_events()` itself pure and
offline-testable (this project's DI convention, e.g.
`case/state.py.fold_events`, `synthesis/citation_gate.py`), and it's the
same abstraction boundary the Synthesis subsystem's `llm_client.py` is
meant to provide once built -- a hosted API or a self-hosted model can sit
behind this interface interchangeably.
"""

from __future__ import annotations

import json
import uuid
from typing import Protocol

from secondlook.case.models import EVENT_TYPES
from secondlook.intake.models import (
    DOCUMENT_TYPES,
    ExtractedEventCandidate,
    FieldValue,
    ProposedCaseEvent,
    UploadedDocument,
)
from secondlook.synthesis.llm_client import LLMClient

LOW_CONFIDENCE_THRESHOLD = 0.7
# No number is specified anywhere in this subsystem's source issue -- it
# says only "low-confidence fields are highlighted for review, not
# silently accepted." 0.7 is a documented placeholder pending real
# extraction-accuracy measurement through the LLM Decision-Quality & Safety
# Monitoring Harness -- see tests/intake/fixtures/ and
# harness/adapters/intake.py. 0.7 is not the same constant as
# INTAKE_THRESHOLD (field-recall on the held-out set); do not merge them.


class ExtractionBackend(Protocol):
    """The injected boundary. A real implementation calls an LLM (or any
    other extraction method); this protocol has no opinion on which.
    """

    def extract(self, text: str, document_type: str) -> list[ExtractedEventCandidate]: ...


def extract_case_events(
    document: UploadedDocument,
    backend: ExtractionBackend,
    *,
    generator_version: str,
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> list[ProposedCaseEvent]:
    """Runs `backend` against `document` and returns proposed events.

    Never writes to `case/store.py` -- returns `ProposedCaseEvent` objects
    only. `generator_version` is required, not optional, because every
    LLM-touched row in this system must be traceable to the prompt version
    that produced it (`CONCEPT_EVALUATION.md` SS4: stamp every LLM-touched
    row with `generator_version`) -- if an extraction later turns out
    wrong, this is what tells you which prompt version to fix or roll back.
    """
    if document.document_type not in DOCUMENT_TYPES:
        raise ValueError(
            f"Unknown document_type {document.document_type!r}; "
            f"must be one of {sorted(DOCUMENT_TYPES)}"
        )
    if not generator_version:
        raise ValueError("generator_version is required and must be non-empty")

    candidates = backend.extract(document.text, document.document_type)

    proposed: list[ProposedCaseEvent] = []
    for candidate in candidates:
        if candidate.event_type not in EVENT_TYPES:
            raise ValueError(
                f"Extraction backend returned unknown event_type "
                f"{candidate.event_type!r}; must be one of {sorted(EVENT_TYPES)}. "
                f"This is a backend/prompt bug, not a caller error -- the backend "
                f"must only ever propose one of case/models.py's five taxonomy types."
            )

        payload = {name: fv.value for name, fv in candidate.fields.items()}
        field_confidences = {name: fv.confidence for name, fv in candidate.fields.items()}
        low_confidence_fields = tuple(
            name
            for name, confidence in field_confidences.items()
            if confidence < low_confidence_threshold
        )

        proposed.append(
            ProposedCaseEvent(
                proposed_id=str(uuid.uuid4()),
                event_type=candidate.event_type,
                payload=payload,
                field_confidences=field_confidences,
                low_confidence_fields=low_confidence_fields,
                occurred_at=candidate.occurred_at,
                source_document_type=document.document_type,
                generator_version=generator_version,
            )
        )

    return proposed


class ExtractionParseError(RuntimeError):
    """Model output was not a usable JSON array of event candidates.

    Raised for a JSON parse failure or a missing required key. All-or-nothing:
    one malformed candidate fails the whole `.extract()` call. `FieldValue`
    validation errors propagate as `ValueError` and are not wrapped here.
    """


class StubExtractionBackend:
    """A deterministic, no-network backend for tests and demos. Returns
    whatever candidates it was constructed with, regardless of input text
    -- it does no actual extraction. This is what every test in
    `tests/intake/` runs against; it is not, and must never be mistaken
    for, a real extraction accuracy signal.
    """

    def __init__(self, candidates: list[ExtractedEventCandidate]):
        self._candidates = candidates

    def extract(self, text: str, document_type: str) -> list[ExtractedEventCandidate]:
        return list(self._candidates)


_JSON_OUTPUT_CONTRACT = (
    "Respond with a JSON array only, [] if nothing found, no prose before or "
    "after. Each object must be "
    '{"event_type": "...", "fields": {"<name>": {"value": ..., "confidence": '
    '0.0-1.0}, ...}, "occurred_at": "YYYY-MM-DD" | null}.'
)

_RAW_RESPONSE_TRUNCATE = 500


def _strip_json_fence(raw: str) -> str:
    """Defensively unwrap a leading/trailing markdown ```json fence."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_extraction_response(raw: str) -> list[ExtractedEventCandidate]:
    """Parse one model completion. All-or-nothing: never return a partial list."""
    stripped = _strip_json_fence(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ExtractionParseError(
            "extraction response is not JSON: " f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
        ) from exc
    if not isinstance(data, list):
        raise ExtractionParseError(
            "extraction response must be a JSON array: " f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
        )
    candidates: list[ExtractedEventCandidate] = []
    for item in data:
        if not isinstance(item, dict):
            raise ExtractionParseError(
                "each extraction candidate must be a JSON object: "
                f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
            )
        if "event_type" not in item or "fields" not in item:
            raise ExtractionParseError(
                "extraction candidate missing required key "
                f"(event_type, fields): {raw[:_RAW_RESPONSE_TRUNCATE]!r}"
            )
        fields_raw = item["fields"]
        if not isinstance(fields_raw, dict):
            raise ExtractionParseError(
                "extraction candidate fields must be an object: "
                f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
            )
        fields: dict[str, FieldValue] = {}
        for name, spec in fields_raw.items():
            if not isinstance(spec, dict) or "value" not in spec or "confidence" not in spec:
                raise ExtractionParseError(
                    f"field {name!r} must be "
                    '{"value": ..., "confidence": 0.0-1.0}: '
                    f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
                )
            # FieldValue.__post_init__ raises ValueError on a bad confidence;
            # do not wrap that — it is already the dedicated validation error.
            fields[name] = FieldValue(value=spec["value"], confidence=spec["confidence"])
        occurred_at = item.get("occurred_at")
        if occurred_at is not None and not isinstance(occurred_at, str):
            raise ExtractionParseError(
                "occurred_at must be a string or null: " f"{raw[:_RAW_RESPONSE_TRUNCATE]!r}"
            )
        candidates.append(
            ExtractedEventCandidate(
                event_type=item["event_type"],
                fields=fields,
                occurred_at=occurred_at,
            )
        )
    return candidates


class ClaudeExtractionBackend:
    """A real `ExtractionBackend` calling an `LLMClient`, one prompt per
    `document_type`.

    Inject `llm_client=` for tests and for the harness (typically
    `get_llm_client()`). When omitted, `.extract()` lazily constructs
    `AnthropicClient(model=..., api_key=...)` — never at `__init__` or
    module import, so a bare `ClaudeExtractionBackend()` stays free of
    any SDK import. Injecting `get_llm_client()` also picks up
    `ATHENA_LLM_PROVIDER=openai_compatible` without a second wrapper.

    Accuracy is measured by `harness/adapters/intake.py` against
    `tests/intake/fixtures/eval_set.yaml`. Confirmation before anything
    reaches `case/store.py` is still the permanent architecture, not a
    training-wheels step.
    """

    _PROMPTS: dict[str, str] = {
        "pathology": (
            "Extract structured findings from this pathology report. "
            "Identify any of: ALTERATION_OBSERVED (gene, variant, variant_type, "
            "assay, tested_on), BIOMARKER_MEASURED (name, value, unit, "
            "measured_on). Return only fields explicitly stated in the text; "
            "never infer or guess a value that is not written. " + _JSON_OUTPUT_CONTRACT
        ),
        "sequencing": (
            "Extract structured findings from this sequencing/NGS report. "
            "Identify any of: ALTERATION_OBSERVED (gene, variant, variant_type, "
            "assay, tested_on), BIOMARKER_MEASURED (name, value, unit, "
            "measured_on, e.g. TMB/MSI/PD-L1 if reported). Return only fields "
            "explicitly stated in the text; never infer or guess a value that "
            "is not written. " + _JSON_OUTPUT_CONTRACT
        ),
        "clinical_note": (
            "Extract structured findings from this clinical note. Identify any "
            "of: TREATMENT_LINE (regimen, line, action: started|stopped, "
            "reason), DISEASE_ASSESSMENT (status: response|stable|progression, "
            "sites, assessed_on), CLINICAL_QUESTION (text, if the note poses an "
            "open question). Return only fields explicitly stated in the text; "
            "never infer or guess a value that is not written. " + _JSON_OUTPUT_CONTRACT
        ),
    }

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        llm_client: LLMClient | None = None,
    ):
        self._model = model
        self._api_key = api_key
        self._llm_client = llm_client
        self._fallback_client: LLMClient | None = None

    def _client(self) -> LLMClient:
        if self._llm_client is not None:
            return self._llm_client
        if self._fallback_client is None:
            from secondlook.synthesis.llm_client import AnthropicClient

            # Lazy: AnthropicClient itself only imports the SDK inside
            # `.complete()`, never here. Constructing this backend with no
            # arguments therefore still has no network/SDK dependency.
            self._fallback_client = AnthropicClient(model=self._model, api_key=self._api_key)
        return self._fallback_client

    def extract(self, text: str, document_type: str) -> list[ExtractedEventCandidate]:
        if document_type not in self._PROMPTS:
            raise ValueError(f"No prompt defined for document_type {document_type!r}")
        # LLMClientError from `.complete()` propagates uncaught — it is
        # already the dedicated type for a transport/API failure.
        raw = self._client().complete(text, system=self._PROMPTS[document_type])
        return _parse_extraction_response(raw)
