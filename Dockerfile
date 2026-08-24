# Athena API image. Also used, with a different command, to run Alembic
# migrations before the API starts -- see docker-compose.yml's `migrate`
# service. One image, two roles, so the migration environment can never
# drift from the API's own.
#
# Deliberately does NOT include the `structural` extra (RDKit, AutoDock
# Vina, Playwright, ...) or `semantic` (sentence-transformers / torch, ~2GB).
# Those are Tier 2 structural-prediction and semantic-retrieval concerns,
# not required to run the case-memory API a hospital actually deploys day
# to day -- see Subsystem O's hardware-sizing guide (docs/deployment/) for
# why keeping this image lean matters on a single mid-spec server.
# Multi-stage: `hgvs` (a core dependency, not just the `api` extra) pulls in
# plain `psycopg2` transitively, which builds from source and needs
# pg_config/libpq-dev + a compiler -- confirmed by an actual failed build,
# not assumed. Those build tools have no reason to exist in the final
# image, so they're isolated to this stage and never copied forward.
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --no-cache-dir -e ".[api]"

FROM python:3.11-slim

WORKDIR /app

# Runtime-only: libpq5, not libpq-dev -- no compiler needed to just
# connect to Postgres, only to build psycopg2 in the stage above.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Runs as a non-root user -- this image has no reason to run as root, and
# "no reason to" is the whole justification a container needs.
RUN useradd --create-home --uid 1000 athena
USER athena

EXPOSE 8000

CMD ["python", "-m", "secondlook.api"]
