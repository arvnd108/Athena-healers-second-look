"""OncoSphere signal generators -- typed functions, not agents.

See CONCEPT_EVALUATION.md §3: each generator is
`(ChangeSet, Question) -> list[Signal]` (via `SignalBatch`), with no
reasoning loop, no tool-calling autonomy, and no inter-generator
negotiation. `registry.py` is a dict keyed on `ChangeKind`.
"""
