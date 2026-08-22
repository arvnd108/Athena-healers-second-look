# Athena — architecture

The founding document for `athena_ultimate`. It replaces the Tier 1 / Tier 2 split
with one pipeline shaped like the reasoning it is meant to perform.

Read §1 and §2 before writing any code. §2 is the part most likely to be assumed
wrong.

---

## 1. What this system is

A patient's standard genomic leads are exhausted. The oncologist has a molecular
profile, a treatment history, and no obvious next move. The question is not
*"what does the literature say about this mutation"* — that question has already
been asked and answered with nothing. The question is:

> **What else is druggable about this tumour, what class of therapy can reach it,
> has anyone reached it before, how would this patient actually get it, and does
> it layer safely on what they are already taking?**

That is the reasoning pattern. It has six moves:

| # | Move | Question |
|---|---|---|
| 1 | **Exhaustion** | Are the standard genomic leads actually dead? |
| 2 | **Target** | What is druggable here, regardless of mutation status? |
| 3 | **Modality** | What *class* of therapy can reach that target at all? |
| 4 | **Agent** | Which specific agents exist in that class? |
| 5 | **Access** | Has anyone done this, and how does this patient get it? |
| 6 | **Combination** | What layers safely onto the current plan? |

Every one of those moves ends in the same place: **an option, with its evidence,
with its sources, with its confidence stated, that a clinician can verify and
overrule.**

The system's job is to run that reasoning transparently. It is not to produce a
recommendation. The distinction is load-bearing and appears again in §5 and §7.

### Why the tier split has to go

The old architecture ran Tier 2 (structural prediction) *only when Tier 1
(evidence retrieval) found nothing*. That encodes two claims, both wrong:

- **That computational prediction is a fallback for missing evidence.** It is
  not. It is a different kind of signal about a different question, useful
  sometimes and irrelevant most of the time, and it does not become more true
  because the literature was silent.
- **That "mutation" is the entry point.** Most of the reasoning above never
  touches a mutation. A target from expression data has no variant to score. A
  radioligand, a CAR-T, or an oncolytic virus has no binding pocket to model.

The fix is not to flatten everything into one undifferentiated pile — see §5,
where the *evidence-class* distinction is preserved far more strictly than the
tier split ever preserved it. The fix is that **execution order stops implying
epistemic rank.** Every signal generator runs when it is *relevant*, not when
something else failed, and each one labels what kind of claim it is making.

---

## 2. What actually carries over

The instinct on reading §1 is that this is a rewrite. It is not. It is a
re-wiring plus four genuinely new components, and being precise about which is
which is the difference between shipping and restarting.

The existing `Athena` repo has **538 passing unit tests** and several modules
that already solve problems the reframe was about to re-raise:

### Already built, already correct — carry over unchanged

| Module | Why it survives the reframe intact |
|---|---|
| `covalent.py` | **Already gates covalent inhibitors out of binding scoring.** The critique that "mCSM-lig/Vina will predict retained binding for osimertinib when C797 is gone" was true of an earlier version and is now explicitly handled — curated list *plus* a SMARTS warhead scan, because the list will never be complete and a missed covalent drug fails in the dangerous direction. Do not rebuild this. |
| `tier1/retrieval.py` | **Already enforces "no citation, no item"** as a hard unconditional rule at every retrieval mode, and returns `filtered_count` to the caller rather than logging it. The "citable, verifiable synthesis" requirement is not a thing to build — it is a thing already enforced structurally. |
| `graph.py` | **Already makes a computational signal structurally incapable of impersonating documented evidence** — separate node label, separate edge type, no shared parent class, and a deliberate *absence* of any `citation_url` field. This is the single most important invariant in the whole system (§5) and it is already load-bearing in code. |
| `ligand_identity.py` | InChIKey identity with an explicitly flagged connectivity-layer fallback, replacing name-substring matching. Correct, and reusable well beyond binding. |
| `proximity.py` | The one structural claim that survived validation as a *measurement*. See §4 stage 7 and §6 for exactly how far it may be taken. |
| `tier1/graph_schema.py`, `graph_connection.py`, `civic_loader.py`, `pubmed_loader.py`, `civic_verify.py` | The FalkorDB evidence spine. Schema is acyclic-by-construction, and `civic_verify` asserts on *parsed response shape*, not status codes. Mature. |
| `uniprot.py`, `ensembl.py`, `mutation_validation.py`, `http_retry.py`, `cache.py` | Identity, validation, retry, and caching plumbing. Entirely orthogonal to the reframe. |
| `candidates.py` + `dgidb.py`/`opentargets.py`/`chembl.py`/`pubchem.py` | Becomes the *small-molecule* agent source under stage 4, rather than the only one. Widened, not replaced. |

