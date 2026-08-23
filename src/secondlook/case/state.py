"""Provisional `CaseState` shape for the diff engine.

Subsystem C (Case Memory Store) should conform to this, not redefine it
independently. Derived by folding the append-only event log.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Alteration:
    gene: str
    variant: str
    assay: str
    event_id: str


@dataclass(frozen=True)
class BiomarkerReading:
    value: float
    event_id: str
    unit: str | None = None


@dataclass(frozen=True)
class Treatment:
    regimen: str
    event_id: str
    action: str = "started"  # started | stopped
    line: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class DiseaseAssessment:
    status: str  # response | stable | progression
    event_id: str
    sites: tuple[str, ...] = ()
    assessed_on: str | None = None


@dataclass(frozen=True)
class CaseState:
    """Folded patient state at one point in the event log.

    Provisional shape — Subsystem C should conform to this, not redefine
    independently. Collections that the diff engine iterates are tuples so
    input order is part of identity; `biomarkers` is a dict as specified,
    and `compute_diff` sorts its keys before emitting changes.
    """

    alterations: tuple[Alteration, ...] = ()
    biomarkers: dict[str, BiomarkerReading] = field(default_factory=dict)
    treatments: tuple[Treatment, ...] = ()
    latest_assessment: DiseaseAssessment | None = None
