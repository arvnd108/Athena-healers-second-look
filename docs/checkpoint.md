# Tier 2 Checkpoint

Living document. Update this whenever a prompt lands or the scope changes — it should always answer "what's built, what's verified, what's left" without needing to reread the whole conversation history.

**Historical note:** this log was written while Tier 2 lived in its own repo, before the two tiers were merged into this one. Tier 1's retrieval spec is now `docs/tier1-retrieval.md`; the remaining docs (`architecture.md`, `api-contracts.md`, `data-sources.md`, `ui-flow.md`, `validation-plan.md`, `tech-stack-setup.md`) describe both tiers and stay put since Tier 2's own sections in them are load-bearing.

## Workflow convention

Each pipeline step is scoped as a numbered implementation step in `docs/tier2-verification.md`, built one at a time. After each step lands it gets reviewed before the next is written: code read in full, tests rerun independently rather than trusted from a report, edge cases probed by hand, live integration claims re-verified from scratch where practical. Bugs found in review are fixed in the same pass, with a regression test added.

## What's built and verified

All five modules below are independently unit-tested, have live-integration tests against the real external services, and were re-verified by hand in review (not just taken on the report's word). All follow the same failure-handling contract: every external call has a dedicated exception type, caught at the specific call site, never a bare `except`/`Exception`, and falls through to the next source or an explicit "unavailable" result rather than crashing.

| Step | Module | Status | Notes |
|---|---|---|---|
| 1. Mutation parsing & validation | `src/secondlook/mutation_validation.py`, `uniprot.py` | Done | HGVS/shorthand normalization, UniProt canonical-sequence fetch, hard reference-residue gate, out-of-scope mutation type rejection. Fails closed on every edge case tried (lowercase shorthand, nonsense, synonymous, out-of-range position, garbage input) — nothing gets marked `valid` incorrectly. |
| 2. AlphaMissense pathogenicity | `src/secondlook/alphamissense.py`, `ensembl.py` | Done | Uses Ensembl VEP REST (returns AlphaMissense inline) instead of the originally-spec'd dbNSFP download — a real simplification found during source verification, not just an implementation choice. Classifies with the paper's numeric thresholds, not Ensembl's own `am_class`. **Bug found + fixed in review:** unhandled `EnsemblError` on gene-resolution/VEP failure crashed the whole lookup instead of returning a structured "unavailable" result. |
| 3. Structure sourcing | `src/secondlook/structure.py`, `rcsb.py`, `alphafold.py`, `esm_atlas.py` | Done | RCSB PDB (ligand-bound preferred) → AlphaFold DB (canonical model, residue-level pLDDT via CA B-factor) → ESM Atlas (off by default, never on demo path). Never folds the mutant. pLDDT < 70 flags low reliability but doesn't suppress the result. **Bug found + fixed in review:** same crash-instead-of-fallback pattern as Step 2, now for `RcsbError`/`AlphaFoldError` — fixed, and this is when the "catch the specific error at every call site" rule got written into subsequent prompts explicitly. |
| 4. Candidate drug generation | `src/secondlook/candidates.py`, `dgidb.py`, `opentargets.py`, `chembl.py`, `pubchem.py` | Done | DGIdb → Open Targets → ChEMBL priority chain, PubChem SMILES with a token-bucket rate limiter (≤5 req/s). Per-candidate PubChem failure isolation (one bad drug name doesn't drop the rest of the shortlist). Clean run — no repeat of the Step 2/3 crash bug, because the pattern was named explicitly before implementation began. |
| 5. Binding affinity — mCSM-lig | `src/secondlook/mcsm_lig.py`, `binding.py` | Done | Automates the mCSM-lig web form via headless Playwright (no documented API exists) rather than replaying a guessed HTTP POST, since the real submit target is JS-owned. Confirmed live the form is synchronous (no job queue). Resolves PDB chain from the actual structure, not assumed `"A"`. **Bug found + fixed in review:** binding-scoring failures (no HET code, apo structure, mCSM+Vina both down) were reusing the pLDDT-based "structure unreliable" message even when the structure was fine (confirmed live on a 1.01 Å experimental structure) — a real misleading-output risk given this project's "never state a fact you didn't compute" rule. Split into a distinct `BINDING_UNAVAILABLE_MESSAGE`. |
| 5b. Binding affinity — Vina/smina fallback | `src/secondlook/vina_dock.py` | Done | For candidates mCSM-lig can't handle (no PDB HET code — the common case). Receptor prep via PDBFixer (protonation, pH 7.4) + meeko → PDBQT. Mutant built by in-place side-chain substitution (PDBFixer `applyMutations` on the existing backbone), never re-folded, per the spec's explicit prohibition. Grid box centers on the co-crystallized ligand's bounding box + 5 Å padding. Each Vina run isolated in its own subprocess with a hard timeout. Verified independently in review: reran the live 2Z4O/D30N dock outside pytest (same delta, 0.035), re-fetched 9C5S from scratch to confirm it really is a ligand-less 14-residue fragment (residues 1–14 only, HET = ACE/NH2/SO4/HOH), and confirmed the grid-box heuristic picks the real ligand (065, 80 atoms) over a co-located acetate ion (4 atoms). |

## Source verification (data-sources.md, Tier 2 rows)

Done as a standalone pass before any code was written — see `docs/tier2-verification.md` for the full results. All Tier 2 sources confirmed live and working. Two findings changed the plan: AlphaMissense (see Step 2 above) and mCSM-lig (confirmed automatable after inspecting the live form, rather than assumed to need manual/cached-only submission).

| 6. Threshold labeling | `src/secondlook/labeling.py` | Built, uncalibrated | Three-label conversion via versioned per-method constants. **Critical finding: the two scoring methods use opposite sign conventions** — mCSM-lig reports reduced binding as *negative* (`-2.056` → "Destabilizing"), while Vina's `mutant - wildtype` delta is *positive* for reduced binding. A single shared threshold would have inverted the label on every docking-scored candidate, i.e. most real traffic. `MethodCalibration.orientation` normalizes both onto one canonical scale. Thresholds are **provisional** and every label says so (`calibration_status="provisional"`) until the gold-standard run sets them. |
| 7. Output assembly | `src/secondlook/pipeline.py`, `graph.py`, `tier1.py`, `cache.py` | Built | `run_tier2()` composes Steps 1→6, loops 5–6 per candidate, emits `api-contracts.md`-shaped items with the §10 disclaimer from one shared constant. Every §8 condition returns a structured `FailureObject` with the exact documented message. Emits graph-ready `StructuralSignal` objects (returns them; does not write to FalkorDB — no `falkordb` dependency added). |

### Bugs found by the first real end-to-end run

Building Step 7 exposed three defects that every module passing its own tests had hidden, because they live in the *seams between* steps:

1. **`RcsbPdbClient` never downloaded coordinates.** `_entry_hit` returned entry metadata only, so every PDB-sourced `StructureResult` carried `pdb_text=None` — silently disabling binding scoring on the *primary* structure path, while still reporting `status="found"` with `reliability_flag="high"`. Fixed with `fetch_pdb_text`. The existing test fixture had no `pdb_text` key either, so it mirrored the buggy client rather than catching it.
2. **No residue-coverage check when selecting a PDB entry.** The RCSB search matches any entry linked to the accession, including complexes containing only a short *peptide*. For BRAF V600E it selected **8VSO — a 14-3-3 sigma / BRAF-phosphopeptide ternary complex** whose BRAF chain spans residues 255–263 and whose only atom numbered 600 is a water molecule. Note `_select_alphafold_model` already did this check; the PDB path never did. Fixed with `covers_residue` plus coverage-aware selection in `search_by_uniprot`.
3. **The validation report claimed measured failure before ever running.** An empty cache rendered as "Pass rate 0/9 — NOT MET" and "Hard-required positive controls: FAILED" — a fabricated accuracy claim about an unrun pipeline. Now renders as "NOT YET RUN" with no verdict, and partial runs are labeled a lower bound.

4. **Transport errors escaped four clients entirely.** `uniprot.py`, `ensembl.py`, `rcsb.py`, and `alphafold.py` each wrapped only `response.raise_for_status()` in their `try` block, while the `httpx` call itself sat on the line *before* it. So HTTP status errors were correctly wrapped, but **connection resets, DNS failures, and timeouts escaped as raw `httpx` exceptions**. UniProt has no fallback source, so one escaped all the way out of `run_tier2` and crashed the pipeline instead of returning §8's "Timeout / external API error". Not a theoretical outage case — RCSB resets connections under sustained load, which is how this surfaced. Fixed in all four by moving the request call inside the `try`, plus a specific `UniProtLookupError` catch in `run_tier2`. Verified against a real reset: the pipeline now returns `status="failed"` with the exact §8 message and `retryable=True`.

   The split was informative — the five clients written *after* the "catch the specific error at every call site" rule went into the prompts (`esm_atlas`, `dgidb`, `opentargets`, `chembl`, `pubchem`) were all correct; the four written before it were all wrong.

5. **Result items shipped two full protein sequences each.** `mutation_validated` was built with `asdict(validation)`, which includes `wildtype_sequence`/`mutant_sequence` — internal working state that is **not** in `api-contracts.md`'s validator schema. With a 20-candidate shortlist that is 40 copies of the sequence sent to a UI that has no use for them. Now rendered through `mutation_validated_payload()`, which emits exactly the ten documented fields.

### Bugs found by the first *successful* docking run

Fixing 1–5 got the pipeline as far as attempting a real dock. Four more defects sat behind that, each independently fatal, and none reachable by the existing unit tests because those used small hand-written PDB fixtures rather than real deposited structures.

6. **Receptor was never restricted to one chain.** meeko's `Polymer.from_pdb_string` cannot prepare a multi-chain asymmetric unit. Worse than the crash: `ligand_hetatm_coords` took the largest HET group *anywhere in the file*, so on a two-copy entry the grid box could sit on chain A's pocket while the mutation being scored was in chain B — docking a real ligand into a site the mutation cannot influence, and reporting the delta as meaningful. Fixed with `select_docking_chain`, which requires the residue and a ligand in the **same** chain and refuses otherwise.

7. **Alternate conformations were passed through.** Crystal structures model side chains in multiple conformations, each atom repeated with an altLoc code. RDKit then sees the same atom twice and reports an over-bonded carbon. 8C7X carries 72 such atoms per conformation; 3OG7 has none — which is why this only broke on some structures. Fixed in `protein_atoms_only`, which now keeps the primary conformation and blanks the altLoc column.

8. **PDBFixer's terminal OXT broke RDKit sanitization.** The added C-terminal carboxylate oxygen is chemically sound (C–OXT 1.27 Å) but lands ~1.83 Å from CA — inside RDKit's `proximityBonding` cutoff — so RDKit bonds OXT to CA *as well as* C, giving a five-bonded alpha carbon and the `Explicit valence for atom # 2 C, 5` failure. Fixed by stripping OXT before receptor prep; it is a single peripheral atom at a chain terminus, far from any pocket.

9. **Structures deposited as the mutant were refused.** `place_sidechain` asserted the structure held the wild-type residue at the position. But well-studied oncogenic mutations are routinely crystallized *as the mutant* — 3OG7 is BRAF with GLU already at 600 — so the assumption rejected precisely the best-characterised structures for the cases that matter most. Now the scorer detects which form is deposited and builds the other by substitution in whichever direction is needed; `delta_score` stays mutant-minus-wild-type either way, with a test pinning that the sign cannot flip based on what happened to be crystallized.

Bugs 1 and 2 share the pattern already fixed once in `binding.py`: **the structure step over-claims success, and the resulting failure gets misattributed to the binding methods.** Bugs 1, 2, 4, and 5 share a deeper one: **every module passed its own tests, because each defect lives in the seam between two steps.** Step 7 is the first thing that exercises those seams — which is why building it found five bugs in modules that were already "done and verified."

## The scope limit the gold-standard run exposed

The single most important result of building Step 7 is not a bug — it is a measurement of what docking can and cannot answer.

Mutation-to-ligand distance, measured on the structures the pipeline itself selected:

| Case | Distance to ligand | Docking delta |
|---|---|---|
| EGFR T790M | **3.5 A** | largest observed, correct direction |
| EGFR C797S | 10.1 A | ~ -0.02 |
| BRAF V600E | 12.2 A | ~ +0.05 |
| KIT D816V | 16.6 A | not scoreable |

Only T790M sits within direct-contact range, and it is the only case whose delta rises above noise. Everything past ~10 A collapses to |delta| < 0.06 regardless of the known clinical direction.

That is the method behaving correctly. Docking scores direct steric and chemical contact; **KIT D816V confers imatinib resistance allosterically** (activation-loop stabilization), and BRAF V600E acts through conformational activation — neither is visible to a docking score at any threshold. `docs/research/tier2-maximum-potential-roadmap.md` predicted exactly this, naming kinase conformational-state selection "the single largest silent error source" and bypass-pathway resistance as out of computational scope.

Consequences now implemented:

- `residue_min_distance_to_ligand()` records the distance on every docking result, so a delta can be read in context.
- `MUTATION_CONTACT_MAX_ANGSTROM = 8.0` gates scoring: beyond it the pipeline **refuses to emit a delta** and returns `MUTATION_OUTSIDE_POCKET_MESSAGE`, which states the measured distance and names allosteric mechanisms as out of scope. Deliberately distinct from `BINDING_UNAVAILABLE_MESSAGE` — nothing failed, and describing an informative negative result as a scoring failure would repeat the pLDDT-message bug.

### Known blocker: mutant-receptor prep (blocks 3 of 9 cases)

ABL1 T315I is the one case that is squarely *inside* docking's domain and still does not score, so it is worth recording precisely rather than lumping in with the out-of-range cases.

Everything up to the dock succeeds: structure 5HU9 (residue 315 and a 43-atom ligand both in chain A, **3.5 A** apart), wild-type residue confirmed THR, ligand prep fine, and the **wild-type** receptor preps cleanly through meeko (210,400 bytes). Only the **mutant** receptor fails:

```
meeko receptor prep failed: Updated 1 H positions but deleted 4
```

The built ILE315 looks correct — 19 atoms with standard PDB naming (N, CA, C, O, CB, CG1, CG2, CD1 plus the expected hydrogens).

**Diagnosis narrowed to an upstream incompatibility.** What has been ruled out:

- *Not* an RDKit chemistry problem: `Chem.SanitizeMol` passes cleanly on the mutant after `strip_terminal_oxt` + `resolve_overvalence`, and zero atoms are over-valent.
- *Not* fixable by re-protonating: stripping the mutant's hydrogens and re-running PDBFixer protonation reproduces the failure exactly.
- *Not* fixable by supplying heavy atoms only: that fails for the **wild-type too** (`deleted 5`), so meeko requires the hydrogens.

The raise comes from `meeko/polymer.py::update_H_positions` (line ~454), reached via `_build_rdkit_mol`. That routine deletes hydrogens needing repositioning, re-adds them with `Chem.AddHs`, copies the new coordinates back, and raises when the number it repositioned differs from the number it deleted. On the mutant it deletes 4 and repositions 1. The wild-type of the same structure passes, so the discrepancy is introduced by `applyMutations` at the substituted residue.

**Next step for whoever picks this up:** this looks like a meeko bug or an unsupported input shape rather than something to patch around in this repo. Either report it upstream with 5HU9/T315I as the reproducer, or replace the receptor-preparation path (meeko `Polymer`) with a different PDBQT route for the mutant. Do not work around it by skipping the mutant receptor — the WT-vs-mutant delta is the entire measurement.

The same failure blocks **ABL1 T315I (5HU9), ALK G1202R and ALK I1171T (both 4Z55)** — every one of which obtained a good ligand-bound experimental structure. This is the single highest-value remaining fix: resolving it would take the scoreable sample from 2 drug-pairs to 5. Until then it is a **tooling limitation**, not evidence about the method's accuracy, and must not be reported as a prediction failure.

**Implication for the pass criteria:** most of `validation-plan.md`'s nine cases are allosteric or conformational resistance, which this method cannot reach. A low pass rate is therefore expected and is a statement about the method's domain, not about the implementation. `validation-plan.md`'s own fallback clause — binding-pocket proximity plus AlphaMissense, with the binding delta dropped from the UI — is the honest V1, and the distance measurement above is precisely the input that fallback needs. **Do not widen the threshold to manufacture a pass rate.**

Also worth recording: the gefitinib delta moved from +0.556 to +0.313 across two runs with a fixed seed, purely from receptor-preparation changes (OXT stripping, valence resolution). The delta is sensitive to prep choices and should be treated as a weak ordinal signal, never a precise quantity.

## What's left (in scope for this repo)

Ordered roughly by what blocks a working end-to-end demo first.

0. **Fix mutant-receptor prep — the current blocker (3 of 9 cases).** See "Known blocker" above: PDBFixer's `applyMutations` output is rejected by meeko's residue templates while the wild-type of the same structure preps cleanly. Highest-value remaining work.

0b. **Superseded — original blocker, now fixed.** With bugs 1 and 2 above fixed, BRAF V600E now sources a covering structure and resolves its chain, but still produces zero scored candidates: the Vina path fails during ligand/receptor prep (RDKit emits `Explicit valence for atom # 2 C, 5, is greater than permitted`, though both candidate SMILES parse cleanly in isolation — so the message is likely from receptor/co-crystal prep, not the ligand). Diagnosis was cut short when RCSB began resetting connections after repeated runs; **back off RCSB for a while before resuming**, and reuse `validation/cache/` rather than re-fetching. Until this is resolved the gold-standard run cannot produce numbers, and therefore Step 6's cutoff cannot be calibrated.

1. **Gold-standard run — DONE, see `validation/results.md`.** Result: **0/9 overall, 0/5 in-scope, both hard-required controls failed, demo-ready NO.** Four cases were correctly declined as out of docking range; three are blocked by the prep bug in #0; two (EGFR T790M) scored but landed `uncertain`. Per `validation-plan.md`'s fallback clause the binding delta/label should be dropped from the UI in favour of binding-pocket proximity plus AlphaMissense. **Do not widen the threshold to manufacture a pass rate.**

2. **Calibrate the Step 6 cutoff.** `labeling.py` ships `MCSM_LIG_CALIBRATION` and `DOCKING_CALIBRATION` as **provisional** constants; `CALIBRATION_STATUS = "provisional"` rides on every label so nothing can be mistaken for validated. Calibration means: run #1, sweep cutoffs with `sweep_thresholds()` (cheap — re-labels cached deltas, no re-docking), pick the values, set `CALIBRATION_STATUS = "calibrated"`, bump `LABELING_VERSION`, record which run produced them. Also decides whether the delta/label can be shown at all, or whether `validation-plan.md`'s fallback clause applies (binding-pocket proximity + AlphaMissense only).

3. **Cache the two locked demo cases.** Cache layer built (`cache.py`) with the `cached: true` marker set on read per `ui-flow.md` Screen 3; **no demo case is actually locked or cached yet**. Needs #1 first, since the Tier 2 demo case must be one the pipeline gets right. Note `validation/cache/` is gitignored — committed demo payloads must be force-added deliberately.

4. **Write the Vina-vs-mCSM-lig coverage note** (spec deliverable 5). The framing is already in code — `DOCKING_CALIBRATION` carries `confidence="low"` and an `accuracy_note` that explicitly does not borrow mCSM-lig's ρ=0.67, and `ValidationReport.method_split` computes the ratio — but the **actual measured proportion** cannot be stated until #1 runs. Do not estimate it.

5. **Verification pass before calling this done** (spec deliverable 7). Full suite plus `pytest -m integration` against live services, and one real assembled output validated field-for-field against `api-contracts.md`. Currently blocked on the same live-service access as #0/#1.

6. **Out of scope for this repo, tracked elsewhere:** CTRI access-mode verification (Tier 1), the FastAPI orchestration layer that decides Tier 1 vs. Tier 2 routing (`architecture.md`'s "Orchestration backend" — spans both tiers, likely owned alongside Tier 1), the frontend, and the LLM synthesis layer's citation-enforcement check.

## Open questions for the user

- Should the other Tier-2-adjacent docs (`data-sources.md`, `api-contracts.md`, `architecture.md`, `ui-flow.md`, `validation-plan.md`) also get split so this repo holds only their Tier 2 sections? They currently stay whole because Tier 2's own code depends on content in them (e.g. the "Tier 2 result item" schema in `api-contracts.md`) — splitting them means either duplicating that content or introducing a cross-repo reference. Flagging rather than doing it silently.
- Who owns Step 7's output assembly boundary with the orchestration layer — does this repo need to expose it as an importable function only (current assumption), or does Tier 2's scope here extend to a small HTTP surface of its own?

## Tier 1 <-> Tier 2 integration — DONE, 2026-08-22

Tier 1 is real and wired in. `github.com/Gagansharma-code/Athena_Tier_1` cloned
to `../Athena_Tier_1` (sibling of this repo, deliberately not vendored).

**Test counts after integration: Tier 2 332 unit, Tier 1 206 unit, all passing.**

### What had to change to make the two repos coexist

1. **`secondlook.tier1` collided.** Tier 2 shipped a placeholder *module*
   `src/secondlook/tier1.py`; Tier 1 ships a real *package*
   `src/secondlook/tier1/`. Both claim the same import path and only one can
   win. The real implementation keeps the name; Tier 2's contracts moved to
   **`tier1_contract.py`** (and `tests/test_tier1.py` -> `test_tier1_contract.py`).
2. **`src/secondlook/__init__.py` was deleted.** `secondlook` must be a PEP 420
   implicit namespace package for both distributions to contribute to it;
   a real `__init__.py` in either repo makes it a regular package that hides
   the other. Re-verified at 306 tests (the Tier 1 README's note was written
   against 154). Nothing imported from the top level except one submodule
   import, which namespace packages support.
3. **Both installs must use compat editable mode.** Setuptools' default
   finder-based editable install resolves `secondlook` to a single directory
   and does not participate in namespace merging.

### New in this repo

- **`tier1_adapter.py`** — the bridge. `Tier1RetrievalPolicy` implements
  `ActivationPolicy` against Tier 1's live graph (Mode 1, falling back to Mode
  2); `FalkorDBGraphSink` implements `GraphSink`. Every `secondlook.tier1`
  import is **lazy**, inside the function that needs it, so Tier 2 still runs
  standalone and the two packages do not import-cycle.
- **`tests/test_tier1_adapter.py`** — 26 unit tests, fake graphs, no network.
- **`tests/test_tier1_integration.py`** — 6 live tests against FalkorDB.

### Two integration bugs found only by running against real data

Both passed every unit test with a mock. Recording them because the lesson
generalises: the seam is where the bugs are.

**(a) StructuralSignal schema drift.** Tier 2 emitted 13 node properties; Tier
1's `STRUCTURAL_SIGNAL` NodeType declared 7. Nothing crashed — `NodeType` is
documentation, not an ORM — so a query written from the declaration would
simply never have read the six missing fields, including
`binding_site_distance_angstrom`, which is *the entire output* of every case in
the current gold-standard run. Fixed in Tier 1's schema, plus a new test there
that compares the declaration against what Tier 2 actually emits, so it cannot
drift silently again.

**(b) The `hgvs_p` variant-join mismatch.** CIViC writes
`NP_005148.2:p.Thr315Ile`; Tier 2 computes `p.Thr315Ile`. The sink's original
`MERGE (v:Variant {hgvs_p: ...})` on the bare form matched **0 of 1** existing
TP53 R273H nodes on the live graph — it would have minted a second, orphan
Variant for a variant the graph already had, unreachable from its Gene and
invisible to every Tier 1 traversal. The sink now *resolves* (find-then-create)
against `(Gene)-[:HAS_VARIANT]->(Variant)` with an accession-anchored suffix
match, links any variant it does create to its Gene, and stamps provenance on
it. Verified: 9 signals -> 9 full `Gene->Variant->Signal->Drug` paths, 0 orphans,
and EGFR T790M correctly resolves to one Variant carrying two drug signals.

### The gold-standard cases are all `no_hit` — and that is correct

Tier 1's loaded scope is 17 rare cancers (`civic_scope.yaml`): NTRK fusions,
EWSR1::FLI1, PAX3::FOXO1 and similar. **None of EGFR/ABL1/KIT/BRAF/ALK are in
it.** Those nine cases were chosen for having well-established ground truth to
validate the *structural method*, not because they are the target population.

So on the real graph every gold-standard case returns `no_hit` and hands off to
Tier 2. That is the product working exactly as designed — Tier 1 covers
documented rare-cancer evidence, Tier 2 fires precisely where Tier 1 has
nothing. Verified live alongside three in-scope variants (FGFR1 N546K, TP53
R273H, KDR A1065T) which correctly return `weak_hit` with real CIViC items.

Worth saying plainly in the presentation: this is the handoff the whole
two-tier architecture exists for, and it is now demonstrable end to end.

### Blocked: Mode 3 (semantic retrieval) — disk, not code

`sentence-transformers` (torch, ~2 GB) will not install: **263 MiB free on a
228 GB disk, 100% full.** 7 Tier 1 integration tests are blocked on this and
nothing else. Modes 1 and 2 are unaffected and fully working.

Docker holds ~5.4 GB of images and 1.58 GB of build cache, most of it stale
projects from four months ago. Reclaiming is the user's call, not this
session's.

### Cross-repo pin points at the wrong Tier 2 repo — needs fixing after the push

`Athena_Tier_1/pyproject.toml` declares:

```
secondlook @ git+https://github.com/amartyatatspandey/Athena_tier_2.git@37eeff2792...
```

Two problems. It names **amartyatatspandey/Athena_tier_2**, which is this repo's
`upstream`, not its `origin` (**KrishG7/Athena_tier_2**) — the repo the team
actually works in. And `37eeff2` predates everything: Steps 6-7, the audit
fixes, and the `src/secondlook/__init__.py` removal the namespace merge
requires. Tier 1's own TODO comment already flags the staleness; the wrong-repo
half is new.

It causes no local breakage — both packages are installed editable from the
working trees, so the pin is never resolved. It breaks anyone who installs Tier
1 from a clean checkout.

Fix after pushing Tier 2, substituting the new SHA:

```bash
cd ../Athena_Tier_1
NEW_SHA=$(git -C ../Athena_tier_2 rev-parse HEAD)
sed -i '' "s|git+https://github.com/amartyatatspandey/Athena_tier_2.git@[0-9a-f]*|git+https://github.com/KrishG7/Athena_tier_2.git@${NEW_SHA}|" pyproject.toml
```

Also delete the now-stale TODO above that line — the `__init__.py` removal it
waits on has landed.

### Live-service state at time of writing

- FalkorDB: running (`docker compose up -d` in `../Athena_Tier_1`), healthy.
- Graph loaded: CIViC (37 variants, 57 `PREDICTS_RESPONSE_TO` edges) + ChEMBL
  drug-class enrichment. PubMed not loaded (needs a query argument).
- Tier 1 integration: 20 passed, 7 blocked on the disk issue above.
- Tier 2 integration: **13 passed, 6 skipped (FalkorDB down), 0 failed.**

### The three Tier 2 integration failures — all triaged, all resolved

1. **AlphaMissense/VEP returned `unavailable`.** Transient Ensembl outage, not
   a regression — passes on re-run with no code change. Same class of event as
   the UniProt 500s that produced the empty gold-standard run.

2. **`test_live_end_to_end_tp53_r175h` asserted every result carries a label,
   method and float delta.** True before the proximity fallback, wrong after
   it: a `proximity_only` item deliberately has none of the three, and TP53
   R175H produces exactly that. The test now branches on the `signal_type`
   discriminator and asserts each shape is *internally complete* — which is
   the stronger check, because it also catches a half-populated item the old
   assertion would have waved through.

3. **`test_live_vina_delta_on_2z4o_with_small_ligand` used D30N.** 2Z4O is a
   two-chain structure: chain A is residues 1-99, chain B is 101-199, and
   ligand 065 sits in chain B. `select_docking_chain()` correctly refuses D30N
   — a grid box around a chain-B ligand says nothing about a chain-A mutation.
   The fixture predated that rule. Now split in two: the live-delta test uses
   **D130N** (2.76 A from ligand 065, same chain), and a new companion test
   asserts D30N stays refused with both chains named in the message.

**Finding worth carrying forward:** D130N docked successfully —
`status=scored`, delta 0.05 kcal/mol. So **the Vina integration is not
broken**. The gold-standard run scores nothing for structure-specific reasons
(the meeko mutant-receptor prep failure on 5HU9 / 4Z55), not because
WT-vs-mutant docking is non-functional. That distinction matters for the
presentation: "the method does not reach these mechanisms" is the honest
claim, not "our docking does not work."

No assertion was placed on that delta's magnitude or sign — 0.05 kcal/mol is
far below docking's 1.0-2.19 error bar, and asserting a direction would encode
noise as an expectation.

### One more bug, found by accident when Docker died

`tests/test_tier1_integration.py`'s skip guard caught
`falkordb.exceptions.ConnectionError` — an attribute that **does not exist**
(`falkordb.exceptions` defines no error classes; the client raises redis-py's
types). And `redis.exceptions.ConnectionError` does not subclass the builtin
`ConnectionError`/`OSError`, so the fallback in the same `except` did not cover
it either. Result: with FalkorDB down the suite **errored** instead of
skipping. Now catches `redis.exceptions.ConnectionError`/`TimeoutError` and
skips with the command to start the database. Verified by running with the
daemon actually down: 6 clean skips.
