# Local Setup (macOS, Apple Silicon)

Verified working on macOS 26 / arm64, Python 3.11.16, on 2026-08-21 — full suite 83/83 passing including live integration tests.

`tech-stack-setup.md` covers stack *choices*; this file covers the concrete install hurdles on Apple Silicon, all of which are non-obvious and cost real time to rediscover. Three dependencies do not install cleanly from `pip install -e ".[dev]"` alone.

## 0. Prerequisites

```bash
brew install python@3.11 libpq swig boost
```

Python must be **3.11** — `hgvs` 1.5.7 requires `>=3.10`, and the system Python on macOS is 3.9.

## 1. Create the venv

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
```

## 2. Core dependencies — the `psycopg2` hurdle

`hgvs` hard-requires `psycopg2` (source-build only; no wheels), which needs `pg_config` from `libpq`, which in turn links against OpenSSL. Without the flags below the build fails at either the configure step (`pg_config is required`) or the link step (`clang: linker command failed`).

```bash
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
export LDFLAGS="-L/opt/homebrew/opt/libpq/lib -L/opt/homebrew/opt/openssl@3/lib -L/opt/homebrew/opt/readline/lib"
export CPPFLAGS="-I/opt/homebrew/opt/libpq/include -I/opt/homebrew/opt/openssl@3/include"
export LIBRARY_PATH="/opt/homebrew/opt/libpq/lib:/opt/homebrew/opt/openssl@3/lib:/opt/homebrew/opt/readline/lib"

.venv/bin/pip install -e .
```

> **Note:** `psycopg2` is dead weight for this project. `mutation_validation.py` uses `hgvs` only for *parsing* (`hgvs.parser`, `AASub`, `HGVSParseError`) and never touches hgvs's UTA database provider. The dependency exists only because it's unconditional in hgvs's own metadata. If the Postgres toolchain ever becomes a blocker, replacing `hgvs` with a small self-contained protein-notation parser is a legitimate option — the project uses a regex pre-parse for shorthand notation already.

## 3. Wheel-available dev dependencies

```bash
.venv/bin/pip install "pytest>=8.0" "playwright>=1.49" rdkit scipy gemmi "meeko>=0.7.1" openmm pdbfixer
.venv/bin/playwright install chromium
```

`playwright install chromium` downloads ~95 MB and **fails intermittently** with `Download failure, code=1`. This is transient — just rerun it.

## 4. `vina` — requires a patched source build

AutoDock Vina publishes **no macOS wheel at all** (PyPI has Linux x86_64 only), so it must build from source, and two things block that on Apple Silicon:

1. **Boost isn't found.** `setup.py:locate_boost()` searches only a conda env, `/usr/local/include`, and `/usr/include` — never `/opt/homebrew`. Its conda branch is gated purely on the `CONDA_DEFAULT_ENV` env var and honors `CONDA_PREFIX`, so pointing those at Homebrew is enough. No conda install is needed.

2. **C++ standard mismatch.** `setup.py` hardcodes `-std=c++11` in `vina_compiler_options`, appended *after* any env `CXXFLAGS`, so it always wins. Boost 1.92's `boost/math/tools/type_traits.hpp` needs C++14+ and fails with a wall of `no member named 'remove_cv_t' in namespace 'std'` errors. The fix is a one-character patch to the sdist.

```bash
# Fetch and patch the sdist
curl -sL -o vina.tar.gz https://files.pythonhosted.org/packages/d2/2a/6746ef5e57b1c643e9fb24ad9e4fa520add7338736d50954e0fbc12ae52e/vina-1.2.7.tar.gz
tar xzf vina.tar.gz
sed -i.bak 's/"-std=c++11",/"-std=c++17",/' vina-1.2.7/setup.py

# Build with Boost located via the conda-branch shim
export CONDA_DEFAULT_ENV=homebrew-shim
export CONDA_PREFIX=/opt/homebrew
.venv/bin/pip wheel ./vina-1.2.7 --no-deps -w wheels/
.venv/bin/pip install wheels/vina-*.whl
```

The resulting wheel is cached in `wheels/` (gitignored) so recreating the venv doesn't mean rebuilding:

```bash
.venv/bin/pip install wheels/vina-1.2.7-cp311-cp311-macosx_26_0_arm64.whl
```

Verify:

```bash
.venv/bin/python -c "from vina import Vina; Vina(sf_name='vina', verbosity=0); print('ok')"
```

## 5. Running tests

```bash
.venv/bin/python -m pytest                # 72 unit tests, offline, ~2s (default)
.venv/bin/python -m pytest -m integration  # 11 live-service tests
.venv/bin/python -m pytest -m ""           # all 83 (~2m50s) — INCLUDES live network calls
```

Integration tests are **opt-in**, via `addopts = "-m 'not integration'"` in `pyproject.toml`, matching `tier2-implementation-spec.md` §3. A command-line `-m` overrides that `addopts` entry, so `-m integration` selects exactly the live tests and `-m ""` clears the filter entirely.

This matters beyond speed: the 11 integration tests hit Ensembl VEP, RCSB, AlphaFold DB, DGIdb, PubChem, and the **shared mCSM-lig academic server** — which has no documented rate limit and should not be hit casually on every local test run.

## Version note

`pyproject.toml` specifies `vina>=1.2.7` and `meeko>=0.7.1`, while `tier2-implementation-spec.md` §3 specifies exact pins (`vina ==1.2.7`, `meeko ==0.7.1`). Installed here: vina 1.2.7, meeko 0.7.1 — consistent with both. Worth reconciling the two documents.
