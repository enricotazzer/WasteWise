"""v0 — the heuristic that ships on day one, and the yardstick v1 has to beat.

The whole model is one line:

    P(consumed before expiry) = coverage * (1 - exp(-rate * days_to_expiry))

`rate` is the household's per-day consumption hazard for this product, `coverage` is the
share of that product that ever gets eaten at all rather than binned, and `days_to_expiry`
is how long there is left. That is exactly the brief's "days-to-expiry × category
perishability × historical consumption rate", written so the three factors can be read off
the output separately instead of being multiplied into an unexplainable number.

`coverage` is doing real work: without it the exponential tends to 1 as the horizon grows,
which would say a tin of chickpeas is certain to be eaten eventually. It is the cure-fraction
that the cause-specific hazard in `features.py` deliberately leaves out.

The exponential is memoryless, which is a simplification and also the only reason v0 can
answer "what about the next 3 days?" for an item that has already been in the fridge for
five without any per-item state. v1's Cox fit removes exactly this assumption, and the
category tier below already does — Kaplan–Meier carries the real shape.

Cold start: the tier chain
--------------------------------------------------------------------------------
    item history  ->  product history  ->  category Kaplan–Meier  ->  seeded shelf-life prior

Every score reports the tier it came from, and the tier lands in `RiskScore.features`. A
score you cannot explain is a score you cannot debug, and on day one *every* score comes from
the last tier — which is a fact the UI should be able to say out loud rather than hide.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter

from app.config import settings
from app.models.enums import RiskBand
from app.services.risk.features import MAX_SHELF_LIFE_DAYS

# Tier names. Strings rather than an enum: they are written into a JSONB blob and read by a
# human debugging one item, and an enum here would buy nothing but an import.
TIER_ITEM: Final = "item"
TIER_PRODUCT: Final = "product"
TIER_CATEGORY_KM: Final = "category_km"
TIER_SHELF_LIFE_PRIOR: Final = "shelf_life_prior"
TIER_HOUSEHOLD: Final = "household"

# One earlier purchase is an anecdote. Below these counts the tier is skipped rather than
# fitted to noise — the point of the chain is to degrade, not to pretend.
_MIN_PRODUCT_CONSUMED: Final = 2
_MIN_CATEGORY_OBSERVED: Final = 5

# The seeded prior, used only when the household has no history at all. A perishable item is
# assumed to be eaten about halfway through its printed shelf life, and roughly four out of
# five perishables make it. These are priors, not measurements; the moment any real outcome
# exists for a product or its category the tiers above replace them.
_PRIOR_CONSUMED_BY_FRACTION: Final = 0.5
_PRIOR_COVERAGE_PERISHABLE: Final = 0.80
_PRIOR_COVERAGE_STABLE: Final = 0.97
# For an item with no expiry date at all there is no shelf life to halve.
_PRIOR_MEAN_DAYS_NO_EXPIRY: Final = 60.0

_EPS: Final = 1e-9


# --- The demo fence ---------------------------------------------------------------------
# Three sides, and this is the first: the version string itself. `seeds/demo.py` fabricates
# outcomes so the app isn't empty on day one, and a probability derived from them is not a
# measurement of anything. Stamping the version means the provenance is welded to every
# `risk_scores` row rather than living in a variable somewhere upstream — you cannot read a
# score out of the database and lose track of what made it.
#
# Nothing here is conditional on a build flag or an environment: once `make demo-clear` has
# run and real logging carries the fit, `trained_on_demo` is False and the suffix disappears
# on its own.


def stamp_version(base: str, *, trained_on_demo: bool, below_gate: bool = False) -> str:
    """`v1-cox` / `v1-cox-demo` / `v1-cox-demo-ungated`.

    `-ungated` marks a fit forced below `risk_v1_min_*`. It is a diagnostic, never evidence,
    and it travels with the score for the same reason `-demo` does.
    """
    return base + ("-demo" if trained_on_demo else "") + ("-ungated" if below_gate else "")


def risk_band(p: float | None, *, threshold: float | None = None) -> RiskBand:
    """HIGH is exactly the at-risk flag every precision/recall figure is reported at.

    Derived from `risk_at_risk_threshold` rather than hard-coded, so the band the user sees
    and the operating point the model is scored at cannot drift apart. MEDIUM is the upper
    half of what's left, which is a presentation choice and nothing more.

    No expiry date means the question "will it be eaten in time?" has no deadline, so there
    is nothing to be at risk of. Non-perishables land here.
    """
    if p is None:
        return RiskBand.LOW
    cut = settings.risk_at_risk_threshold if threshold is None else threshold
    if p < cut:
        return RiskBand.HIGH
    if p < (cut + 1.0) / 2.0:
        return RiskBand.MEDIUM
    return RiskBand.LOW


@dataclass(frozen=True, slots=True)
class BaselineScore:
    """One item's v0 score, with the reasoning attached rather than summarised away."""

    p_consume_before_expiry: float | None
    p_consume_3d: float
    p_consume_7d: float
    band: RiskBand
    # Which tier produced `rate`. The headline: it is the one that says how much of this
    # number is the household's own history and how much is a seeded guess.
    tier: str
    coverage_tier: str
    rate_per_day: float
    coverage: float
    horizon_days: float | None
    # Observations the rate tier rested on. `tier=product, n=2` and `tier=product, n=40` are
    # very different claims wearing the same word.
    tier_n: int

    def as_features(self) -> dict[str, Any]:
        """The snapshot written to `RiskScore.features`."""
        return {
            "model": "v0",
            "tier": self.tier,
            "tier_n": self.tier_n,
            "coverage_tier": self.coverage_tier,
            "rate_per_day": round(self.rate_per_day, 6),
            "coverage": round(self.coverage, 4),
            "horizon_days": self.horizon_days,
        }