### Re-wired — same code, different position

| Module | Change |
|---|---|
| `pipeline.py` | Rewritten as the stage orchestrator of §4. The module-calling logic mostly survives; the tier gating and the mutation-only entry gate do not. |
| `tier1_contract.py`, `tier1_adapter.py` | The cross-repo seam they exist to protect is gone — one package now. Collapse into direct calls. Keep the lazy-import discipline for `falkordb` and `sentence-transformers`. |
| `binding.py`, `vina_dock.py`, `mcsm_lig.py`, `labeling.py` | Demoted from product claim to one optional signal generator. Kept in-repo, kept tested, **not** on the presented path. §6. |
| `validation.py` | The pre-committed-criteria harness generalises well. New signals get their own pre-committed criteria in the same shape. |

### Genuinely new — this is the actual build

1. **Target discovery that does not require a mutation** (stage 3)
2. **Modality mapping** — target → therapy class (stage 4)
3. **Access-pathway registry** — regulatory route, expanded access, India-first (stage 6)
4. **Transparent synthesis** — scorecard with adjustable weights (stage 9)

Four components. Not a rewrite.

---

## 3. The core idea: one signal graph, no tiers

Everything the system knows about a patient converges on a single structure:

```
PatientProfile
  └── Target (a protein/pathway worth hitting, + why we think so)
        └── ModalityOption (a therapy class that can reach it, + why)
              └── Agent (a specific drug/product/construct)
                    ├── Signal (documented evidence)
                    ├── Signal (trial availability)
                    ├── Signal (access pathway)
                    ├── Signal (structural/computational — when relevant)
                    └── Signal (combination interaction vs current plan)
```

A **Signal** is the atomic unit. Every signal carries the same envelope:

```python
@dataclass(frozen=True)
class Signal:
    kind: SignalKind              # documented_evidence | trial | access_pathway
                                  # | computational | combination | cohort_context
    evidence_class: EvidenceClass # §5 — the hard part
    claim: str                    # what is being asserted, in one sentence
    source: Source | None         # citation URL for documented; method for computed
    confidence: Confidence        # stated, never inferred by an LLM
    caveats: tuple[str, ...]      # non-empty whenever the signal is qualified
    computed_at: datetime
    generator_version: str
```

Three rules make this work, and all three already have precedent in the current
codebase:

- **A signal with `kind="documented_evidence"` and no `source.url` is never
  constructed.** Already enforced in `tier1/retrieval.py`.
- **A computational signal has a `method`, not a citation, and no field shaped
  like one.** Already enforced in `graph.py`.
- **Filtered-out signals are counted and returned, never silently dropped.**
  Already enforced in `tier1/retrieval.py` as `filtered_count`.

Signal generators run **in parallel over the target set**, not in sequence gated
on each other's failure. A generator that has nothing to say returns an empty
list with a reason — never `None`, never a silent gap.

---

## 4. The pipeline

Nine stages. Each is independently testable, each declares what it does when it
has nothing, and none of them gate a later stage on having succeeded.

### Stage 1 — Intake

**In:** whatever the patient actually has. Mutation(s), expression data, pathway
alterations, IHC results, tumour type, treatment history, current regimen.
**Out:** `PatientProfile`.

The old entry gate demanded a validated missense mutation. That gate is deleted.
A profile with *only* a tumour type and a treatment history is a valid input that
still reaches stages 3–6. A profile with a mutation gets more signals, not
permission to proceed.

**Nothing case:** an empty profile is rejected at intake with a stated reason. It
is the one hard gate in the system.

### Stage 2 — Exhaustion assessment

**In:** `PatientProfile`. **Out:** `ExhaustionAssessment`.

Answers: have the standard genomic leads actually been tried, and are they dead?
This is **routing context, not a gate.** It changes how results are ordered and
framed; it never blocks a stage.

Its real job is honesty: if a patient has a standard-of-care option they have not
tried, that must surface *first and loudly*, above anything exploratory. A system
that surfaces radioligand therapy to someone who has not yet tried first-line
chemotherapy is dangerous, and this stage is the guard against that.

**Nothing case:** insufficient treatment history → `unknown`, stated explicitly,
exploratory options carry an extra caveat.

### Stage 3 — Target identification

**In:** `PatientProfile`. **Out:** `list[Target]`, each with a provenance record.

Targets come from four sources, and **the source determines the strength of the
claim, permanently**:

