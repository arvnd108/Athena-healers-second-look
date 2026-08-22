# Validation Plan

Run this before the pitch deck is finalized. Do not claim reliability you haven't checked here.

## Method
For each case below, run the mutation through Tier 2 **as if it were undocumented** (even though the clinical answer is known) and check whether the pipeline's predicted label matches the known direction. This is a rediscovery test, not proof of general accuracy — state it as such.

## Gold-standard cases (known clinical directionality)

| Gene / mutation | Drug | Known direction | Notes |
|---|---|---|---|
| EGFR T790M | gefitinib / erlotinib (1st-gen) | **Resistance** | Gatekeeper mutation |
| EGFR T790M | osimertinib (3rd-gen) | **Sensitive** | Designed for T790M |
| EGFR C797S | osimertinib | **Resistance** | Abolishes covalent binding at C797 |
| ABL1 T315I | imatinib | **Resistance** | Classic gatekeeper mutation |
| KIT D816V | imatinib | **Resistance** | Kinase-domain mutation |
| KIT V560G | imatinib | **More sensitive** (contrast case) | Juxtamembrane mutation — use alongside D816V to test the pipeline distinguishes direction, not just "mutation = resistance" |
| BRAF V600E | vemurafenib | **Sensitive** | Drug designed for this mutation |
| ALK G1202R | crizotinib | **Resistance** | Sensitive to lorlatinib instead |
| ALK I1171T | crizotinib | **Resistance** | Sensitive to ceritinib/lorlatinib instead |

## Pre-committed pass/fail criteria (set BEFORE running, do not adjust after seeing results)

- **Pass threshold:** correct resistance-vs-sensitive directionality on ≥70% of the cases above.
- **Hard requirement regardless of overall score:** no confidently-wrong call on the two "designed-for" positive-control cases (BRAF V600E/vemurafenib should show retained/increased binding; EGFR T790M/osimertinib should show retained/increased binding). If either of these fails, treat the pipeline as not ready to demo as-is.
- **If overall pass rate is below threshold:** fall back to the narrower claim — "mutation is in/near the known binding pocket" plus AlphaMissense pathogenicity flag only, dropping the binding-affinity delta/label from the UI. Document this fallback decision plainly if you have to invoke it; it's still a legitimate, honest V1.

## Binding-site proximity fallback — pre-committed criterion

This section is independent of the binding-delta criteria above. Those stay frozen. This one exists because the binding-delta claim did not survive validation, and the documented fallback ("mutation is in/near the known binding pocket") had no checkable pass/fail bar of its own. The bar is written here **before** any evaluation script is run against the recorded distances. Do not edit this section after seeing whether it passes.

The proximity bands themselves (`in_contact` ≤ 5 Å, `pocket_adjacent` ≤ 8 Å, `distant` otherwise) are structural-biology convention, already implemented in `src/secondlook/proximity.py`. They are not a parameter of this criterion and must not be retuned to fit outcomes.

### Criterion (one sentence)

The proximity signal correctly separates mutations whose established effect on the named drug acts at the occupied inhibitor-contact surface from mutations whose established effect is conformational, allosteric, or otherwise away from that surface, across the gold-standard cases that can be given a clean mechanism label.

### Operational definitions

A case is **contact-mediated** when the mutation's established effect on that drug is understood to act by changing the occupied ATP-site / inhibitor-contact surface directly. Gatekeeper residues, solvent-front residues that sterically clash with the inhibitor, and covalent-attachment residues that sit in the ATP site all count. Clinical direction (resistance vs sensitivity) is irrelevant: a pocket residue the drug was designed to accommodate is still a contact residue.

A case is **non-contact** when the established effect is conformational, allosteric, activation-loop stabilization of an inhibitor-incompatible kinase state, or loss of autoinhibition at a site that is not part of the inhibitor-contact surface.

