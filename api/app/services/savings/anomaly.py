"""Price intelligence over `price_observations` — the table nothing read until now.

Two questions, and they are answered very differently on purpose.

**Price anomalies.** Per `(product, store)`, is the most recent price out of line with what
this household has paid there before? Median and a MAD-derived spread rather than mean and
standard deviation, because a single outlier is exactly what we are looking for and it would
drag both a mean and an SD toward itself, hiding the thing it *is*.

Silent below `MIN_OBSERVATIONS` — five. Prices in this system are optional and typed by
hand, so a product with two observations is not a price history, it is two anecdotes, and
"this is 40% above normal" computed from them is a sentence with no evidence behind it. The
threshold is a refusal, not a tuning knob: below it the function returns nothing at all
rather than a flagged result with a caveat attached.

**Batch-purchase advice.** This one has a second gate, and the gate is the point. "The 1 kg
pack is cheaper per kilo" is arithmetic anybody can do. Whether you will *eat* a kilo before
it goes off is the question this app exists to answer, and getting it wrong here would be
worse than useless: in a food-waste app, recommending a bigger pack that then rots is
advice to waste more, printed by the system that measures waste.

So the advice fires only when both hold:

    1. the larger pack's unit price is materially lower (>= MIN_UNIT_SAVING_PCT), and
    2. `P(consumed before expiry)` at the larger quantity stays above the risk threshold,
       computed with the same `services/risk` baseline that scores the kitchen.

The quantity adjustment is stated rather than hidden: the risk model has no pack-size
covariate, so a pack `k` times larger is modelled as taking `k` times as long to finish —
the consumption hazard is divided by the size ratio. That is an assumption about how a
household eats, it is visible in the output as `assumed_rate_divisor`, and it is the weakest
link in this function.
"""

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import InventoryItem, PriceObservation, Product
from app.services.risk.baseline import BaselineScorer
from app.services.risk.features import build_frame

log = logging.getLogger(__name__)

#: Below this many observations for a (product, store) pair, no claim is made at all.
MIN_OBSERVATIONS = 5
#: How far from the trailing median counts as an anomaly, in robust sigmas.
SIGMA_THRESHOLD = 1.5
#: 1 / Φ⁻¹(3/4). Scales a median absolute deviation onto the same footing as a standard
#: deviation for normally-distributed data, so one threshold means the same thing whichever
#: spread estimate was available.
_MAD_TO_SIGMA = 1.4826
#: A unit-price drop smaller than this is noise or a rounding artifact, not a bulk discount.
MIN_UNIT_SAVING_PCT = 10.0
#: Pack sizes closer than this are the same pack with a different label.
MIN_SIZE_RATIO = 1.5


@dataclass(frozen=True, slots=True)
class PriceAnomaly:
    """One out-of-line price, with everything needed to judge it.

    `direction` is reported rather than filtered: a price well *below* the trailing median
    is a deal worth knowing about, and a savings app that only ever reported bad news would
    be missing half of its own subject.
    """

    product_id: int
    product_name: str
    store: str | None
    unit: str
    observed_at: date
    unit_price_eur: Decimal
    median_eur: Decimal
    #: Robust sigma of the trailing window. Zero when every earlier price was identical.
    sigma_eur: Decimal
    deviation_sigmas: float
    delta_pct: float
    direction: str  # "above" | "below"
    observations: int


@dataclass(frozen=True, slots=True)
class BatchAdvice:
    """A bulk-buy recommendation that has passed the waste gate."""

    product_id: int
    product_name: str
    store: str | None
    unit: str
    small_size: float
    small_unit_price_eur: Decimal
    large_size: float
    large_unit_price_eur: Decimal
    saving_pct: float
    #: P(consumed before expiry) at the larger quantity, from the v0 baseline.
    p_consume_large: float
    threshold: float
    #: large_size / small_size — the factor the consumption hazard was divided by.
    assumed_rate_divisor: float
    observations: int


