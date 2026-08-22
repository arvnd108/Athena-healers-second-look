# MdrDB: measured coverage of our nine gold-standard cases

Source: `github.com/tencent-quantum-lab/MdrDB` @ `af00532`, file
`data/MdrDB_CoreSet_release_v1.0.2022.tsv` — 4,292 rows, 167 columns, and a
measured `DDG.EXP` on **every** row. Cloned locally to
`~/Desktop/Developer/SIH_MediTech/MdrDB`. Everything below is computed from that
file, not quoted from the paper.

## 1. Direct coverage: 1 of 9

| Case | Measured ΔΔG | Status |
|---|---|---|
| EGFR T790M / gefitinib | **−1.21** (AIMMS, 2ITY) | present |
| EGFR T790M / osimertinib | — | mutation present, gefitinib only |
| EGFR C797S / osimertinib | — | mutation absent (EGFR: 181 rows, 45 muts) |
| ABL1 T315I / imatinib | — | mutation present, 5 other drugs |
| KIT D816V / imatinib | — | mutation present, B49 only |
| KIT V560G / imatinib | — | mutation absent (KIT: 44 rows, 33 muts) |
| BRAF V600E / vemurafenib | — | mutation present, PLX-4720 and RAF-265 |
| ALK G1202R / crizotinib | — | mutation absent (ALK: 70 rows, 42 muts) |
| ALK I1171T / crizotinib | — | mutation absent |

An earlier draft of `state-of-the-art.md` claimed the odds of coverage were
"very high". That was wrong and has been corrected. **MdrDB is training data,
not a lookup table** — which is what its own paper uses it for.

## 2. Sign convention, established from the data

Not assumed — inferred, then cross-checked three ways:

- `KinaseMD` (a curated *resistance* dataset) is **100% positive**, n=68.
- `TKI` 81% positive, `RET` 82%, `AIMMS` 83% — all resistance-weighted sets.
- Three individually known directions agree: ABL1 T315I with dasatinib
  (**+3.29**) and nilotinib (**+3.00**), both clinically resistant; ABL1 T315I
  with axitinib (**−1.26**), which is clinically *active* against T315I.

**Positive ΔΔG = weaker binding = resistance.** Note this is the same
orientation as our docking delta (`mutant − wildtype`) and the opposite of
mCSM-lig — `labeling.MethodCalibration.orientation` already handles this.

## 3. What is measured at our exact mutation sites

Nine rows, usable as directional checks even where the drug differs:

| Gene | Mutation | Drug | ΔΔG | PDB | Dataset |
|---|---|---|---|---|---|
| EGFR | T790M | Gefitinib | −1.21 | 2ITY | AIMMS |
| ABL1 | T315I | 1N1 | +5.05 | 4XEY | TKI |
| ABL1 | T315I | AXI (axitinib) | −1.26 | 4WA9 | TKI |
| ABL1 | T315I | DB8 | +2.44 | 3UE4 | TKI |
| ABL1 | T315I | Dasatinib | +3.29 | 2GQG | AIMMS |
| ABL1 | T315I | Nilotinib | +3.00 | 3CS9 | AIMMS |
| KIT | D816V | B49 | −0.26 | 3G0E | Platinum |
| BRAF | V600E | PLX-4720 | −2.58 | 7M0X | Depmap |
| BRAF | V600E | RAF-265 | −1.83 | 7M0X | Depmap |

## 4. Two consequences for the validation plan

**(a) One hard-required control is supported by measured data.** BRAF V600E with
PLX-4720 — the direct vemurafenib scaffold analogue — measures **−2.58**:
stronger binding to the mutant, i.e. sensitivity. That is exactly the direction
`validation-plan.md` requires for BRAF V600E / vemurafenib. So that control is
answerable by a ΔΔG method in principle; our failing it is about our
implementation, not about the question being ill-posed.

**(b) One gold-standard case appears to be mislabelled *for this method*.**
`EGFR T790M / gefitinib` is listed as `resistance` in `GOLD_STANDARD_CASES`, but
the measured binding change is **−1.21 — gefitinib binds the T790M mutant more
tightly, not less.** Both statements can be true at once: T790M is
uncontroversially a clinical gefitinib-resistance mutation, and its measured
effect on gefitinib *binding affinity* is favourable. Clinical resistance and
ΔΔG are different quantities, and for this variant they point opposite ways.

