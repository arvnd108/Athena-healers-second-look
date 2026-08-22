# SecondLook — AI Second-Opinion Copilot for Rare & Treatment-Exhausted Cancers

India-localized extension of RareCure (open-source, MIT-licensed, peer-reviewed pipeline). Gives oncologists a fast, evidence-ranked second opinion for cases where standard-of-care has failed or the mutation is undocumented.

**Read [`../ARCHITECTURE.md`](../ARCHITECTURE.md) first.** It's the target design and supersedes the tier framing below as the direction of record; everything in this folder describes the tier-based system as currently implemented, which the code still matches today.

## Doc index

| File | Covers |
|---|---|
| `architecture.md` | Full system architecture of the *current* implementation, both tiers, data flow, module boundaries |
| `tier1-retrieval.md` | Evidence-retrieval tier: knowledge graph + literature RAG spec |
| `tier2-structural-prediction.md` | Structural drug-response prediction tier spec (build-ready, and what the code implements) |
| `data-sources.md` | Every external API/DB used: endpoints, auth, rate limits, licensing |
| `api-contracts.md` | Internal request/response schemas between frontend, backend, and each module |
| `ui-flow.md` | Doctor-facing UX: intake form → results screen, field-by-field |
| `validation-plan.md` | Gold-standard test cases and pass/fail criteria, including the proximity-fallback criterion |
| `tech-stack-setup.md` | Stack choices, install/setup steps, environment variables |
| `local-setup.md` | macOS/Apple Silicon install hurdles: psycopg2, Playwright, the patched vina source build |
| `secondlook_tier_2_audit.md` | External scientific/engineering audit of Tier 2 — see its status banner for what's since landed |
| `rarecure-build-reference.md` | Patterns taken from, and failure modes avoided from, the RareCure prototype |
| `research/` | Source material behind the design decisions above, including the founder-mode use case `../ARCHITECTURE.md` is built from |

Every known problem and its fix lives in [`../ISSUES.md`](../ISSUES.md) at the repo root.

## Non-negotiable system-wide rule

**The LLM never states a medical fact it did not retrieve or compute in a traceable step.** Every output — Tier 1 or Tier 2 — carries a source (database ID, paper citation, or named computational method + result). This rule is enforced in the API contract (see `api-contracts.md`) and the UI (see `ui-flow.md`), not just in a prompt — every response object requires a non-empty `evidence` field or it must be rejected before reaching the UI.

## Status

The build order this section used to describe (tech stack → data sources →
Tier 1 → UI → Tier 2 → validation) is done — see the repo-root `README.md`
for how to run what exists. What's actually left is tracked in two places:
`../ISSUES.md` for known problems in the current implementation, and
`../ARCHITECTURE.md` §7 for the build order of the *new* work (access-pathway
registry, modality mapping, transparent synthesis, etc.) that doesn't exist
yet.

## Scope discipline

V1 = Tier 1 fully working + Tier 2's core path (AlphaMissense + mCSM-lig) on missense mutations only, demoed on pre-validated cases. Everything else in the specs marked "stretch" stays out of V1 unless V1 is done early. Both tiers are now done to that scope; the "stretch" items are what `ARCHITECTURE.md` picks back up.