@dataclass
class PriceReport:
    """Everything the endpoint returns, coverage included.

    `pairs_below_threshold` is not a diagnostic afterthought: on a household that types
    prices occasionally it will be most of the table, and an empty anomaly list means
    something very different depending on whether it is 0 or 40.
    """

    generated_at: date
    demo_included: bool
    observations: int
    demo_observations: int
    pairs_examined: int
    pairs_below_threshold: int
    min_observations: int = MIN_OBSERVATIONS
    anomalies: list[PriceAnomaly] = field(default_factory=list)
    advice: list[BatchAdvice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Observation:
    price: Decimal
    observed_at: date
    unit: str
    quantity: float | None
    is_demo: bool


def _load(db: Session, *, user_id: int, include_demo: bool) -> tuple[dict, int, int]:
    """Observations grouped by (product_id, store), oldest first.

    Left-joined to the inventory item because two facts live there and not on the
    observation: whether the row is synthetic, and what pack size was bought. The join is
    outer because an observation whose item was deleted is still a real price.
    """
    rows = db.execute(
        select(PriceObservation, InventoryItem.is_demo, InventoryItem.quantity)
        .outerjoin(InventoryItem, PriceObservation.inventory_item_id == InventoryItem.id)
        .where(PriceObservation.user_id == user_id)
        .order_by(PriceObservation.product_id, PriceObservation.observed_at, PriceObservation.id)
    ).all()

    grouped: dict[tuple[int, str | None], list[_Observation]] = defaultdict(list)
    total = 0
    demo = 0
    for observation, is_demo, quantity in rows:
        synthetic = bool(is_demo)
        total += 1
        if synthetic:
            demo += 1
            if not include_demo:
                continue
        grouped[(observation.product_id, observation.store)].append(
            _Observation(
                price=observation.unit_price_eur,
                observed_at=observation.observed_at,
                unit=observation.unit.value,
                quantity=float(quantity) if quantity is not None else None,
                is_demo=synthetic,
            )
        )
    return grouped, total, demo


def _robust_sigma(values: list[float]) -> float:
    """MAD-derived spread, falling back to the sample standard deviation.

    MAD is 0 whenever more than half the observations share one price — common for a staple
    bought at the same shop — and a 0 sigma would make every other price infinitely
    anomalous. The SD fallback is what keeps that from happening.
    """
    if len(values) < 2:
        return 0.0
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    if mad > 0:
        return mad * _MAD_TO_SIGMA
    try:
        return statistics.stdev(values)
    except statistics.StatisticsError:  # pragma: no cover - guarded by the length check
        return 0.0


def price_anomalies(
    db: Session, *, user_id: int, include_demo: bool = False
) -> tuple[list[PriceAnomaly], int, int]:
    """Flag the latest price of each (product, store) against its own trailing history.

    "Trailing" is literal: the comparison window is every observation *before* the one being
    judged. Including it would let a single large outlier pull the median toward itself and
    partly conceal its own deviation.
    """
    grouped, _, _ = _load(db, user_id=user_id, include_demo=include_demo)
    names = {p.id: p.name_it for p in db.scalars(select(Product))}

    anomalies: list[PriceAnomaly] = []
    examined = 0
    below = 0

    for (product_id, store), observations in grouped.items():
        examined += 1
        # The latest observation is the one being judged, so the history behind it must be
        # at least MIN_OBSERVATIONS on its own.
        if len(observations) < MIN_OBSERVATIONS + 1:
            below += 1
            continue

        latest = observations[-1]
        trailing = [float(o.price) for o in observations[:-1]]
        median = statistics.median(trailing)
        sigma = _robust_sigma(trailing)
        if sigma <= 0 or median <= 0:
            continue

        deviation = (float(latest.price) - median) / sigma
        if abs(deviation) < SIGMA_THRESHOLD:
            continue

        anomalies.append(
            PriceAnomaly(
                product_id=product_id,
                product_name=names.get(product_id, f"#{product_id}"),
                store=store,
                unit=latest.unit,
                observed_at=latest.observed_at,
                unit_price_eur=latest.price,
                median_eur=Decimal(str(round(median, 2))),
                sigma_eur=Decimal(str(round(sigma, 2))),
                deviation_sigmas=round(deviation, 2),
                delta_pct=round((float(latest.price) - median) / median * 100.0, 1),
                direction="above" if deviation > 0 else "below",
                observations=len(observations),
            )
        )

    anomalies.sort(key=lambda a: -abs(a.deviation_sigmas))
    return anomalies, examined, below


def _p_consume_at_scale(scorer: BaselineScorer, row: dict, *, divisor: float) -> float | None:
    """P(consumed before expiry) if the pack were `divisor` times bigger.

    Scored at purchase — full shelf life ahead, nothing eaten yet — because that is the
    moment the advice is for. The size adjustment is applied by scaling `fraction_remaining`
    so the baseline's own item tier sees a proportionally slower burn-down; where that tier
    doesn't apply, the implied hazard is scaled directly below.
    """
    base = scorer.score(row, horizon_days=row.get("shelf_life_days"), elapsed_days=0.0)
    if base.p_consume_before_expiry is None:
        return None
    # p = coverage * (1 - exp(-rate * horizon)); a pack `k` times larger takes `k` times as
    # long, i.e. the same curve at rate/k. Recomputed from the reported rate rather than
    # re-fitted, so the number stays traceable to a tier the user can be shown.
    horizon = base.horizon_days
    if horizon is None:
        return None
    scaled = base.coverage * (1.0 - math.exp(-(base.rate_per_day / divisor) * horizon))
    return min(max(scaled, 0.0), 1.0)


def batch_advice(db: Session, *, user_id: int, include_demo: bool = False) -> list[BatchAdvice]:
    """Bulk-buy suggestions that survive the waste gate.

    Pack size comes from the inventory item the observation was written with, so this only
    ever compares sizes the household has actually bought — never a hypothetical pack.
    """
    grouped, _, _ = _load(db, user_id=user_id, include_demo=include_demo)
    products = {p.id: p for p in db.scalars(select(Product))}

    frame = build_frame(db, user_id=user_id, include_demo=include_demo)
    scorer = BaselineScorer.fit(frame)
    threshold = settings.risk_at_risk_threshold
    today = date.today()

    advice: list[BatchAdvice] = []
    for (product_id, store), observations in grouped.items():
        sized = [o for o in observations if o.quantity and o.quantity > 0]
        if len(sized) < MIN_OBSERVATIONS:
            continue

        # Median unit price per pack size: one lucky promotion on the big pack should not
        # produce standing advice to buy it.
        by_size: dict[float, list[float]] = defaultdict(list)
        for observation in sized:
            by_size[round(observation.quantity, 3)].append(float(observation.price))
        if len(by_size) < 2:
            continue

        sizes = sorted(by_size)
        small, large = sizes[0], sizes[-1]
        if large / small < MIN_SIZE_RATIO:
            continue

        small_price = statistics.median(by_size[small])
        large_price = statistics.median(by_size[large])
        if small_price <= 0:
            continue
        saving = (small_price - large_price) / small_price * 100.0
        if saving < MIN_UNIT_SAVING_PCT:
            continue

        product = products.get(product_id)
        if product is None:
            continue

        divisor = large / small
        probability = _p_consume_at_scale(scorer, _synthetic_row(product, today), divisor=divisor)
        if probability is None or probability < threshold:
            # The gate doing its job. Cheaper per kilo and likely to be binned is not a
            # saving, and this is the one place in the app where saying nothing is the
            # correct output.
            log.debug(
                "batch advice withheld for %s: p=%s below %.2f",
                product.name_it,
                probability,
                threshold,
            )
            continue

        advice.append(
            BatchAdvice(
                product_id=product_id,
                product_name=product.name_it,
                store=store,
                unit=sized[-1].unit,
                small_size=small,
                small_unit_price_eur=Decimal(str(round(small_price, 2))),
                large_size=large,
                large_unit_price_eur=Decimal(str(round(large_price, 2))),
                saving_pct=round(saving, 1),
                p_consume_large=round(probability, 3),
                threshold=threshold,
                assumed_rate_divisor=round(divisor, 2),
                observations=len(sized),
            )
        )

    advice.sort(key=lambda a: -a.saving_pct)
    return advice


def _synthetic_row(product: Product, today: date) -> dict:
    """A feature row for "this product, bought today, at full shelf life".

    Only the fields the v0 baseline reads. Built by hand rather than pulled from the frame
    because the question is counterfactual — there is no such item in the kitchen yet.
    """
    shelf = product.effective_shelf_life_days
    return {
        "product_id": product.id,
        "category_code": product.category.code,
        "is_perishable": product.is_perishable,
        "shelf_life_days": float(shelf) if shelf else float("nan"),
        "days_to_expiry": float(shelf) if shelf else float("nan"),
        "days_in_house": 0.0,
        "fraction_remaining": 1.0,
    }


def report(db: Session, *, user_id: int, include_demo: bool = False) -> PriceReport:
    """Both halves, with the coverage that makes them readable."""
    _, total, demo = _load(db, user_id=user_id, include_demo=True)
    anomalies, examined, below = price_anomalies(db, user_id=user_id, include_demo=include_demo)
    advice = batch_advice(db, user_id=user_id, include_demo=include_demo)

    notes: list[str] = []
    if total == 0:
        notes.append(
            "no prices recorded yet — prices are optional and typed by hand, so this "
            "surface stays empty until some exist"
        )
    elif examined and below == examined:
        notes.append(
            f"every product/store pair has fewer than {MIN_OBSERVATIONS} observations, "
            "so no anomaly claim is made"
        )
    if demo and not include_demo:
        notes.append(f"{demo} synthetic price observations excluded")

    return PriceReport(
        generated_at=date.today(),
        demo_included=include_demo,
        observations=total,
        demo_observations=demo,
        pairs_examined=examined,
        pairs_below_threshold=below,
        anomalies=anomalies,
        advice=advice,
        notes=notes,
    )
