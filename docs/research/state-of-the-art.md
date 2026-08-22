# How this is actually solved — research findings and a revised architecture

Research pass on the question the gold-standard run left open: *if docking-delta
cannot answer this, what can?* Everything below is sourced; the numbers are the
papers' own.

## The short answer

Three findings change what we should build.

1. **The professional method (alchemical free energy / FEP) reaches ~1.0 kcal/mol
   RMSE and ~0.81 classification accuracy** — good, but GPU-days per mutation-drug pair.
2. **A Random Forest trained on historical ΔΔG values reaches the *same* ~0.81
   classification accuracy** at orders of magnitude less compute, deployable "in a
   few lines of Python", no GPU. The gap to physics is ~0.3 kcal/mol RMSE.
3. **The training data exists, is free, and is downloadable** — MdrDB, 100,537
   measured samples across 240 proteins, 2,503 mutations, 440 drugs, with 10 ML
   baselines published alongside it.

Together these say: we do not need physics to make a useful claim, and we do not
need to invent a method. We need to stop computing a quantity badly and start
using measurements other people already made.

---

## 1. What the professional standard actually is

Alchemical free-energy calculation — FEP+ (Schrödinger, commercial), or open
implementations **OpenFE** (OpenMM-based) and **pmx** (GROMACS-based).

A 2024/25 prospective evaluation compared five methods on kinase-inhibitor
resistance, which is exactly our problem:

| Method | Imatinib RMSE | Dasatinib RMSE | Classification accuracy | Compute |
|---|---|---|---|---|
| FEP+ replica exchange | ~1.0 kcal/mol | ~0.7 | ~0.81 | GPU-days |
| pmx non-equilibrium | ~1.0 | ~0.7–0.8 | ~0.81 | GPU-days |
| Rosetta flex_ddg | ~1.2 | ~0.8 | ~0.81 | CPU, "orders of magnitude" cheaper |
| **Random Forest** | **~1.3** | **~0.9** | **~0.81** | **commodity hardware** |

The headline is not that physics wins. It is that **all five land at roughly the
same classification accuracy**, and the physics advantage is "small but
consistent, at most 0.5 kcal/mol". For a three-way label — which is all we emit —
that difference is largely invisible.

## 2. The nuance that decides our architecture

The same evaluation found that physics-based methods predict effects of residues
**up to 30 Å away**, while **Rosetta and machine learning "collapse at a
distance"**.

This is the single most useful sentence in the literature for us, because it is
the mechanistic explanation for what we measured independently:

- Our contact-range cases (3.5–3.7 Å) produced the only above-noise deltas.
- Our distant cases (10–20 Å) produced noise regardless of true direction.

We had inferred the boundary empirically. The literature says the boundary is
**method-dependent**, not absolute: cheap methods work near the pocket, and only
physics reaches past it. That converts our 8 Å gate from a limitation into a
**routing rule**.

## 3. The data we did not know we had

**MdrDB** (Tencent Quantum Lab, 2022) — <https://quantum.tencent.com/mdrdb/>,
code at <https://github.com/tencent-quantum-lab/MdrDB>.

- 100,537 samples · 240 proteins · 5,119 PDB structures · 2,503 mutations · 440 drugs
- Aggregates GDSC, DepMap, AIMMS, **KinaseMD**, **Platinum**, **TKI**, RET
- Ships `MdrDB_ML_baselines.ipynb` with 10 evaluated models, plus a demo folder
  testing against a held-out TKI set

Reported baseline on the **TKI** set — our exact target class:

| Metric | Value |
|---|---|
| Pearson r | 0.607 |
| RMSE | 0.656 kcal/mol |
| AUPRC | 0.538 |

Two readings, both worth stating. The RMSE is *better* than the FEP figures
quoted above — but AUPRC 0.538 is modest, so this is a usable regressor and a
weak classifier. Treat the RMSE as the headline only alongside the AUPRC.

### The features are computable for unseen cases — with tools we already have

146 features, and the pipeline is deterministic for a new (protein, mutation,
drug) triple:

| Feature group | Count | Tool | Already installed? |
|---|---|---|---|
| Ligand properties (logP, MW, HBD/HBA) | 18 | RDKit | |
| Mutated amino-acid properties | 12 | RDKit | |
| Mutation-environment / atom distribution | 21 | Biopython | pip install |
| Protein–ligand interactions (H-bond, π-stack, salt bridge…) | 6 | PLIP | pip install |
| Vina score + 58 scoring terms | 59 | Δ-Vina XGBoost | Vina yes, needs xgboost |
| Pharmacophore SASA | 30 | Δ-Vina XGBoost | as above |

**Our existing Vina integration is not wasted.** In this architecture the Vina
score stops being the *answer* and becomes one of 146 *features* — which is the
role it can actually support. That reframing alone justifies the docking work.

Mutant structures: PyMOL rotamer sampling for substitutions, AlphaFold otherwise,
with a pLDDT > 70 filter and mutation-site pLDDT ≥ 50 — close to what we built.

---

## 4. Retrieval-first: predicted large, measured small

