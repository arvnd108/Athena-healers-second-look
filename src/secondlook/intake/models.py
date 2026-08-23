"""Data shapes for the intake pipeline. No SQLAlchemy dependency, matching
`case/state.py`'s pattern -- this package does no I/O of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The three document types this subsystem's issue names explicitly.
# Deliberately not open-ended -- a new document type needs its own prompt
# and its own extraction-accuracy eval set (see tests/intake/fixtures/),
# not silent inference from an unrecognized string.
DOCUMENT_TYPES: frozenset[str] = frozenset({"pathology", "sequencing", "clinical_note"})


@dataclass(frozen=True)
class UploadedDocument:
    """A document handed to the pipeline. `text` is the document's raw
    extracted text (already OCR'd upstream if it came from a scan -- OCR
    itself is out of scope for this module).

    **`text` is never persisted by anything in this package.** It is read
    once by `extract_case_events()` and passed to an `ExtractionBackend`;
    nothing downstream of that call keeps a copy. `case/models.py`'s
    `case_events.source_document` field is a citation-style reference (a
    document identifier/description), never the document body -- storing
    raw clinical documents is a deployment-level decision outside the
    Case Memory Store's schema, and outside this module's scope.
    """

    document_id: str
    document_type: str
    text: str


@dataclass(frozen=True)
class FieldValue:
    """One extracted field, with its own confidence -- never a single
    document-level confidence covering every field. A backend that is sure
    about `gene` but unsure about `variant_type` must be able to say so.
    """

    value: Any
    confidence: float  # 0.0-1.0

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")


@dataclass(frozen=True)
class ExtractedEventCandidate:
    """One event an `ExtractionBackend` believes it found in a document.

    `event_type` is chosen by the backend, not hardcoded per document_type
    in this module -- a single sequencing report can legitimately contain
    both an `ALTERATION_OBSERVED` and a `BIOMARKER_MEASURED` finding, and
    which event types a given document_type's prompt is designed to
    recognize is exactly the judgment call `IMPLEMENTATION_PLAN.md`'s
    "one extraction model/prompt per document_type" puts in the prompt's
    hands, not in a rigid type-to-type mapping here.
    """

    event_type: str
    fields: dict[str, FieldValue]
    occurred_at: str | None = None  # ISO date, if the document states one


@dataclass(frozen=True)
class ProposedCaseEvent:
    """The pipeline's only output shape. Never committed to the Case Memory
    Store by this module -- `status` is always `"pending_confirmation"`,
    and turning one into a real `CaseEvent` via `case/store.py`'s
    `append_event()` is a separate, explicit action a clinician (or the
    code path a clinician's confirmation click drives) performs, never
    this pipeline itself.
    """

    proposed_id: str
    event_type: str
    payload: dict[str, Any]
    field_confidences: dict[str, float]
    low_confidence_fields: tuple[str, ...]
    occurred_at: str | None
    source_document_type: str
    generator_version: str
    status: str = field(default="pending_confirmation")

    def __post_init__(self):
        if self.status != "pending_confirmation":
            raise ValueError(
                "ProposedCaseEvent.status must always be 'pending_confirmation' -- "
                "this module never marks its own output confirmed; see intake/__init__.py"
            )
