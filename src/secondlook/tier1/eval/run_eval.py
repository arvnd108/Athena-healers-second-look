"""Eval harness for Tier 1 retrieval Modes 1-3, run against ground_truth.yaml.

This phase produces the eval set and a runnable harness ONLY -- it does
not tune retrieve_exact/retrieve_relaxed/retrieve_semantic or their
thresholds. That is deliberately left to a later phase, once this harness
exists to tune against.

Each ground-truth pair calls the ONE retrieval function its bucket
actually tests (an exact-bucket pair calls retrieve_exact directly, never
"try all three and see what sticks"). A pair's outcome is one of:

    pass            expected citation found, correctly labeled
    not_found       expected citation absent from the returned items
    mislabeled      expected citation WAS found, but retrieval_mode /
                    relaxation_type / match_key doesn't match what the
                    pair expects -- e.g. the right evidence surfaced via
                    the wrong relaxation path. Counted separately from
                    not_found: finding the right answer through the wrong
                    mechanism is a different kind of bug than not finding
                    it at all, and collapsing the two would hide which one
                    is actually happening.
    unexpected_hit  a negative-expectation pair (expected_citation_id is
                    null) returned items when it should have returned none.

Run:
    python -m secondlook.tier1.eval.run_eval
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.retrieval import retrieve_exact, retrieve_relaxed
from secondlook.tier1.semantic_retrieval import retrieve_semantic

logger = logging.getLogger(__name__)

DEFAULT_GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.yaml"

MODE1_EXACT = "mode1_exact"
MODE1_EXACT_NEGATIVE = "mode1_exact_negative"
MODE2_PATH_A = "mode2_path_a"
MODE2_PATH_B = "mode2_path_b"
MODE3_SEMANTIC = "mode3_semantic"

BUCKETS = frozenset({MODE1_EXACT, MODE1_EXACT_NEGATIVE, MODE2_PATH_A, MODE2_PATH_B, MODE3_SEMANTIC})

# The four display groups the spec asks for -- mode1_exact and
# mode1_exact_negative share one display group ("exact") since both are
# about exact-match behavior (positive and negative), while path (a) and
# path (b) are kept visibly separate: their real coverage right now is
# very different (5 pairs vs. 5 pairs drawn from entirely different graph
# shapes), and collapsing "relaxed" into one number would hide that.
DISPLAY_GROUPS: dict[str, tuple[str, ...]] = {
    "exact": (MODE1_EXACT, MODE1_EXACT_NEGATIVE),
    "relaxed_same_gene": (MODE2_PATH_A,),
    "relaxed_drug_class": (MODE2_PATH_B,),
    "semantic": (MODE3_SEMANTIC,),
}

OUTCOMES = ("pass", "not_found", "mislabeled", "unexpected_hit")

_REQUIRED_PAIR_KEYS = (
    "id",
    "bucket",
    "gene",
    "key_type",
    "key_value",
    "disease",
    "drug_class",
    "query_text",
    "expected_mode",
    "expected_relaxation_type",
    "expected_citation_id",
)


class GroundTruthError(ValueError):
    """Raised when ground_truth.yaml is missing, malformed, or references
    an unknown bucket -- never silently skipped or guessed at."""


def load_ground_truth(path: Path = DEFAULT_GROUND_TRUTH_PATH) -> list[dict]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GroundTruthError(f"ground truth file not found at {path}") from exc
    except yaml.YAMLError as exc:
        raise GroundTruthError(f"ground truth file at {path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict) or not raw.get("config_version"):
        raise GroundTruthError("ground truth file is missing required key 'config_version'")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise GroundTruthError("ground truth file must list at least one pair under 'pairs'")

    for pair in pairs:
        missing = [key for key in _REQUIRED_PAIR_KEYS if key not in pair]
        if missing:
            raise GroundTruthError(f"pair {pair.get('id', '<no id>')!r} missing keys: {missing}")
        if pair["bucket"] not in BUCKETS:
            raise GroundTruthError(
                f"pair {pair['id']!r} has unknown bucket {pair['bucket']!r}, expected one of "
                f"{sorted(BUCKETS)}"
            )

    return pairs


@dataclass
class PairResult:
    id: str
    bucket: str
    outcome: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bucket": self.bucket,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass
class EvalSummary:
    results: list[PairResult] = field(default_factory=list)
    skipped_buckets: dict[str, str] = field(default_factory=dict)

    def counts_by_display_group(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {
            group: dict.fromkeys(OUTCOMES, 0) for group in DISPLAY_GROUPS
        }
        for group, buckets in DISPLAY_GROUPS.items():
            for result in self.results:
                if result.bucket in buckets:
                    counts[group][result.outcome] += 1
        return counts

    def overall_counts(self) -> dict[str, int]:
        overall = dict.fromkeys(OUTCOMES, 0)
        for result in self.results:
            overall[result.outcome] += 1
        overall["total"] = len(self.results)
        return overall

    def to_dict(self) -> dict:
        by_group = self.counts_by_display_group()
        for counts in by_group.values():
            counts["total"] = sum(counts.values())
        return {
            "by_group": by_group,
            "overall": self.overall_counts(),
            "skipped_buckets": self.skipped_buckets,
            "results": [r.to_dict() for r in self.results],
        }


def _key_kwargs(pair: dict) -> dict:
    if pair["key_type"] == "hgvs_p":
        return {"hgvs_p": pair["key_value"], "variant_name": None}
    if pair["key_type"] == "variant_name":
        return {"hgvs_p": None, "variant_name": pair["key_value"]}
    raise GroundTruthError(f"pair {pair['id']!r} has unsupported key_type {pair['key_type']!r}")


def _run_pair(pair: dict, *, graph):
    bucket = pair["bucket"]
    if bucket in (MODE1_EXACT, MODE1_EXACT_NEGATIVE):
        keys = _key_kwargs(pair)
        return retrieve_exact(
            pair["gene"], keys["hgvs_p"], variant_name=keys["variant_name"], graph=graph
        )
    if bucket == MODE2_PATH_A:
        keys = _key_kwargs(pair)
        return retrieve_relaxed(
            pair["gene"], keys["hgvs_p"], variant_name=keys["variant_name"], graph=graph
        )
    if bucket == MODE2_PATH_B:
        keys = _key_kwargs(pair)
        return retrieve_relaxed(
            pair["gene"],
            keys["hgvs_p"],
            pair["disease"],
            variant_name=keys["variant_name"],
            drug_class=pair["drug_class"],
            graph=graph,
        )
    if bucket == MODE3_SEMANTIC:
        return retrieve_semantic(pair["query_text"], graph=graph)
    raise GroundTruthError(f"pair {pair['id']!r} has unknown bucket {bucket!r}")


def _evaluate(pair: dict, result) -> tuple[str, str]:
    expected_id = pair["expected_citation_id"]

    if expected_id is None:
        if not result.items:
            return "pass", "correctly returned no items"
        return "unexpected_hit", f"expected no items, got {len(result.items)}"

    matching = [item for item in result.items if item["citation"]["id"] == expected_id]
    if not matching:
        return (
            "not_found",
            f"expected citation {expected_id!r} not among {len(result.items)} returned items",
        )

    item = matching[0]
    if item["retrieval_mode"] != pair["expected_mode"]:
        return (
            "mislabeled",
            f"found citation {expected_id!r} but retrieval_mode="
            f"{item['retrieval_mode']!r}, expected {pair['expected_mode']!r}",
        )
    expected_relaxation = pair["expected_relaxation_type"]
    if expected_relaxation is not None and item.get("relaxation_type") != expected_relaxation:
        return (
            "mislabeled",
            f"found citation {expected_id!r} but relaxation_type="
            f"{item.get('relaxation_type')!r}, expected {expected_relaxation!r}",
        )
    if pair["bucket"] in (MODE1_EXACT, MODE1_EXACT_NEGATIVE):
        if item.get("match_key") != pair["key_type"]:
            return (
                "mislabeled",
                f"found citation {expected_id!r} but match_key="
                f"{item.get('match_key')!r}, expected {pair['key_type']!r}",
            )
    if pair["bucket"] == MODE3_SEMANTIC:
        # "found the right PMID" is not the same claim as "found it
        # correctly labeled as literature" -- a semantic hit that somehow
        # carried a CIViC evidence_level (A-E) instead of "literature"
        # would otherwise pass this check on retrieval_mode alone.
        if item.get("evidence_level") != "literature":
            return (
                "mislabeled",
                f"found citation {expected_id!r} but evidence_level="
                f"{item.get('evidence_level')!r}, expected 'literature'",
            )
    return "pass", "ok"


def _mode3_data_is_loaded(graph) -> bool:
    count = graph.query(
        "MATCH (p:Publication) WHERE p.abstract_embedding IS NOT NULL RETURN count(p)"
    ).result_set[0][0]
    return count > 0


def run_eval(ground_truth_path: Path = DEFAULT_GROUND_TRUTH_PATH, *, graph=None) -> EvalSummary:
    graph = graph if graph is not None else connect_graph()
    pairs = load_ground_truth(ground_truth_path)

    skipped_buckets: dict[str, str] = {}
    if not _mode3_data_is_loaded(graph):
        skipped_buckets[MODE3_SEMANTIC] = (
            "no Publication nodes with abstract_embedding found in the graph -- "
            "Phase 5's PubMed loader has not been run against it. Reporting this "
            "gap explicitly rather than running mode3_semantic pairs and letting "
            "them read as ordinary retrieval failures."
        )

    results: list[PairResult] = []
    for pair in pairs:
        if pair["bucket"] in skipped_buckets:
            continue
        result = _run_pair(pair, graph=graph)
        outcome, detail = _evaluate(pair, result)
        results.append(
            PairResult(id=pair["id"], bucket=pair["bucket"], outcome=outcome, detail=detail)
        )

    return EvalSummary(results=results, skipped_buckets=skipped_buckets)


def _print_report(summary: EvalSummary) -> None:
    for group, counts in summary.counts_by_display_group().items():
        total = sum(counts.values())
        label = f"{group} (n={total})" if total else f"{group} (SKIPPED)"
        print(f"\n{label}")
        if total:
            for outcome in OUTCOMES:
                print(f"  {outcome}: {counts[outcome]}")

    if summary.skipped_buckets:
        print("\nskipped buckets:")
        for bucket, reason in summary.skipped_buckets.items():
            print(f"  {bucket}: {reason}")

    overall = summary.overall_counts()
    print(f"\noverall (n={overall['total']})")
    for outcome in OUTCOMES:
        print(f"  {outcome}: {overall[outcome]}")

    print("\nfailures:")
    failures = [r for r in summary.results if r.outcome != "pass"]
    if not failures:
        print("  none")
    for r in failures:
        print(f"  [{r.bucket}] {r.id}: {r.outcome} -- {r.detail}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        summary = run_eval()
    except GroundTruthError as exc:
        print(f"[FAIL] {exc}")
        return 1

    _print_report(summary)
    print()
    print(json.dumps(summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
