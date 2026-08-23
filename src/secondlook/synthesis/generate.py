"""One LLM call per question, then the already-merged citation gate.

HARD RULES:
- `enforce_citations()` is not optional and not reimplemented. Every
  LLM-generated string passes through the real
  `citation_gate.enforce_citations()` before this function returns.
- Only signals whose source is `DocumentedSource` or `RegulatorySource`
  (and that actually carry a `citation_url`) become citable items.
  `ComputedMethod` and `source is None` (contextual/cohort) are real
  information but not citable-as-published-evidence per ARCHITECTURE.md
  §5 -- excluding them is deliberate, not an oversight. They are never
  sent to the LLM and can never appear in `cited_ids`.
- The prompt gets the question text and the citable items' claims --
  nothing else. No raw CaseState, no patient identifiers
  (IMPLEMENTATION_PLAN.md §7).
- The system prompt is a module-level constant: describe evidence, never
  assert a treatment recommendation, end every sentence with
  `[ref:<item_id>]`. Callers cannot override it per-call.
- `llm_used=False` covers both "flag off / client is None" and "client
  errored". A caller checking degraded-mode does not need to distinguish
  those; a two-state flag is deliberate.

The `ATHENA_LLM_ENABLED=false` path is the primary path every other path
degrades *from*: `llm_client=None` (or a caught `LLMClientError`, or an
empty citable-item set) returns a real, non-empty result built from the
citable items themselves (claim + `[ref:id]`), never an empty string
standing in for "LLM disabled".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from secondlook.case.questions import Question
from secondlook.signals.mapping import clock
from secondlook.signals.types import DocumentedSource, RegulatorySource, Signal
from secondlook.synthesis.citation_gate import enforce_citations
from secondlook.synthesis.llm_client import LLMClient, LLMClientError

# Fixed text -- not a per-call override. Mirrors the shared-constant
# disclaimer pattern used for Tier 2 structural signals.
SYNTHESIS_SYSTEM_PROMPT = (
    "You describe documented evidence for a clinical question. "
    "Describe evidence only. Never assert a treatment recommendation. "
    "End every sentence with [ref:<item_id>] using only ids supplied in "
    "the user message. Invent no ids, no claims, and no case data beyond "
    "the question text and the provided item claims."
)


@dataclass(frozen=True)
class SynthesisResult:
    text: str
    cited_ids: list[str]
    dropped_sentence_count: int
    llm_used: bool
    citable_item_count: int


def _citable_items(signals: list[Signal]) -> list[dict]:
    items: list[dict] = []
    for signal in signals:
        source = signal.source
        if isinstance(source, DocumentedSource):
            item_id = source.citation_id or source.citation_url
            url = source.citation_url
        elif isinstance(source, RegulatorySource) and source.citation_url:
            item_id = source.instrument
            url = source.citation_url
        else:
            continue
        items.append(
            {
                "citation": {"id": str(item_id), "url": url},
                "summary": signal.claim,
            }
        )
    return items


def _punctuated(claim: str) -> str:
    stripped = claim.strip()
    if stripped.endswith((".", "!", "?")):
        return stripped
    return stripped + "."


def _template_text(items: list[dict]) -> str:
    if not items:
        return "No citable evidence items were available for this question."
    parts = []
    for item in items:
        item_id = item["citation"]["id"]
        parts.append(f"{_punctuated(item['summary'])}[ref:{item_id}]")
    return " ".join(parts)


def _template_result(items: list[dict]) -> SynthesisResult:
    # Deliberately does NOT route through enforce_citations(): that gate
    # splits on any sentence-ending punctuation, including punctuation
    # *inside* one item's claim (e.g. a multi-sentence CIViC summary). A
    # claim built here carries its [ref:id] marker only once, at the very
    # end -- running a multi-sentence claim through the gate would treat
    # everything before the final period as a separate, unmarked sentence
    # and drop it, silently truncating real evidence that was never
    # actually uncited. The gate exists to catch LLM hallucination; there
    # is no LLM in this path, and every item here is cited by construction
    # (one marker per item, verified 1:1 against `items` right here) --
    # gating it would introduce a false-drop bug instead of preventing one.
    text = _template_text(items)
    return SynthesisResult(
        text=text,
        cited_ids=[str(item["citation"]["id"]) for item in items],
        dropped_sentence_count=0,
        llm_used=False,
        citable_item_count=len(items),
    )


def _user_prompt(question: Question, items: list[dict]) -> str:
    lines = [f"Question: {question.text}", "", "Citable items:"]
    for item in items:
        lines.append(f"- [{item['citation']['id']}] {item['summary']}")
    return "\n".join(lines)


def generate_synthesis(
    question: Question,
    signals: list[Signal],
    *,
    llm_client: LLMClient | None,
    now: datetime | None = None,
) -> SynthesisResult:
    clock(now)  # injectable clock, same helper as signals/mapping.py
    items = _citable_items(signals)
    if llm_client is None or not items:
        return _template_result(items)

    try:
        raw = llm_client.complete(
            _user_prompt(question, items),
            system=SYNTHESIS_SYSTEM_PROMPT,
        )
    except LLMClientError:
        return _template_result(items)

    accepted, cited_ids, dropped = enforce_citations(raw, items)
    return SynthesisResult(
        text=accepted,
        cited_ids=cited_ids,
        dropped_sentence_count=dropped,
        llm_used=True,
        citable_item_count=len(items),
    )
