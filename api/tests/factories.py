"""Payload and row builders shared across test modules."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import ConsumptionEvent, InventoryItem, Product, User
from app.models.enums import EventSource, EventType, ItemStatus, Unit


def off_response(
    barcode: str = "8076809513692",
    *,
    name: str | None = "Napoletana",
    brand: str = "Barilla",
    quantity: str = "400g",
    quantity_value: float | None = 400,
    quantity_unit: str = "g",
    categories: list[str] | None = None,
    found: bool = True,
) -> dict:
    """An Open Food Facts v2 payload, shaped like the real one.

    The field names and the `status: 1` envelope were verified against the live API on
    2026-08-05 with barcode 8076809513692 (Barilla Napoletana). If OFF ever changes shape,
    this builder is what should be corrected first — everything else mocks against it.
    """
    if not found:
        return {"code": barcode, "status": 0, "status_verbose": "product not found"}
    return {
        "code": barcode,
        "status": 1,
        "status_verbose": "product found",
        "product": {
            "code": barcode,
            "product_name": name,
            "product_name_it": name,
            "brands": brand,
            "quantity": quantity,
            "product_quantity": quantity_value,
            "product_quantity_unit": quantity_unit,
            # Real ordering: broad -> specific, which is what makes reverse matching matter.
            "categories_tags": categories
            if categories is not None
            else ["en:plant-based-foods", "en:sauces", "en:tomato-sauces"],
        },
    }


def item(
    db: Session,
    user: User,
    product: Product,
    *,
    quantity: str | Decimal = "1",
    unit_price_eur: str | Decimal | None = None,
    unit: Unit | None = None,
    acquired_at: date | None = None,
    expiry_date: date | None = None,
    status: ItemStatus = ItemStatus.ACTIVE,
    resolved_at: datetime | None = None,
    fraction_remaining: float = 1.0,
    is_demo: bool = False,
) -> InventoryItem:
    """An inventory row written straight to the database.

    Deliberately bypasses the API, because half of what the ledger has to cope with was
    never created through it: the demo seeder, the repurchase job and eight weeks of history
    all write rows directly.
    """
    row = InventoryItem(
        user_id=user.id,
        product_id=product.id,
        quantity=Decimal(str(quantity)),
        unit=unit or product.default_unit,
        unit_price_eur=None if unit_price_eur is None else Decimal(str(unit_price_eur)),
        acquired_at=acquired_at or date.today() - timedelta(days=7),
        expiry_date=expiry_date,
        status=status,
        resolved_at=resolved_at,
        fraction_remaining=fraction_remaining,
        is_demo=is_demo,
    )
    db.add(row)
    db.flush()
    return row


def event(
    db: Session,
    item: InventoryItem,
    event_type: EventType,
    *,
    fraction_used: float = 1.0,
    occurred_at: datetime | None = None,
    source: EventSource = EventSource.MANUAL,
) -> ConsumptionEvent:
    row = ConsumptionEvent(
        inventory_item_id=item.id,
        event_type=event_type,
        fraction_used=fraction_used,
        occurred_at=occurred_at or datetime.now(UTC),
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def resolved_history(
    db: Session,
    user: User,
    products: Sequence[Product],
    *,
    consumed: int,
    wasted: int,
    span_days: int = 90,
    end: date | None = None,
    is_demo: bool = False,
) -> list[InventoryItem]:
    """A stretch of finished history, spread evenly across `span_days` and ending at `end`.

    The two outcomes are interleaved rather than blocked. A fixture that put all the waste in
    the final fortnight would make the time-split and gate tests pass for the wrong reason —
    the training half would contain no waste at all, and nobody would notice until real data
    arrived.

    Durations are deterministic and outcome-shaped: consumed items resolve around the middle
    of their shelf life, wasted ones a day after expiry. That is the pattern the risk model
    is supposed to find, so a test asserting it found *something* is asserting something.
    """
    end = end or date.today()
    total = consumed + wasted
    if total == 0:
        return []

    # Positions of the waste events, evenly spaced through the timeline.
    waste_at = {round(i * total / wasted) for i in range(wasted)} if wasted else set()
    rows: list[InventoryItem] = []

    for index in range(total):
        offset = round(span_days * (total - 1 - index) / max(total - 1, 1))
        acquired = end - timedelta(days=offset)
        product = products[index % len(products)]
        shelf = product.shelf_life_days or product.category.default_shelf_life_days or 7
        expiry = acquired + timedelta(days=shelf)

        is_waste = index in waste_at
        resolved_on = (
            expiry + timedelta(days=1)
            if is_waste
            else acquired + timedelta(days=max(1, shelf // 2))
        )
        resolved_at = datetime.combine(min(resolved_on, end), time(hour=12), tzinfo=UTC)

        row = item(
            db,
            user,
            product,
            acquired_at=acquired,
            expiry_date=expiry,
            status=ItemStatus.WASTED if is_waste else ItemStatus.CONSUMED,
            resolved_at=resolved_at,
            fraction_remaining=0.0,
            is_demo=is_demo,
        )
        event(
            db,
            row,
            EventType.WASTED if is_waste else EventType.CONSUMED,
            occurred_at=resolved_at,
        )
        rows.append(row)

    db.flush()
    return rows
