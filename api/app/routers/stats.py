"""Cross-domain reporting surface, reading `savings_entries`.

Thin by design: every number is produced by `services/savings/aggregate.py`, which is also
what the reconciler and the deprecated `/api/inventory/stats` delegate go through. A figure
that exists in only one endpoint is a figure nobody can check.

Every response echoes `demo_included` and `demo_entries`. A number never travels without its
provenance — a euro total computed over synthetic data is not a measurement, and the caller
must not have to guess which kind it received.
"""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.models.enums import SavingsDomain
from app.schemas.stats import (
    ByCategoryResponse,
    PriceIntelligenceResponse,
    StatsOverview,
    TimeseriesResponse,
)
from app.services.savings import aggregate, anomaly

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverview)
def overview(
    db: DbSession,
    user: CurrentUser,
    include_demo: bool = Query(default=False),
    include_inferred: bool = Query(default=False),
) -> StatsOverview:
    """Headline figures. `save_rate` is the one to read first: it is count-based over
    recorded outcomes, so unlike every euro figure here it has no coverage caveat.

    Read it next to `domains[].unaccounted`, which is the value bought and never resolved.
    The save rate is defined over resolved items only, and unresolved food is overwhelmingly
    food that was forgotten — so a high save rate beside a large unaccounted figure is an
    unmeasured result, not a good one.
    """
    return aggregate.overview(
        db, user_id=user.id, include_demo=include_demo, include_inferred=include_inferred
    )


@router.get("/timeseries", response_model=TimeseriesResponse)
def timeseries(
    db: DbSession,
    user: CurrentUser,
    bucket: Literal["week", "month"] = Query(default="week"),
    domain: SavingsDomain | None = None,
    weeks: int = Query(default=12, ge=1, le=104),
    include_demo: bool = Query(default=False),
    include_inferred: bool = Query(default=False),
) -> TimeseriesResponse:
    """Waste over time. `points` is contiguous and zero-filled; `by_category` is sparse.

    `weeks` counts buckets, not weeks, when `bucket=month` — the parameter keeps its name
    because the frontend and the URL already use it and the week view is the default.
    """
    return aggregate.timeseries(
        db,
        user_id=user.id,
        bucket=bucket,
        domain=domain,
        periods=weeks,
        include_demo=include_demo,
        include_inferred=include_inferred,
    )


@router.get("/by-category", response_model=ByCategoryResponse)
def by_category(
    db: DbSession,
    user: CurrentUser,
    bucket: Literal["week", "month"] = Query(default="month"),
    domain: SavingsDomain | None = None,
    months: int = Query(default=6, ge=1, le=36),
    include_demo: bool = Query(default=False),
    include_inferred: bool = Query(default=False),
) -> ByCategoryResponse:
    """Where the money goes and where it stops being food. Deltas are `None` when there is
    no prior bucket to compare against — never 0.0, which would read as "unchanged"."""
    return aggregate.by_category(
        db,
        user_id=user.id,
        bucket=bucket,
        domain=domain,
        periods=months,
        include_demo=include_demo,
        include_inferred=include_inferred,
    )


@router.get("/prices", response_model=PriceIntelligenceResponse)
def prices(
    db: DbSession,
    user: CurrentUser,
    include_demo: bool = Query(default=False),
) -> PriceIntelligenceResponse:
    """Price anomalies and bulk-buy advice, from `price_observations`.

    Both halves are deliberately quiet. An anomaly needs at least `min_observations` earlier
    prices at the same shop before any claim is made — two prices are not a history — and
    bulk-buy advice additionally requires the risk model to say the larger pack would still
    be eaten in time. In a food-waste app, "buy the big one" without that second check is
    advice to waste more.

    `pairs_below_threshold` is why an empty `anomalies` list is readable: it separates "no
    price is unusual" from "not enough prices have been typed to tell".
    """
    result = anomaly.report(db, user_id=user.id, include_demo=include_demo)
    return PriceIntelligenceResponse(
        generated_at=result.generated_at,
        demo_included=result.demo_included,
        demo_observations=result.demo_observations,
        observations=result.observations,
        pairs_examined=result.pairs_examined,
        pairs_below_threshold=result.pairs_below_threshold,
        min_observations=result.min_observations,
        # asdict, not __dict__: both are slotted frozen dataclasses and have no __dict__.
        anomalies=[asdict(a) for a in result.anomalies],
        advice=[asdict(a) for a in result.advice],
        notes=result.notes,
    )