**Measured 2026-08-21, re-verified 2026-08-22 against a durable clone.** Full
detail in [`mdrdb-coverage.md`](mdrdb-coverage.md).

The hypothesis was that MdrDB is **not only training data but a lookup table of
100,537 measured values** (2,503 mutations, 440 drugs, spanning KinaseMD, TKI
and Platinum) — enough to answer most of our cases by retrieval instead of
computation.

**That was wrong.** Checking the CoreSet (4,292 rows) against our nine pairs:
**only 1 of 9 has a measured ΔΔG** — EGFR T790M/gefitinib, -1.21 kcal/mol
(AIMMS, PDB 2ITY). Five more mutations are present but paired with *different*
drugs; three are absent entirely.

The CoreSet is 4,292 rows against the full database's 100,537, so the full set may
cover more — unverified until it is downloaded. But on present evidence the
retrieval branch is **thinner than predicted**, and MdrDB's value is primarily as
**training data**, which is what its own paper uses it for.

**Computing a worse estimate of an already-measured quantity is negative value.**
Retrieval must come before prediction — and this is precisely what Tier 1 is
built to do. The architectural boundary between the tiers was drawn in the wrong
place: measured binding data is *documented evidence*, not a computation.

---

## 5. Revised architecture

A distance-stratified router, not a single scorer:

```
(gene, mutation, drug)
      |
      ├─ 0. Covalent?              → mechanism_invalidated        [BUILT]
      |
      ├─ 1. Measured value exists? → cite MdrDB/Platinum          [NEW — highest value]
      |     (retrieval, not prediction; a citation, not an estimate)
      |
      ├─ 2. Mutation ≤ 8 Å?        → MdrDB-trained model + ΔΔG    [NEW — laptop-feasible]
      |     (ML works near the pocket; Vina score becomes a feature)
      |
      ├─ 3. Mutation > 8 Å?        → proximity_only + "needs FEP" [BUILT]
      |     (ML/Rosetta collapse at distance; only physics reaches 30 Å)
      |
      └─ 4. Curated demo cases     → pre-computed FEP via OpenFE  [FUNDED PHASE]
```

Each branch is honest about its own regime, and each is separately verifiable.

### Why this is better than what we have

- Branch 1 replaces an unreliable estimate with a **measured, citable value**.
- Branch 2 gives a real ΔΔG where a real ΔΔG is obtainable, at published accuracy,
  with **no GPU**.
- Branch 3 is what we already ship, now with a *reason* rather than only a refusal.
- Nothing claims accuracy it cannot show.

---

## 6. Build order

| # | Task | Effort | Unlocks |
|---|---|---|---|
| 1 | Download MdrDB CoreSet; check coverage of our nine cases | hours | Tells us how much is retrieval vs prediction |
| 2 | Retrieval branch: exact (UniProt, mutation, InChIKey) lookup | 1–2 days | Citable measured values |
| 3 | Feature pipeline: RDKit + Biopython + PLIP + Δ-Vina | 2–3 days | Reuses existing Vina/RDKit work |
| 4 | Train/evaluate against their published baselines | 1–2 days | A real ΔΔG with a real error bar |
| 5 | Conformational-state gate (KLIFS/Kincore, DFG-in/out) | 1 day | Removes the largest silent error |
| 6 | OpenFE on the locked demo case only | funded | Physics where it matters most |

Step 1 is the decision point: **if MdrDB already contains most of our nine cases,
the honest product is mostly retrieval and the ML branch is a smaller add-on than
it looks.**

---

## 7. Caveats — read before quoting any number above

- **AUPRC 0.538 is modest.** MdrDB's baseline is a decent regressor and a weak
  classifier. Do not quote RMSE 0.656 without it.
- **Their baseline is theirs, not ours.** Reproducing it on their held-out split is
  step 4, and until that runs we have no accuracy figure of our own.
- **The 0.81 accuracy figures come from an Abl-focused evaluation.** Generalisation
  beyond kinases is not established by these papers.
- **MdrDB v1.0 is from 2022.** Post-2022 drugs and mutations will be absent.
- **Retrieval coverage is unknown until step 1.** The claim that our cases are
  probably present is an expectation, not a measurement.
- **License unverified.** Must be checked before any redistribution.

## Sources

- Prospective evaluation of structure-based simulations for kinase mutation effects — <https://pmc.ncbi.nlm.nih.gov/articles/PMC12038917/>
- Predicting resistance of clinical Abl mutations via alchemical free-energy calculations, *Commun Biol* 2018 — <https://www.nature.com/articles/s42003-018-0075-x>
- Predicting Resistance to Small Molecule Kinase Inhibitors, *JCIM* 2025 — <https://pubs.acs.org/doi/10.1021/acs.jcim.4c02313>
- MdrDB, *Commun Chem* 2023 — <https://pmc.ncbi.nlm.nih.gov/articles/PMC10267113/> · data <https://quantum.tencent.com/mdrdb/> · code <https://github.com/tencent-quantum-lab/MdrDB>
- Platinum database — <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4384026/>
- OpenFE documentation — <https://docs.openfree.energy/>
