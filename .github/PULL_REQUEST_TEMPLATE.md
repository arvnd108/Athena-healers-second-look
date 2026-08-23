## What does this PR do?

<!-- Describe the change. Link the issue it addresses, e.g. "Closes #12". -->

## Which subsystem(s) does this touch?

<!-- Reference the relevant issue/area label(s), e.g. area: diff-engine -->

## Checklist

- [ ] `pytest` passes locally (offline suite, expect "549 passed" plus
      any tests this PR adds).
- [ ] `ruff` / `black` run clean.
- [ ] New code has test coverage in `tests/` (or `tests/tier1/`).
- [ ] I have not softened, removed, or bypassed any existing
      validation/caveat language (e.g., Tier 2's binding-affinity
      disclaimers) as part of this change.
- [ ] If this touches a deterministic component (e.g., the diff/
      assumption engine), it introduces **no LLM calls** — see
      `CONTRIBUTING.md`.
- [ ] If this adds/changes a data source, it's consistent with
      `docs/data-sources.md` (open/free sources only in any shipped
      path).
- [ ] This PR contains **no real patient data, PHI, or PII** — in code,
      tests, fixtures, commit messages, or this description. See
      `POLICY.md` §5.
- [ ] Docs updated if this changes documented behavior (`README.md`,
      `ARCHITECTURE.md`, or the relevant subsystem issue).

## Anything reviewers should pay special attention to?
