# Athena — known issues and how to fix them

**For the team.** Everything here is measured or read out of code. Nothing is
estimated; where something is unverified it says so.

Each section is: what's wrong → why → what to do. Sections are independent, so
you can take one without reading the rest.

**Read §1 before presenting this project to anyone.**

## Status at a glance

| Area | State |
|---|---|
| Both tiers install and import as one package | Working |
| 549 unit tests | Passing, 0 failures |
| Tier 1 retrieval Modes 1 & 2 against live CIViC graph | Working |
| Tier 1 → Tier 2 activation handoff | Verified live |
| Tier 2 → graph writes, full traversal | Verified live |
| WT-vs-mutant docking runs end to end | Verified (2Z4O D130N) |
| Binding-site proximity, reproducible | All 9 cases exact on re-run |
| **Validated binding-change prediction** | **0/9 — see §1** |
| **Proximity fallback criterion, pre-committed + evaluated** | **FAIL — 7/8 non-ambiguous, EGFR C797S misses — see §1** |
| Labeling calibration | `provisional`, blocked on §2 |
| 3 of 9 cases blocked by receptor-prep bug | §2 |
| Trial source (ClinicalTrials.gov / CTRI) | Not built — §5 |
| Mode 3 semantic retrieval | Never verified end to end — §10 |

## Where to start

| If you have | Work on |
|---|---|
| Half a day and want the biggest scientific win | **§2** — the meeko fix. Takes scoreable cases from 2 to 5. |
| A day and want the honest headline framing sorted out | **§1** — the proximity criterion is now pre-committed and evaluated (result: FAIL, 7/8); decide how to present two honestly-negative results without either burying them or overstating what §2's fix would change |
| Backend/API interest | **§5** — build the ClinicalTrials.gov loader |
| ML interest | **§12 step 5** — MdrDB training run |

---

## 1. The headline: the pipeline does not currently predict binding change

### What the numbers say

From `validation/results.md`, regenerated from a clean full run:

```
Pass rate: 0/9 (0%) — threshold 70% — NOT MET
Hard-required positive controls: FAILED
Demo-ready: NO
scored_by_method across all 8 cases: {'null': 9}
```

**Zero candidates were scored by any method.** All nine results are
`signal_type="proximity_only"` — a measured distance in Ångströms, with no
binding-change prediction attached.

So today SecondLook Tier 2 answers *"how far is this mutation from the drug
pocket?"* and not *"will this drug still work?"* Those are different products
and the difference must not be blurred in a presentation.

### Why — and this part is not a bug

Docking's published error is **1.0–2.19 kcal/mol**. The deltas we measured are
**0.02–0.56 kcal/mol**. The instrument is 20–100× less precise than the effect
being measured. That gap does not close with better code or more compute.

Worse, the measured distances show *why* most cases are unreachable:

| Case | Drug | Distance | Band |
|---|---|---|---|
| EGFR T790M | gefitinib | 3.5 Å | `in_contact` |
| EGFR T790M | osimertinib | 3.5 Å | `in_contact` |
| ABL1 T315I | imatinib | 3.5 Å | `in_contact` |
| ALK G1202R | crizotinib | 3.7 Å | `in_contact` |
| ALK I1171T | crizotinib | 6.9 Å | `pocket_adjacent` |
| EGFR C797S | osimertinib | 10.1 Å | `distant` |
| BRAF V600E | vemurafenib | 12.2 Å | `distant` |
| KIT D816V | imatinib | 16.6 Å | `distant` |
| KIT V560G | imatinib | 20.0 Å | `distant` |

Four of nine sit 10–20 Å from the drug. Those are **conformational and
allosteric** mechanisms — the mutation changes the protein's shape or activation
state, not its contact with the drug. A pocket-geometry method cannot see them.
Not with a fix, not ever.

### What to do about it

Not "try harder." **Narrow the claim to what can be proven, then prove it
completely.** Proximity is a *measurement*, not a prediction — distance from
experimental coordinates reproduces to the decimal, which was confirmed by
re-running the full set and getting all nine values back identically.

Critical methodological rule:

- You may **not** move the 70% threshold on the binding-prediction claim. That
  is fitting the test to the data, and `docs/validation-plan.md` forbids it in
  writing.
