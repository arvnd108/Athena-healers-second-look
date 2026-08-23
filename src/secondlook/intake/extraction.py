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

import uuid
from typing import Protocol

from secondlook.case.models import EVENT_TYPES
from secondlook.intake.models import (
    DOCUMENT_TYPES,
    ExtractedEventCandidate,
    ProposedCaseEvent,
    UploadedDocument,
)

LOW_CONFIDENCE_THRESHOLD = 0.7
# No number is specified anywhere in this subsystem's source issue -- it
# says only "low-confidence fields are highlighted for review, not
# silently accepted." 0.7 is a documented placeholder pending real
# extraction-accuracy measurement through the LLM Decision-Quality & Safety
# Monitoring Harness (subsystem P, not yet built) -- see this module's
# eval fixtures under tests/intake/fixtures/ for the accuracy-measurement
# starting point once that harness exists.


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


class ClaudeExtractionBackend:
    """A real `ExtractionBackend` calling the Anthropic API, one prompt per
    `document_type`.

    **Unverified.** This class has never been run against a live model --
    there is no API key available in the environment this was built in,
    and no automated test exercises it (the whole test suite runs against
    `StubExtractionBackend` instead, which is offline and free). Per this
    project's own standing rule (`docs/validation-plan.md`'s discipline,
    extended by subsystem P's not-yet-built harness), an unvalidated
    extraction path must not be presented as trustworthy. Before this
    class is used for anything beyond a manual smoke test:

    1. Run it against `tests/intake/fixtures/`'s hand-labeled eval set and
       measure real accuracy per field -- do not assume the prompts below
       work as written.
    2. Wire that measurement through subsystem P's harness once it exists,
       the same way subsystem F's criteria-extraction harness is meant to.
    3. Only then should a caller depend on this class's output without a
       human catching every mistake -- which, per this module's own
       design, is every extraction, always, regardless of measured
       accuracy: confirmation is not a training-wheels step to remove
       once accuracy is good, it is the permanent architecture.

    The prompts below are a first-draft starting point, not a tuned or
    validated artifact.
    """

    _PROMPTS: dict[str, str] = {
        "pathology": (
            "Extract structured findings from this pathology report. "
            "Identify any of: ALTERATION_OBSERVED (gene, variant, variant_type, "
            "assay, tested_on), BIOMARKER_MEASURED (name, value, unit, "
            "measured_on). Return only fields explicitly stated in the text; "
            "never infer or guess a value that is not written."
        ),
        "sequencing": (
            "Extract structured findings from this sequencing/NGS report. "
            "Identify any of: ALTERATION_OBSERVED (gene, variant, variant_type, "
            "assay, tested_on), BIOMARKER_MEASURED (name, value, unit, "
            "measured_on, e.g. TMB/MSI/PD-L1 if reported). Return only fields "
            "explicitly stated in the text; never infer or guess a value that "
            "is not written."
        ),
        "clinical_note": (
            "Extract structured findings from this clinical note. Identify any "
            "of: TREATMENT_LINE (regimen, line, action: started|stopped, "
            "reason), DISEASE_ASSESSMENT (status: response|stable|progression, "
            "sites, assessed_on), CLINICAL_QUESTION (text, if the note poses an "
            "open question). Return only fields explicitly stated in the text; "
            "never infer or guess a value that is not written."
        ),
    }

    def __init__(self, *, model: str = "claude-sonnet-5", api_key: str | None = None):
        self._model = model
        self._api_key = api_key

    def extract(self, text: str, document_type: str) -> list[ExtractedEventCandidate]:
        if document_type not in self._PROMPTS:
            raise ValueError(f"No prompt defined for document_type {document_type!r}")

        # Deliberately not implemented against a live client here. Wiring
        # this to `anthropic.Anthropic(...)` and parsing its response into
        # `ExtractedEventCandidate` objects is the next step -- but doing
        # so without the ability to run it against a real model and check
        # the output would mean shipping unverified prompt-parsing code
        # dressed up as working code. See this class's docstring.
        raise NotImplementedError(
            "ClaudeExtractionBackend is a documented skeleton, not a working "
            "implementation -- see this class's docstring for what's needed "
            "before it can be. Use StubExtractionBackend for tests."
        )
