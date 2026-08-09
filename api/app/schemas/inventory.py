from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EventSource, EventType, ExpirySource, ItemStatus, Unit


class InventoryItemCreate(BaseModel):
    product_id: int
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: Unit | None = None  # defaults to the product's unit
    # Optional by design: prices are typed by hand, so most items won't have one and every
    # euro figure reports its coverage instead of pretending to be complete.
    unit_price_eur: Decimal | None = Field(default=None, ge=0)
    store: str | None = None
    acquired_at: date | None = None  # defaults to today
    # If the package has a printed date, pass it — it beats every inferred estimate.
    expiry_date: date | None = None
    note: str | None = None


class InventoryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_name: str
    category_code: str
    barcode: str | None = None
    quantity: Decimal
    unit: Unit
    unit_price_eur: Decimal | None
    acquired_at: date
    expiry_date: date | None
    expiry_source: ExpirySource | None
    opened_at: date | None
    status: ItemStatus
    fraction_remaining: float
    days_to_expiry: int | None
    note: str | None

    @classmethod
    def from_item(cls, item: Any) -> "InventoryItemOut":
        """Single construction point for every route that returns an item.

        product_name/category_code/days_to_expiry are flattened from relationships and a
        property, so `from_attributes` alone can't do it. Building this by hand in more than
        one router is how a field silently goes missing from one endpoint.
        """
        return cls(
            id=item.id,
            product_id=item.product_id,
            product_name=item.product.name_it,
            category_code=item.product.category.code,
            barcode=item.barcode,
            quantity=item.quantity,
            unit=item.unit,
            unit_price_eur=item.unit_price_eur,
            acquired_at=item.acquired_at,
            expiry_date=item.expiry_date,
            expiry_source=item.expiry_source,
            opened_at=item.opened_at,
            status=item.status,
            fraction_remaining=item.fraction_remaining,
            days_to_expiry=item.days_to_expiry,
            note=item.note,
        )


class ConsumptionEventCreate(BaseModel):
    event_type: EventType
    fraction_used: float = Field(default=1.0, gt=0, le=1)
    occurred_at: datetime | None = None  # defaults to now
    reason: str | None = None
    note: str | None = None


class ConsumptionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inventory_item_id: int
    event_type: EventType
    fraction_used: float
    occurred_at: datetime
    source: EventSource
    reason: str | None
    note: str | None


class OpenItemIn(BaseModel):
    opened_at: date | None = None


class RecentProduct(BaseModel):
    """One row of the quick-add list — the path for produce, which has no barcode."""

    product_id: int
    product_name: str
    category_code: str
    default_unit: Unit
    times_bought: int
    last_bought: date


class InventoryStats(BaseModel):
    """Baseline counters. Superseded by `schemas/stats.StatsOverview`, which attaches a
    coverage figure to each euro total instead of one shared caveat at the bottom.

    Retained unchanged apart from `demo_included` because the frontend still reads it. The
    euro fields cannot express "no price was typed" — they collapse it to 0.00 — which is the
    exact flattening `MoneyFigure` exists to prevent.
    """

    active_items: int
    expiring_within_3d: int
    expired_still_active: int
    resolved_total: int
    consumed_total: int
    wasted_total: int
    eur_wasted: Decimal
    eur_consumed: Decimal
    save_rate: float | None  # consumed / (consumed + wasted); None until data exists
    # Share of resolved food outcomes that carried a price. Every euro figure above is
    # computed over this fraction only — reporting the total without it would overstate
    # completeness, since prices are optional and typed by hand.
    price_coverage: float | None
    # Whether synthetic rows were counted. Defaults to False at the endpoint, which is the
    # fix for these numbers previously being computed over demo data with no indication.
    demo_included: bool = False
