# Athena — SecondLook

AI second-opinion copilot for **rare and treatment-exhausted cancers**. Given a
patient's gene, mutation and cancer type, Athena answers two questions:

1. **What is already documented?** — Tier 1 searches a curated knowledge graph
   (CIViC + PubMed in FalkorDB) for real, citable clinical evidence.
2. **What can be computed when nothing is documented?** — Tier 2 sources an
   experimental protein structure and measures where the mutation sits relative
   to a candidate drug's binding pocket.

Tier 1 runs first. Only when it finds nothing strong does Tier 2 run — so a
computed signal never displaces real evidence. **That two-tier split is the
current implementation, not the intended end state** — see
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the target design (one signal graph
across target discovery, therapy modality, access pathway, and evidence, with
no tier gating one path on another's failure) and §2 there for exactly which
of today's modules already carry over unchanged.

> **Before you demo this, read [`ISSUES.md`](ISSUES.md) §1.** The pipeline runs
> end to end, but it does **not** currently produce a validated binding-change
> prediction, and its documented fallback — binding-site *proximity* — has
> since been evaluated against a pre-committed criterion and also did not pass
> cleanly (7/8 non-ambiguous cases; see `validation/results.md`'s "Proximity
> criterion evaluation" section). Neither result is a bug to hide — both are
> honestly reported, pre-committed evaluations. Describe them as what they are.

---

## Quick start

```bash
cd athena_ultimate
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                    # 549 tests, ~10s, fully offline
```

If that prints `549 passed`, the project is working. Everything below is for
running the parts that need live services. This repo has no remote configured
yet — set one before treating any `git clone`/push instructions elsewhere as
applicable here.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Full install](#2-full-install)
3. [Start the knowledge graph](#3-start-the-knowledge-graph)
4. [Run the tests](#4-run-the-tests)
5. [Use the pipeline](#5-use-the-pipeline)
6. [Run gold-standard validation](#6-run-gold-standard-validation)
7. [Pre-demo checklist](#7-pre-demo-checklist)
8. [Troubleshooting](#8-troubleshooting)
9. [Repo map](#9-repo-map)

---

## 1. Prerequisites

| Need | Why |
|---|---|
| **Python 3.11** | 3.12+ untested; `hgvs`/`pysam` are fussy |
| **Docker Desktop** | Runs FalkorDB, the knowledge graph |
| **≥3 GB free disk** | `sentence-transformers` pulls torch (~2 GB). A full disk wedged Docker during development — see [§8](#8-troubleshooting) |

macOS / Apple Silicon also needs:

```bash
brew install libpq openssl readline swig boost open-babel
```

**AutoDock Vina has no macOS arm64 wheel** and needs a patched source build.
Follow [`docs/local-setup.md`](docs/local-setup.md) *before* installing the
`structural` extra, not after.

---

## 2. Full install

Athena is **one Python package** — `secondlook`, with Tier 1 as the
`secondlook.tier1` subpackage. One install, no path juggling.

```bash
python3.11 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"           # core + test tooling — enough for `pytest`
pip install -e ".[structural]"    # Vina/RDKit/PDBFixer — read docs/local-setup.md first
pip install -e ".[semantic]"      # sentence-transformers, for retrieval Mode 3
```

Verify:

```bash
python -c "
from secondlook.pipeline import run_tier2
from secondlook.tier1 import retrieval
from secondlook.tier1_adapter import Tier1RetrievalPolicy
print('Athena OK — both tiers importable')"
```

---

## 3. Start the knowledge graph

```bash
docker compose up -d              # FalkorDB on :6379, browser UI on :3000

# Confirm CIViC's live API shape BEFORE loading. Don't skip this — the loader
# parses a specific response shape, and this is what catches an upstream change
# before it corrupts the graph.
python -m secondlook.tier1.civic_verify

python -m secondlook.tier1.civic_loader     # ~37 variants, 57 response edges
python -m secondlook.tier1.chembl_enrich    # drug_class — retrieval Mode 2 needs it
```

Optional, for Mode 3 semantic retrieval over literature:

```bash
python -m secondlook.tier1.pubmed_loader "NTRK1 fusion sarcoma larotrectinib" --max-results 20
```

Check it's alive:

```bash
docker exec falkordb redis-cli ping        # -> PONG
```

Browse the graph visually at <http://localhost:3000>.

---

## 4. Run the tests

Integration tests hit live services and a running FalkorDB. They're
**deselected by default**, and they **skip** (never fail) when the database is
down.

```bash
pytest                       # 549 unit tests, offline    -> expect "549 passed"
pytest -m integration        # live services + FalkorDB   -> expect ~33 passed
pytest tests/tier1           # Tier 1 only
pytest tests/test_pipeline.py -v
```

**Expected: 549 passing, 0 failures.** Anything else is a regression — do not
demo until it's green.

---

## 5. Use the pipeline

### Tier 2 alone

```python
from secondlook.pipeline import run_tier2

out = run_tier2("ABL1", "T315I", restrict_to_drugs=("imatinib",))
print(out.status)                        # complete | partial | failed
for item in out.results:
    print(item.drug, item.signal_type, item.binding_site_distance_angstrom)
```

Two contract-valid result shapes, told apart by `signal_type`:

| `signal_type` | Meaning | Populated |
|---|---|---|
| `binding_delta` | A binding change was computed | `method`, `delta_score`, `label` |
| `proximity_only` | Position vs pocket measured; **no prediction made** | `proximity` only; the other three are `None` |

`proximity_only` is **not a failure**. It's the honest output wherever a
defensible delta couldn't be computed. Every current gold-standard result is
this shape — see [`ISSUES.md`](ISSUES.md) §1.

### Tier 1 activation, then Tier 2

```python
from secondlook.tier1_adapter import Tier1RetrievalPolicy

d = Tier1RetrievalPolicy().decide(gene="TP53", mutation="NP_000537.3:p.Arg273His")
print(d.state, d.should_run_tier2, d.reason)
for item in d.tier1_results:
    print(item.evidence_level, item.drug, item.citation_url)
```

| State | Tier 2 runs? | Means |
|---|---|---|
| `strong_hit` | No | Level A/B CIViC evidence for this exact variant |
| `weak_hit` | Yes | Evidence exists but weaker, or only for a related variant |
| `no_hit` | Yes | Nothing documented |
| `manual_override` | Yes | Clinician asked for it explicitly |

Thresholds come from `tier1-retrieval.md` §Activation and are implemented in
**exactly one place** — `tier1_adapter._classify`. Don't duplicate them.

### Write results back to the graph

```python
from secondlook.tier1_adapter import FalkorDBGraphSink
sink = FalkorDBGraphSink()
for signal in out.signals:
    sink.emit(signal)
```

Produces
`(Gene)-[:HAS_VARIANT]->(Variant)-[:HAS_COMPUTATIONAL_SIGNAL]->(StructuralSignal)-[:PREDICTS_BINDING_CHANGE]->(Drug)`.

---

## 6. Run gold-standard validation

```bash
python validation/run_gold_standard.py            # ~40 min, live services
python validation/run_gold_standard.py --report-only   # regenerate from cache
```

Writes `validation/results.md`. Successful runs cache to `validation/cache/`
(gitignored), and a failed re-run **will not** overwrite a good cached result.

> The criteria in `docs/validation-plan.md` were **pre-committed** and must
> never be adjusted after seeing results. Falling below threshold is a
> documented outcome with a defined fallback — not something to fix by moving
> the bar.

---

## 7. Pre-demo checklist

Run all five. All five must pass.

```bash
# 1. package imports
python -c "from secondlook.pipeline import run_tier2; from secondlook.tier1 import retrieval; print('1 OK')"

# 2. unit tests green
pytest -q | tail -1

# 3. graph reachable
docker exec falkordb redis-cli ping

# 4. activation works both ways
python -c "
from secondlook.tier1_adapter import Tier1RetrievalPolicy
p = Tier1RetrievalPolicy()
a = p.decide(gene='TP53', mutation='NP_000537.3:p.Arg273His')
b = p.decide(gene='ABL1', mutation='NP_005148.2:p.Thr315Ile')
print('in  scope:', a.state, '-> Tier 2:', a.should_run_tier2)
print('out scope:', b.state, '-> Tier 2:', b.should_run_tier2)
assert b.state == 'no_hit' and b.should_run_tier2
print('4 OK')"

# 5. live graph round-trip
pytest tests/test_tier1_integration.py -m integration -q | tail -1
```

---

## 8. Troubleshooting

**`pytest` collects 0 tests / import errors**
Install in editable mode: `pip install -e ".[dev]"`. Don't run pytest against a
copied source tree without installing.

**`ModuleNotFoundError: falkordb` or connection refused on :6379**
`docker compose up -d`. Integration tests skip cleanly when it's down; if they
*error* instead, that's a bug — report it.

**Docker hangs, `docker ps` never returns**
Almost always a full disk. Check `df -h`, then `docker builder prune`. If the
daemon is already wedged, quit Docker Desktop from the menu bar and relaunch —
the CLI can't recover it.

**`vina` won't install on Apple Silicon**
Expected. See [`docs/local-setup.md`](docs/local-setup.md) for the patched
source build.

**A test that passed yesterday fails today**
Check the upstream service before assuming a regression — Ensembl and UniProt
outages both caused false alarms during development:

```bash
curl -o /dev/null -w "%{http_code}\n" https://rest.uniprot.org/uniprotkb/P00533.fasta
curl -o /dev/null -w "%{http_code}\n" https://files.rcsb.org/download/2ITY.pdb
```

---

## 9. Repo map

```
src/secondlook/
├── pipeline.py          Step 7 — run_tier2(), composes everything
├── labeling.py          Step 6 — delta -> label, orientation-normalised
├── proximity.py         Binding-site distance bands
├── vina_dock.py         AutoDock Vina WT-vs-mutant docking
├── binding.py           mCSM-lig scoring + covalent gate
├── covalent.py          Refuses non-covalent scoring of covalent drugs
├── graph.py             StructuralSignal — the graph contract
├── tier1_contract.py    Tier boundary contracts + inert placeholders
├── tier1_adapter.py     Real Tier 1 backing (activation policy, graph sink)
├── validation.py        Gold-standard harness, pre-committed criteria
└── tier1/               Tier 1 — CIViC loader, retrieval Modes 1-3, graph schema

tests/                   549 unit tests (tests/tier1/ for Tier 1)
validation/              Gold-standard runner and results
docs/                    Specs, setup guides, research notes
ISSUES.md                Every known problem and its candidate fix
```

| Doc | Covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **The target design — read this first.** Why the tier split is going away, what carries over unchanged, build order. |
| [`ISSUES.md`](ISSUES.md) | Every known problem, cause, and fix in the *current* implementation. Read before presenting. |
| [`docs/architecture.md`](docs/architecture.md) | System architecture of the *current* tier-based implementation (frontend/backend/Tier 1/Tier 2) |
| [`docs/validation-plan.md`](docs/validation-plan.md) | Pre-committed pass/fail criteria |
| [`docs/local-setup.md`](docs/local-setup.md) | macOS install hurdles |
| [`docs/briefing/`](docs/briefing/) | Standalone explanation of the method and its limits |

---

## The one rule

**Never state a medical fact that was not retrieved or computed in a traceable
step.** Every claim carries its citation or its method. No exceptions — this is
what separates Athena from a chatbot that sounds confident.
