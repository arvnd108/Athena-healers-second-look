"""Automated Case Intake / Document Ingestion Pipeline.

Turns an uploaded document into proposed `CaseEvent`s a clinician confirms,
rather than types by hand -- see `IMPLEMENTATION_PLAN.md`'s intake framing
and `CONCEPT_EVALUATION.md`/research-lessons on EHR-integrated intake
reducing manual entry.

**The one rule this whole package exists to enforce:** the LLM never writes
to the Case Memory Store directly. `extract_case_events()` returns
`ProposedCaseEvent` objects only -- committing one to `case/store.py` is a
separate, explicit, human-confirmed action this package does not perform.
"""
