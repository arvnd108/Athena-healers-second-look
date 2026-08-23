# Support

## Before asking for help

1. Check `README.md` — most setup problems (Docker, FalkorDB, Python
   version, Apple Silicon build issues) are covered in its
   Troubleshooting section (§8).
2. Check `ISSUES.md` — it documents every currently known problem in the
   system and, where applicable, its candidate fix. If what you're
   hitting is already listed there, it's a known limitation, not a new
   bug.
3. Search [existing issues](../../issues) (open and closed) for your
   problem.

## Where to get help

- **Setup / usage questions:** open a GitHub issue using the "Question /
  Support" template (or the closest available template if that's not
  present yet).
- **Something is broken:** open a GitHub issue using the Bug Report
  template.
- **You want to propose or discuss a new feature:** open a GitHub issue
  using the Feature Request template, or comment on the relevant
  subsystem issue if one already covers it (see the `area:` labels).
- **Security vulnerability or suspected PHI/PII exposure:** do **not**
  open a public issue — follow `SECURITY.md` / `POLICY.md` §5.5 instead.

## What to include when asking for help

- Your OS and Python version (`python --version`).
- The exact command you ran and its full output.
- Whether `pytest` (offline, 549 tests) passes on a clean checkout —
  this isolates environment issues from code issues.
- If it's data/clinical-output related: a **synthetic or de-identified**
  example only — never real patient data (`POLICY.md` §5).

## Response times

This is a volunteer-maintained research project, not a supported
commercial product. There is no guaranteed response time. Well-scoped,
reproducible reports with the information above get resolved fastest.