@dataclass(frozen=True, slots=True)
class _Stats:
    """What one product or category has actually done in this kitchen."""

    observed: int
    consumed: int
    mean_days_to_consume: float | None


@dataclass(frozen=True, slots=True)
class _Curve:
    """A category's Kaplan–Meier fit, carrying its own sample size and support.

    Both are kept here rather than read back off the fitter: lifelines' internal attribute
    names are not a public contract, `tier_n` is reported to a human, and `support` decides
    where the curve stops being evidence and starts being an artifact.
    """

    fitter: KaplanMeierFitter
    observations: int
    support: float


class BaselineScorer:
    """Built once from history, then applied to many items.

    `fit` takes a frame and `score` takes a row of one, which is what keeps the evaluation
    honest: the time-based split hands `fit` the training rows only, so the tier tables can
    never contain an outcome from the test period. Live scoring hands it everything resolved.
    """

    def __init__(
        self,
        *,
        products: dict[int, _Stats],
        categories: dict[str, _Stats],
        km: dict[str, _Curve],
        household_rate: float | None,
        household_mean_days: float | None,
        threshold: float | None = None,
    ) -> None:
        self._products = products
        self._categories = categories
        self._km = km
        self._household_rate = household_rate
        self._household_mean_days = household_mean_days
        self._threshold = settings.risk_at_risk_threshold if threshold is None else threshold

    # --- construction ---------------------------------------------------------------

    @classmethod
    def fit(cls, history: pd.DataFrame, *, threshold: float | None = None) -> "BaselineScorer":
        """Summarise a frame into the tier tables. An empty frame is a valid input: it
        produces a scorer whose every answer comes from the seeded prior, which is precisely
        the day-one state and must not be an error path."""
        products: dict[int, _Stats] = {}
        categories: dict[str, _Stats] = {}
        km: dict[str, _Curve] = {}
        household_rate: float | None = None
        household_mean: float | None = None

        if len(history):
            observed = history[(history["event_consumed"] + history["event_wasted"]) > 0]
            if len(observed):
                household_rate = float(observed["event_consumed"].mean())
                eaten = observed[observed["event_consumed"] == 1]
                if len(eaten):
                    household_mean = float(eaten["duration_days"].mean())

                for product_id, group in observed.groupby("product_id"):
                    products[int(product_id)] = cls._stats(group)
                for code, group in observed.groupby("category_code"):
                    categories[str(code)] = cls._stats(group)

            # Kaplan–Meier runs over *all* rows of the category, censored ones included —
            # that is the entire reason to use KM rather than a mean over resolved items.
            # An item still sitting in the fridge on day 9 is evidence about day 9.
            for code, group in history.groupby("category_code"):
                if len(group) < _MIN_CATEGORY_OBSERVED or group["event_consumed"].sum() < 1:
                    continue
                durations = group["duration_days"].to_numpy(dtype=float)
                fitter = KaplanMeierFitter()
                fitter.fit(durations, event_observed=group["event_consumed"].to_numpy(dtype=int))
                km[str(code)] = _Curve(
                    fitter=fitter,
                    observations=int(len(group)),
                    support=float(durations.max()),
                )

        return cls(
            products=products,
            categories=categories,
            km=km,
            household_rate=household_rate,
            household_mean_days=household_mean,
            threshold=threshold,
        )

    @staticmethod
    def _stats(group: pd.DataFrame) -> _Stats:
        eaten = group[group["event_consumed"] == 1]
        return _Stats(
            observed=int(len(group)),
            consumed=int(len(eaten)),
            mean_days_to_consume=float(eaten["duration_days"].mean()) if len(eaten) else None,
        )

    # --- scoring --------------------------------------------------------------------

    def score(
        self,
        row: Mapping[str, Any],
        *,
        horizon_days: float | None = None,
        elapsed_days: float | None = None,
    ) -> BaselineScore:
        """Score one frame row.

        `horizon_days` / `elapsed_days` default to the row's live values (days left before
        expiry, days already in the house). The evaluation passes `shelf_life_days` and `0`
        instead, which asks the harder question the detector is really judged on: *at the
        moment this was carried through the door, would we have flagged it?*
        """
        elapsed = float(elapsed_days if elapsed_days is not None else row["days_in_house"])
        elapsed = max(elapsed, 0.0)

        raw_horizon = horizon_days if horizon_days is not None else row["days_to_expiry"]
        unbounded = raw_horizon is None or _isnan(raw_horizon)
        horizon = None if unbounded else max(float(raw_horizon), 0.0)

        coverage, coverage_tier = self._coverage(row)
        rate, tier, tier_n = self._rate(row, elapsed=elapsed)

        if tier == TIER_CATEGORY_KM:
            curve = self._km[str(row["category_code"])]
            p3 = coverage * _km_probability(curve, elapsed, 3.0)
            p7 = coverage * _km_probability(curve, elapsed, 7.0)
            p_expiry = (
                None if horizon is None else coverage * _km_probability(curve, elapsed, horizon)
            )
            # Report the tier's 7-day average hazard so every tier hands the evaluation a
            # comparable per-day risk score, whatever shape produced it.
            rate = _implied_rate(p7, coverage, 7.0)
        else:
            p3 = coverage * (1.0 - math.exp(-rate * 3.0))
            p7 = coverage * (1.0 - math.exp(-rate * 7.0))
            p_expiry = None if horizon is None else coverage * (1.0 - math.exp(-rate * horizon))

        p_before_expiry = None if p_expiry is None else _clamp(p_expiry)
        return BaselineScore(
            p_consume_before_expiry=p_before_expiry,
            p_consume_3d=_clamp(p3),
            p_consume_7d=_clamp(p7),
            band=risk_band(p_before_expiry, threshold=self._threshold),
            tier=tier,
            coverage_tier=coverage_tier,
            rate_per_day=rate,
            coverage=coverage,
            horizon_days=horizon,
            tier_n=tier_n,
        )

    def score_frame(self, frame: pd.DataFrame, *, at_purchase: bool = False) -> list[BaselineScore]:
        """Score every row. `at_purchase=True` is the evaluation view — see `score`."""
        rows = frame.to_dict("records")
        if not at_purchase:
            return [self.score(row) for row in rows]
        return [
            self.score(row, horizon_days=row["shelf_life_days"], elapsed_days=0.0) for row in rows
        ]

    # --- the tier chain -------------------------------------------------------------

    def _rate(self, row: Mapping[str, Any], *, elapsed: float) -> tuple[float, str, int]:
        """Per-day consumption hazard, from the most specific evidence available."""
        # 1. The item itself. It has been opened and partly used, so we are not guessing how
        #    fast this household eats this thing — we are watching it happen.
        remaining = float(row.get("fraction_remaining", 1.0))
        if 0.0 < remaining < 1.0 and elapsed >= 1.0:
            per_day = (1.0 - remaining) / elapsed
            if per_day > 0:
                return per_day / remaining, TIER_ITEM, 1

        # 2. This product's own history in this kitchen.
        stats = self._products.get(int(row["product_id"]))
        if (
            stats is not None
            and stats.consumed >= _MIN_PRODUCT_CONSUMED
            and stats.mean_days_to_consume
        ):
            return 1.0 / stats.mean_days_to_consume, TIER_PRODUCT, stats.observed

        # 3. The category's empirical survival curve. Handled by the caller, which needs the
        #    fitter itself rather than a summary rate.
        code = str(row["category_code"])
        if code in self._km:
            # nan is a placeholder: `score` reads the curve itself and back-fills the rate
            # with the hazard it implies. There is no single rate for a KM tier by design.
            return math.nan, TIER_CATEGORY_KM, self._km[code].observations

        # 4. Nothing at all: the seeded shelf-life prior.
        return self._prior_rate(row), TIER_SHELF_LIFE_PRIOR, 0

    def _prior_rate(self, row: Mapping[str, Any]) -> float:
        """Assume an item is typically eaten halfway through its printed shelf life.

        Falls back to the household's own average time-to-consumption when there is one but
        the product and category tiers were both too thin to use.
        """
        shelf = row.get("shelf_life_days")
        if shelf is not None and not _isnan(shelf) and float(shelf) > 0:
            mean_days = min(float(shelf), MAX_SHELF_LIFE_DAYS) * _PRIOR_CONSUMED_BY_FRACTION
        elif self._household_mean_days:
            mean_days = self._household_mean_days
        else:
            mean_days = _PRIOR_MEAN_DAYS_NO_EXPIRY
        return 1.0 / max(mean_days, 0.5)

    def _coverage(self, row: Mapping[str, Any]) -> tuple[float, str]:
        """The ceiling: what share of this product ever gets eaten rather than binned.

        Its own chain, shrunk toward the level above at each step, because a product with one
        recorded outcome must not read as a 0% or 100% coverage — that single number would
        otherwise decide the whole score.
        """
        household = self._household_rate
        product = self._products.get(int(row["product_id"]))
        category = self._categories.get(str(row["category_code"]))

        category_level = household
        if category is not None and category.observed:
            base = household if household is not None else category.consumed / category.observed
            category_level = _shrink(category.consumed, category.observed, base)

        if product is not None and product.observed:
            own = product.consumed / product.observed
            base = category_level if category_level is not None else own
            return _clamp(_shrink(product.consumed, product.observed, base)), TIER_PRODUCT
        if category is not None and category.observed and category_level is not None:
            return _clamp(category_level), TIER_CATEGORY_KM
        if household is not None:
            return _clamp(household), TIER_HOUSEHOLD

        return (
            _PRIOR_COVERAGE_PERISHABLE if bool(row["is_perishable"]) else _PRIOR_COVERAGE_STABLE
        ), TIER_SHELF_LIFE_PRIOR


