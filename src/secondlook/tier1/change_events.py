"""Change events for graph writes that modify an existing node.

Subsystem A's second half. The Emerging Evidence Radar (Subsystem G) needs to
know *what changed* between ingestion runs, not merely what the graph currently
holds. A loader that only MERGEs cannot answer "did CIViC re-grade this evidence
item from C to B last Tuesday?" -- the old value is gone by the time anyone asks.

Two design constraints, both from the issue:

1. **Emit even when nothing is listening.** Loaders must not import the radar,
   check whether it exists, or branch on whether anyone subscribed. They emit
   into a sink; the default sink discards. Coupling ingestion to a consumer that
   is not built yet is how the consumer never gets built.

2. **Creations are not changes.** An event is raised only when a node that
   already existed comes back with different properties. A first write is the
   baseline, not a change, and emitting one would drown a real re-grade in
   thousands of no-op events on the first run.

Provenance keys are excluded from the comparison by default: `retrieved_at`
changes on every single run by definition, so including it would make every node
look changed on every pass and render the whole signal useless.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from secondlook.tier1.graph_schema import PROVENANCE_PROPERTIES

logger = logging.getLogger(__name__)

#: Keys whose change is not evidence of anything. `retrieved_at` moves every
#: run; `source_version` moves whenever upstream cuts a release, independently
#: of whether this node's content moved with it. Per-property provenance keys
#: written by `with_enrichment_provenance` follow the same rule via the suffix
#: check in `is_provenance_key`.
IGNORED_KEYS: frozenset[str] = frozenset(PROVENANCE_PROPERTIES)

_PROVENANCE_SUFFIXES = ("_source", "_retrieved_at", "_source_version")


def is_provenance_key(key: str) -> bool:
    """True for node-level and per-property provenance keys alike.

    `with_enrichment_provenance` writes `drug_class_source`,
    `drug_class_retrieved_at`, ... one set per enriched property, so the
    provenance key set is open-ended and cannot be enumerated as a constant.
    """
    return key in IGNORED_KEYS or key.endswith(_PROVENANCE_SUFFIXES)


def diff_properties(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    ignore_provenance: bool = True,
) -> dict[str, tuple[Any, Any]]:
    """Keys whose value differs, mapped to `(old_value, new_value)`.

    Only keys present in `new` are considered. A loader writes with
    `SET n += $props`, which cannot remove a property, so a key in `old` and
    absent from `new` was not deleted -- it was simply not part of this write,
    and reporting it as a change would be wrong.
    """
    changed: dict[str, tuple[Any, Any]] = {}
    for key, new_value in new.items():
        if ignore_provenance and is_provenance_key(key):
            continue
        old_value = old.get(key)
        if old_value != new_value:
            changed[key] = (old_value, new_value)
    return changed


@dataclass(frozen=True)
class EvidenceChangeEvent:
    """One existing node's properties changed during an ingestion run."""

    node_type: str
    node_id: str
    old_props: dict[str, Any]
    new_props: dict[str, Any]
    source: str
    retrieved_at: str
    #: Only the keys that actually differ, `key -> (old, new)`. Stored rather
    #: than recomputed so a consumer reading a serialized event does not have to
    #: re-derive it, and so the comparison rules in force at emit time travel
    #: with the event.
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    @property
    def changed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.changed))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # JSON has no tuples; a consumer reading this back should get an
        # explicit shape rather than a two-element array it has to interpret.
        payload["changed"] = {
            key: {"old": old, "new": new} for key, (old, new) in self.changed.items()
        }
        return payload


def build_event(
    *,
    node_type: str,
    node_id: str,
    old_props: dict[str, Any],
    new_props: dict[str, Any],
    source: str,
    retrieved_at: str | None = None,
) -> EvidenceChangeEvent | None:
    """An event for this write, or None when nothing meaningful changed.

    Returning None rather than an event with an empty `changed` map is
    deliberate: it makes "no change" unrepresentable downstream, so a consumer
    cannot accidentally count no-ops as activity.
    """
    changed = diff_properties(old_props, new_props)
    if not changed:
        return None
    return EvidenceChangeEvent(
        node_type=node_type,
        node_id=str(node_id),
        old_props=dict(old_props),
        new_props=dict(new_props),
        source=source,
        retrieved_at=retrieved_at or datetime.now(UTC).isoformat(),
        changed=changed,
    )


@runtime_checkable
class ChangeEventSink(Protocol):
    """Where change events go. Implemented by the radar when it exists."""

    def emit(self, event: EvidenceChangeEvent) -> None: ...


class NullChangeEventSink:
    """Discards events. The default, so loaders can emit unconditionally.

    Counts what it dropped, which turns "is the loader emitting at all?" into a
    question answerable without wiring up a real consumer.
    """

    def __init__(self) -> None:
        self.dropped = 0

    def emit(self, event: EvidenceChangeEvent) -> None:
        self.dropped += 1


class JsonlChangeEventSink:
    """Appends one JSON object per line to a file.

    Append-only and line-delimited so a run that is killed part-way leaves every
    completed event intact and readable -- which matters, because ingestion runs
    are long and this project has already lost several to being killed mid-run.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.written = 0

    def emit(self, event: EvidenceChangeEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            handle.flush()
        self.written += 1


class CollectingChangeEventSink:
    """Keeps events in memory. For tests and for the scheduler's run summary."""

    def __init__(self) -> None:
        self.events: list[EvidenceChangeEvent] = []

    def emit(self, event: EvidenceChangeEvent) -> None:
        self.events.append(event)


def read_events(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL event log, skipping any truncated final line.

    A run killed mid-write can leave a partial last line. Skipping it is right:
    the alternative is refusing to read an otherwise complete log because its
    tail was cut off.
    """
    events: list[dict[str, Any]] = []
    file = Path(path)
    if not file.exists():
        return events
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping malformed change-event line in %s", file)
    return events
