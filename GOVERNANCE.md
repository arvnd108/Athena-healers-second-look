# Governance

Athena is an early-stage open-source project. This document describes
how it is actually run today, and how that is expected to evolve — it
does not assert a more formal structure than exists yet.

## Current model: maintainer-led

At this stage, the project is maintained by a small set of maintainers
under the `healers-second-look` GitHub organization, who are responsible
for:

- Reviewing and merging pull requests.
- Triaging and labeling issues (see the label taxonomy below).
- Making final calls on architectural direction, informed by
  `ARCHITECTURE.md` and the subsystem specifications referenced from the
  issue tracker.
- Enforcing `CODE_OF_CONDUCT.md` and `POLICY.md`.

There is no formally elected steering committee or board at this time.
As the contributor base grows, this document should be revisited and
replaced with a more formal structure (e.g., defined maintainer roles
per subsystem, a documented process for adding new maintainers) — this
is intentionally left lightweight rather than pre-declaring structure
the project doesn't have yet.

## Decision-making

- **Day-to-day / implementation decisions:** made through normal GitHub
  issue and pull-request review, per `CONTRIBUTING.md`.
- **Architectural decisions:** `ARCHITECTURE.md` and the subsystem specs
  referenced from the issue tracker are the source of truth for the
  target design. A change that contradicts a stated architectural
  invariant (e.g., "the diff engine never calls an LLM") needs explicit
  maintainer sign-off and a documented rationale, not just a passing
  test suite.
- **Disagreements:** raised and resolved in the relevant GitHub issue or
  PR thread. If a technical disagreement can't be resolved there, a
  maintainer makes the final call and documents the reasoning in the
  thread.

## Subsystem ownership

The project's roadmap is organized into subsystems (A–S), each tracked
as a GitHub issue with a `feature` label, a `status:` label, and an
`area:` label. Ownership of a given subsystem's implementation is
whoever is actively working the corresponding issue — there is no
standing, exclusive ownership beyond that. Several subsystems are
explicitly flagged in their issue as curation-heavy (e.g., the guideline
knowledge base, the access-pathway registry) and benefit from a
domain-expert contributor, not only engineering review.

## Adding maintainers

There is no formal process for this yet. In practice, sustained,
high-quality contribution and review activity is how someone becomes a
maintainer; a decision to add a new maintainer is made by the existing
maintainers. This section should be replaced with a concrete process
once the project has more than a handful of regular contributors.

## Changing this document

Propose changes to governance via a pull request against this file, with
the rationale explained in the PR description. Given the current small
maintainer base, changes here are approved the same way any other PR is.
