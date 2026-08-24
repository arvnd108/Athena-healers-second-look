# Trial criteria extraction — pre-committed validation criteria

**Written before any accuracy number was measured.** Criteria here are fixed and
must not be adjusted after seeing results, following the same rule
`docs/validation-plan.md` applies to the structural pipeline. Falling below a
threshold is a documented outcome with a defined fallback, not something to fix
by moving the bar.

## Why this document exists

The Subsystem F issue states the requirement directly:

> Criteria extraction accuracy is unvalidated until a spot-check harness exists
> — **do not ship the bucketing as authoritative without a documented
> sample-accuracy check.**

The risk is specific. A matcher that buckets a patient `highly_compatible` on a
misread criterion sends someone to a trial that will turn them away. A matcher
that buckets them `probably_incompatible` on a misread criterion hides a trial
they could have joined. Neither failure announces itself: both produce
confident, plausible output.

## What is measured

Extraction is scored **per line**, against a hand-annotated corpus of real
ClinicalTrials.gov eligibility sections in `tests/trials/fixtures/`.

For each line the annotation records the predicate type a careful human reader
says it is. The extractor's output for that line is then one of:

| Outcome | Meaning |
|---|---|
| **correct** | extracted type equals the annotated type |
| **missed** | annotated as a real type, extracted as `UNPARSEABLE` |
| **wrong** | extracted a real type that is not the annotated one |

These are deliberately not collapsed into one accuracy figure, because they
carry very different costs.

## Criteria

### C1 — No wrong extractions (hard requirement)

**`wrong` must be 0.**

A `missed` line becomes an `UNPARSEABLE` predicate, which the matcher resolves
to `needs_verification` — the honest answer, and a human reads the criterion. A
`wrong` line becomes a confidently evaluated predicate that is about the wrong
thing, and it buckets a patient with no signal that anything went awry.

This is the only criterion with a zero tolerance, and it is deliberate: the
system is allowed to not know, and is not allowed to be confidently wrong.

### C2 — Recall on the regular types

**At least 70% of lines annotated `AGE_RANGE` or `ECOG_MAX` are extracted
correctly.**

These two are the genuinely regular patterns in registry prose. If the
rule-based extractor cannot reach 70% on them, rules are the wrong tool and the
LLM-assisted path is required rather than optional.

70% is chosen as the same threshold `validation-plan.md` uses, for consistency
rather than from any property of this task.

### C3 — Nothing is silently dropped

**Every non-empty line in the source text produces exactly one predicate.**

`lines_seen` must equal `len(predicates)`. A dropped criterion reads downstream
as a criterion the patient satisfies.

### C4 — Section attribution

**Every line inside an inclusion or exclusion block is attributed to that
section.**

Inclusion and exclusion invert the meaning of the same sentence: "prior
anthracycline therapy" is a requirement under one and a disqualifier under the
other. A line attributed to `unknown` when the source made it plain is a
correctness failure, not a formatting one.

## Corpus

Target is **~30 real eligibility sections**, per the issue. Fixtures are real
registry text, not written for the test — the failure modes that matter are the
ones real sponsors produce.

**Status: 34 sections, 177 annotated lines.** Fetched from the
ClinicalTrials.gov v2 API across the sarcoma conditions in
`src/secondlook/tier1/config/civic_scope.yaml`.

### How the annotated lines were chosen

Each fixture stores its **complete** eligibility section, so C3 — which compares
`lines_seen` to `len(predicates)` — sees all 1,017 lines in the corpus.

C1, C2 and C4 score only lines carrying an annotation, and those are a
**seeded random sample of up to 5 lines per section** (`random.Random(20260824)`,
sampling indices from `split_criteria` output). Hand-annotating all 1,017 lines
to the standard C1 demands was not feasible; annotating whichever lines looked
tractable would have been worse than a smaller corpus, because it selects
against exactly the prose the extractor handles badly. A seeded sample is
unbiased across sponsors and section lengths, and reproducible.