- You **may** pre-commit a *new* criterion for the *new, narrower* claim —
  written down **before** running it. **This has since been done and
  evaluated** (`docs/validation-plan.md` "Binding-site proximity fallback —
  pre-committed criterion", evaluated in `validation/results.md`): does the
  proximity signal correctly separate contact-mediated from non-contact
  mechanisms across the gold-standard cases that have a clean mechanism
  label. **Result: FAIL** — 7/8 non-ambiguous cases matched, one (EGFR
  C797S/osimertinib) did not, one (ALK I1171T) excluded as ambiguous. Per the
  pre-committed wording, a partial match is a fail and is reported as one, not
  rounded up. So the proximity fallback is **not** currently a claim you can
  make either — it is an honestly-measured, honestly-evaluated result that
  happens to say "not yet," same as the binding-delta claim above.

---

## 2. meeko mutant-receptor prep fails — blocks 3 of 9 cases

**Highest-value remaining fix.** Resolving it takes the scoreable sample from 2
drug-pairs to 5.

### Symptom

```
meeko receptor prep failed: Updated 1 H positions but deleted 4
```

Affects **ABL1 T315I (5HU9)**, **ALK G1202R (4Z55)**, **ALK I1171T (4Z55)** —
each of which obtained a good ligand-bound experimental structure. The
**wild-type** receptor of the same structure preps fine (210,400 bytes); only
the **mutant** fails.

### Cause

`meeko/polymer.py::update_H_positions` (~line 380). It deletes hydrogens that
need repositioning, re-adds them with `Chem.AddHs`, copies new coordinates back,
and raises when the counts don't match:

```python
if len(used_h) != len(to_del):
    raise RuntimeError(f"Updated {len(used_h)} H positions but deleted {len(to_del)}")
```

PDBFixer's `applyMutations` changes hydrogen topology at the substituted
residue — THR has a hydroxyl hydrogen, ILE does not — so the matching loop finds
fewer re-addable hydrogens than it deleted.

### Already ruled out

- **Not an RDKit chemistry problem.** `Chem.SanitizeMol` passes cleanly on the
  mutant after `strip_terminal_oxt` + `resolve_overvalence`; zero over-valent atoms.
- **Not fixable by re-protonating.** Stripping the mutant's hydrogens and
  re-running PDBFixer protonation reproduces the failure exactly.
- **Not fixable by supplying heavy atoms only.** That fails for the wild-type
  too (`deleted 5`), so meeko requires the hydrogens.
- **Not a version issue.** Already on meeko 0.7.1, the latest release.

### Candidate fixes, in order of likely success

1. **Replace the receptor PDBQT path with Open Babel.** `brew install
   open-babel`, then `obabel -ipdb -opdbqt`. Sidesteps meeko's residue templates
   entirely. Not yet installed or attempted — this is the first thing to try.
2. **ADFR suite's `prepare_receptor4.py`** — the reference implementation Vina
   was originally built against.
3. **Report upstream** with 5HU9 / T315I as a clean minimal reproducer.

### Do NOT do this

meeko offers `--delete_residues` to skip problem residues. **That deletes the
mutated residue** — which is the entire measurement. A run that "succeeds" that
way is comparing a wild-type structure against itself.

### Until it's fixed

This is a **tooling limitation, not evidence about the method's accuracy**. It
must not be reported as a prediction failure.

---

## 3. Labeling calibration is provisional and cannot be calibrated yet

`src/secondlook/labeling.py`:

```python
LABELING_VERSION  = "0.1.0-provisional"
CALIBRATION_STATUS = "provisional"
```

`CALIBRATION_STATUS` rides on every label and every graph node, so nothing can
be mistaken for a validated threshold. Calibration means: get a run that
actually scores, sweep cutoffs with `validation/sweep_thresholds.py` (cheap — it
re-labels cached deltas without re-docking), pick values, flip to `"calibrated"`,
bump `LABELING_VERSION`, record which run produced them.

**Blocked on §2.** Nothing scores, so there is nothing to calibrate against.

---

## 4. One gold-standard case is mislabelled *for this method*

`EGFR T790M / gefitinib` is listed as `resistance` in `GOLD_STANDARD_CASES`.
MdrDB's measured binding change is **−1.21 kcal/mol** — gefitinib binds the
T790M mutant **more tightly**, not less.

Both statements are true. T790M is beyond dispute a clinical gefitinib-resistance
mutation, and its measured effect on gefitinib *binding affinity* is favourable.
Clinical resistance and ΔΔG are different quantities, and here they point
opposite ways.

**Consequence:** on this case a *perfectly accurate* ΔΔG predictor is scored as
wrong. That is a property of the validation set, not of the predictor.

**Do not fix this by flipping the label.** The mechanism usually cited is that
T790M restores ATP affinity, so resistance arises through competition rather
than loss of drug binding — but that explanation has **not been retrieved and
cited**, and under this project's own rule it must be before anyone repeats it.
The defensible action is to mark the case out of scope for a binding delta, with
the measured ΔΔG as the evidence, and say so openly.

Sign convention, established from MdrDB data rather than assumed: `KinaseMD` (a
curated resistance set) is **100% positive**, n=68; TKI 81%; AIMMS 83%. Three
individually known directions agree — ABL1 T315I with dasatinib (**+3.29**) and
nilotinib (**+3.00**), both clinically resistant, versus axitinib (**−1.26**),
clinically active against T315I. **Positive ΔΔG = weaker binding = resistance.**

---

## 5. No clinical-trial source — half the activation rule is unevaluated

`tier1-retrieval.md` §Activation defines a strong hit as *"≥1 CIViC evidence
item at level A or B for this exact variant, **OR** ≥1 recruiting trial matching
this exact biomarker."*

Tier 1 loads CIViC and PubMed. It loads **no** trial source — no
ClinicalTrials.gov, no CTRI. So the second half of that clause cannot be
evaluated.

Treating "no trial data" as "no trial" would silently convert an unimplemented
source into a negative clinical finding. `tier1_adapter._classify` therefore
appends to every strong/weak rationale:

> *Trial half of the strong-hit rule not evaluated (no trial source loaded).*

**Fix:** implement a ClinicalTrials.gov loader in Tier 1 (endpoints and rate
limits are in `docs/data-sources.md`), then drop the caveat string. CTRI access
mode still needs verifying and is tracked as Tier 1 work.

---

## 6. All nine gold-standard cases return `no_hit` from Tier 1 — this is correct

Tier 1's loaded scope is 17 rare cancers (`civic_scope.yaml`): NTRK fusions,
EWSR1::FLI1, PAX3::FOXO1 and similar. **None of EGFR / ABL1 / KIT / BRAF / ALK
are in it.**

Those nine cases were chosen because they have well-established ground truth for
validating the *structural method* — not because they are the target population.
So on the real graph every one returns `no_hit` and hands off to Tier 2.

**That is the two-tier architecture working exactly as designed**, and it is
worth saying plainly rather than letting someone discover it and read it as a
gap. Verified live alongside three in-scope variants (FGFR1 N546K, TP53 R273H,
KDR A1065T) which correctly return `weak_hit` with real CIViC evidence attached.

---

## 7. Repo layout — the two-repo split is gone

**Resolved by this repo.** Recorded because the failure mode is instructive and
the old repos still exist.

Athena was originally two repos — `Athena_tier_2` and `Athena_Tier_1` — sharing
one `secondlook` package as a PEP 420 implicit namespace. That arrangement had
three fragile rules (neither repo may ship `src/secondlook/__init__.py`; both
must install with `--config-settings editable_mode=compat`; they must be
siblings on disk), and a cross-repo git pin that had drifted to the wrong fork
at a stale commit.

**This repo is one package.** `secondlook` with `secondlook.tier1` as a normal
subpackage. One `pip install -e .`, one `pytest`, no namespace rules, no pin.
Every failure mode above is structurally impossible here.

### The old repos

| Repo | Status |
|---|---|
| `KrishG7/Athena_tier_2` | Superseded by this repo. History preserved. |
| `KrishG7/Athena_Tier_1` | Fork of Gagan's, with the §8a schema fix. Superseded. |
| `Gagansharma-code/Athena_Tier_1` | **Still has the §8a bug.** Gagan should take the fix. |

**One more layer, since this file was originally written for the `Athena`
repo and now lives in `athena_ultimate`:** this repo is a further
consolidation on top of `Athena` — same code (plus a handful of fixes ported
from the pre-merge `athena_tier_2`, see root `ARCHITECTURE.md` §2), reset to
a single fresh git history with no upstream cross-repo dependency at all. If
you have a clone of `Athena`, `athena_tier_2`, or `athena_Tier_1`, this repo
supersedes all three the same way `Athena` superseded the two-repo split
below.

If you have an old clone, switch to this repo. If you're Gagan: the
`StructuralSignal` schema fix in §8a should land in your repo too, or anyone
cloning from you gets a schema that silently drops the only field Tier 2
currently outputs.

## 8. Two integration bugs found only by running against real data

Both passed every unit test with a mock. Recording them because the lesson
generalises: **the seam is where the bugs are.**

### (a) StructuralSignal schema drift — fixed

Tier 2 emitted 13 node properties; Tier 1's `STRUCTURAL_SIGNAL` NodeType
declared 7. Nothing crashed — `NodeType` is documentation, not an ORM — so a
query built from the declaration would simply never have read the missing six,
one of which is `binding_site_distance_angstrom`, *the entire output* of every
current gold-standard case.

Fixed in Tier 1's schema, plus a test there comparing the declaration against
what Tier 2 actually emits, so it cannot drift silently again.

### (b) The `hgvs_p` variant-join mismatch — fixed

CIViC writes `NP_005148.2:p.Thr315Ile`; Tier 2 computes `p.Thr315Ile`. The graph
sink's original `MERGE (v:Variant {hgvs_p: ...})` on the bare form matched **0 of
1** existing TP53 R273H nodes on the live graph. It would have minted a second,
orphan Variant for a variant the graph already had — unreachable from its Gene
and invisible to every Tier 1 traversal.

The sink now resolves find-then-create against
`(Gene)-[:HAS_VARIANT]->(Variant)` with an accession-anchored suffix match.
Verified: 9 signals → 9 full `Gene→Variant→Signal→Drug` paths, 0 orphans, EGFR
T790M correctly resolving to one Variant carrying two drug signals.

---

## 9. Environment issues that will bite you

### Docker wedges when the disk fills

During development the disk hit **263 MiB free of 228 GB**. `sentence-transformers`
(torch, ~2 GB) failed mid-install, and Docker Desktop's backend hung — `docker ps`
stopped responding entirely and would not restart from the CLI.

**Keep ≥3 GB free.** Reclaim with `docker builder prune` (safe) and by removing
stale images (`docker images` — check what you actually still need first).

### The test suite must skip, not error, when FalkorDB is down

`tests/test_tier1_integration.py`'s skip guard originally caught
`falkordb.exceptions.ConnectionError` — an attribute that **does not exist**;
`falkordb.exceptions` defines no error classes, the client raises redis-py's
types. And `redis.exceptions.ConnectionError` does **not** subclass the builtin
`ConnectionError`/`OSError`, so the fallback missed it too.

Result: with the database down the suite **errored** instead of skipping. Fixed
to catch `redis.exceptions.ConnectionError`/`TimeoutError`. If you add live
tests, get this right — a suite that errors when a service is down looks broken
in a demo.

### Live services do go down

An Ensembl VEP outage and a UniProt HTTP 500 both produced failures during
development that looked like regressions and were not. `http_retry.py` handles
429/5xx with backoff. Before diagnosing a failure as a code problem, check the
service:

```bash
curl -o /dev/null -w "%{http_code}\n" https://rest.uniprot.org/uniprotkb/P00533.fasta
curl -o /dev/null -w "%{http_code}\n" https://files.rcsb.org/download/2ITY.pdb
```

---

## 10. Smaller open items

| Item | Status |
|---|---|
| SIFTS UniProt↔PDB residue mapping | Never implemented. Partially mitigated by the existing `_residue_name_at` identity check. |
| Demo cases locked and cached | Cache layer exists (`cache.py`); **no case is actually locked**. Needs a run that scores first. |
| Vina-vs-mCSM-lig coverage note | Spec deliverable 5. Framing is in code; the measured proportion cannot be stated until something scores. |
| Mode 3 semantic retrieval | Never verified end to end — 7 Tier 1 integration tests blocked on `sentence-transformers`, blocked on disk. |
| MdrDB full set | Only the 4,292-row CoreSet is in hand. The 100,537-row full set is behind a registration form needing an academic email. |

---

## 11. MdrDB licence — read before shipping anything

Retrieved from <https://quantum.tencent.com/mdrdb/download> (the repo has no
LICENSE file; terms live on the download page):

