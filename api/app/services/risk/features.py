"""The feature frame: one row per `inventory_item`, in survival form.

How the three outcomes are encoded — the part a reviewer should read first
--------------------------------------------------------------------------------
The unit of observation is one inventory item. The clock starts at `acquired_at`.

    duration_days   T = resolved_at - acquired_at for a resolved item,
                        as_of      - acquired_at for one still in the kitchen.
    event_consumed  1 iff the item ended CONSUMED. **This is the event of interest.**
    event_wasted    1 iff the item ended WASTED. This is a *competing* event: it makes
                    consumption impossible from that moment on, it is not a longer wait.
    censored        1 iff the item is ACTIVE or PARTIAL at `as_of` — right-censored, we know
                    only that it survived at least T days without being eaten.

Competing risk is handled as a **cause-specific hazard**: the Cox fit in `survival.py`
treats a waste event as censoring at its observed time. That makes `1 - S(t)` the
probability of consumption *in a world where nothing gets thrown out*, so it overstates the
true cumulative incidence; the ranking it produces — which is what the c-index and the
at-risk flag consume — is unaffected. The alternative, a Fine–Gray subdistribution model,
was rejected because lifelines does not ship one and hand-rolling IPCW weights over ~150
items would add more variance than the bias it removes. `baseline.py` covers the same gap a
different way, with an explicit `coverage` ceiling: the share of a product that ever gets
eaten at all.

**Repurchase-inferred outcomes are censored, not counted as consumption.** `jobs/repurchase`
closes a stale item because its product was bought again, which is a guess that conflates
"eaten" with "quietly binned" — the exact distinction this model exists to make. Treating
it as an unknown outcome (censored at the inference date) is the only honest encoding; it
keeps the item's survival time, which is real, and drops the label, which isn't.

Leakage
--------------------------------------------------------------------------------
Every history-derived covariate here is computed from **strictly earlier** rows: a product's
consumption rate for the item bought on 12 March uses only items of that product that had
already been resolved before 12 March. Computing those rates over the whole table would let
each row see its own outcome, and would inflate every metric downstream far more visibly
than a random train/test split would.

Dynamic state (`days_to_expiry`, `days_in_house`, `is_opened`, `fraction_remaining`) is
carried on the frame because the v0 scorer and the UI both need it, but it is deliberately
**not** in `DESIGN_COLUMNS`. Under the survival framing those quantities are functions of
the model's own time axis — `days_to_expiry(t) = shelf_life_days - t` — so feeding them to a
Cox fit is circular. The information survives as `shelf_life_days` plus the conditional
`S(d+k)/S(d)` that `survival.py` evaluates. Time-varying covariates would need a
counting-process fit (`CoxTimeVaryingFitter`) and several times this much data.
"""

import math
from datetime import date
from typing import Any, Final

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import Category, Product
from app.models.enums import EventSource, ItemStatus
from app.models.inventory import ConsumptionEvent, InventoryItem

# lifelines refuses T <= 0, and an item bought and eaten the same day is a real observation
# (bread, a roast chicken) and often the *most* informative one. Half a day keeps those rows
# in the fit instead of silently dropping the fastest-moving half of the kitchen.
MIN_DURATION_DAYS: Final = 0.5

# Past this, the printed shelf life stops carrying information — a 540-day passata and a
# 720-day tin of chickpeas behave identically. Capping keeps `log_shelf_life` from being a
# proxy for "is a tin".
MAX_SHELF_LIFE_DAYS: Final = 400

# Smoothing for the prior-only consumption rates: a product with one earlier observation
# should not read as a 0% or 100% consumption rate. Pulls toward the household average.
_RATE_PRIOR_WEIGHT: Final = 2.0

# Everything else (ACTIVE, PARTIAL) is right-censored at `as_of`. Defined as the terminal
# set rather than the censored one because that is the direction the encoding reads: an
# outcome has to be *observed* to count, and anything unobserved is censored by default.
TERMINAL_STATUSES: Final = (ItemStatus.CONSUMED, ItemStatus.WASTED)