| Source | Strength | Notes |
|---|---|---|
| Patient's own mutation in a known druggable gene | Strong | Existing path |
| Patient's own expression/IHC data | Strong | New — this is Sid's actual path |
| Evidence graph — variants already typed as `OVEREXPRESSION`/fusion/amplification | Moderate | **Cheaper than it looks:** `tier1/retrieval.py` notes only 4 of 37 loaded variants carry `hgvs_p`; the rest are already named expression/fusion/amplification events. The graph already holds non-mutation targets. |
| Public cohort data (TCGA/GEO) for this tumour type | **Weak — background only** | See the warning below |

> **Epistemic warning, non-negotiable.** "This gene is overexpressed in this
> tumour *type*" does not establish "this gene is overexpressed in *this
> patient's* tumour," and neither establishes "this gene is a druggable target
> for this patient." Cohort-derived targets are rendered as clearly-labelled
> background context and **may never appear as a recommended option.** Getting
> this wrong is the single most likely way this system produces confident
> nonsense. The project's own standing rule — no evidence, no claim — applies
> with full force here.

**Nothing case:** zero targets → stages 4–8 return empty with a reason, stage 9
renders "no druggable target identified from the available profile" and says what
additional data would change that.

### Stage 4 — Modality mapping

**In:** `Target`. **Out:** `list[ModalityOption]`.

This is the architectural shift. The question changes from *"which small molecule
binds this protein"* to *"what class of therapy can reach this target at all."*

Modality classes: small molecule · monoclonal antibody · antibody-drug conjugate ·
radioligand conjugate · CAR-T / engineered cell · oncolytic virus · neoantigen
vaccine · checkpoint inhibitor · bispecific engager.

Feasibility is a property of the target, not a lookup: surface accessibility
gates antibody/ADC/CAR-T/radioligand; intracellular localisation gates small
molecule; expression specificity vs normal tissue gates anything cytotoxic-
delivering. These are derivable and citable, not guessed.

**Honest cost note.** DGIdb, Open Targets, and ChEMBL are small-molecule-centric
— that part of the critique is correct. But this is not "no data exists": ChEMBL
carries molecule-type classification, Open Targets covers some biologics, and the
cell/virus/radioligand modalities live in **trial registries** rather than drug
databases, which stage 5 already has to query. The build is a curated
target→modality feasibility table seeded from those sources, not an ML problem.