The consequence is a real limit on precision: with 177 annotated lines, a single
wrong extraction is 0.6% of the sample. Treat C2's rate as having roughly that
granularity.

Annotations were read by hand against the source text. They were **not** taken
from extractor output, and were fixed before the extractor was run against them.

### The annotations have not been clinically reviewed

C1's whole premise is that "the predicate type a careful human reader says it
is" is the ground truth. That makes the reader part of the instrument, and this
corpus's reader **is not a clinician**.

The distinction matters because several judgement calls in here are genuinely
arguable and were made by one person with no oncology training:

- `Known distant metastases` is annotated `UNPARSEABLE` rather than
  `DISEASE_STAGE_REQUIRES`, on the rule that a stage is only recorded when the
  source names one. A clinician might reasonably read metastatic status *as* a
  stage constraint, and 70% recall thresholds move on decisions like that.
- Lines requiring a washout from a named agent are annotated
  `PRIOR_THERAPY_EXCLUDES` even when they sit in an inclusion section, because
  it is the only prior-therapy type in the schema.
- `Palliative radiotherapy within 2 weeks of study entry` is annotated as prior
  therapy, treating a modality as an agent.

None of these is obviously wrong. None is obviously right either, and the
corpus silently inherits whichever way they went.

**Before these figures are presented as validation to anyone outside the team,
a clinical reviewer should spot-check a sample of the annotations.** A
disagreement rate from that check is worth more than another twenty fixtures
annotated the same way, because it measures the instrument rather than the
thing being instrumented. Until that happens, C1 through C4 evidence that the
extractor agrees with *this* corpus — not that either is clinically correct.

## Measured results

First run against the expanded corpus, before any fix:

| | |
|---|---|
| C1 no wrong extractions | **FAIL** — 4 wrong |
| C2 recall on AGE_RANGE / ECOG_MAX | PASS — 13/16 |
| C3 nothing silently dropped | PASS |
| C4 section attribution | **FAIL** — 2 lines |

Per the rule at the top of this document the thresholds were not touched. Three
defects in the extractor were fixed instead:

1. **`NON-INCLUSION CRITERIA` matched no header.** NCT04055220 heads its
   exclusions that way, so all of them stayed attributed to the inclusion header
   above — inverting every one. This is the failure C4 exists to catch.
2. **A bare `[0-4]` anywhere on the line became an ECOG cap.** On NCT00004157 it
   matched the `2` of a bilirubin limit of `2.5` and capped ECOG at 2. The value
   is now sought only within 40 characters of the words that name it.
3. **An age keying a laboratory table read as an eligibility bound.**
   NCT03320330 and NCT03213652 tabulate creatinine limits by age band; read as
   "must be 16 or older" that excludes younger patients the trial accepts.
   NCT01692496 states an age inside a definition of post-menopausal status.

After the fixes: **C1 PASS (0 wrong), C2 PASS (12/16), C3 PASS, C4 PASS.**

C2 fell from 13/16 to 12/16. The lost line is NCT00004157's, a legacy record
whose entire patient-characteristics section is a single line fusing an age
bound with a dozen laboratory limits; it is now `missed` rather than wrong. That
is the intended direction — `missed` routes to `needs_verification` and a human
reads the criterion.

## What passing does and does not license

Passing means extraction is accurate enough on this corpus that the
`needs_verification` bucket reflects genuine data gaps rather than parser
failures.

It does **not** license presenting `highly_compatible` as a clinical
recommendation. Bucketing is a triage aid over registry text; it does not read
the protocol, does not know the site's current slots, and cannot see anything a
sponsor did not write down.

## Fallback if a criterion fails

If C1 fails, the rule-based extractor is not fit to run unsupervised: route all
extraction through the LLM-assisted path with human spot-checks, or restrict
predicate types to those with no `wrong` results.

If C2 fails, report `needs_verification` for the affected types rather than
lowering the threshold.
