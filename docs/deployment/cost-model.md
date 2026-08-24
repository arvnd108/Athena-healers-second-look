# Cost Model Worksheet

Two genuinely different kinds of numbers live in this document, and
they're kept visually separate on purpose:

- **LLM API pricing** — pulled directly from Anthropic's own pricing
  documentation on 2026-08-24, exact and sourced.
- **Server/hosting cost** — deliberately left as a worksheet with your
  own numbers, not a single asserted figure. Server pricing varies by
  provider, region, and whether you're using existing hardware a hospital
  already owns (a very real option for the target deployment context —
  see `Concept.md` §8) versus renting cloud compute. Any single number
  here would be more confident-sounding than accurate.

## Part 1 — Fixed infrastructure cost

Fill in your own figures; the two reference points below are
**illustrative market ranges, not a live quote** — check current pricing
from your actual provider before budgeting against this.

| Item | Your cost | Notes |
|---|---|---|
| Server (small: 2 vCPU/4GB/20GB) | $ ___ /month | Or $0 if using existing hospital hardware — a legitimate option this project is explicitly sized for |
| Server (medium: 4–8 vCPU/8–16GB/50–100GB) | $ ___ /month | See `hardware-sizing.md` — this tier's sizing is directional, not load-tested |
| Backup storage | $ ___ /month | Postgres (case memory) is the data that actually matters to back up; FalkorDB's evidence graph is re-derivable from public sources |
| Domain + TLS certificate | $ ___ /year | Let's Encrypt makes the certificate itself free; a domain name typically isn't |
| **Total fixed monthly cost** | **$ ___** | |

Illustrative reference (small budget-VPS-class providers, general market
range, not a specific verified quote): roughly $20–40/month for a small
instance, $80–160/month for a medium one, as of general 2026 market
conditions. Verify against an actual provider before treating these as
real numbers.

## Part 2 — Marginal LLM cost per case

This part is precisely sourced: current Anthropic API pricing, fetched
2026-08-24 from `platform.claude.com/docs/en/about-claude/pricing`.

Athena's default model (`DEFAULT_ANTHROPIC_MODEL` in
`src/secondlook/synthesis/llm_client.py`) is **Claude Sonnet 5**:

| | Price |
|---|---|
| Input | $2.00 / million tokens |
| Output | $10.00 / million tokens |

`AnthropicClient.complete()` caps `max_tokens=1024` per call — verified
directly in `llm_client.py`, not assumed. Combined with
`synthesis/generate.py`'s documented design ("one LLM call per question,"
prompt containing only the question text and citable items' claims — no
raw case data), a single synthesis call's cost ceiling is:

```
worst case:  1,024 output tokens × $10.00/M = $0.01024
           + input tokens (question + citable claims — typically a few
             hundred to low thousands of tokens; not measured against a
             real production trace in this pass) × $2.00/M
           ≈ $0.01–0.02 per synthesis call, most of it capped by the
             1024-token output limit regardless of input size
```

**This is a per-question cost, not a per-patient-case cost** — a case
may generate several questions over its lifetime as new data or evidence
arrives (that's the whole point of the diff engine). A rough case-level
estimate: **5–10 synthesis calls per case ≈ $0.05–0.20/case in LLM
cost**, assuming the 5-question estimate holds — this multiplier is a
reasonable guess based on the architecture, not a measured average
across real cases.

**Cheaper alternative, same architecture:** swapping `ATHENA_LLM_MODEL`
to Claude Haiku 4.5 ($1.00/M input, $5.00/M output — roughly half the
cost) requires no code change, just an environment variable — see
`.env.example`. Whether Haiku's synthesis quality is adequate for this
use case is unvalidated (the same caveat `llm_client.py`'s own docstring
states about model choice generally) — a quality/cost tradeoff decision
for whoever operates a real deployment, not one this document makes for
you.

**Free alternative:** set `ATHENA_LLM_PROVIDER=openai_compatible` and
point `ATHENA_LLM_BASE_URL` at a self-hosted model (vLLM, Ollama,
text-generation-inference — see `OpenAICompatibleClient` in
`llm_client.py`). Zero marginal per-call cost, but requires
GPU-capable hardware to run the model itself, which is a real capital
cost this worksheet doesn't size (highly dependent on which open-weight
model you choose). Setting `ATHENA_LLM_ENABLED=false` disables the
synthesis feature entirely — retrieval-only mode, genuinely $0 marginal
LLM cost, no external network call.

## Part 3 — Putting it together

```
Monthly total ≈ Part 1's fixed infrastructure cost
              + (cases handled per month × ~$0.05–0.20 LLM cost/case,
                 or $0 if running self-hosted/disabled)
```

For a small deployment (Part 1 ≈ $20–40/month, or $0 on existing
hardware) handling, say, 50 cases/month with hosted Claude Sonnet 5
synthesis: **roughly $20–50/month total**, the large majority of it
fixed infrastructure rather than LLM usage — consistent with the
project's own design principle (`README.md`, `POLICY.md` §4) that LLM
calls are deliberately bounded and retrieval does the expensive-looking
work deterministically instead.

## What would make this worksheet stronger

- A real measured average of questions-per-case and tokens-per-call from
  actual usage, not the architectural estimate used above
- A verified, dated quote from at least one specific hosting provider,
  not an illustrative range
- Cost of running a self-hosted open-weight model at the GPU tier needed
  for adequate synthesis quality, once that's validated (tracked as an
  open question in issue #10, Synthesis & Question Generation)