def _shrink(successes: int, trials: int, toward: float, weight: float = 2.0) -> float:
    return (successes + weight * toward) / (trials + weight)


def _km_survival(curve: _Curve, t: float) -> float:
    """S(t), with an exponential tail past the last observation.

    Kaplan–Meier is *flat* beyond its support: eight weeks of logging has no opinion about
    day 500, and the curve expresses that by simply not moving. Reading that flatness as an
    answer is how a tin of chickpeas with sixteen months left gets flagged as about to be
    wasted — the first version of this function did exactly that, and it put 25 pantry items
    into the HIGH band.

    So past the support the curve is continued at the average hazard it observed,
    `-ln S(t_max) / t_max`. The average rather than the local hazard near `t_max`, which on
    a handful of observations is mostly noise. It is an extrapolation either way; this one is
    at least a statement about the household instead of an artifact of when logging started.
    """
    if t <= curve.support:
        return float(np.asarray(curve.fitter.predict(t)).reshape(-1)[0])
    tail = float(np.asarray(curve.fitter.predict(curve.support)).reshape(-1)[0])
    if tail <= _EPS:
        return 0.0
    rate = -math.log(tail) / max(curve.support, 1.0)
    return tail * math.exp(-rate * (t - curve.support))


def _km_probability(curve: _Curve, elapsed: float, horizon: float) -> float:
    """P(consumed in the next `horizon` days | still here after `elapsed`) = 1 - S(e+h)/S(e).

    Conditioning is the whole point: an item that has already survived nine days in the
    fridge is not the same bet as one bought this morning, and the unconditional curve would
    happily tell you it is.
    """
    if horizon <= 0:
        return 0.0
    start = _km_survival(curve, elapsed)
    if start <= _EPS:
        return 0.0
    return _clamp(1.0 - _km_survival(curve, elapsed + horizon) / start)


def _implied_rate(probability: float, coverage: float, horizon: float) -> float:
    """Back out the constant hazard that would produce this probability over this horizon."""
    share = probability / coverage if coverage > _EPS else 0.0
    return -math.log(max(1.0 - min(share, 1.0 - _EPS), _EPS)) / horizon


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _isnan(value: Any) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
