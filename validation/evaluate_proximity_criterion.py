#!/usr/bin/env python
"""Evaluate the pre-committed binding-site proximity criterion.

The criterion and per-case mechanism labels live in
``docs/validation-plan.md`` ("Binding-site proximity fallback — pre-committed
criterion") and were committed before this script existed. This file copies
those labels so the check is executable; if they ever disagree with the plan,
the plan wins and this copy is wrong.

Does not retune band thresholds. Does not edit the criterion after seeing
results.

Usage::

    python validation/evaluate_proximity_criterion.py
    python validation/evaluate_proximity_criterion.py --no-write
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

MechanismClass = Literal["contact_mediated", "non_contact", "ambiguous"]
CaseVerdict = Literal["pass", "fail", "excluded"]

#: Copied from docs/validation-plan.md. Do not edit to fit an evaluation.
PRECOMMITTED_MECHANISMS: dict[tuple[str, str, str], MechanismClass] = {
    ("EGFR", "T790M", "gefitinib"): "contact_mediated",
    ("EGFR", "T790M", "osimertinib"): "contact_mediated",
    ("EGFR", "C797S", "osimertinib"): "contact_mediated",
    ("ABL1", "T315I", "imatinib"): "contact_mediated",
    ("KIT", "D816V", "imatinib"): "non_contact",
    ("KIT", "V560G", "imatinib"): "non_contact",
    ("BRAF", "V600E", "vemurafenib"): "non_contact",
    ("ALK", "G1202R", "crizotinib"): "contact_mediated",
    ("ALK", "I1171T", "crizotinib"): "ambiguous",
}

_CONTACT_BANDS = frozenset({"in_contact", "pocket_adjacent"})
_NON_CONTACT_BANDS = frozenset({"distant"})

RESULTS_PATH = REPO_ROOT / "validation" / "results.md"
SECTION_HEADING = (
    "## Proximity criterion evaluation "
    "(pre-committed in docs/validation-plan.md, evaluated after)"
)

_DISTANCE_ROW = re.compile(
    r"^\|\s*([A-Za-z0-9]+)\s+([A-Za-z0-9]+)\s*\|\s*([^|]+?)\s*\|\s*"
    r"([^|]+?)\s*\|\s*`?([^`|]+?)`?\s*\|$"
)


@dataclass(frozen=True)
class OverallVerdict:
    passed: bool
    n_scored: int
    n_correct: int
    n_fail: int
    n_excluded: int


@dataclass(frozen=True)
class CaseEvaluation:
    gene: str
    mutation: str
    drug: str
    mechanism: MechanismClass
    distance_angstrom: float | None
    band: str
    verdict: CaseVerdict


def evaluate_case(*, mechanism: MechanismClass, band: str) -> CaseVerdict:
    """Apply the pre-committed expected-band table to one classified case."""
    if mechanism == "ambiguous":
        return "excluded"
    if mechanism == "contact_mediated":
        return "pass" if band in _CONTACT_BANDS else "fail"
    if mechanism == "non_contact":
        return "pass" if band in _NON_CONTACT_BANDS else "fail"
    raise ValueError(f"unknown mechanism class: {mechanism!r}")


def overall_verdict(results: list[CaseVerdict]) -> OverallVerdict:
    """Pass only when every non-excluded case matches. Partial is a fail."""
    n_excluded = sum(1 for r in results if r == "excluded")
    n_fail = sum(1 for r in results if r == "fail")
    n_correct = sum(1 for r in results if r == "pass")
    n_scored = n_correct + n_fail
    return OverallVerdict(
        passed=n_scored > 0 and n_fail == 0,
        n_scored=n_scored,
        n_correct=n_correct,
        n_fail=n_fail,
        n_excluded=n_excluded,
    )


def parse_results_md_distances(text: str) -> dict[tuple[str, str, str], float | None]:
    """Read distances from the 'Binding-site proximity' table in results.md."""
    in_table = False
    distances: dict[tuple[str, str, str], float | None] = {}
    for line in text.splitlines():
        if line.startswith("## Binding-site proximity"):
            in_table = True
            continue
        if in_table and line.startswith("## ") and not line.startswith("## Binding-site"):
            break
        if not in_table:
            continue
        match = _DISTANCE_ROW.match(line)
        if not match:
            continue
        gene, mutation, drug, distance_cell, _band = match.groups()
        drug = drug.strip()
        distance_cell = distance_cell.strip()
        if distance_cell in {"—", "-", ""}:
            distances[(gene, mutation, drug)] = None
            continue
        number = re.search(r"[\d.]+", distance_cell)
        if number is None:
            distances[(gene, mutation, drug)] = None
            continue
        distances[(gene, mutation, drug)] = float(number.group())
    return distances


def distances_from_cache(cache_dir: Path) -> dict[tuple[str, str, str], float | None]:
    """Same payload path `run_gold_standard.py` uses; distance only, re-banded below."""
    try:
        from secondlook.cache import load_payload
        from secondlook.validation import GOLD_STANDARD_CASES, find_drug_result
    except ImportError:
        return {}

    found: dict[tuple[str, str, str], float | None] = {}
    for case in GOLD_STANDARD_CASES:
        payload = load_payload(case.gene, case.mutation, cache_dir=cache_dir)
        if payload is None:
            continue
        item = find_drug_result(payload, case.drug)
        prox = (item or {}).get("proximity") or {}
        if "distance_angstrom" not in prox and not prox.get("band"):
            continue
        found[(case.gene, case.mutation, case.drug)] = prox.get("distance_angstrom")
    return found


def load_distances(results_path: Path, cache_dir: Path) -> dict[tuple[str, str, str], float | None]:
    cached = distances_from_cache(cache_dir)
    if len(cached) == len(PRECOMMITTED_MECHANISMS):
        return cached
    if not results_path.exists():
        raise FileNotFoundError(
            f"Need {results_path} or a complete validation cache; found {len(cached)} cached cases."
        )
    parsed = parse_results_md_distances(results_path.read_text())
    # Cache wins per-case when present; results.md fills the rest.
    merged = dict(parsed)
    merged.update(cached)
    return merged


def evaluate_gold_standard(
    distances: dict[tuple[str, str, str], float | None],
) -> list[CaseEvaluation]:
    from secondlook.proximity import classify_proximity

    rows: list[CaseEvaluation] = []
    for (gene, mutation, drug), mechanism in PRECOMMITTED_MECHANISMS.items():
        key = (gene, mutation, drug)
        distance = distances.get(key)
        band = classify_proximity(distance)
        rows.append(
            CaseEvaluation(
                gene=gene,
                mutation=mutation,
                drug=drug,
                mechanism=mechanism,
                distance_angstrom=distance,
                band=band,
                verdict=evaluate_case(mechanism=mechanism, band=band),
            )
        )
    return rows


def render_markdown(rows: list[CaseEvaluation], verdict: OverallVerdict) -> str:
    outcome = "PASS" if verdict.passed else "FAIL"
    lines = [
        SECTION_HEADING,
        "",
        "Evaluated against the mechanism labels and pass condition committed in",
        "`docs/validation-plan.md` **before** this script existed. Bands are",
        "re-derived with `proximity.classify_proximity` from the recorded distances;",
        "thresholds were not moved. A partial tally is a fail under that wording.",
        "",
        f"**Criterion: {outcome}** — "
        f"{verdict.n_correct}/{verdict.n_scored} non-ambiguous cases matched; "
        f"{verdict.n_fail} failed; {verdict.n_excluded} excluded as ambiguous.",
        "",
        "| Case | Drug | Mechanism (pre-committed) | Distance | Band | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        shown = "—" if row.distance_angstrom is None else f"{row.distance_angstrom:.1f} A"
        lines.append(
            f"| {row.gene} {row.mutation} | {row.drug} | `{row.mechanism}` | "
            f"{shown} | `{row.band}` | {row.verdict.upper()} |"
        )
    c797s = next(
        (r for r in rows if r.gene == "EGFR" and r.mutation == "C797S"),
        None,
    )
    if c797s is not None and c797s.verdict == "fail" and c797s.band == "distant":
        lines += [
            "",
            "EGFR C797S is contact-mediated (covalent ATP-site attachment residue) and",
            "banded `distant` relative to the co-crystallized ligand. Per the",
            "pre-committed ligand-identity caveat that ligand is frequently not",
            "osimertinib, so this is **not** a clean negative for a contact mechanism.",
            "Under the pass condition it is still a failed contact-mediated case —",
            "evidence about the signal as implemented (distance to the co-crystal",
            "ligand), not a reason to relabel the mechanism after seeing the number.",
            "",
        ]
    else:
        lines.append("")
    return "\n".join(lines)


def upsert_section(existing: str, section: str) -> str:
    """Append the evaluation section, or replace it on re-run so it does not duplicate."""
    if SECTION_HEADING in existing:
        start = existing.index(SECTION_HEADING)
        rest = existing[start + len(SECTION_HEADING) :]
        next_h2 = re.search(r"\n## ", rest)
        end = start + len(SECTION_HEADING) + (next_h2.start() if next_h2 else len(rest))
        return existing[:start].rstrip() + "\n\n" + section.rstrip() + existing[end:]
    return existing.rstrip() + "\n\n" + section.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default=str(RESULTS_PATH))
    parser.add_argument("--cache-dir", default="validation/cache")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print only; do not append/replace the section in results.md",
    )
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir

    distances = load_distances(Path(args.results), cache_dir)
    missing = [key for key in PRECOMMITTED_MECHANISMS if key not in distances]
    if missing:
        print(f"Missing distances for {missing}", file=sys.stderr)
        return 1

    rows = evaluate_gold_standard(distances)
    verdict = overall_verdict([r.verdict for r in rows])
    section = render_markdown(rows, verdict)
    print(section)

    if not args.no_write:
        path = Path(args.results)
        path.write_text(upsert_section(path.read_text(), section))
        print(f"Wrote {path}", file=sys.stderr)
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
