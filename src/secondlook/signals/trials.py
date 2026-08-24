"""Trials signal generator -- Cypher against the Trial label.

The Trial Intelligence & Eligibility Matcher (issue #7) and
`ctgov_loader.py` (issue #8) do not exist yet, so the live graph has
zero Trial nodes. This function still runs a real, parameterized query
against the schema issue #2 already landed (`TRIAL`, `TRIAL_STATUSES`,
`TARGETS_BIOMARKER`, `RECRUITS_FOR`). It returns `[]` today because the
query returns zero rows -- the honest structural reason -- not because
of a hand-written stub. The moment issue #8's loader writes Trial
nodes, this file starts returning live Signals with no code change.

Actionable statuses (a documented subset of `TRIAL_STATUSES`):
`RECRUITING`, `NOT_YET_RECRUITING`, `ENROLLING_BY_INVITATION`. Anything
else (TERMINATED, COMPLETED, ...) is dropped and counted. The allowlist
is applied in Cypher *and* in Python so a mocked row set that includes
a TERMINATED row is filtered the same way the live graph will be.

A Trial row with no `eligibility_url` and no `registry_id` cannot become
a documented Signal (no citation URL) and is counted in `filtered_count`.
"""

from __future__ import annotations

from datetime import datetime

from secondlook.case.diff import ChangeSet
from secondlook.case.questions import Question
from secondlook.signals.mapping import batch_from, clock, gene_in_scope
from secondlook.signals.trial_matching import MATCHER_VERSION
from secondlook.signals.types import (
    ComputedMethod,
    DocumentedSource,
    EvidenceClass,
    Signal,
    SignalBatch,
    SignalKind,
)
from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import TRIAL_STATUSES, assert_valid

GENERATOR_VERSION = "signals.trials/1"

# Statuses a patient can still be referred to. Kept as a frozenset subset
# of TRIAL_STATUSES so an upstream typo fails `assert_valid` rather than
# rendering as "not recruiting".
ACTIONABLE_TRIAL_STATUSES: frozenset[str] = frozenset(
    {
        "RECRUITING",
        "NOT_YET_RECRUITING",
        "ENROLLING_BY_INVITATION",
    }
)
assert ACTIONABLE_TRIAL_STATUSES <= TRIAL_STATUSES

_RETURN = (
    "RETURN t.registry_id, t.registry, t.status, t.phase, "
    "t.brief_title, t.eligibility_url, t.eligibility_criteria "
    "ORDER BY t.registry_id"
)

_TRIALS_BY_GENE = f"""
MATCH (t:Trial)-[:TARGETS_BIOMARKER]->(g:Gene {{symbol: $gene}})
WHERE t.status IN $actionable_statuses
{_RETURN}
"""

_TRIALS_BY_GENE_AND_DISEASE = f"""
MATCH (t:Trial)-[:TARGETS_BIOMARKER]->(g:Gene {{symbol: $gene}})
MATCH (t)-[:RECRUITS_FOR]->(d:Disease {{name: $disease}})
WHERE t.status IN $actionable_statuses
{_RETURN}
"""


