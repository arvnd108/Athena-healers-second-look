"""Threshold labeling of binding-affinity deltas (Tier 2 Step 6).

Converts a `BindingScore.delta_score` into one of exactly three labels. Two
things make this less trivial than "compare against a number", and both are
load-bearing:

**1. The two scoring methods use opposite sign conventions.**

- mCSM-lig reports ``log(affinity fold change)``; its own result page labels a
  *negative* value "Destabilizing". So **negative means reduced binding**.
  (Verified against the live 2Z4O/D30N result: ``-2.056`` → ``Destabilizing``.)
- `vina_dock.score` returns ``mutant_score - wildtype_score`` in kcal/mol, where
  a *more negative* docking score means *stronger* binding. A mutant that binds
  worse scores higher (less negative), so the delta is positive. So **positive
  means reduced binding** — the opposite of mCSM-lig.

A single threshold applied to the raw `delta_score` would therefore invert the
label for every docking-scored candidate. Per `tier2-implementation-spec.md`
§1.3 item 6, docking — not mCSM-lig — carries most real traffic, so that would
mean most results being confidently backwards. `MethodCalibration.orientation`
normalizes both onto one canonical scale where **negative means reduced
binding**, and every threshold below is expressed on that canonical scale.

**2. The units are not comparable**, so the thresholds cannot be shared. A
0.5 shift in log-fold-change and a 0.5 kcal/mol shift are unrelated quantities.
Each method therefore carries its own calibration record.

## Calibration status

The cutoffs below are **PROVISIONAL and not yet empirically calibrated.**
`tier2-structural-prediction.md` §6 is explicit that no established clinical
threshold exists and that the cutoff must be set empirically from the
`validation-plan.md` gold-standard run — which cannot happen until the Step 7
pipeline exists to run those cases through. Until that run happens:

- these values are documented starting points, not validated cutoffs;
- `CALIBRATION_STATUS` is `"provisional"` and is surfaced on every label, so no
  caller can mistake an uncalibrated label for a validated one;
- `label_binding_delta` takes a `calibration` override so the validation
  harness can sweep candidate cutoffs without editing this module.

When the gold-standard run lands, update the constants here, set
`CALIBRATION_STATUS = "calibrated"`, bump `LABELING_VERSION`, and record which
run produced the values. Never hardcode a per-case decision anywhere.

Neither method's label is clinical evidence; see the §10 disclaimer attached to
every pipeline result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Bump whenever a threshold or the labeling rule changes. Recorded on every
# StructuralSignal so a cached signal is traceable to the rule that produced it.
LABELING_VERSION = "0.1.0-provisional"

# "provisional" until validation-plan.md's nine gold-standard cases have been run.
CalibrationStatus = Literal["provisional", "calibrated"]
CALIBRATION_STATUS: CalibrationStatus = "provisional"

BindingLabel = Literal[
    "likely_reduced_binding",
    "likely_retained_or_increased_binding",
    "uncertain",
]

#: The complete set of labels this module may emit. The post-condition in
#: `label_binding_delta` asserts membership against this tuple — see the
#: RareCure `clamp_weights` failure mode (`rarecure-build-reference.md`), where
#: an output documented as bounded silently wasn't, and the test had been
#: relaxed to accommodate the escape rather than catch it.
BINDING_LABELS: tuple[BindingLabel, ...] = (
    "likely_reduced_binding",
    "likely_retained_or_increased_binding",
    "uncertain",
)

ScoringMethod = Literal["mCSM-lig", "docking"]

# Confidence framing is deliberately NOT uniform across methods
# (`tier2-implementation-spec.md` §5 deliverable 1). mCSM-lig has a published
# correlation figure for this exact task; docking does not.
LabelConfidence = Literal["moderate", "low"]


@dataclass(frozen=True)
class MethodCalibration:
    """Per-method thresholds and provenance for turning a delta into a label.

    `orientation` maps a raw `delta_score` onto the canonical scale where
    negative means reduced binding: ``canonical = delta_score * orientation``.
    """

    method: ScoringMethod
    unit: str
    orientation: Literal[1, -1]
    reduced_at_or_below: float
    increased_at_or_above: float
    confidence: LabelConfidence
    accuracy_note: str

    def canonical_delta(self, delta_score: float) -> float:
        """Raw method-specific delta → canonical scale (negative = reduced binding)."""
        return delta_score * self.orientation


#: mCSM-lig: negative log(affinity fold change) is already "reduced binding",
#: so orientation is +1. The ±0.5 band is roughly a 3x affinity change either
#: way, chosen as a deliberately conservative dead zone around mCSM-lig's own
#: Destabilizing/Stabilizing boundary at 0 — PROVISIONAL, see module docstring.
MCSM_LIG_CALIBRATION = MethodCalibration(
    method="mCSM-lig",
    unit="log(affinity fold change)",
    orientation=1,
    reduced_at_or_below=-0.5,
    increased_at_or_above=0.5,
    confidence="moderate",
    accuracy_note=(
        "mCSM-lig reports correlation up to rho = 0.67 with experimental data "
        "(Pires, Blundell & Ascher, Sci Rep 2016). Independent benchmarking shows "
        "accuracy degrades 5-30% on modeled rather than experimental structures."
    ),
)

#: AutoDock Vina: positive (mutant - wildtype) means the mutant binds worse, so
#: orientation is -1 to put it on the canonical scale. The 1.0 kcal/mol band is
#: below Vina's own reported RMSE, so anything inside it is treated as noise —
#: PROVISIONAL, see module docstring.
DOCKING_CALIBRATION = MethodCalibration(
    method="docking",
    unit="kcal/mol (mutant minus wild-type)",
    orientation=-1,
    reduced_at_or_below=-1.0,
    increased_at_or_above=1.0,
    confidence="low",
    accuracy_note=(
        "AutoDock Vina has no published correlation figure for mutation-effect "
        "delta scoring, unlike mCSM-lig. Docking deltas on a single side-chain "
        "substitution are weaker evidence than an mCSM-lig score and must not be "
        "presented with equivalent confidence."
    ),
)

DEFAULT_CALIBRATIONS: dict[ScoringMethod, MethodCalibration] = {
    "mCSM-lig": MCSM_LIG_CALIBRATION,
    "docking": DOCKING_CALIBRATION,
}


@dataclass(frozen=True)
class BindingLabelResult:
    """A label plus everything needed to interpret it honestly."""

    label: BindingLabel
    method: ScoringMethod
    delta_score: float
    canonical_delta: float
    unit: str
    confidence: LabelConfidence
    calibration_status: CalibrationStatus
    labeling_version: str
    accuracy_note: str
    heuristic_note: str


#: Attached to every label. `tier2-structural-prediction.md` §6 requires the
#: cutoff be described as an internal heuristic in code *and* in the UI.
HEURISTIC_NOTE = (
    "Label derived from an internal heuristic cutoff, not a validated clinical "
    "threshold. No established clinical cutoff exists for predicted binding-affinity "
    "change."
)

_UNCALIBRATED_SUFFIX = (
    " This cutoff has not yet been calibrated against the gold-standard validation "
    "cases and should be treated as a placeholder."
)


def calibration_for(
    method: ScoringMethod,
    calibrations: dict[ScoringMethod, MethodCalibration] | None = None,
) -> MethodCalibration:
    """Look up the calibration record for `method`.

    Raises `KeyError` for an unknown method rather than silently defaulting —
    a new scoring method must get an explicit calibration, since inheriting
    another method's thresholds would be meaningless across different units.
    """
    table = DEFAULT_CALIBRATIONS if calibrations is None else calibrations
    if method not in table:
        raise KeyError(
            f"No labeling calibration for method {method!r}. Add a MethodCalibration "
            "with its own unit, orientation, and thresholds before labeling its deltas."
        )
    return table[method]


def label_binding_delta(
    delta_score: float,
    method: ScoringMethod,
    *,
    calibration: MethodCalibration | None = None,
) -> BindingLabelResult:
    """Convert a raw binding delta into one of the three defined labels.

    `calibration` overrides the module default, which is how the validation
    harness sweeps candidate cutoffs without mutating module state.
    """
    resolved = calibration if calibration is not None else calibration_for(method)
    canonical = resolved.canonical_delta(delta_score)

    if canonical <= resolved.reduced_at_or_below:
        label: BindingLabel = "likely_reduced_binding"
    elif canonical >= resolved.increased_at_or_above:
        label = "likely_retained_or_increased_binding"
    else:
        label = "uncertain"

    # Post-condition, not a happy-path check: this asserts the real documented
    # contract (exactly three labels, always), so a future edit to the branching
    # above cannot silently widen the output set.
    assert label in BINDING_LABELS, f"labeling produced undeclared label {label!r}"

    heuristic_note = HEURISTIC_NOTE
    if CALIBRATION_STATUS == "provisional":
        heuristic_note += _UNCALIBRATED_SUFFIX

    return BindingLabelResult(
        label=label,
        method=resolved.method,
        delta_score=delta_score,
        canonical_delta=canonical,
        unit=resolved.unit,
        confidence=resolved.confidence,
        calibration_status=CALIBRATION_STATUS,
        labeling_version=LABELING_VERSION,
        accuracy_note=resolved.accuracy_note,
        heuristic_note=heuristic_note,
    )
