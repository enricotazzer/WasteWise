"""What gets scored, and what that score is allowed to claim.

One code path, two consumers: `jobs/scoring.py` runs it nightly and writes the result to
`risk_scores`; `routers/risk.py` runs it on the request and returns it. A figure that exists
in only one of those is a figure nobody can check — the same argument `services/savings`
makes about its aggregates.

Which model is in charge
--------------------------------------------------------------------------------
v0 unless a fitted v1 exists **and beat v0 on the held-out concordance**. Shipping a learned
model that lost to the heuristic it was supposed to improve on would be worse than not
shipping one, and `FittedModel.beats_v0` is decided at fit time on data the model never saw.

Provenance
--------------------------------------------------------------------------------
`trained_on_demo` is the second side of the fence, and it is deliberately broader than "the
Cox fit saw synthetic rows": it is true whenever *any* synthetic row contributed to the
number. v0 has no coefficients, but its tier tables are summarised from the household's
history, so a v0 score computed over a demo-filled kitchen is just as synthetic as a v1 one.
The version stamp is regenerated here from that combined fact rather than copied off the
model, so it cannot be right about the fit and wrong about the score.

Idempotency
--------------------------------------------------------------------------------
`risk_scores` is **history, not state** — evaluating a detector against later outcomes needs
the score as it was at the time, so rows accumulate. Re-running on the same day replaces that
day's rows for the same (item, model_version) rather than adding to them, which makes the job
safe to run twice and still leaves one row per item per day. Reads go through
`latest_scores`, which takes the newest row per item and nothing else.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import ItemStatus, RiskBand
from app.models.risk import RiskScore
from app.services.risk import survival
from app.services.risk.baseline import BaselineScorer, risk_band, stamp_version
from app.services.risk.features import build_frame, design_matrix

ACTIVE_STATUSES = (ItemStatus.ACTIVE, ItemStatus.PARTIAL)

STAGE_V0 = "v0"
STAGE_V1 = "v1"


@dataclass(frozen=True, slots=True)
class ScoredItem:
    """One row of the at-risk list, carrying enough product detail to render without a join."""

    item_id: int
    product_id: int
    product_name: str
    category_code: str
    unit: str
    quantity: float
    acquired_at: date
    expiry_date: date | None
    days_to_expiry: int | None
    days_in_house: int
    is_opened: bool
    is_demo: bool
    p_consume_3d: float
    p_consume_7d: float
    p_consume_before_expiry: float | None
    band: RiskBand
    features: dict[str, Any]
    # When the nightly job last wrote this item to `risk_scores`. None until it has run —
    # the score above is live either way, this only says whether history exists yet.
    last_persisted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScoringRun:
    computed_at: datetime
    model_version: str
    model_stage: str
    trained_on_demo: bool
    fitted_below_gate: bool
    demo_included: bool
    demo_items: int
    items: list[ScoredItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def score_active(
    db: Session,
    *,
    user_id: int | None = None,
    include_demo: bool = True,
    as_of: date | None = None,
    use_fitted_model: bool = True,
) -> ScoringRun:
    """Score every item still in the kitchen.

    `include_demo` defaults to True because the Kitchen screen shows demo items and an
    unscored row would look broken. They are scored, counted in `demo_items`, and the whole
    run is stamped accordingly — visible, not filtered away.
    """
    as_of = as_of or date.today()
    now = datetime.now(UTC)
    frame = build_frame(db, user_id=user_id, include_demo=include_demo, as_of=as_of)
    notes: list[str] = []

    active = frame[frame["status"].isin(ACTIVE_STATUSES)].reset_index(drop=True)
    demo_items = int(active["is_demo"].sum()) if len(active) else 0
    history_has_demo = bool(frame["is_demo"].any()) if len(frame) else False

    model = survival.load_model() if use_fitted_model else None
    if model is not None and not model.beats_v0:
        notes.append(
            f"a fitted {model.version} exists but did not beat v0 on held-out concordance; "
            "scoring stays on the heuristic"
        )
        model = None

    trained_on_demo = history_has_demo or bool(model is not None and model.trained_on_demo)
    below_gate = bool(model is not None and model.fitted_below_gate)
    stage = STAGE_V1 if model is not None else STAGE_V0
    base = survival.V1_BASE if model is not None else settings.risk_model_version
    version = stamp_version(base, trained_on_demo=trained_on_demo, below_gate=below_gate)

    if len(active) == 0:
        return ScoringRun(
            computed_at=now,
            model_version=version,
            model_stage=stage,
            trained_on_demo=trained_on_demo,
            fitted_below_gate=below_gate,
            demo_included=include_demo,
            demo_items=0,
            notes=notes or ["nothing in the kitchen to score"],
        )

    # The baseline is summarised from the whole frame, resolved rows and all. Active items
    # are censored observations, so they inform the Kaplan-Meier curves without contributing
    # an outcome — which is exactly what censoring is for.
    scorer = BaselineScorer.fit(frame)
    baselines = scorer.score_frame(active)

    cox = _cox_scores(model, active, notes) if model is not None else None
    if model is not None and cox is None:
        stage = STAGE_V0
        version = stamp_version(
            settings.risk_model_version, trained_on_demo=trained_on_demo, below_gate=False
        )
        below_gate = False

    persisted = latest_scores(db, item_ids=[int(i) for i in active["item_id"]])
    rows = active.to_dict("records")
    items: list[ScoredItem] = []

    for position, (row, baseline) in enumerate(zip(rows, baselines, strict=True)):
        if cox is None:
            p3 = baseline.p_consume_3d
            p7 = baseline.p_consume_7d
            expiry = baseline.p_consume_before_expiry
            features = baseline.as_features()
        else:
            p3 = float(cox["p3"][position])
            p7 = float(cox["p7"][position])
            expiry = None if row["has_expiry"] == 0 else float(cox["expiry"][position])
            features = {
                "model": base,
                "partial_hazard": round(float(cox["hazard"][position]), 6),
                "horizon_days": None if row["has_expiry"] == 0 else float(row["days_to_expiry"]),
                "elapsed_days": float(row["days_in_house"]),
                # v0's reasoning is kept alongside v1's number. It costs one dict and it is
                # the only thing that makes a surprising Cox score debuggable at 8am.
                "baseline_tier": baseline.tier,
                "baseline_tier_n": baseline.tier_n,
                "baseline_p_consume_before_expiry": baseline.p_consume_before_expiry,
            }

        stored = persisted.get(int(row["item_id"]))
        items.append(
            ScoredItem(
                item_id=int(row["item_id"]),
                product_id=int(row["product_id"]),
                product_name=str(row["product_name"]),
                category_code=str(row["category_code"]),
                unit=str(row["unit"]),
                quantity=float(row["quantity"]),
                acquired_at=row["acquired_at"],
                expiry_date=row["expiry_date"],
                days_to_expiry=None if row["has_expiry"] == 0 else int(row["days_to_expiry"]),
                days_in_house=int(row["days_in_house"]),
                is_opened=bool(row["is_opened"]),
                is_demo=bool(row["is_demo"]),
                p_consume_3d=p3,
                p_consume_7d=p7,
                p_consume_before_expiry=expiry,
                band=risk_band(expiry),
                features=features,
                last_persisted_at=stored.computed_at if stored else None,
            )
        )

    # Highest risk first, then soonest expiry — the Kitchen screen's order, decided here so
    # every caller gets the same one.
    items.sort(
        key=lambda i: (
            i.p_consume_before_expiry if i.p_consume_before_expiry is not None else 2.0,
            i.days_to_expiry if i.days_to_expiry is not None else 10_000,
        )
    )

    return ScoringRun(
        computed_at=now,
        model_version=version,
        model_stage=stage,
        trained_on_demo=trained_on_demo,
        fitted_below_gate=below_gate,
        demo_included=include_demo,
        demo_items=demo_items,
        items=items,
        notes=notes,
    )


def _cox_scores(
    model: survival.FittedModel, active: pd.DataFrame, notes: list[str]
) -> dict[str, np.ndarray] | None:
    """Apply the fitted Cox model, or give up and let v0 answer.

    A model fitted against a column set this build no longer produces is a real possibility
    across a schema change, and the correct response is a note plus the heuristic — not a
    500 in the middle of the nightly chain.
    """
    try:
        matrix = design_matrix(active, model.defaults)[model.design_columns]
    except KeyError as exc:
        notes.append(f"fitted model expects a covariate this build does not produce: {exc}")
        return None

    matrix = matrix.reset_index(drop=True)
    elapsed = active["days_in_house"].to_numpy(dtype=float)
    horizon = active["days_to_expiry"].to_numpy(dtype=float)
    try:
        return {
            "hazard": survival.cox_hazard(model.cox, matrix),
            "p3": survival.cox_probabilities(
                model.cox, matrix, start=elapsed, horizon=np.full(len(matrix), 3.0)
            ),
            "p7": survival.cox_probabilities(
                model.cox, matrix, start=elapsed, horizon=np.full(len(matrix), 7.0)
            ),
            "expiry": survival.cox_probabilities(model.cox, matrix, start=elapsed, horizon=horizon),
        }
    except Exception as exc:  # a stale pickle can fail in several unrelated ways
        notes.append(f"fitted model failed to score ({exc.__class__.__name__}) — fell back to v0")
        return None


def latest_scores(db: Session, *, item_ids: Sequence[int]) -> dict[int, RiskScore]:
    """The newest persisted score per item.

    `risk_scores` is append-per-day, so a plain SELECT would return a month of rows per item.
    DISTINCT ON is the Postgres form of "one row per group, the newest"; the suite runs on
    Postgres precisely so queries like this are the ones under test.
    """
    if not item_ids:
        return {}
    rows = db.scalars(
        select(RiskScore)
        .where(RiskScore.inventory_item_id.in_(list(item_ids)))
        .distinct(RiskScore.inventory_item_id)
        .order_by(RiskScore.inventory_item_id, RiskScore.computed_at.desc(), RiskScore.id.desc())
    )
    return {row.inventory_item_id: row for row in rows}


def persist(db: Session, run: ScoringRun) -> int:
    """Write a run to `risk_scores`, replacing anything this model already wrote today.

    Replace-per-day rather than replace-per-item: history is the point (a detector can only
    be evaluated against later outcomes if the score it gave *at the time* survives), but a
    job re-run an hour later is a correction of today's answer, not a second observation.
    """
    if not run.items:
        return 0

    day = run.computed_at.astimezone(UTC).date()
    start = datetime.combine(day, time.min, tzinfo=UTC)
    item_ids = [item.item_id for item in run.items]

    db.execute(
        delete(RiskScore)
        .where(
            RiskScore.inventory_item_id.in_(item_ids),
            RiskScore.model_version == run.model_version,
            RiskScore.computed_at >= start,
            RiskScore.computed_at < start + timedelta(days=1),
        )
        .execution_options(synchronize_session=False)
    )
    db.add_all(
        RiskScore(
            inventory_item_id=item.item_id,
            computed_at=run.computed_at,
            model_version=run.model_version,
            p_consume_3d=item.p_consume_3d,
            p_consume_7d=item.p_consume_7d,
            p_consume_before_expiry=item.p_consume_before_expiry,
            risk_band=item.band,
            features=item.features,
        )
        for item in run.items
    )
    db.commit()
    return len(run.items)
