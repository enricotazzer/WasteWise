"""The risk contract.

One rule shapes all of it: **no probability travels without saying what produced it.**

`services/savings` established the pattern for money — a euro figure carries the fraction of
its rows that had a price. The same argument applies harder here, because a risk score has no
visible units and reads as authoritative whatever it was built from. So every response in
this module carries `trained_on_demo` at the top level, and a `provenance` block with the
model version, the stage, and pre-written banner copy.

`trained_on_demo` is duplicated at the top level of every response on purpose. It is the one
field a client must not be able to miss, and burying it one level down inside `provenance` is
exactly how a warning band ends up unrendered.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import RiskBand, Unit
from app.services.risk.scoring import ScoredItem, ScoringRun
from app.services.risk.survival import EvaluationReport, GateStatus, ModelMetrics

# `model_version` / `model_stage` collide with pydantic's `model_` protected namespace. The
# names match the `risk_scores.model_version` column and the vocabulary in PLAN.md, which is
# worth more than avoiding a namespace pydantic only reserves defensively.
_ALLOW_MODEL_PREFIX = ConfigDict(protected_namespaces=())


def _demo_warning(*, trained_on_demo: bool, below_gate: bool) -> str | None:
    """Banner copy, written here rather than in the client.

    The frontend should render this string verbatim whenever it is non-null. Producing it
    server-side means the sentence cannot drift from the flag that triggered it, and a second
    client (or a curl session, or a screenshot in the README) gets the same warning.

    Italian, because it is rendered verbatim into an Italian UI — the same reason
    `categories.name_it` and the waste reasons are. Copy that ships to the user is localized
    at the source; log messages and docstrings stay in English.
    """
    if not trained_on_demo and not below_gate:
        return None
    parts: list[str] = []
    if trained_on_demo:
        parts.append(
            "Questi punteggi vengono dalla dispensa dimostrativa, non da consumi reali. "
            "Sono illustrativi, non misure."
        )
    if below_gate:
        parts.append(
            "Il modello e' stato addestrato sotto la soglia minima di dati: le cifre di "
            "accuratezza qui sotto sono diagnostica, non prove."
        )
    return " ".join(parts)


class RiskProvenance(BaseModel):
    """Where the numbers in this response came from."""

    model_config = _ALLOW_MODEL_PREFIX

    # `v0` / `v0-demo` / `v1-cox-demo-ungated` — the same string written to
    # `risk_scores.model_version`, so a score on screen can be traced to a row.
    model_version: str
    model_stage: str  # "v0" (heuristic, always available) or "v1" (fitted survival model)
    trained_on_demo: bool
    fitted_below_gate: bool
    demo_included: bool
    demo_items: int
    # Non-null exactly when a warning band should be showing. Render it verbatim.
    warning: str | None


class RiskItemOut(BaseModel):
    """One item, with enough product detail to render a list row without a second call."""

    model_config = _ALLOW_MODEL_PREFIX

    item_id: int
    product_id: int
    product_name: str
    category_code: str
    unit: Unit
    quantity: float
    acquired_at: date
    expiry_date: date | None
    days_to_expiry: int | None
    days_in_house: int
    is_opened: bool
    is_demo: bool

    p_consume_3d: float
    p_consume_7d: float
    # None when the item has no expiry date: there is no deadline, so nothing to be at risk
    # of. Non-perishables land here and are never flagged.
    p_consume_before_expiry: float | None
    risk_band: RiskBand
    # The operating point, precomputed. Identical to `risk_band == HIGH`, and to the
    # condition every precision/recall figure in `/evaluation` is measured at.
    at_risk: bool
    # Which cold-start tier produced the score, the rate, the coverage. A score you cannot
    # explain is a score you cannot debug — this is the same blob stored in
    # `risk_scores.features`.
    explain: dict[str, Any]
    # When the nightly job last wrote this item to `risk_scores`. Null means the job has not
    # run; the score above is computed live either way.
    last_persisted_at: datetime | None

    @classmethod
    def from_scored(cls, item: ScoredItem, *, threshold: float) -> "RiskItemOut":
        at_risk = (
            item.p_consume_before_expiry is not None and item.p_consume_before_expiry < threshold
        )
        return cls(
            item_id=item.item_id,
            product_id=item.product_id,
            product_name=item.product_name,
            category_code=item.category_code,
            unit=Unit(item.unit),
            quantity=item.quantity,
            acquired_at=item.acquired_at,
            expiry_date=item.expiry_date,
            days_to_expiry=item.days_to_expiry,
            days_in_house=item.days_in_house,
            is_opened=item.is_opened,
            is_demo=item.is_demo,
            p_consume_3d=item.p_consume_3d,
            p_consume_7d=item.p_consume_7d,
            p_consume_before_expiry=item.p_consume_before_expiry,
            risk_band=item.band,
            at_risk=at_risk,
            explain=item.features,
            last_persisted_at=item.last_persisted_at,
        )


class RiskScoresResponse(BaseModel):
    model_config = _ALLOW_MODEL_PREFIX

    generated_at: datetime
    # Top level, deliberately duplicated from `provenance`. See the module docstring.
    trained_on_demo: bool
    provenance: RiskProvenance
    threshold: float
    total_items: int
    at_risk_count: int
    band_counts: dict[str, int]
    items: list[RiskItemOut]
    notes: list[str]

    @classmethod
    def from_run(cls, run: ScoringRun, *, threshold: float) -> "RiskScoresResponse":
        items = [RiskItemOut.from_scored(item, threshold=threshold) for item in run.items]
        counts = {band.value: 0 for band in RiskBand}
        for item in items:
            counts[item.risk_band.value] += 1
        return cls(
            generated_at=run.computed_at,
            trained_on_demo=run.trained_on_demo,
            provenance=RiskProvenance(
                model_version=run.model_version,
                model_stage=run.model_stage,
                trained_on_demo=run.trained_on_demo,
                fitted_below_gate=run.fitted_below_gate,
                demo_included=run.demo_included,
                demo_items=run.demo_items,
                warning=_demo_warning(
                    trained_on_demo=run.trained_on_demo, below_gate=run.fitted_below_gate
                ),
            ),
            threshold=threshold,
            total_items=len(items),
            at_risk_count=sum(1 for item in items if item.at_risk),
            band_counts=counts,
            items=items,
            notes=run.notes,
        )


class GateStatusOut(BaseModel):
    """The v1 data gate, live. `met=false` is the normal state early on, not an error."""

    resolved: int
    waste_events: int
    min_resolved: int
    min_waste_events: int
    met: bool
    resolved_short: int
    waste_short: int
    # "gate not met: 152/150 resolved, 25/40 waste events" — ready to print.
    summary: str

    @classmethod
    def from_gate(cls, gate: GateStatus) -> "GateStatusOut":
        return cls(
            resolved=gate.resolved,
            waste_events=gate.waste_events,
            min_resolved=gate.min_resolved,
            min_waste_events=gate.min_waste_events,
            met=gate.met,
            resolved_short=gate.resolved_short,
            waste_short=gate.waste_short,
            summary=gate.summary,
        )


class ModelMetricsOut(BaseModel):
    model_config = _ALLOW_MODEL_PREFIX

    name: str
    # Rank quality over time-to-consumption, censored rows included. None when the test
    # period has no admissible pairs.
    concordance: float | None
    threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    # None, never 0.0, when the denominator is empty: a model that flagged nothing has an
    # undefined precision rather than a bad one.
    precision: float | None
    recall: float | None
    f1: float | None
    flagged: int
    positives: int
    evaluated: int
    ranked: int

    @classmethod
    def from_metrics(cls, metrics: ModelMetrics) -> "ModelMetricsOut":
        return cls(**{field: getattr(metrics, field) for field in cls.model_fields})


class RiskEvaluationResponse(BaseModel):
    """v0 vs v1 on a held-out later period, plus the gate that decides whether v1 exists.

    `models` always contains v0 — the heuristic ships whatever happens, so its numbers are
    the ones in force — and contains v1 rows only once a fit has been persisted.
    """

    model_config = _ALLOW_MODEL_PREFIX

    generated_at: datetime
    trained_on_demo: bool
    provenance: RiskProvenance
    gate: GateStatusOut
    fitted: bool
    threshold: float
    # The train/test boundary. Always a date: the split is by `acquired_at`, never random.
    split_on: date | None
    train_rows: int
    test_rows: int
    total_rows: int
    demo_rows: int
    models: list[ModelMetricsOut]
    best_model: str | None
    # None when the comparison could not be made at all — not False, which would claim v1
    # was measured and lost.
    v1_beats_v0: bool | None
    design_columns: list[str]
    dropped_columns: list[str]
    coefficients: dict[str, float]
    notes: list[str]

    @classmethod
    def from_report(
        cls, report: EvaluationReport, *, include_demo: bool
    ) -> "RiskEvaluationResponse":
        return cls(
            generated_at=report.generated_at,
            trained_on_demo=report.trained_on_demo,
            provenance=RiskProvenance(
                # `or "v0"` only for a report with no split at all (an empty database): the
                # heuristic is what would run, so naming it beats an empty string.
                model_version=report.model_version or "v0",
                model_stage="v1" if report.fitted else "v0",
                trained_on_demo=report.trained_on_demo,
                fitted_below_gate=report.fitted_below_gate,
                demo_included=include_demo,
                demo_items=report.demo_rows,
                warning=_demo_warning(
                    trained_on_demo=report.trained_on_demo, below_gate=report.fitted_below_gate
                ),
            ),
            gate=GateStatusOut.from_gate(report.gate),
            fitted=report.fitted,
            threshold=report.threshold,
            split_on=report.split_on,
            train_rows=report.train_rows,
            test_rows=report.test_rows,
            total_rows=report.total_rows,
            demo_rows=report.demo_rows,
            models=[ModelMetricsOut.from_metrics(m) for m in report.models],
            best_model=report.best_model,
            v1_beats_v0=report.v1_beats_v0,
            design_columns=report.design_columns,
            dropped_columns=report.dropped_columns,
            coefficients=report.coefficients,
            notes=report.notes,
        )