- **"Academic, or Educational Use Only."**
- **"a non-exclusive, non-transferable, limited license for academic research."**
- **"Not use the database or its contents for commercial purposes unless
  explicitly approved in writing by us"** — commercial licensing via
  `qlab@tencent.com`.
- Users must correctly cite the data source.

| Action | Allowed |
|---|---|
| Download and use as a university student | **Yes** |
| Train and evaluate a model for this project | **Yes** |
| Report metrics computed from it, citing MdrDB | **Yes** |
| Commit the TSV into this repo | **No** — the licence is *non-transferable* |
| Ship SecondLook commercially with MdrDB-derived data or weights | **Not without written approval** |

The paper itself is CC BY 4.0 and open access — DOI `10.1038/s42004-023-00920-7`,
*Communications Chemistry* 6:123, 2023. No university network needed.

**If SecondLook is pitched as a product, this needs a commercial licence.** SIH
submissions routinely gesture at deployment — decide early which track you are
in. One email settles it, and it is far cheaper to send now than after a model
is trained.

---

## 12. Suggested order of work

1. **Commit and push.** Everything else is worthless if the work is lost.
2. **Environment** — free disk, restart Docker, install `sentence-transformers`,
   verify Mode 3. Fix the cross-repo pin (§7).
3. **Pre-commit the proximity criterion**, then evaluate it. This is the claim
   you can actually make bulletproof.
4. **Timebox the meeko fix** (§2) — half a day via Open Babel, not three days.
   It is the only remaining work with real scientific payoff.
5. **MdrDB training run** to reproduce the published baseline (Scenario 3,
   ExtraTrees: RMSE 0.656, Pearson 0.607, R² 0.362). Gives you an accuracy
   figure you own, and it is the honest roadmap slide.

Note on that baseline before treating it as solved: in the paper's Scenario 1,
**all ten models have negative R²** — worse than predicting the mean. Scenario 3
is the only configuration that clears zero, and even there RMSE 0.656 kcal/mol
is the same order of magnitude as docking's error bar.
