"""Governance / De-identification / Privacy Gate + Structured Case Index.

`export.py` (this subsystem, N) owns the allowlist and k-anonymity gate that
governs anything moving from the Case Memory Store toward the Structured
Case Index. `similarity.py` (subsystem K, not yet built) reads this
module's output -- it never reaches around it into raw case data.

Source: `CONCEPT_EVALUATION.md` SS5, `patient-schema-mvp.md` SS7.
"""