A case is **ambiguous** when published accounts mix contact and conformational mechanisms without a primary geometry, so forcing a clean label would be a guess. Ambiguous cases are excluded from the pass/fail tally; they are not counted as passes.

Expected band given the label:

| Mechanism class | Expected `classify_proximity` band |
|---|---|
| contact-mediated | `in_contact` or `pocket_adjacent` |
| non-contact | `distant` |
| ambiguous | excluded — not scored |

An `unknown` band (distance not measurable) on a non-ambiguous case is a fail for that case: the criterion is about a measured geometric signal, and a missing measurement is not a match.

### Per-case mechanism classification

Classifications use the gold-standard table's Notes column plus standard kinase-inhibitor-resistance literature. They are **not** derived from the recorded Å distances.

| Case | Drug | Class | Basis |
|---|---|---|---|
| EGFR T790M | gefitinib / erlotinib | **contact-mediated** | Notes: "Gatekeeper mutation." T790 is the ATP-site gatekeeper residue of the inhibitor-contact surface (Kobayashi et al., *NEJM* 2005). Yun et al. (*PNAS* 2008) showed the dominant *biochemical* resistance mechanism vs 1st-gen TKIs is increased ATP affinity rather than steric occlusion; that revises *how* the pocket residue competes with the drug, not *where* it sits. The residue remains a contact-surface residue, which is what proximity can assess. |
| EGFR T790M | osimertinib | **contact-mediated** | Same gatekeeper residue. Osimertinib was designed to bind EGFR T790M (Cross et al., *Cancer Discov* 2014); the clinical direction is sensitivity, but the substitution is still at the occupied ATP-site contact surface. |
| EGFR C797S | osimertinib | **contact-mediated** | Notes: "Abolishes covalent binding at C797." C797 is the covalent-attachment cysteine **in the ATP-binding pocket** (Thress et al., *Nat Med* 2015). Loss of that covalent contact is a change to the drug-contacting surface, not an allosteric or conformational mechanism. See the ligand-identity caveat below — it qualifies how a `distant` band on this case must be *read*, it does not reclassify the mechanism. |
| ABL1 T315I | imatinib | **contact-mediated** | Notes: "Classic gatekeeper mutation." T315 is the BCR-ABL gatekeeper; T315I sterically occludes imatinib in the ATP site (Gorre et al., *Science* 2001). |
| KIT D816V | imatinib | **non-contact** | Notes say only "kinase-domain mutation"; the literature is more specific. D816 sits in the activation loop (exon 17). Imatinib is a type II inhibitor of the inactive DFG-out state; D816V shifts the conformational equilibrium toward the active DFG-in state that type II inhibitors cannot bind (Gajiwala et al., *PNAS* 2009). Activation-loop conformational resistance, not a change to the imatinib-contact surface. |
| KIT V560G | imatinib | **non-contact** | Notes: "Juxtamembrane mutation." V560 is in the autoinhibitory juxtamembrane domain, not the ATP-site contact surface. JM mutations relieve autoinhibition; V560G/D remain (and are often more) imatinib-sensitive (Mol et al., *J Biol Chem* 2004; Laine et al., *PLoS Comput Biol* 2015). Conformational / autoinhibitory, not contact. |
| BRAF V600E | vemurafenib | **non-contact** | Notes: "Drug designed for this mutation" describes clinical direction, not geometry. V600 is in the activation segment; V600E constitutively activates BRAF by destabilizing the inactive conformation (Wan et al., *Cell* 2004). Vemurafenib (PLX4032) occupies the ATP site of that activated kinase (Bollag et al., *Nature* 2010; PDB 3OG7) — it was designed for the mutant's conformational state, not because V600 is a drug-contact residue. |
| ALK G1202R | crizotinib | **contact-mediated** | G1202 is a solvent-front residue of the ATP-site entrance; G1202R sterically hinders crizotinib (and most 2nd-generation ALK TKIs) by narrowing the inhibitor-access channel (Gainor et al., *Cancer Discov* 2016; Lin et al., *J Clin Oncol* 2017). Direct contact-surface clash. |
| ALK I1171T | crizotinib | **ambiguous** | I1171 sits in the αC-helix. Published accounts mix two geometries without a clear primary one: αC-helix distortion that interferes with TKI binding (Lin et al., *J Clin Oncol* 2017 / Lin & Shaw, *Clin Cancer Res* 2017), and thermodynamic / steric effects characterized mainly for alectinib rather than crizotinib (Katayama et al., *Clin Cancer Res* 2014). It is not a clean gatekeeper or solvent-front contact residue, and it is not a clean activation-loop / JM conformational site either. Forced either way, the label would be a guess. |

