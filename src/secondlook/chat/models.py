"""Model registry for the chat surface -- issue #103, Phase 2.

Every model a session can select resolves through the SAME `LLMClient`
Protocol synthesis already uses (`secondlook.synthesis.llm_client`): one
`complete(prompt, system=...) -> str` method. `engine.py` asks this module
for a client and calls it. It never branches on which model was chosen.
That is precisely Phase 2's "one common interface, not a per-model special
case in the request handler".

The two `mock-*` models are not filler. They are the offline proof that
the abstraction is real: same interface, same prompt, visibly different
output. They also let the whole chat surface run -- and be tested -- with
no API key and no network, which is the same posture
`ATHENA_LLM_ENABLED=false` gives the synthesis path.

HARD RULE inherited from llm_client.py: no open-weight model is named or
recommended as a default here. `openai-compatible` is whatever
ATHENA_LLM_BASE_URL is serving; this module does not know or care.
"""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass

from secondlook.synthesis.llm_client import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicClient,
    LLMClient,
    LLMClientError,
    OpenAICompatibleClient,
)


@dataclass(frozen=True)
class ModelSpec:
    """What the picker shows and what the router needs to build a client.

    `available` is computed from configuration, never assumed. A model the
    deployment cannot actually reach is still listed -- greyed out with a
    reason -- rather than hidden, so a user who expects Claude and does not
    see it gets an explanation instead of a silently shorter list.
    """

    id: str
    label: str
    provider: str
    description: str
    available: bool
    unavailable_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "description": self.description,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


class MockOutlineClient:
    """Deterministic. Answers in structured sections with source counts.

    Reads the prompt it is handed rather than ignoring it, so attaching a
    retrieval source or a knowledge graph visibly changes what comes back
    -- which is what makes it usable as evidence for Phases 3, 4 and 6
    instead of a decorative stub.
    """

    model = "mock-outline"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        question, context_lines = _split_prompt(prompt)
        parts = [f"## On: {question}", ""]
        if context_lines:
            parts.append(f"### What the {len(context_lines)} attached source(s) say")
            parts.extend(f"- {line}" for line in context_lines)
        else:
            parts.append("### No sources attached")
            parts.append(
                "- Nothing was retrieved for this turn. Attach a retrieval source "
                "or a knowledge graph to ground an answer in real evidence."
            )
        parts += [
            "",
            "### Caveats",
            "- Deterministic offline model. It restates retrieved context; it does "
            "not reason over it and must not be read as clinical advice.",
        ]
        return "\n".join(parts)


class MockTerseClient:
    """Deterministic. Same inputs as MockOutlineClient, deliberately
    different shape: a couple of prose sentences, no headings.

    Phase 2 asks for two models that produce *visibly* different output
    from the same request. Outline-vs-prose is that difference, and it is
    visible in a screenshot without squinting at token counts.
    """

    model = "mock-terse"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        question, context_lines = _split_prompt(prompt)
        if not context_lines:
            return (
                f"No grounded answer available for {question!r}: zero sources were "
                "attached to this turn. Attach CIViC, literature, or a knowledge "
                "graph and ask again."
            )
        head = context_lines[0]
        rest = len(context_lines) - 1
        tail = f" {rest} further source(s) were retrieved." if rest else ""
        return textwrap.fill(
            f"Across {len(context_lines)} retrieved source(s) for {question!r}, the "
            f"strongest is: {head}.{tail} Deterministic offline model -- restated "
            "evidence, not clinical advice.",
            width=100,
        )


CONTEXT_MARKER = "### Retrieved context"


def _split_prompt(prompt: str) -> tuple[str, list[str]]:
    """Pull the question and the retrieved-context bullets back apart.

    `engine.build_prompt` is the only writer of this format, so the two
    live or die together; `tests/chat/test_models.py` pins the round trip
    so a change to one that forgets the other fails loudly rather than
    quietly producing sourceless answers.
    """
    question, _, remainder = prompt.partition(CONTEXT_MARKER)
    lines = [
        line.strip().lstrip("- ").strip()
        for line in remainder.splitlines()
        if line.strip().startswith("-")
    ]
    return question.strip().splitlines()[-1].strip() if question.strip() else "", lines


def _anthropic_available() -> tuple[bool, str | None]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, None
    return False, "ANTHROPIC_API_KEY is not set in this deployment."


def _openai_compatible_available() -> tuple[bool, str | None]:
    missing = [
        name for name in ("ATHENA_LLM_BASE_URL", "ATHENA_LLM_MODEL") if not os.environ.get(name)
    ]
    if missing:
        return False, f"{' and '.join(missing)} not set in this deployment."
    return True, None


def list_models() -> list[ModelSpec]:
    """Every model this deployment can offer, availability computed live.

    Not cached: an operator who exports ANTHROPIC_API_KEY and restarts
    uvicorn --reload should see Claude become selectable without a code
    change, and a test that monkeypatches the env should see it too.
    """
    anthropic_ok, anthropic_why = _anthropic_available()
    openai_ok, openai_why = _openai_compatible_available()
    return [
        ModelSpec(
            id="mock-outline",
            label="Athena Outline (offline)",
            provider="mock",
            description="Deterministic. Structured sections over retrieved sources.",
            available=True,
        ),
        ModelSpec(
            id="mock-terse",
            label="Athena Terse (offline)",
            provider="mock",
            description="Deterministic. Two sentences of prose, same inputs.",
            available=True,
        ),
        ModelSpec(
            id="anthropic",
            label=f"Anthropic ({os.environ.get('ATHENA_LLM_MODEL') or DEFAULT_ANTHROPIC_MODEL})",
            provider="anthropic",
            description="Hosted Claude via the Anthropic API.",
            available=anthropic_ok,
            unavailable_reason=anthropic_why,
        ),
        ModelSpec(
            id="openai-compatible",
            label=f"Self-hosted ({os.environ.get('ATHENA_LLM_MODEL') or 'unconfigured'})",
            provider="openai_compatible",
            description=(
                "Any server speaking OpenAI chat-completions -- vLLM, Ollama, TGI. "
                "Which weights are behind the URL is a deployment choice."
            ),
            available=openai_ok,
            unavailable_reason=openai_why,
        ),
    ]


DEFAULT_MODEL_ID = "mock-outline"


def get_model_spec(model_id: str) -> ModelSpec | None:
    return next((spec for spec in list_models() if spec.id == model_id), None)


def build_client(model_id: str) -> LLMClient:
    """model id -> an `LLMClient`. The only place model choice is decoded.

    Raises `LLMClientError` for an unknown or unconfigured id rather than
    silently falling back to a mock: a session that believes it is talking
    to Claude and is quietly served a deterministic stub would make every
    screenshot taken of it a lie.
    """
    spec = get_model_spec(model_id)
    if spec is None:
        known = ", ".join(s.id for s in list_models())
        raise LLMClientError(f"unknown model {model_id!r}; known models are {known}")
    if not spec.available:
        raise LLMClientError(f"model {model_id!r} is not configured: {spec.unavailable_reason}")
    if spec.provider == "mock":
        return MockOutlineClient() if model_id == "mock-outline" else MockTerseClient()
    if spec.provider == "anthropic":
        return AnthropicClient()
    return OpenAICompatibleClient()


__all__ = [
    "CONTEXT_MARKER",
    "DEFAULT_MODEL_ID",
    "ModelSpec",
    "MockOutlineClient",
    "MockTerseClient",
    "build_client",
    "get_model_spec",
    "list_models",
]
