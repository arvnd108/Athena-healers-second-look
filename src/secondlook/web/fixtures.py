"""Load Subsystem M's fixture set.

Subsystem L (issue #13, the REST API) does not exist yet, and issue #14 is
built fixture-backed on purpose so it lands independently rather than
blocking on it. The fixtures live under `web/fixtures/` and are read by
*both* consumers -- the Vite client imports the same JSON files this module
loads -- so the server-rendered fallback and the client it stands in for
cannot show different things.

Shapes mirror IMPLEMENTATION_PLAN.md SS5's routes exactly:

    case.json     -> GET /api/cases/{id}
    changes.json  -> GET /api/cases/{id}/changes
    queue.json    -> GET /api/cases/{id}/queue
    findings.json -> GET /api/findings/{id}, keyed by finding id

so wiring the real API in is a base-URL swap, not a reshape.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: Repo root: src/secondlook/web/fixtures.py -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = _REPO_ROOT / "web" / "fixtures"


def _read(name: str, fixture_dir: Path | None = None) -> dict:
    path = (fixture_dir or FIXTURE_DIR) / name
    if not path.is_file():
        # No silent gap: a missing fixture is a broken checkout, and
        # returning {} here would render an empty case as if it were a
        # case with nothing in it.
        raise FileNotFoundError(
            f"Subsystem M fixture {name} not found at {path}. "
            "Expected it under web/fixtures/ in the repository root."
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_all(fixture_dir: str | None = None) -> dict:
    """`{case, changes, queue, findings}` -- the whole fixture set.

    Cached because the dev server re-reads it per request otherwise, and a
    fixture set that changes between two requests in one page load would
    make the no-JS view disagree with itself.
    """
    directory = Path(fixture_dir) if fixture_dir else None
    return {
        "case": _read("case.json", directory),
        "changes": _read("changes.json", directory),
        "queue": _read("queue.json", directory),
        "findings": _read("findings.json", directory),
    }
