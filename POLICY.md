# Athena Responsible Use, Data Handling & Clinical Safety Policy

This document is the single source of truth for what Athena is (and is
not) safe to use for, what data may and may not go anywhere near this
repository, and what every contributor and deployer must uphold. It
consolidates and makes binding the principles already stated across
`README.md`, `ARCHITECTURE.md`, `ISSUES.md`, and `docs/validation-plan.md`
— it does not introduce new claims about the system's accuracy or
capabilities beyond what those documents already establish.

If anything in this file appears to conflict with a claim made elsewhere
in the repository, this file's framing of limitations and prohibitions
takes precedence, because it exists specifically to prevent overclaiming.

---

## 1. What Athena is

Athena (SecondLook) is a **research and clinical decision-support
prototype**. Given a patient's gene, mutation, and cancer type, it:

- Searches a curated knowledge graph (CIViC + PubMed) for documented
  clinical evidence (Tier 1).
- Optionally computes a structural signal — binding-site proximity —
  when no strong documented evidence exists (Tier 2).

Every output is designed to carry its citation or its computational
method, per the project's stated rule: *"Never state a medical fact that
was not retrieved or computed in a traceable step."*

## 2. What Athena is explicitly not

- **Athena is not a diagnostic tool.**
- **Athena is not a treatment recommender.** It surfaces evidence and
  computational signals for a qualified clinician to weigh — it does not
  decide a course of treatment.
- **Athena is not a substitute for a licensed oncologist, tumor board, or
  other qualified medical professional**, and nothing it outputs should
  be treated as a final clinical decision or acted upon without
  qualified human clinical judgment.
- **Athena is not a validated binding-affinity or drug-response
  predictor.** Per `ISSUES.md` §1 and `docs/validation-plan.md`, the
  binding-change prediction path failed pre-committed validation (2/9
  cases), and its documented fallback (binding-site proximity) also
  failed its own pre-committed criterion (7/8 non-ambiguous cases).
  These are honestly reported, pre-committed evaluation outcomes, not
  bugs to hide — and they mean any structural ("Tier 2") output must be
  read as a caveated measurement, never a prediction of drug efficacy.
- **Athena has not undergone clinical validation, regulatory review, or
  approval of any kind** (e.g., not FDA-cleared, not a registered medical
  device) as of this writing. It must not be described or deployed as if
  it has.
- **Athena's structural signals do not establish mechanism.** A measured
  proximity reflects "whatever ligand was co-crystallized" in the source
  structure, not necessarily the candidate drug in question — proximity
  is not proof of drug-specific binding.

## 3. Who Athena is for, and how it should be used

Athena is built for **research use and for qualified clinicians** as a
second-opinion research aid on rare or treatment-exhausted cases —
explicitly targeting settings (per `ARCHITECTURE.md` and the project's
broader design docs) without ready access to a molecular tumor board.

Appropriate use:
- A clinician or researcher using Athena's output as one input among many
  in their own clinical reasoning process.
- Research into evidence-graph retrieval, structural signal computation,
  or clinical decision-support system design.
- Educational exploration of how the pipeline works, using the same
  gold-standard/synthetic cases already checked into `validation/`.

Inappropriate use:
- Presenting Athena's output directly to a patient as medical advice.
- Using Athena as the sole basis for a treatment decision.
- Running Athena against real patient data without the safeguards in
  Section 5 below, and without institutional oversight appropriate to
  that setting (e.g., IRB/ethics review where applicable).
- Describing Athena, in any public-facing material, as validated,
  approved, or clinically proven beyond what Sections 1–2 state.

## 4. Responsible AI principles this project holds itself to

These are structural commitments, not aspirations — see `ARCHITECTURE.md`
for the enforcement mechanisms behind each:

1. **No evidence, no claim.** A signal with `kind="documented_evidence"`
   and no source citation is never constructed. Filtered-out items are
   counted and reported, never silently dropped.
2. **Computed is never dressed up as documented.** Computational signals
   carry a method and a disclaimer, never a citation-shaped field.
3. **Failures are reported, not hidden.** Pre-committed validation
   criteria are fixed before a run and never adjusted after seeing
   results (`docs/validation-plan.md`). A result that falls below
   threshold is a documented outcome, not something to work around by
   moving the bar.
4. **Minimal, bounded LLM use.** Where LLMs are used (or proposed in
   roadmap subsystems — see the issue tracker), they are constrained to
   retrieval-grounded, citation-checked generation, never open-ended
   clinical reasoning presented as fact.
5. **Tooling limitations are never reported as scientific results.** An
   environment/dependency failure (e.g., a Docker or upstream API outage)
   must not be misdiagnosed or presented as a finding about the method.