**Nothing case:** no modality reaches this target → said plainly, with the
blocking property named (e.g. "intracellular, no small-molecule chemistry
known").

### Stage 5 — Agent identification

**In:** `ModalityOption`. **Out:** `list[Agent]`.

Small molecules keep the existing DGIdb → Open Targets → ChEMBL → PubChem path.
Other modalities resolve primarily through trial registries and approved-product
lists.

**Nothing case:** modality is feasible but no agent exists → this is *useful
information*, not a dead end, and is reported as such.

### Stage 6 — Access pathway

**In:** `Agent` + `PatientProfile`. **Out:** `list[AccessPathway]`.

**This is the highest-value component in the entire system and the cheapest to
build.** The therapy that mattered most in the motivating case was reachable
through a regulatory route that existed, worked, and was approved in 48 hours —
and the binding constraint was that *nobody knew the route existed*. That is not
a data problem. It is a curation problem, and curation is tractable.

Pathways to model: approved on-label · approved off-label · active trial
(recruiting, with eligibility pre-screen) · expanded access / compassionate use
(**US FDA Form 3926 individual-patient IND; India CDSCO compassionate-use and
named-patient import**) · named-patient import · manufacturer access programme.

Requirements:

- **India-first.** CTRI as a first-class registry alongside ClinicalTrials.gov,
  and Indian regulatory routes modelled with the same care as US ones. This is a
  documented existing gap (no trial source is loaded at all today, so half the
  activation rule was never evaluable) and it is squarely in scope now.
- **Precedent matters.** "Expanded access has been granted for this agent in this
  indication before" is a materially different claim from "a pathway
  theoretically exists," and the two must never render identically.
- **Every pathway cites its regulatory instrument.** No pathway without a source.

**Nothing case:** no route → stated, because "there is no access route" is
genuinely actionable information for a clinician.

### Stage 7 — Signal attachment

**In:** `Agent`. **Out:** `list[Signal]`, from generators running in parallel.

- **Documented evidence** — CIViC + PubMed via the existing graph. Unchanged, and
  already citation-enforced.
- **Trial availability** — from stage 6, re-expressed as a signal.
- **Structural / computational** — *only when all of:* the agent is a small
  molecule, the patient has a missense mutation in the target, `covalent.py` does
  not gate it, and a structure covering the residue exists. In practice this is a
  minority of cases, which is the correct outcome and not a deficiency. Emits
  **proximity** (a measurement) — see §6 for why it does not emit a binding
  delta.
- **Cohort context** — weak-class background only, never an option driver.

**Nothing case:** each generator independently returns empty-with-reason. An
agent with no signals still appears, marked as having none.

### Stage 8 — Combination reasoning

**In:** candidate `Agent` + patient's current regimen. **Out:** `list[Signal]`
of kind `combination`.

**Scoped deliberately narrow, for safety.** The system reports:

- **Mechanism class of each** — whether two agents act through categorically
  different mechanisms or the same one.
- **Documented interactions** — cited, from real interaction sources.
- **Documented overlapping toxicities** — cited.

The system does **not** generate novel combination regimens, does not assert
synergy, and does not rank combinations. Mechanistic complementarity is
*displayed as a fact about mechanism classes* and left for the clinician to
interpret.

This is the most clinically dangerous component in the system and the one where
an ungrounded inference does the most harm. The read-only scoping is the whole
safety design, not a placeholder to be relaxed later.

**Nothing case:** no current regimen recorded → skipped, stated.

### Stage 9 — Transparent synthesis

**In:** everything. **Out:** the rendered result.

Per option, always visible: evidence class · access route and its precedent
strength · evidence level · known toxicity · mechanism class · every citation,
clickable.

**Scorecard dimensions are shown separately and weighted adjustably by the
clinician.** There is no hidden composite score, and specifically **no
LLM-generated score.** Any ordering the system applies is stated, and any weight
it uses is visible and changeable.

Every computational signal carries the existing Tier 2 disclaimer text verbatim.

---

## 5. Evidence classes — what flattening the tiers must **not** flatten

Removing the tier gate removes a false *execution* hierarchy. It must not remove
the true *epistemic* one. Every signal is permanently one of:

| Class | Meaning | Rendering rule |
|---|---|---|
| `documented` | A human published this, with a citation | Citation always shown, always clickable |
| `computed` | A model or measurement produced this | Method + version + disclaimer, **never** a citation-shaped field |
| `regulatory` | A pathway defined by an instrument | Instrument cited; precedent stated separately from theoretical availability |
| `contextual` | Cohort/background, not about this patient | Visually subordinate; can never drive an option |

`graph.py` already enforces the `documented` / `computed` separation structurally
— separate node label, separate edge, no shared parent, no citation field on the
computed side. **Extend that same structural enforcement to all four classes.
Do not weaken it into a string tag on a shared class.** A distinction that is
merely documented will eventually be blurred by someone in a hurry; a distinction
that is structurally impossible to blur will not.

---

## 6. What we deliberately do not build, and do not claim

- **No binding-affinity delta as a product claim.** It failed its pre-committed
  validation 0/9. The measured deltas (0.02–0.56 kcal/mol) sit *inside* the
  methods' own published error bar (1.0–2.19 kcal/mol) — the instrument is
  20–100× less precise than the effect. That gap does not close with better code.
  The code stays in-repo and tested; it does not appear in output.
- **No proximity claim beyond what it passed.** Proximity is a *measurement* from
  experimental coordinates, reproducible to the decimal. Its pre-committed
  criterion was evaluated and **failed 7/8** (EGFR C797S missed, because the
  distance is measured to whatever ligand was co-crystallised, which is often not
  the drug in question). It ships as a **measurement with its caveat attached**,
  never as a validated classifier. The failure is reported, not buried.
- **No cohort-derived target presented as a patient-specific finding.** §4 stage 3.
- **No generated combination regimens.** §4 stage 8.
- **No LLM composite scores.** §4 stage 9.
- **No MdrDB-derived data or weights in any commercial framing.** The licence is
  academic-use-only and explicitly non-transferable; the TSV must not be
  committed. If this is pitched as a product, that needs a written commercial
  licence first.
- **No treating an unimplemented source as a negative finding.** If no trial
  source is loaded, the system says the trial half was not evaluated — it does
  not say "no trial found." This rule already exists in the codebase and must
  survive the port.

---

## 7. Build order

Sequenced by value ÷ cost, with the cheap high-integrity work first.

| # | Work | Cost | Why here |
|---|---|---|---|
| 1 | **Port + re-wire to the signal graph.** One package, stage orchestrator, `Signal` envelope, four evidence classes enforced structurally. | Medium | Everything else needs the spine. Mostly moving working, tested code. |
| 2 | **Access-pathway registry** (stage 6), India-first, with real trial sources | Medium | Highest differentiation per unit effort in the whole system. Curated, citable, and closes a known gap where half an existing rule was never evaluable. |
| 3 | **Modality mapping** (stage 4) | Medium | The conceptual centre of the reframe. Curated feasibility table, not ML. |
| 4 | **Transparent synthesis** (stage 9) | Low–Medium | Nothing above is presentable without it. |
| 5 | **Target discovery — patient-data path only** (stage 3, strong sources) | Medium | Real capability. Deliberately excludes the cohort path. |
| 6 | **Exhaustion assessment** (stage 2) | Low | Small, and it is the standard-of-care safety guard. |
| 7 | **Combination reasoning — read-only** (stage 8) | Medium | Last on purpose. Highest harm potential; scope must not drift. |
| 8 | *Cohort/TCGA background context* | High | Only if 1–7 are genuinely done. Weak-class rendering only. |

Structural signals (proximity, gated by `covalent.py`) come along free in step 1 —
already built, already tested, already correctly scoped.

---

## 8. Invariants carried over — do not renegotiate these

These were paid for with real failures. They survive the reframe unchanged.

1. **No evidence, no claim — and no silent gaps either.** Anything filtered is
   counted and surfaced, never log-only.
2. **Every external call gets its own exception type, caught at the call site.**
   No bare `except`, no `except Exception`. This class of bug was found and fixed
   **five separate times** across `uniprot`/`ensembl`/`rcsb`/`alphafold`/ligand
   prep — every instance being an external call whose `try:` did not wrap it.
3. **Dependency injection everywhere.** Every client is an injectable parameter.
   This is why the suite runs offline.
4. **Validation criteria are pre-committed and never fitted afterward.** Write
   the criterion, commit it, *then* evaluate. A partial pass is a fail and is
   reported as one.
5. **Calibration status rides on every label.** Nothing uncalibrated can be
   mistaken for validated.
6. **A status code is not a shape check.** Verify parsed response structure
   against live APIs; a `200 text/html` returning zero results must fail loudly.
7. **The seam is where the bugs are.** Both integration bugs that mattered passed
   every mocked unit test. Integration tests against real data are not optional.
8. **A tooling limitation is never reported as a scientific result.** A case that
   fails because a library crashed is not evidence about the method's accuracy.
9. **Cached results are always shown as cached.** Never presented as live.
10. **Lazy-import heavy dependencies** (`falkordb`, `sentence-transformers`,
    `openmm`) so an unrelated caller never pays for them.

---

## 9. Open decisions

Flagged rather than silently resolved — each needs a real answer before the
component that depends on it is built.

1. **Where does the access-pathway registry's data actually come from?** Curated
   by hand seeded from FDA/CDSCO instruments, or scraped from expanded-access
   listings? Curation is more defensible and slower. Recommend curated seed,
   scoped to the modalities in stage 4, with provenance per row.
2. **What counts as sufficient patient expression data?** RNA-seq, IHC score,
   and a pathology report are very different evidentiary objects. The strength
   ladder in stage 3 needs a rung for each.
3. **CTRI access mode.** Whether CTRI is queryable programmatically or needs a
   cached snapshot is still unverified, and it gates stage 6's India-first claim.
4. **Does the graph stay FalkorDB?** Yes unless something forces otherwise — the
   schema, loaders, and live verification all work today, and Cypher plus a
   native vector index in one engine is exactly what stage 7 wants.
5. **Mode 3 semantic retrieval was never verified end to end.** It is written and
   its tests are blocked on `sentence-transformers`. Verify before relying on it
   in stage 7.

---

## 10. Repo layout

```
athena_ultimate/
├── ARCHITECTURE.md              ← this file
├── src/athena/
│   ├── profile/                 stage 1–2   intake, exhaustion
│   ├── targets/                 stage 3     target discovery + provenance
│   ├── modality/                stage 4     target → therapy class
│   ├── agents/                  stage 5     agent identification
│   ├── access/                  stage 6     regulatory + trial pathways
│   ├── signals/                 stage 7–8   generators, Signal envelope,
│   │                                        evidence classes
│   ├── synthesis/               stage 9     scorecard, rendering contracts
│   ├── graph/                   FalkorDB schema, loaders, retrieval
│   ├── structural/              proximity, covalent gate, ligand identity
│   │                                        (binding delta retained, unshipped)
│   └── clients/                 external APIs, one module per source
├── tests/
├── validation/                  pre-committed criteria + harnesses
└── docs/
```

Package renamed `secondlook` → `athena`. One package, no namespace rules, no
cross-repo pin — the failure mode that produced three divergent repos and a
stale cross-repo pin is structurally impossible here, and that is worth
preserving deliberately.