def generate(
    change_set: ChangeSet,
    question: Question,
    *,
    graph=None,
    dgidb_client=None,
    case=None,
    now: datetime | None = None,
) -> SignalBatch:
    del dgidb_client
    when = clock(now)
    gene = gene_in_scope(change_set, question)
    if not gene:
        return batch_from(
            [],
            filtered_count=0,
            empty_reason="no gene in scope to match Trial-[:TARGETS_BIOMARKER]->Gene",
        )

    graph = graph if graph is not None else connect_graph()
    disease = question.detail.get("cancer_type")
    params = {
        "gene": gene,
        "actionable_statuses": sorted(ACTIONABLE_TRIAL_STATUSES),
    }
    if disease:
        params["disease"] = disease
        result = graph.query(_TRIALS_BY_GENE_AND_DISEASE, params=params)
    else:
        result = graph.query(_TRIALS_BY_GENE, params=params)

    signals: list[Signal] = []
    filtered_count = 0
    seen: set[str] = set()

    for row in result.result_set:
        # Unpacked defensively rather than by fixed arity: the criteria column
        # was added for issue #46, and a row set recorded before it (including
        # every already-merged test fixture) is still valid input.
        registry_id, registry, status, phase, brief_title, eligibility_url = row[:6]
        criteria_text = row[6] if len(row) > 6 else None
        assert_valid(str(status) if status is not None else "", TRIAL_STATUSES, "status")
        if status not in ACTIONABLE_TRIAL_STATUSES:
            filtered_count += 1
            continue
        citation_url = eligibility_url or (
            f"https://clinicaltrials.gov/study/{registry_id}" if registry_id else None
        )
        if not citation_url:
            filtered_count += 1
            continue
        ident = str(registry_id or citation_url)
        if ident in seen:
            continue
        seen.add(ident)
        title = brief_title or ident
        phase_bit = f", {phase}" if phase else ""
        registry_bit = registry or "trial registry"
        claim = f"{title} ({ident}{phase_bit}) is {status} on {registry_bit}."
        signals.append(
            Signal(
                kind=SignalKind.TRIAL,
                evidence_class=EvidenceClass.DOCUMENTED,
                claim=claim,
                source=DocumentedSource(
                    citation_url=str(citation_url),
                    citation_id=str(registry_id) if registry_id else None,
                ),
                confidence="stated",
                caveats=(),
                computed_at=when,
                generator_version=GENERATOR_VERSION,
            )
        )

        # Issue #46: when a case is available, also say whether THIS patient
        # plausibly qualifies -- not merely that a matching trial is recruiting.
        #
        # Emitted as a second, `computed` signal rather than folded into the
        # claim above. The registry published "this study is recruiting"; it did
        # not publish "this patient probably does not qualify". Appending the
        # verdict to a DOCUMENTED claim would put a computed conclusion behind
        # that trial's citation, which is exactly the ambiguity
        # IMPLEMENTATION_PLAN.md 9.2 tells the UI not to reintroduce -- and it
        # would pass `Signal.__post_init__`, which guards `source` but cannot
        # read free text in `claim`.
        #
        # Two signals per trial is the intended shape, not a compromise:
        # types.py's docstring already assigns reconciliation to subsystem I/M
        # ("rendering them side by side is subsystem I/M"), and 9.2's four
        # visual treatments exist so a reader can tell the two apart at a glance.
        if case is not None and criteria_text:
            bucket, bucket_caveats = _bucket_for(str(ident), str(criteria_text), case)
            signals.append(
                Signal(
                    kind=SignalKind.TRIAL_ELIGIBILITY,
                    evidence_class=EvidenceClass.COMPUTED,
                    claim=(
                        f"For this case, {ident} reads as "
                        f"{bucket.replace('_', ' ')} against its published criteria."
                    ),
                    source=ComputedMethod(
                        method="signals.trial_matching.match_trial",
                        version=MATCHER_VERSION,
                    ),
                    # Never above `low`: the extractor feeding this matcher is
                    # scored against a corpus that has not reached the ~30
                    # sections docs/trial-extraction-validation-plan.md targets,
                    # so its accuracy numbers are still provisional.
                    confidence="low",
                    caveats=bucket_caveats,
                    computed_at=when,
                    generator_version=GENERATOR_VERSION,
                )
            )

    return batch_from(
        signals,
        filtered_count=filtered_count,
        empty_reason=(
            f"no Trial rows for gene={gene} after actionable-status filter "
            f"({filtered_count} filtered)"
        ),
    )


#: Cap on criteria carried into a caveat list. A long exclusion section can run
#: to dozens of lines, and a signal whose caveats are unreadable is a signal
#: nobody reads.
MAX_CAVEATS = 4


def _bucket_for(trial_id: str, criteria_text: str, case) -> tuple[str, tuple[str, ...]]:
    """Bucket one trial for one case, as (bucket, caveats).

    Extraction is deliberately the rule-based path: this runs per patient per
    query, and the LLM-assisted extractor is a one-time cached cost that belongs
    in the loader, not here.
    """
    from secondlook.signals.trial_matching import match_trial
    from secondlook.tier1.criteria_extraction import RuleBasedExtractor

    predicates = RuleBasedExtractor().extract(trial_id, criteria_text).predicates
    result = match_trial(trial_id, predicates, case)

    caveats: list[str] = []
    if result.violated_criteria:
        caveats.append(
            f"{len(result.violated_criteria)} criterion/criteria appear unmet: "
            + "; ".join(c[:120] for c in result.violated_criteria[:MAX_CAVEATS])
        )
    if result.unresolved_criteria:
        # Named explicitly, because "needs verification" without saying WHAT
        # needs verifying gives a clinician nothing to act on.
        caveats.append(
            f"{len(result.unresolved_criteria)} criterion/criteria could not be "
            "checked against this case: "
            + "; ".join(c[:120] for c in result.unresolved_criteria[:MAX_CAVEATS])
        )
    unevaluable = result.unresolvable_predicate_types()
    if unevaluable:
        caveats.append(
            "Criterion types this system cannot evaluate at all: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(unevaluable.items()))
        )
    caveats.append(
        "Eligibility bucketing is a triage aid over registry text. It does not "
        "read the protocol and cannot see anything the sponsor did not write down."
    )
    return result.bucket, tuple(caveats)