## 5. Patient data, PHI/PII, and de-identification

**This is the most important section of this document. Read it before
opening any issue, pull request, or discussion that involves anything
resembling real patient information.**

### 5.1 What must never be committed to this repository, in any form

Never commit, paste into an issue/PR/discussion, or otherwise upload to
this repository (including in code comments, test fixtures, log output,
screenshots, or attached files):

- Real patient names, dates of birth, medical record numbers, or any
  other direct identifier.
- Real pathology reports, sequencing/NGS reports, clinical notes, or
  imaging — even "just as an example," even redacted by hand, even from
  a case you have full authority over. Manual redaction reliably misses
  identifiers; the safe rule is **do not upload the source document at
  all.**
- Any dataset, export, or screenshot that could allow re-identification
  of a real, specific patient — including small-cohort data where the
  combination of rare cancer type + demographic details is itself
  identifying.
- Real institutional data (a specific hospital's case logs, EHR exports,
  etc.) without that institution's explicit written authorization *and*
  a documented, approved de-identification process — and even then, this
  belongs in a private, access-controlled system, not this public
  open-source repository.

If you are unsure whether something counts as PHI/PII, **treat it as
PHI/PII and do not upload it.** Ask a maintainer first via a private
channel (Section 7), not by posting the data itself to ask "is this
okay?"

### 5.2 What is safe to use

- **Synthetic or clearly fictional cases**, explicitly labeled as such
  (e.g., "Patient X, synthetic case for testing subsystem F").
- **Published, already-public data** already used by this project's
  gold-standard validation set (`validation/`) — genes, variants, and
  drug names drawn from CIViC, PubMed, and similar open sources, which
  are aggregate scientific facts, not patient records.
- **De-identified, aggregate statistics** from a real dataset, with the
  de-identification method stated, and only when you have clear
  authority to share that aggregate (e.g., a published paper's summary
  numbers).

### 5.3 Consent

Any work that involves real patient-derived data at any stage (even
upstream of what touches this repository — e.g., a connected deployment's
database) must have documented patient consent (or an applicable
regulatory basis, such as IRB-approved secondary use) appropriate to the
jurisdiction and institution involved, *before* that data is used. This
repository does not, and cannot, provide that consent process — it is the
responsibility of whoever operates a real deployment against real
patients, entirely outside the scope of the open-source codebase itself.

### 5.4 Schema-level enforcement

Per the project's own design principles (see the issue tracker's Case
Memory Store and Governance features), any patient-record schema
introduced into this codebase must exclude direct identifiers (name,
date of birth, MRN) by construction, not by convention — enforced with a
schema-level test that fails CI if a disallowed column is added. If
you're contributing schema or storage code, this is a hard requirement,
not a suggestion.

### 5.5 If you discover PHI/PII already in the repository

If you find what looks like real patient data anywhere in this
repository's history (including old commits — deleting a file does not
remove it from git history), **do not open a public issue describing
it.** Report it privately per Section 7 immediately. A maintainer will
need to assess whether history rewriting and a rotation of any exposed
downstream credentials is required.

## 6. Data sourcing and licensing constraints

Per `docs/data-sources.md` and `ARCHITECTURE.md`, this project only uses
open or free evidence sources (CIViC, PubMed, ClinicalTrials.gov) in any
shipped path. Contributions must not introduce:

- Licensed datasets excluded project-wide for licensing reasons (e.g.,
  NCCN, OncoKB, DrugBank) into any code path that ships by default.
- Data whose license prohibits redistribution or commercial use, without
  clearly gating it behind an explicit, documented opt-in and preserving
  the license's attribution/use restrictions in full (see `ISSUES.md`
  §17 for the existing precedent with MdrDB).

## 7. Reporting a concern

- **Security vulnerabilities:** see `SECURITY.md`.
- **Suspected PHI/PII exposure:** report privately and immediately as
  described in Section 5.5 — treat this with at least the urgency of a
  security vulnerability, not as a normal bug report.
- **Code of Conduct violations:** see `CODE_OF_CONDUCT.md`.
- **Concerns about a specific clinical claim or output** the system
  produces: open a normal GitHub issue, tag it for the relevant
  subsystem, and describe the case in de-identified/synthetic terms only.

## 8. For anyone deploying Athena, not just contributing code

If you are standing up a real Athena deployment against real patients,
you are responsible for satisfying — independently of this repository —
whatever regulatory, ethical, institutional, and legal requirements apply
in your jurisdiction and setting (data protection law, IRB/ethics
approval, clinical governance, informed consent, and any medical-device
or clinical-software regulation that may apply to your specific use).
Nothing in this repository constitutes legal, regulatory, or clinical
advice, and the maintainers make no representation that using Athena
satisfies any such requirement.
