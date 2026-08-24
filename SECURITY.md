# Security Policy

## Scope

This policy covers security vulnerabilities in Athena's own code
(`src/secondlook/`, the knowledge-graph loaders, and any deployment
tooling in this repository). For anything involving real patient data
exposure or suspected PHI/PII in the repository, see `POLICY.md` §5.5 —
report it with the same urgency as a vulnerability, using the same
private channel below.

## Supported versions

Athena is pre-1.0 research software (`pyproject.toml` currently declares
`1.0.0`, but the project has not been through a formal release process
or clinical/regulatory validation — see `POLICY.md` §2). There is
currently one actively maintained line: `main`. Security fixes are made
against `main`; there is no separate long-term-support branch at this
time.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security
vulnerability.**

Report it privately using GitHub's built-in private vulnerability
reporting for this repository:

1. Go to the repository's **Security** tab.
2. Select **Report a vulnerability**.
3. Provide as much detail as you can: affected component/file, steps to
   reproduce, potential impact, and any suggested fix.

If private reporting is not available to you for any reason, contact the
maintainers through the `healers-second-look` GitHub organization rather
than filing a public issue.

You should expect an initial acknowledgment within a reasonable time.
Because this is a volunteer-maintained research project rather than a
company with a dedicated security team, please be patient — but treat
that as a reason to report early, not a reason not to report.

## What counts as a security issue here

Examples relevant to Athena's actual architecture:

- Injection or unsafe query construction against FalkorDB
  (`graph_connection.py`, Cypher-building code).
- Unsafe deserialization or parsing of external API responses (CIViC,
  PubMed, ClinicalTrials.gov, RCSB, UniProt, etc.).
- Credential or API-key handling issues (e.g., a key logged, committed,
  or sent somewhere it shouldn't be).
- Any path by which real patient data could be persisted, logged, cached
  to disk, or transmitted outside the boundary a deployer expects
  (this project's own design principle is that a self-hosted
  deployment's patient data should not leave the institution by
  default — a bug that violates this is a security issue, not just a
  privacy one).
- Dependency vulnerabilities in packages this project pulls in (see
  `pyproject.toml`).
- Authentication/authorization in the API + MCP interface layer. REST
  write routes require `ATHENA_API_KEY`; MCP requires
  `ATHENA_MCP_API_KEY` only when bound beyond loopback
  (`--allow-remote`). Remaining gaps: loopback MCP and stdio are
  unauthenticated by design; there is no token rotation, expiry, or
  per-tool authorization; a read-only MCP tool must still not be usable
  to write clinical decisions (enforced by the `ReadOnlyCaseView` type
  boundary, not the transport-layer gate).

## What is out of scope

- The known, pre-committed validation failures already documented in
  `ISSUES.md` and `docs/validation-plan.md` (e.g., binding-affinity
  prediction not meeting its threshold) — these are documented
  scientific/statistical limitations, not vulnerabilities. See
  `POLICY.md` §2.
- Missing features tracked as roadmap subsystems in the issue tracker.

## Disclosure

Please give maintainers a reasonable opportunity to investigate and
address a reported vulnerability before any public disclosure. Once a
fix is available, the maintainers will coordinate with the reporter on
disclosure timing and credit (if desired).
