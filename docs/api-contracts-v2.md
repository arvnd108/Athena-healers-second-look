# API contracts v2 (case memory)

These are the Pydantic models in `secondlook.query.contracts`. MCP tools
return them inside the uniform `{items, empty_reason}` envelope defined by
`QueryResult`. Issue #59's REST layer is expected to reuse these shapes
without a second translation step.

REST routes themselves are **not** in this PR (issue #59). MCP
authentication is **not** in this PR (issue #60).

## Envelope

Every tool/query returns:

```json
{"items": [], "empty_reason": "string or null"}
```

`empty_reason` is required when `items` is empty. Never a silent `[]`.

## Models

See `src/secondlook/query/contracts.py`:

- `CaseSummary` — case demographics, folded alterations, question counts, active finding count
- `EvidenceItem` — one structured retrieval hit (`retrieval_mode` is `exact` or `relaxed`; semantic retrieval is not chained)
- `TrialMatchResult` — bucketed eligibility match for one trial
- `AccessPathway` — country-level expanded-access route after Drug identity resolution
- `ChangeSetForApi` — **display-shaped** diff (human-readable `summary` strings). Not a field-for-field mirror of `case/diff.py`'s `ChangeSet`

## Honest gaps this PR does not hide

- `biomarker_thresholds` for `get_recent_changes` defaults to `{}`. No production thresholds exist in the repo, so biomarker-shift detection is inert until a clinically authorized caller supplies values.
- AccessPathway nodes are country-level, not per-drug. Drug identity is still resolved first (name / `chembl_id` / optional InChIKey via SMILES) so an unrecognized name cannot silently receive another country's generic pathways. `ligand_identity.py` is InChIKey matching, not RxNorm.
- Trial *signal* dispatch already attaches eligibility buckets (issue #46). This query layer's `match_trials` tool is a separate, case-scoped compose of the same matcher; it does not change generator dispatch.
