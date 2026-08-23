"""Load a curated guideline knowledge base for exhaustion assessment.

Subsystem Q. Curated YAML, never scraped, never LLM-authored.

**This module must never invent or load real-sounding clinical guideline
content by default.** Issue #18 is explicit that the real knowledge base is
seeded by a clinician on the team. The files under `guideline_kb/` that
ship with this PR are fabricated test fixtures (`review_status:
synthetic_placeholder`). They are not NCCN/ESMO/ASCO-derived, they are not
unreviewed transcriptions of a real source, and they must not be presented
as guidance.

Three rules are enforced at load time and will refuse a file rather than
partial-load it:

1. **Every recommendation cites an instrument and a source URL.** A
   recommendation without a citation cannot be surfaced to a clinician.

2. **`review_status` is required and must be a known value.** Unknown
   statuses are refused. `synthetic_placeholder` is a categorically
   different marker from Subsystem J's `unreviewed`: J's `unreviewed`
   means "transcribed but not checked against the source by a specialist";
   `synthetic_placeholder` means "not real content at all, fabricated for
   testing the machinery."

3. **The default load path refuses `synthetic_placeholder` files.** Callers
   must pass `allow_synthetic=True` to load them. This is a hard safety
   gate, not a warning — the same shape as integration tests being
   deselected by default rather than opt-out. Nothing in a default run
   path sets that flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

GUIDELINE_KB_DIR = Path(__file__).resolve().parents[3] / "guideline_kb"

SYNTHETIC_PLACEHOLDER = "synthetic_placeholder"
UNREVIEWED = "unreviewed"
REVIEWED = "reviewed"
ALLOWED_REVIEW_STATUSES: frozenset[str] = frozenset({SYNTHETIC_PLACEHOLDER, UNREVIEWED, REVIEWED})


class GuidelineConfigError(RuntimeError):
    """A seed file is missing, malformed, or makes an unsupported claim."""


@dataclass(frozen=True)
class GuidelineRecommendation:
    """Validated, normalised shape of one YAML recommendations entry."""

    regimen: str
    line: int
    molecular_requirement: tuple[str, str] | None
    applicable_stages: tuple[str, ...]
    instrument: str
    source_url: str
    cancer_type: str
    review_status: str


@dataclass
class GuidelineLoadSummary:
    files_read: int = 0
    recommendations_loaded: int = 0
    rejected: list[str] = field(default_factory=list)
    synthetic_loaded: int = 0

    def render(self) -> str:
        lines = [
            f"files read              : {self.files_read}",
            f"recommendations loaded  : {self.recommendations_loaded}",
            f"synthetic loaded        : {self.synthetic_loaded}",
        ]
        if self.rejected:
            lines.append("REJECTED:")
            lines += [f"  {r}" for r in self.rejected]
        if self.synthetic_loaded:
            lines.append(
                "\nNote: synthetic_placeholder entries are fabricated test "
                "data, not real guidance. They must not be presented to a "
                "clinician."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class GuidelineKnowledgeBase:
    """In-memory index of recommendations by cancer_type.

    No graph connection, no database session — the same reason
    `compute_diff` and `match_trials` take plain data rather than a store.
    """

    by_cancer_type: dict[str, tuple[GuidelineRecommendation, ...]]

    def recommendations_for(self, cancer_type: str) -> tuple[GuidelineRecommendation, ...]:
        return self.by_cancer_type.get(cancer_type, ())


def validate_recommendation(
    entry: dict,
    *,
    cancer_type: str,
    review_status: str,
    path: Path | None = None,
) -> GuidelineRecommendation:
    """Return a normalised recommendation, or raise with a specific reason.

    `path` only names the file in the error message. It is optional so this
    can be called on a single entry from a test.
    """
    where = path.name if path is not None else f"<{cancer_type} recommendation>"

    for required in ("regimen", "instrument", "source_url"):
        value = entry.get(required)
        if not value or not str(value).strip():
            raise GuidelineConfigError(
                f"{where}: recommendation missing required field {required!r} -- "
                "a recommendation without a citation cannot be surfaced to a clinician"
            )

    line = entry.get("line")
    if not isinstance(line, int):
        raise GuidelineConfigError(f"{where}: recommendation missing required integer field 'line'")

    molecular = entry.get("molecular_requirement")
    molecular_requirement: tuple[str, str] | None
    if molecular is None or molecular == {}:
        molecular_requirement = None
    else:
        if not isinstance(molecular, dict):
            raise GuidelineConfigError(f"{where}: molecular_requirement must be a mapping")
        gene = molecular.get("gene")
        variant = molecular.get("variant")
        if not gene or not variant:
            raise GuidelineConfigError(
                f"{where}: molecular_requirement needs both 'gene' and 'variant'"
            )
        molecular_requirement = (str(gene), str(variant))

    stages = entry.get("applicable_stages") or []
    if not isinstance(stages, list):
        raise GuidelineConfigError(f"{where}: applicable_stages must be a list")

    return GuidelineRecommendation(
        regimen=str(entry["regimen"]).strip(),
        line=line,
        molecular_requirement=molecular_requirement,
        applicable_stages=tuple(str(s) for s in stages),
        instrument=str(entry["instrument"]).strip(),
        source_url=str(entry["source_url"]).strip(),
        cancer_type=cancer_type,
        review_status=review_status,
    )


def read_seed_file(path: Path) -> tuple[dict, list[GuidelineRecommendation]]:
    """(file-level fields, validated recommendations). Raises on any malformed entry."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except UnicodeDecodeError as exc:
        raise GuidelineConfigError(f"could not parse {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GuidelineConfigError(f"could not parse {path}: {exc}") from exc

    cancer_type = raw.get("cancer_type")
    config_version = raw.get("config_version")
    review_status = raw.get("review_status")
    if not cancer_type or not str(cancer_type).strip():
        raise GuidelineConfigError(f"{path.name}: needs `cancer_type`")
    if not config_version or not str(config_version).strip():
        raise GuidelineConfigError(f"{path.name}: needs `config_version`")
    if not review_status or not str(review_status).strip():
        raise GuidelineConfigError(f"{path.name}: needs `review_status`")

    status = str(review_status).strip()
    if status not in ALLOWED_REVIEW_STATUSES:
        raise GuidelineConfigError(
            f"{path.name}: unknown review_status {status!r}; "
            f"must be one of {sorted(ALLOWED_REVIEW_STATUSES)}"
        )

    entries = raw.get("recommendations")
    if not isinstance(entries, list) or not entries:
        raise GuidelineConfigError(f"{path.name}: declares no recommendations")

    header = {
        "cancer_type": str(cancer_type).strip(),
        "config_version": str(config_version).strip(),
        "review_status": status,
    }
    recs = [
        validate_recommendation(
            entry,
            cancer_type=header["cancer_type"],
            review_status=header["review_status"],
            path=path,
        )
        for entry in entries
    ]
    return header, recs


def load_guideline_kb(
    kb_dir: Path,
    *,
    allow_synthetic: bool = False,
) -> tuple[GuidelineKnowledgeBase, GuidelineLoadSummary]:
    """Walk `kb_dir` and return an in-memory KB plus a load summary.

    Default (`allow_synthetic=False`) raises on any `synthetic_placeholder`
    file — a hard safety gate, not a skip. Malformed files are rejected
    whole and reported on the summary, never partial-loaded, matching
    Subsystem J's `run_load`.
    """
    if not kb_dir.is_dir():
        raise GuidelineConfigError(f"no guideline-kb directory at {kb_dir}")

    summary = GuidelineLoadSummary()
    grouped: dict[str, list[GuidelineRecommendation]] = {}

    for path in sorted(kb_dir.glob("*.yaml")):
        try:
            header, recs = read_seed_file(path)
        except (GuidelineConfigError, ValueError) as exc:
            summary.rejected.append(str(exc))
            continue

        if header["review_status"] == SYNTHETIC_PLACEHOLDER and not allow_synthetic:
            raise GuidelineConfigError(
                f"{path.name}: review_status is {SYNTHETIC_PLACEHOLDER!r} — "
                "this file is fabricated test data, not real guidance. "
                "Pass allow_synthetic=True to load it in tests."
            )

        summary.files_read += 1
        if header["review_status"] == SYNTHETIC_PLACEHOLDER:
            summary.synthetic_loaded += len(recs)
        grouped.setdefault(header["cancer_type"], []).extend(recs)
        summary.recommendations_loaded += len(recs)

    by_cancer_type = {key: tuple(value) for key, value in sorted(grouped.items())}
    return GuidelineKnowledgeBase(by_cancer_type=by_cancer_type), summary
