"""Chat engine -- the one function the API route calls (Phases 1-6).

`run_turn` coordinates:
- Entity extraction (via plugins / variant-normalizer)
- Knowledge graph facts (Phase 4)
- Live FalkorDB retrieval grounding (Phase 6)
- Plugin transformations (Phase 3)
- Model execution (Phase 2)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from secondlook.chat.knowledge import describe_context, retrieve_evidence_for_turn
from secondlook.chat.models import CONTEXT_MARKER, DEFAULT_MODEL_ID, build_client
from secondlook.chat.plugins import Turn, apply_attachments

DEFAULT_SYSTEM = (
    "You are Athena, a clinical evidence synthesis assistant. You ground "
    "every claim in retrieved sources and cite them using bracketed indices "
    "like [1], [2]. Never fabricate citations. When no source exists for a "
    "claim, you state that explicitly."
)


@dataclass
class TurnResult:
    """What the API sends back for one chat turn."""

    id: str
    role: str  # "assistant"
    content: str
    model_id: str
    timestamp: float
    entities: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    context_lines: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    sources_count: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def build_prompt(question: str, context_lines: list[str]) -> str:
    """Assemble the prompt the model actually sees."""
    parts = [question]
    if context_lines:
        parts.append("")
        parts.append(CONTEXT_MARKER)
        for line in context_lines:
            parts.append(f"- {line}")
    return "\n".join(parts)


def run_turn(
    message: str,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    attachment_ids: list[str] | None = None,
    context_id: str | None = None,
    system: str | None = None,
) -> TurnResult:
    """Execute one chat turn end-to-end (Phases 1-6)."""
    turn = Turn(
        message=message,
        system_prompt=system or DEFAULT_SYSTEM,
    )

    # Phase 3 attachments pre-processing (e.g. variant-normalizer extracts entities)
    if attachment_ids:
        apply_attachments(turn, attachment_ids)

    # Phase 4: KG context facts
    if context_id:
        kg_lines = describe_context(context_id)
        turn.context_lines.extend(kg_lines)

    # Phase 6: Live FalkorDB evidence retrieval
    retrieved_sources = retrieve_evidence_for_turn(
        entities=turn.entities,
        context_id=context_id,
        limit=turn.max_sources,
    )
    turn.sources = retrieved_sources

    # Inject retrieved sources into context lines with numbered citation markers
    for src in retrieved_sources:
        idx = src["citation_index"]
        level = src.get("evidence_level", "B")
        pmid = src.get("pmid", "N/A")
        title = src.get("title", "")
        summary = src.get("summary", "")
        url = src.get("citation_url", "")
        turn.context_lines.append(
            f"[{idx}] (Level {level}, PMID {pmid}) {title}: {summary} [Ref: {url}]"
        )

    # Re-apply citation-guard if attached now that sources are loaded
    if attachment_ids and "citation-guard" in attachment_ids:
        if turn.sources:
            turn.notes.append(f"retrieval attached {len(turn.sources)} live CIViC source(s)")

    # Build prompt and call model
    prompt = build_prompt(turn.message, turn.context_lines)
    client = build_client(model_id)
    content = client.complete(prompt, system=turn.system_prompt)

    return TurnResult(
        id=str(uuid.uuid4()),
        role="assistant",
        content=content,
        model_id=model_id,
        timestamp=time.time(),
        entities=turn.entities,
        notes=turn.notes,
        context_lines=turn.context_lines,
        sources=turn.sources,
        sources_count=len(turn.sources),
    )


__all__ = ["DEFAULT_SYSTEM", "TurnResult", "build_prompt", "run_turn"]
