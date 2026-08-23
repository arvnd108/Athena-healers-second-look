"""Provisional `EvidenceSnapshot` shape for evidence-aware assumptions.

Subsystem A's evidence-snapshot extension should conform to this, not
redefine it independently. Nothing in Subsystem D inspects item properties
yet — the four built-in assumptions are case-state predicates — but
`compute_affected_findings` passes the snapshot through to `holds()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Dict-like lookup of evidence properties by `(gene, variant_or_drug)`.

    Provisional shape — Subsystem A's evidence-snapshot extension should
    conform to this, not redefine independently.
    """

    items: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)

    def get(
        self,
        gene: str,
        variant_or_drug: str,
        default: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        return self.items.get((gene, variant_or_drug), default)

    def __contains__(self, key: object) -> bool:
        return key in self.items

    def __getitem__(self, key: tuple[str, str]) -> dict[str, object]:
        return self.items[key]
