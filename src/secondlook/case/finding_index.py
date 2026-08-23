"""Inverted index: `(gene, variant_or_drug) -> finding_id`.

Maintained incrementally as findings are created or marked superseded.
Lookup is an indexed get, not a scan of every active finding.
"""

from __future__ import annotations


class FindingsByEvidenceKey:
    """`(gene, variant_or_drug) -> set[finding_id]`, plus a reverse map.

    The reverse map (`finding_id -> its keys`) lets `remove_finding` drop a
    finding from every bucket it occupied without scanning the index.
    """

    def __init__(self) -> None:
        self._index: dict[tuple[str, str], set[str]] = {}
        self._finding_keys: dict[str, frozenset[tuple[str, str]]] = {}

    def add_finding(self, finding_id: str, keys: frozenset[tuple[str, str]]) -> None:
        """Index a finding under every evidence key its assumptions declared."""
        if finding_id in self._finding_keys:
            self.remove_finding(finding_id)
        self._finding_keys[finding_id] = keys
        for key in keys:
            bucket = self._index.get(key)
            if bucket is None:
                bucket = set()
                self._index[key] = bucket
            bucket.add(finding_id)

    def remove_finding(self, finding_id: str) -> None:
        """Remove a finding from all keys it was indexed under.

        Used when a finding is marked superseded. Unknown ids are a no-op
        so callers can remove idempotently.
        """
        keys = self._finding_keys.pop(finding_id, None)
        if keys is None:
            return
        for key in keys:
            bucket = self._index.get(key)
            if bucket is None:
                continue
            bucket.discard(finding_id)
            if not bucket:
                del self._index[key]

    def lookup(self, gene: str, variant_or_drug: str) -> frozenset[str]:
        """All finding_ids indexed under this exact key, plus wildcards.

        Wildcard convention (see `assumptions.py`):
        - `(gene, "")` — any variant in this gene (`NoAlterationIn`)
        - `("", variant_or_drug)` — this drug/variant with no specific gene
          (`DrugNotYetTried`)

        Returns a frozenset so downstream consumers cannot accidentally
        iterate a raw set into non-deterministic output; convert with
        `tuple(sorted(...))` before emitting.
        """
        ids: set[str] = set()
        ids.update(self._index.get((gene, variant_or_drug), ()))
        ids.update(self._index.get((gene, ""), ()))
        ids.update(self._index.get(("", variant_or_drug), ()))
        return frozenset(ids)