This matters because our validation set scores direction-matching against the
**clinical** label. On T790M/gefitinib, a *perfectly accurate* ΔΔG predictor
would be scored as wrong. That is a property of the validation set, not of the
predictor.

**Do not fix this by flipping the label.** The literature mechanism usually
given is that T790M restores ATP affinity so resistance arises through
competition rather than loss of drug binding — but that explanation is *not yet
retrieved and cited here*, and under the project's own rule it must be before it
is repeated to anyone. The defensible action now is to mark the case
`out_of_scope_for_binding_delta` with this measured ΔΔG as the evidence, and say
so openly in the presentation.

## 5. Published baselines (verified from committed artifacts, not the paper)

`results/*_performance.tsv`. The paper's three scenarios train on different
sets and all test on the TKI dataset:

| Scenario | Training set | Best model | RMSE | Pearson | R² |
|---|---|---|---|---|---|
| 1 | Platinum (no tyrosine kinase) | RandomForest | 0.831 | 0.248 | **−0.025** |
| 2 | Platinum | SVR | 0.786 | 0.335 | 0.084 |
| 3 | **MdrDB CoreSet** | **ExtraTrees** | **0.656** | **0.607** | **0.362** |

Worth reading carefully before treating this as a solved problem: in Scenario 1
**every one of the ten models has negative R²** — worse than predicting the
mean. Scenario 3 is the only configuration that clears it, and even there
R² = 0.362 with RMSE 0.656 kcal/mol. That RMSE is the number to compare against
docking's 1.0–2.19: better, but the same order of magnitude, and still large
next to the 0.02–0.56 deltas we measured.

## 6. Licensing — resolved, terms retrieved 2026-08-22

Retrieved from <https://quantum.tencent.com/mdrdb/download> (the repo has no
LICENSE file; the terms live on the download page). Verbatim points:

- **"Academic, or Educational Use Only."** Users must "be a member of a relevant
  academic institution, research institute, or higher education institution."
- **"a non-exclusive, non-transferable, limited license for academic research."**
- **"Not use the database or its contents for commercial purposes unless
  explicitly approved in writing by us"** — commercial licensing via
  `qlab@tencent.com`.
- Users "must ensure that the data ... is used in research achievements with
  integrity, following international academic norms, and correctly cites the
  data sources."
- Violation remedies include required deletion of downloaded data.

The **paper** is separately CC BY 4.0 (DOI `10.1038/s42004-023-00920-7`,
*Communications Chemistry* 6:123, 2023) and is open access — quoting and citing
it is unrestricted.

### What this means for us

| Action | Allowed |
|---|---|
| Download and read the data as a university student | **Yes** — we are the intended user |
| Train and evaluate a model on it for this project | **Yes** — academic research |
| Report metrics computed from it, citing MdrDB | **Yes** — citation is required, and we would anyway |
| Commit the TSV into this repo / push it to GitHub | **No** — the licence is *non-transferable*; redistribution is not granted |
| Ship SecondLook commercially with MdrDB-derived data or weights | **Not without written approval** from `qlab@tencent.com` |

Two consequences worth acting on:

1. **`MdrDB/` stays outside `Athena_tier_2/` and out of git.** Not caution — the
   licence word is *non-transferable*. Anyone reproducing our work downloads it
   themselves, which is a normal and reasonable ask.
2. **If SecondLook is ever pitched as a product, this needs a commercial
   licence.** SIH submissions routinely gesture at deployment, so decide early
   whether MdrDB sits in the research track or the product track. One email to
   `qlab@tencent.com` settles it, and it is much cheaper to send now than after
   a model is trained.

### Getting the Full Set

The 23.60 MB "Full Set" `.tsv` is behind a **registration form** requiring an
academic email, name, institution, and agreement to the terms above — which is
why the download link is JS-driven and cannot be fetched. Use your university
address; the CoreSet already in hand is the 4,292-row subset of the same data.