# The frame's contract. Declared as dtypes rather than a bare column list so an empty
# database produces a frame with the *same* columns and the same types as a full one —
# every caller below does arithmetic on these, and `object`-typed empties break differently
# from `float64`-typed ones, which is the worst kind of bug to find in a nightly job.
_DTYPES: Final[dict[str, str]] = {
    "item_id": "int64",
    "user_id": "int64",
    "product_id": "int64",
    "product_name": "object",
    "category_code": "object",
    "unit": "object",
    "status": "object",
    "is_demo": "bool",
    "is_staple": "bool",
    "is_perishable": "bool",
    # --- survival encoding ---
    "duration_days": "float64",
    "event_consumed": "int64",
    "event_wasted": "int64",
    "censored": "int64",
    "outcome_inferred": "bool",
    # --- dates, kept as dates for the API and as ordinals for the arithmetic ---
    "acquired_at": "object",
    "expiry_date": "object",
    "acquired_ord": "int64",
    "expiry_ord": "float64",
    "resolved_ord": "float64",
    # --- covariates known at acquisition (these are the ones the model may use) ---
    "shelf_life_days": "float64",
    "log_shelf_life": "float64",
    "has_expiry": "int64",
    "quantity": "float64",
    "qty_ratio": "float64",
    "month": "int64",
    "month_sin": "float64",
    "month_cos": "float64",
    "same_category_active": "int64",
    "product_consume_rate": "float64",
    "product_prior_n": "int64",
    "category_consume_rate": "float64",
    "category_prior_n": "int64",
    # --- dynamic state: for v0 and the UI, never for the fit. See the module docstring. ---
    "days_to_expiry": "float64",
    "days_in_house": "float64",
    "is_opened": "int64",
    "fraction_remaining": "float64",
}

FRAME_COLUMNS: Final[tuple[str, ...]] = tuple(_DTYPES)

# What the v1 fit is actually allowed to see. Product and category enter as prior-only
# target encodings rather than one-hot dummies: ~50 products against ~150 resolved items
# would give a design matrix wider than it is tall, and Cox would fit the row ids.
DESIGN_COLUMNS: Final[tuple[str, ...]] = (
    "log_shelf_life",
    "has_expiry",
    "is_perishable",
    "is_staple",
    "qty_ratio",
    "product_consume_rate",
    "category_consume_rate",
    "month_sin",
    "month_cos",
    "same_category_active",
)


def empty_frame() -> pd.DataFrame:
    """A zero-row frame with the full column set and the right dtypes."""
    return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in _DTYPES.items()})


