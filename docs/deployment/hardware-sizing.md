# Hardware Sizing Guide

## What's actually measured here, and what isn't

The idle resource figures below are **real measurements** — `docker
stats` against the actual containers built from this repo's
`Dockerfile` / `web/Dockerfile`, running the real API and a real
Postgres instance with all three migrations applied, taken 2026-08-24.
They are not estimates.

What this does **not** include: sizing under real clinical-case load
(concurrent users querying the API, a full FalkorDB knowledge graph
loaded via `civic_loader.py`/`pubmed_loader.py`, Tier 2 structural
prediction running, or LLM synthesis calls in flight). That's a
follow-up load-testing pass against the seeded demo cases the issue
asks for — flagged honestly as not done yet, rather than presented as
though it were.

## Measured idle footprint

| Service | RAM (idle) | CPU (idle) | Notes |
|---|---|---|---|
| `api` | 60 MiB | ~0.2% | Freshly started, no requests served yet beyond a health check |
| `web` (nginx) | 12 MiB | ~0% | Static file serving only |
| `postgres` | 35 MiB | ~10%¹ | ¹ Momentary — measured seconds after the migration container applied 3 schema migrations; steady-state idle is lower |
| `falkordb` | 95 MiB | ~0.3% | Measured against an existing instance with prior data loaded — a truly empty instance would likely be lower |

**Idle total: well under 300 MiB of RAM across all four application
containers**, before any OS/Docker daemon overhead. This is a small
footprint by design — see the image-size finding below for the one
place it's heavier than it should be.

## Image sizes (measured, 2026-08-24)

| Image | Size |
|---|---|
| `athena-api` / `athena-migrate` (same image) | 635 MB |
| `athena-web` (nginx + built bundle) | 74 MB |
| `postgres:16` (upstream, unmodified) | 642 MB |
| `falkordb/falkordb:latest` (upstream, unmodified) | 744 MB |

**A real finding, not swept under the rug:** the API image is heavier
than it should be for an API-only deployment. `hgvs` is a *core*
dependency (not gated behind an extra), and it transitively pulls in
`pysam` (wraps `htslib`, a compiled genomics library) — a Tier
2/mutation-notation concern, not something the REST API itself needs at
runtime for case-memory/diff-engine/synthesis operations. Slimming this
is a reasonable follow-up (either making `hgvs` itself extras-gated, or
building a separate, smaller `api`-only image that doesn't import
anything from `mutation_validation.py`'s dependency chain) but wasn't
done as part of this pass — flagged, not silently fixed, since it would
change what `pip install -e ".[api]"` actually installs.

## Sizing recommendation

Built on the measured baseline above plus known scaling factors — not a
second independent measurement:

| Scale | vCPU | RAM | Disk | Notes |
|---|---|---|---|---|
| **Small** (one hospital, single clinician workflow) | 2 | 4 GB | 20 GB | Idle footprint measured above (~300 MB) leaves ample headroom for Postgres/FalkorDB data growth and a handful of concurrent API requests. Does not include the `structural`/`semantic` extras (Tier 2 docking, sentence-transformers) — see below. |
| **Medium** (regional network, several hospitals sharing one instance) | 4–8 | 8–16 GB | 50–100 GB | FalkorDB's memory footprint grows with the evidence graph's size (more CIViC/PubMed/trial data loaded); Postgres grows with case-history volume. Neither was load-tested here — treat this row as directional, not measured. |

**If running Tier 2 structural prediction** (`structural` extra: RDKit,
AutoDock Vina, Playwright) **or Mode 3 semantic retrieval**
(`semantic` extra: sentence-transformers, pulls ~2 GB via torch) **on
the same server**, add substantially more RAM — `README.md`'s existing
prerequisites table already documents ≥3 GB free disk for
`sentence-transformers` alone, from prior real experience ("a full disk
wedged Docker during development"). Those extras are not part of the
`docker-compose.yml` stack this guide sizes; they run as local Python
tooling, not containerized services, as of this pass.

## What would make this a stronger measurement

- Load-test against the actual seeded demo cases mentioned in the
  project's own docs, with concurrent API requests
- Measure FalkorDB's real memory footprint after a full `civic_loader.py`
  + `pubmed_loader.py` run, not an instance with unknown pre-existing
  data
- Measure Postgres growth over a realistic case-history volume (e.g. 100
  cases × 50 events each) rather than an empty schema
- Address the `hgvs`/`pysam` image-size finding above, then re-measure