### Ligand-identity caveat (documented before evaluation, not discovered after)

The measured distance is to **whatever ligand was co-crystallized in the chosen structure**, which is frequently **not** the candidate drug being evaluated. `proximity.py` already states this: the claim is pocket-level geometry, not "distance to this drug." That matters most where the established mechanism is drug-specific chemistry rather than occupancy of a shared ATP site.

EGFR C797S is the named case. C797 is osimertinib's covalent-attachment residue. A reversible co-crystallized ligand occupying the same ATP site need not extend a warhead toward C797, so the residue can measure farther from *that* ligand than it sits from osimertinib's acrylamide. A `distant` band on this case is therefore **not** a clean negative for a contact mechanism, and must not be reported as one. Under the pass condition below it is a failed contact-mediated case if the band is `distant` — and the failure, if it happens, is evidence about the signal *as implemented* (distance to the co-crystal ligand), which this caveat exists to keep from being over-read.

The same caveat applies more weakly to every case: two drugs scored against one co-crystal ligand (EGFR T790M / gefitinib and osimertinib is the obvious pair) will share a distance that is about the pocket, not about either molecule.

### Pass condition

Let *S* be the set of gold-standard cases not classified **ambiguous** above.

- **Pass:** every case in *S* matches the expected band in the table (contact-mediated → `in_contact` or `pocket_adjacent`; non-contact → `distant`).
- **Fail:** one or more cases in *S* do not match, including any case in *S* whose band is `unknown`.

A partial result such as "all non-ambiguous cases except one" is a **fail**, reported with the per-case tally (e.g. 7/8 non-ambiguous correct, 1 excluded). It is not rounded up to a pass. Ambiguous cases are listed as excluded; they do not inflate either numerator or denominator.

This wording is not to be loosened after the evaluation is run.

## Demo case selection

Pick exactly two cases for the live demo:
1. **Tier 1 path** — a case with strong documented CIViC/trial evidence, to show the "fast, sourced retrieval" experience.
2. **Tier 2 path** — one gold-standard case from the table above (sarcoma-related if possible, ties back to RareCure's own validation cohort and the project's origin story) where Tier 2 correctly rediscovers the known direction.

Both must be **pre-computed and cached** (see `tech-stack-setup.md`) — do not run either live on stage for the first time.

## What to report to judges (accurate framing)

State plainly: "We validated Tier 2 by checking whether it rediscovers known resistance/sensitivity patterns in textbook cases like ABL1 T315I and EGFR T790M — it passed [X/9]. This is a sanity check on the method, not clinical validation, and every result is labeled accordingly in the UI."

## Cite real accuracy figures, not assumed ones

Quote the underlying models' own published figures rather than implying higher confidence:
- mCSM-lig: correlation up to ρ = 0.67 with experimental data (Pires, Blundell & Ascher, *Sci Rep* 2016).
- Independent benchmarking shows accuracy degrades 5–30% when using modeled (vs. experimental) structures.
- AlphaMissense: thresholds chosen for 90% precision on ClinVar (Cheng et al., *Science* 2023) — this is a pathogenicity classifier, not a drug-response predictor; don't conflate the two in your pitch.
