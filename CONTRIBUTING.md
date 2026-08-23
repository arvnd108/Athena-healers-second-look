# Contributing to Athena

Thanks for considering a contribution. Athena is a research/clinical
decision-support prototype (see `POLICY.md` for exactly what that does
and doesn't mean) built around a two-tier evidence pipeline, with a
larger roadmap of subsystems tracked as GitHub issues. This document
covers the practical mechanics of contributing code, docs, or data
tooling.

**Read `POLICY.md` before your first contribution, especially Section 5
(patient data, PHI/PII). It is not optional reading — it governs what you
may and may not put in an issue, PR, commit, or test fixture.**

## Before you start

1. Read `README.md` for setup and `ARCHITECTURE.md` for where the
   project is headed.
2. Browse the [issue tracker](../../issues) — every planned subsystem is
   tracked there with a `feature` label, a `status:` label (`built`,
   `spec'd`, or `new`), and an `area:` label identifying which part of
   the system it belongs to. Priority-P0/P1/P2 labels flag safety- or
   adoption-critical work.
3. For anything nontrivial, comment on the relevant issue (or open one
   first) before writing code, so effort isn't duplicated and the
   approach can be checked against the subsystem's documented interface
   contract.

## Development setup

Follow `README.md` §§1–4 (Prerequisites through Run the tests). In short:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # expect "549 passed"
```

Integration tests that hit live services or FalkorDB are marked
`@pytest.mark.integration` and are deselected by default — see
`README.md` §4 and §3 for bringing up FalkorDB locally if you need them.

## Code standards

- **Python 3.11.** The project's `pyproject.toml` targets 3.11; 3.12+ is
  explicitly untested.
- **Formatting/linting:** `ruff` and `black` are declared dev
  dependencies — run them before opening a PR.
- **Tests:** new code needs test coverage in `tests/` (or
  `tests/tier1/` for Tier 1 code). Follow the existing pattern of
  offline-safe unit tests plus `@pytest.mark.integration` tests for
  anything hitting a live service — never make a live-service call a
  hard dependency of the default `pytest` run.
- **Shape-verification over status-code checks.** Following
  `civic_verify.py`'s existing pattern, any code parsing an external
  API's response should assert on parsed structure, not just a 200
  status code — this is how the project catches upstream API changes
  before they corrupt data.
- **No LLM calls in deterministic components.** The diff/assumption
  engine and similar deterministic subsystems must never call an LLM —
  this is a stated architectural invariant (`IMPLEMENTATION_PLAN.md`
  §13, referenced from the subsystem issue tracker), and a PR that
  introduces one there will be rejected regardless of how well it tests.
- **Caveats stay caveats.** Do not soften, remove, or "improve" the
  existing disclaimers around Tier 2's structural signals (see
  `POLICY.md` §2 and `ISSUES.md` §1) as part of an unrelated change.

## Commit and PR process

1. Fork the repository (or use a feature branch if you have write
   access) and create a branch named descriptively, e.g.
   `feature/access-pathway-loader` or `fix/civic-loader-retry`.
2. Keep commits focused — one logical change per commit where practical.
3. Open a pull request against `main` using the PR template. Link the
   issue it addresses (e.g., `Closes #12`).
4. Make sure `pytest` passes locally and CI is green before requesting
   review.
5. Be responsive to review feedback. For subsystem work, review may
   involve someone with domain context on that subsystem (see the
   `area:` label) rather than only a generalist reviewer.

## What to work on

- **Good first issues:** look for smaller, well-scoped items in the
  tracker, particularly around documentation, test coverage, or
  self-contained utility modules.
- **Subsystem features:** each subsystem issue lists its own Purpose,
  Interfaces, Deliverables, and Caveats — read the full issue before
  starting, and honor its stated dependencies (many subsystems build on
  the Dependency-Tracked Diff Engine and should not be started before
  it, per that issue's notes).
- **Curated data (guidelines, access pathways):** some subsystems are
  explicitly curation-heavy rather than engineering-heavy (see the
  Access Pathway Registry and Guideline-Grounded Exhaustion Assessment
  issues) and benefit from contributors with regulatory-affairs or
  clinical-informatics background, not only engineers.

## Data contributions specifically

If your contribution touches data — a new loader, a seed YAML file, a
test fixture, a gold-standard case:

- It must come from an open, redistributable source consistent with
  `docs/data-sources.md` (CIViC, PubMed, ClinicalTrials.gov, and similar
  — not NCCN, OncoKB, DrugBank, or other sources this project excludes
  for licensing reasons).
- It must contain **no real patient data of any kind** — see `POLICY.md`
  §5 before opening the PR, not after.
- New seed/config data (e.g., an access-pathway YAML) should follow the
  existing `config_version`-tracked pattern used by `civic_scope.yaml`,
  with a documented source URL for every factual claim.

## Reporting bugs vs. proposing features

- **Bug in existing behavior:** open an issue using the Bug Report
  template.
- **New subsystem or feature idea:** check whether it's already covered
  by a tracked subsystem issue first. If it's genuinely new, open an
  issue using the Feature Request template and explain which existing
  subsystem it extends or why it needs to be a new one.
- **Security or PHI/PII concern:** do **not** use a public issue — see
  `SECURITY.md` and `POLICY.md` §5.5.

## Questions

See `SUPPORT.md` for where to ask for help before assuming something is
a bug.
