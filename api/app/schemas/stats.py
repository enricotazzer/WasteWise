"""The reporting contract, built around one primitive: no euro figure travels without the
fraction of its own rows that actually carried a price.

Prices in this system are optional and typed by hand, so every total is computed over a
subset. A bare `eur_wasted: 46.67` reads as a measurement of the household; it is really a
measurement of the priced 58% of it. `MoneyFigure` makes that impossible to forget, because
the number and its denominator are the same object.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.models.enums import EntryBasis, SavingsDomain

# Strongest first. When a slice somehow spans two money bases, the strongest one wins and
# the weaker rows are left out of the sum rather than blended in: a figure that adds a price
# the user typed to one a model guessed is not a measurement of anything. The excluded rows
# stay in `entries`, so the refusal shows up as coverage falling — visible, not silent.
_BASIS_STRENGTH = {
    EntryBasis.MEASURED: 3,
    EntryBasis.INFERRED_PRICE: 2,
    EntryBasis.MODELLED: 1,
    EntryBasis.UNPRICED: 0,
}

# (basis, entries, priced_entries, eur) — one aggregated row per basis in a slice.
BasisRow = tuple[EntryBasis, int, int, Decimal | None]


class MoneyFigure(BaseModel):
    """A euro total that carries its own provenance."""

    # None when nothing in the slice carried a price. Never 0.00 for that case: zero is a
    # real answer ("nothing was wasted") and must stay distinguishable from "we don't know".
    eur: Decimal | None
    entries: int
    priced_entries: int
    coverage: float | None  # priced_entries / entries; None only when there are no entries
    basis: EntryBasis

    @classmethod
    def from_rows(cls, rows: Iterable[BasisRow]) -> "MoneyFigure":
        collected = list(rows)
        entries = sum(row[1] for row in collected)
        priced = [row for row in collected if row[2] > 0]

        if not priced:
            return cls(
                eur=None,
                entries=entries,
                priced_entries=0,
                coverage=0.0 if entries else None,
                basis=EntryBasis.UNPRICED,
            )

        basis = max((row[0] for row in priced), key=lambda b: _BASIS_STRENGTH[b])
        counted = [row for row in priced if row[0] is basis]
        priced_entries = sum(row[2] for row in counted)
        total = sum((row[3] or Decimal("0")) for row in counted)
        return cls(
            eur=total.quantize(Decimal("0.01")),
            entries=entries,
            priced_entries=priced_entries,
            coverage=(priced_entries / entries) if entries else None,
            basis=basis,
        )

    @classmethod
    def empty(cls) -> "MoneyFigure":
        return cls(eur=None, entries=0, priced_entries=0, coverage=None, basis=EntryBasis.UNPRICED)


class MassFigure(BaseModel):
    """Kilograms, with their own coverage — which is *not* the price coverage.

    Only kg/g items have a mass at all, so a household that buys mostly packs and pieces can
    have 90% price coverage and 20% mass coverage over the identical rows.
    """

    kg: Decimal | None
    entries: int
    measured_entries: int
    coverage: float | None

    @classmethod
    def build(cls, *, entries: int, measured_entries: int, kg: Decimal | None) -> "MassFigure":
        return cls(
            kg=kg.quantize(Decimal("0.001")) if kg is not None and measured_entries else None,
            entries=entries,
            measured_entries=measured_entries,
            coverage=(measured_entries / entries) if entries else None,
        )

    @classmethod
    def empty(cls) -> "MassFigure":
        return cls(kg=None, entries=0, measured_entries=0, coverage=None)


class DomainTotal(BaseModel):
    """One domain's books. Food fills this in today; utilities and subscriptions will use
    the same shape, which is the reason the ledger is domain-keyed at all."""

    domain: SavingsDomain
    spend: MoneyFigure
    used: MoneyFigure
    waste: MoneyFigure
    # Always empty for FOOD, by design — see the R6 note in services/savings/rules.py.
    avoided: MoneyFigure
    # SPEND minus its partitions: value bought and never resolved to an outcome.
    #
    # This is where the save rate's bias becomes visible. The save rate is defined over
    # *resolved* items only, so anything sitting in here is excluded from it — and the
    # unresolved items are overwhelmingly the ones that were forgotten at the back of the
    # fridge, i.e. almost certainly waste. A high save rate next to a large unaccounted
    # figure is not a good result, it is an unmeasured one.
    #
    # Derived rather than counted: its entry counts are the SPEND base it was computed from,
    # because there is no such thing as an "unaccounted row".
    unaccounted: MoneyFigure
    waste_kg: MassFigure


class StatsOverview(BaseModel):
    generated_at: datetime
    # Provenance travels with every response. A number computed over synthetic data is not a
    # measurement, and the caller must never have to guess which kind it got.
    demo_included: bool
    demo_entries: int  # demo rows in the ledger, reported whether or not they were included

    # Inventory state. These read `inventory_items` and count *items*, food or not — they are
    # facts about the kitchen, not about money, which is why a non-food item moves
    # `wasted_total` but nothing below it.
    active_items: int
    expiring_within_3d: int
    expired_still_active: int
    resolved_total: int
    consumed_total: int
    wasted_total: int

    # THE headline. Counted over recorded food outcomes, so it has no coverage caveat: an
    # outcome either was logged or wasn't, and no price is needed to know which. An item
    # half eaten and half binned contributes one of each, which is more truthful than
    # forcing it into a single label.
    save_rate: float | None
    # The euro-weighted version, over priced entries only — a different question ("what
    # share of the money worked out") answered over a partial sample, hence its own coverage.
    save_rate_eur: float | None
    save_rate_eur_coverage: float | None

    wasted: MoneyFigure
    used: MoneyFigure
    spend: MoneyFigure
    wasted_kg: MassFigure

    domains: list[DomainTotal]


class TimeseriesPoint(BaseModel):
    """One contiguous bucket. Present even when nothing happened in it."""

    bucket_start: date
    waste_eur: Decimal | None
    used_eur: Decimal | None
    spend_eur: Decimal | None
    waste_kg: Decimal | None
    waste_entries: int
    used_entries: int
    spend_entries: int
    # Coverage for this bucket's waste figure specifically. A week where nothing was priced
    # gets waste_eur = None, which a chart draws as a gap — correctly, because the amount is
    # unknown rather than zero.
    waste_priced_entries: int


class CategoryPoint(BaseModel):
    """Sparse by design: emitting every category for every bucket would be mostly zeros."""

    bucket_start: date
    category_code: str
    waste_eur: Decimal | None
    waste_kg: Decimal | None
    waste_entries: int


class TimeseriesResponse(BaseModel):
    bucket: Literal["week", "month"]
    start: date
    end: date
    domain: SavingsDomain | None
    demo_included: bool
    demo_entries: int
    # Contiguous and zero-filled. A missing bucket makes a line chart interpolate straight
    # across the gap, which reads as "nothing was wasted" when it means "nothing was logged".
    points: list[TimeseriesPoint]
    by_category: list[CategoryPoint]
    # Window-level, so the chart can be captioned once instead of per point.
    price_coverage: float | None
    mass_coverage: float | None


class CategoryBucket(BaseModel):
    category_code: str
    bucket_start: date
    spend: MoneyFigure
    waste: MoneyFigure
    # waste_eur / spend_eur for this category and bucket: the share of what was bought that
    # bought nothing.
    waste_share: float | None
    # None — never 0.0 — when there is no prior bucket to compare against. A zero would read
    # as "unchanged", which is a different and much stronger claim than "unknown".
    spend_delta_pct: float | None
    waste_delta_pct: float | None


class ByCategoryResponse(BaseModel):
    bucket: Literal["week", "month"]
    start: date
    end: date
    domain: SavingsDomain | None
    demo_included: bool
    demo_entries: int
    rows: list[CategoryBucket]


class PriceAnomalyOut(BaseModel):
    """One price out of line with its own history at that shop.

    Carries `observations` for the same reason every euro figure carries its coverage: "40%
    above normal" from 6 prices and from 60 are different claims, and the endpoint refuses
    to make either one below `min_observations`.
    """

    product_id: int
    product_name: str
    store: str | None
    unit: str
    observed_at: date
    unit_price_eur: Decimal
    median_eur: Decimal
    sigma_eur: Decimal
    deviation_sigmas: float
    delta_pct: float
    # "below" is reported too — a price well under the trailing median is a deal, and only
    # reporting bad news would be a strange thing for a savings module to do.
    direction: Literal["above", "below"]
    observations: int


class BatchAdviceOut(BaseModel):
    """A bulk-buy suggestion that passed the waste gate.

    `p_consume_large` and `assumed_rate_divisor` are exposed, not hidden behind the verdict:
    the recommendation rests on a modelled probability and an explicit assumption about how
    a bigger pack is consumed, and both belong on screen next to the euros.
    """

    product_id: int
    product_name: str
    store: str | None
    unit: str
    small_size: float
    small_unit_price_eur: Decimal
    large_size: float
    large_unit_price_eur: Decimal
    saving_pct: float
    p_consume_large: float
    threshold: float
    assumed_rate_divisor: float
    observations: int


class PriceIntelligenceResponse(BaseModel):
    generated_at: date
    demo_included: bool
    demo_observations: int
    observations: int
    # How many (product, store) pairs exist, and how many were too thin to say anything
    # about. An empty anomaly list means something different at 0 examined than at 40.
    pairs_examined: int
    pairs_below_threshold: int
    min_observations: int
    anomalies: list[PriceAnomalyOut]
    advice: list[BatchAdviceOut]
    notes: list[str]