def _rows(db: Session, *, user_id: int | None, include_demo: bool, include_nonfood: bool) -> list:
    # One flag per item saying whether any of its outcome events came from the repurchase
    # job. EXISTS rather than a join so an item with several events still yields one row.
    inferred = (
        select(ConsumptionEvent.inventory_item_id)
        .where(ConsumptionEvent.source == EventSource.REPURCHASE_INFERRED)
        .distinct()
        .subquery()
    )
    stmt = (
        select(
            InventoryItem.id,
            InventoryItem.user_id,
            InventoryItem.product_id,
            InventoryItem.quantity,
            InventoryItem.unit,
            InventoryItem.acquired_at,
            InventoryItem.expiry_date,
            InventoryItem.opened_at,
            InventoryItem.resolved_at,
            InventoryItem.status,
            InventoryItem.fraction_remaining,
            InventoryItem.is_demo,
            Product.name_it.label("product_name"),
            Product.is_staple,
            Category.code.label("category_code"),
            Category.is_perishable,
            (inferred.c.inventory_item_id.isnot(None)).label("outcome_inferred"),
        )
        .join(Product, Product.id == InventoryItem.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(inferred, inferred.c.inventory_item_id == InventoryItem.id)
        .order_by(InventoryItem.acquired_at, InventoryItem.id)
    )
    if user_id is not None:
        stmt = stmt.where(InventoryItem.user_id == user_id)
    if not include_demo:
        stmt = stmt.where(InventoryItem.is_demo.is_(False))
    if not include_nonfood:
        # Same exclusion the savings ledger applies at R0. Detergent has no expiry, is never
        # thrown away for going off, and would only dilute every rate in here.
        stmt = stmt.where(Category.is_food.is_(True))
    return list(db.execute(stmt))


def _expanding_consume_rate(frame: pd.DataFrame, key: str) -> tuple[np.ndarray, np.ndarray]:
    """Prior-only consumption rate per row, plus how many observations it rests on.

    For each row: of the items sharing `key` whose outcome was already known on the day this
    one was bought, what share ended consumed? Rows with no such history get NaN, which the
    design matrix resolves down the fallback chain rather than guessing here.

    A per-group loop rather than a clever vectorization: a household has hundreds of items,
    not millions, and this is the one calculation in the module where being able to read the
    leakage argument off the code matters more than the microseconds.
    """
    size = len(frame)
    rate = np.full(size, np.nan)
    count = np.zeros(size, dtype=np.int64)
    if size == 0:
        return rate, count

    acquired = frame["acquired_ord"].to_numpy(dtype=float)
    resolved = frame["resolved_ord"].to_numpy(dtype=float)
    consumed = frame["event_consumed"].to_numpy(dtype=float)
    observed = (frame["event_consumed"] + frame["event_wasted"]).to_numpy() > 0

    for positions in frame.groupby(key, sort=False).indices.values():
        known = positions[observed[positions] & ~np.isnan(resolved[positions])]
        if known.size == 0:
            continue
        known = known[np.argsort(resolved[known], kind="stable")]
        times = resolved[known]
        cumulative = np.cumsum(consumed[known])
        # side="left": an outcome recorded on the same day the new item was bought does not
        # count as prior knowledge. Conservative, and it removes any same-day ambiguity.
        prior = np.searchsorted(times, acquired[positions], side="left")
        taken = np.where(prior > 0, cumulative[np.clip(prior - 1, 0, None)], 0.0)
        count[positions] = prior
        rate[positions] = np.where(prior > 0, taken / np.maximum(prior, 1), np.nan)
    return rate, count


def _shrink(rate: np.ndarray, count: np.ndarray, toward: float) -> np.ndarray:
    """Pull a rate estimated from `count` observations toward the household average.

    One earlier item that happened to be eaten is not a 100% consumption rate, and Cox will
    happily fit a coefficient to that fiction if nobody stops it.
    """
    with np.errstate(invalid="ignore"):
        observed = np.nan_to_num(rate, nan=0.0) * count
    smoothed = (observed + _RATE_PRIOR_WEIGHT * toward) / (count + _RATE_PRIOR_WEIGHT)
    return np.where(count > 0, smoothed, np.nan)


def _active_at_acquisition(frame: pd.DataFrame) -> np.ndarray:
    """How many items of the same category were already in the house when this one arrived.

    The competition term: a fourth courgette in a fridge that already holds three is much
    less likely to get eaten in time than the first one was.
    """
    size = len(frame)
    out = np.zeros(size, dtype=np.int64)
    if size == 0:
        return out

    acquired = frame["acquired_ord"].to_numpy(dtype=float)
    resolved = frame["resolved_ord"].to_numpy(dtype=float)

    for positions in frame.groupby("category_code", sort=False).indices.values():
        bought = np.sort(acquired[positions])
        gone = np.sort(resolved[positions][~np.isnan(resolved[positions])])
        acquired_here = acquired[positions]
        out[positions] = np.searchsorted(bought, acquired_here, side="left") - np.searchsorted(
            gone, acquired_here, side="left"
        )
    return out


def _qty_ratio(frame: pd.DataFrame) -> np.ndarray:
    """Quantity against the household's typical purchase of that product, prior-only.

    1.0 where there is no earlier purchase to compare against — an unseen product is, by
    definition, a typical one.
    """
    size = len(frame)
    ratio = np.ones(size)
    if size == 0:
        return ratio

    quantity = frame["quantity"].to_numpy(dtype=float)
    acquired = frame["acquired_ord"].to_numpy(dtype=float)

    for positions in frame.groupby("product_id", sort=False).indices.values():
        ordered = positions[np.argsort(acquired[positions], kind="stable")]
        times = acquired[ordered]
        cumulative = np.cumsum(quantity[ordered])
        prior = np.searchsorted(times, acquired[positions], side="left")
        total = np.where(prior > 0, cumulative[np.clip(prior - 1, 0, None)], 0.0)
        typical = np.where(prior > 0, total / np.maximum(prior, 1), np.nan)
        usable = (prior > 0) & (typical > 0)
        safe = np.where(usable, typical, 1.0)
        ratio[positions] = np.where(usable, quantity[positions] / safe, 1.0)
    return ratio


def build_frame(
    db: Session,
    *,
    user_id: int | None = None,
    include_demo: bool = True,
    include_nonfood: bool = False,
    as_of: date | None = None,
) -> pd.DataFrame:
    """One row per inventory item, ready for v0 scoring, a v1 fit, or a live read.

    `include_demo` defaults to True because the *shape* of the frame is the same either way
    and every consumer decides for itself: the fit stamps `-demo` on the model it produces,
    the API echoes `trained_on_demo`. Silently dropping synthetic rows here would make the
    fence invisible instead of loud.

    Never raises on an empty database — it returns an empty frame with the full column set,
    which is what lets the cold-start path be a normal path rather than a special case.
    """
    as_of = as_of or date.today()
    as_of_ord = as_of.toordinal()

    rows = _rows(db, user_id=user_id, include_demo=include_demo, include_nonfood=include_nonfood)
    if not rows:
        return empty_frame()

    records: list[dict[str, Any]] = []
    for row in rows:
        acquired_ord = row.acquired_at.toordinal()
        expiry_ord = float(row.expiry_date.toordinal()) if row.expiry_date is not None else math.nan

        terminal = row.status in TERMINAL_STATUSES
        # A terminal status with no timestamp is a data defect, not an outcome we can time.
        # Falling back to `as_of` keeps the row (its label is still real) at the cost of an
        # over-long duration, which is strictly better than dropping an observed waste event.
        resolved_day = row.resolved_at.date() if row.resolved_at is not None else as_of
        resolved_ord = float(resolved_day.toordinal()) if terminal else math.nan

        end_ord = resolved_ord if terminal else float(as_of_ord)
        duration = max(float(end_ord - acquired_ord), MIN_DURATION_DAYS)

        # An outcome the repurchase job inferred is an outcome nobody observed: keep the
        # duration, drop the label, censor.
        inferred = bool(row.outcome_inferred)
        consumed = int(terminal and row.status is ItemStatus.CONSUMED and not inferred)
        wasted = int(terminal and row.status is ItemStatus.WASTED and not inferred)

        shelf_life = float(expiry_ord - acquired_ord) if row.expiry_date is not None else math.nan
        month = row.acquired_at.month

        records.append(
            {
                "item_id": row.id,
                "user_id": row.user_id,
                "product_id": row.product_id,
                "product_name": row.product_name,
                "category_code": row.category_code,
                "unit": row.unit.value,
                "status": row.status,
                "is_demo": bool(row.is_demo),
                "is_staple": bool(row.is_staple),
                "is_perishable": bool(row.is_perishable),
                "duration_days": duration,
                "event_consumed": consumed,
                "event_wasted": wasted,
                "censored": int(not (consumed or wasted)),
                "outcome_inferred": inferred,
                "acquired_at": row.acquired_at,
                "expiry_date": row.expiry_date,
                "acquired_ord": acquired_ord,
                "expiry_ord": expiry_ord,
                "resolved_ord": resolved_ord,
                "shelf_life_days": shelf_life,
                "log_shelf_life": math.log1p(min(shelf_life, MAX_SHELF_LIFE_DAYS))
                if not math.isnan(shelf_life)
                else math.nan,
                "has_expiry": int(row.expiry_date is not None),
                "quantity": float(row.quantity),
                "qty_ratio": 1.0,  # filled below, once the whole frame exists
                "month": month,
                "month_sin": math.sin(2 * math.pi * month / 12),
                "month_cos": math.cos(2 * math.pi * month / 12),
                "same_category_active": 0,
                "product_consume_rate": math.nan,
                "product_prior_n": 0,
                "category_consume_rate": math.nan,
                "category_prior_n": 0,
                "days_to_expiry": (expiry_ord - as_of_ord)
                if row.expiry_date is not None
                else math.nan,
                "days_in_house": float(as_of_ord - acquired_ord),
                "is_opened": int(row.opened_at is not None),
                "fraction_remaining": float(row.fraction_remaining),
            }
        )

    frame = pd.DataFrame.from_records(records).astype(_DTYPES)

    observed = frame[(frame["event_consumed"] + frame["event_wasted"]) > 0]
    household_rate = float(observed["event_consumed"].mean()) if len(observed) else 0.5

    product_rate, product_n = _expanding_consume_rate(frame, "product_id")
    category_rate, category_n = _expanding_consume_rate(frame, "category_code")
    frame["product_consume_rate"] = _shrink(product_rate, product_n, household_rate)
    frame["product_prior_n"] = product_n
    frame["category_consume_rate"] = _shrink(category_rate, category_n, household_rate)
    frame["category_prior_n"] = category_n
    frame["same_category_active"] = _active_at_acquisition(frame)
    frame["qty_ratio"] = _qty_ratio(frame)

    return frame


def design_defaults(frame: pd.DataFrame) -> dict[str, float]:
    """The fill values the design matrix uses for missing covariates.

    Derived from the **training** frame and then carried on the fitted model, so scoring a
    new item a year later fills its blanks the same way the fit did. Recomputing them at
    score time would quietly shift the model under its own coefficients.
    """
    observed = frame[(frame["event_consumed"] + frame["event_wasted"]) > 0] if len(frame) else frame
    return {
        "consume_rate": float(observed["event_consumed"].mean()) if len(observed) else 0.5,
        "qty_ratio": 1.0,
        "log_shelf_life": math.log1p(MAX_SHELF_LIFE_DAYS),
    }


def design_matrix(frame: pd.DataFrame, defaults: dict[str, float]) -> pd.DataFrame:
    """`DESIGN_COLUMNS` as floats, with every blank filled and nothing left to surprise a fit.

    Only acquisition-time covariates, by construction: see the leakage note in the module
    docstring for why `days_to_expiry` and friends are not here.
    """
    if len(frame) == 0:
        return pd.DataFrame({name: pd.Series(dtype="float64") for name in DESIGN_COLUMNS})

    rate_default = defaults["consume_rate"]
    matrix = pd.DataFrame(index=frame.index)
    matrix["log_shelf_life"] = frame["log_shelf_life"].fillna(defaults["log_shelf_life"])
    matrix["has_expiry"] = frame["has_expiry"].astype(float)
    matrix["is_perishable"] = frame["is_perishable"].astype(float)
    matrix["is_staple"] = frame["is_staple"].astype(float)
    # Clipped: a one-off bulk buy at 12x the usual quantity is a real event and a terrible
    # covariate, and Cox has no way to shrug it off on its own.
    matrix["qty_ratio"] = frame["qty_ratio"].fillna(defaults["qty_ratio"]).clip(0.0, 5.0)
    # The cold-start chain, expressed as a fill order: this product -> its category -> the
    # household. Same shape as v0's tier chain, for the same reason.
    matrix["product_consume_rate"] = (
        frame["product_consume_rate"].fillna(frame["category_consume_rate"]).fillna(rate_default)
    )
    matrix["category_consume_rate"] = frame["category_consume_rate"].fillna(rate_default)
    matrix["month_sin"] = frame["month_sin"]
    matrix["month_cos"] = frame["month_cos"]
    matrix["same_category_active"] = frame["same_category_active"].astype(float)
    return matrix[list(DESIGN_COLUMNS)].astype("float64")


def drop_degenerate(matrix: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Remove constant columns before fitting, and say which went.

    A household that never opens anything, or one whose every item is perishable, produces a
    zero-variance column. lifelines raises a convergence error on those rather than ignoring
    them, so a perfectly reasonable small dataset would otherwise make the whole fit fail.
    """
    if len(matrix) == 0:
        return matrix, []
    keep = [name for name in matrix.columns if matrix[name].nunique(dropna=False) > 1]
    dropped = [name for name in matrix.columns if name not in keep]
    return matrix[keep], dropped
