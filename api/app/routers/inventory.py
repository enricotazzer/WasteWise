from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import CurrentUser, DbSession
from app.models import ConsumptionEvent, InventoryItem, PriceObservation, Product
from app.models.enums import EventSource, EventType, ExpirySource, ItemStatus
from app.schemas.inventory import (
    ConsumptionEventCreate,
    ConsumptionEventOut,
    InventoryItemCreate,
    InventoryItemOut,
    InventoryStats,
    OpenItemIn,
    RecentProduct,
)
from app.services.shelf_life import estimate_expiry

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# Below this, an item is treated as used up rather than leaving a 0.02 sliver forever.
_EPSILON = 0.01


_to_out = InventoryItemOut.from_item


def _load_item(db: DbSession, user_id: int, item_id: int) -> InventoryItem:
    item = db.scalar(
        select(InventoryItem)
        .options(joinedload(InventoryItem.product).joinedload(Product.category))
        .where(InventoryItem.id == item_id, InventoryItem.user_id == user_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


@router.post("/items", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED)
def create_item(payload: InventoryItemCreate, db: DbSession, user: CurrentUser) -> InventoryItemOut:
    """Manual entry / quick-add. The barcode path in `routers/scan.py` writes to the same
    table with `barcode` set; loose produce, which has no code to scan, comes through here."""
    product = db.scalar(
        select(Product)
        .options(joinedload(Product.category))
        .where(Product.id == payload.product_id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    acquired = payload.acquired_at or date.today()
    expiry, source = estimate_expiry(
        product=product, acquired_at=acquired, label_expiry=payload.expiry_date
    )

    item = InventoryItem(
        user_id=user.id,
        product_id=product.id,
        quantity=payload.quantity,
        unit=payload.unit or product.default_unit,
        unit_price_eur=payload.unit_price_eur,
        acquired_at=acquired,
        expiry_date=expiry,
        expiry_source=source,
        status=ItemStatus.ACTIVE,
        fraction_remaining=1.0,
        note=payload.note,
    )
    db.add(item)
    db.flush()

    # Prices are optional and hand-typed, so each one is worth keeping as an observation —
    # it's the only price signal in the system.
    if payload.unit_price_eur is not None:
        db.add(
            PriceObservation(
                user_id=user.id,
                product_id=product.id,
                store=payload.store,
                unit_price_eur=payload.unit_price_eur,
                unit=item.unit,
                observed_at=acquired,
                inventory_item_id=item.id,
            )
        )

    db.commit()
    db.refresh(item)
    return _to_out(item)


@router.get("/recent-products", response_model=list[RecentProduct])
def recent_products(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=12, le=50),
) -> list[RecentProduct]:
    """Most-bought products, most recent first — the quick-add list.

    Loose produce has no barcode and is the biggest waste category, so re-adding it has to
    be one tap. Ranking by frequency then recency puts the weekly shop at the top.
    """
    rows = db.execute(
        select(
            InventoryItem.product_id,
            func.count(InventoryItem.id).label("times"),
            func.max(InventoryItem.acquired_at).label("last"),
        )
        .where(InventoryItem.user_id == user.id)
        .group_by(InventoryItem.product_id)
        .order_by(func.count(InventoryItem.id).desc(), func.max(InventoryItem.acquired_at).desc())
        .limit(limit)
    ).all()
    if not rows:
        return []

    products = {
        p.id: p
        for p in db.scalars(
            select(Product)
            .options(joinedload(Product.category))
            .where(Product.id.in_([r.product_id for r in rows]))
        )
    }
    return [
        RecentProduct(
            product_id=r.product_id,
            product_name=products[r.product_id].name_it,
            category_code=products[r.product_id].category.code,
            default_unit=products[r.product_id].default_unit,
            times_bought=r.times,
            last_bought=r.last,
        )
        for r in rows
        if r.product_id in products
    ]


@router.get("/items", response_model=list[InventoryItemOut])
def list_items(
    db: DbSession,
    user: CurrentUser,
    active_only: bool = Query(default=True),
    limit: int = Query(default=500, le=2000),
) -> list[InventoryItemOut]:
    """Ordered the way the Kitchen screen needs it: soonest expiry first, undated last."""
    stmt = (
        select(InventoryItem)
        .options(joinedload(InventoryItem.product).joinedload(Product.category))
        .where(InventoryItem.user_id == user.id)
    )
    if active_only:
        stmt = stmt.where(InventoryItem.status.in_([ItemStatus.ACTIVE, ItemStatus.PARTIAL]))
    stmt = stmt.order_by(
        InventoryItem.expiry_date.is_(None),  # NULLs last
        InventoryItem.expiry_date.asc(),
        InventoryItem.acquired_at.asc(),
    ).limit(limit)
    return [_to_out(i) for i in db.scalars(stmt)]


@router.post(
    "/items/{item_id}/events",
    response_model=ConsumptionEventOut,
    status_code=status.HTTP_201_CREATED,
)
def log_event(
    item_id: int,
    payload: ConsumptionEventCreate,
    db: DbSession,
    user: CurrentUser,
) -> ConsumptionEvent:
    """Log what actually happened to an item. This is the label the survival model trains on.

    A PARTIAL event decrements `fraction_remaining` and keeps the item in play; once the
    remainder falls below epsilon the item resolves as CONSUMED. CONSUMED and WASTED
    resolve immediately regardless of fraction.
    """
    item = _load_item(db, user.id, item_id)
    if item.status in (ItemStatus.CONSUMED, ItemStatus.WASTED):
        raise HTTPException(status_code=409, detail=f"Item already resolved as {item.status.value}")

    occurred = payload.occurred_at or datetime.now(UTC)
    event = ConsumptionEvent(
        inventory_item_id=item.id,
        event_type=payload.event_type,
        fraction_used=payload.fraction_used,
        occurred_at=occurred,
        source=EventSource.MANUAL,
        reason=payload.reason,
        note=payload.note,
    )
    db.add(event)

    if payload.event_type in (EventType.CONSUMED, EventType.WASTED):
        item.fraction_remaining = 0.0
        item.status = (
            ItemStatus.CONSUMED if payload.event_type == EventType.CONSUMED else ItemStatus.WASTED
        )
        item.resolved_at = occurred
    else:  # PARTIAL
        item.fraction_remaining = max(0.0, item.fraction_remaining - payload.fraction_used)
        if item.fraction_remaining <= _EPSILON:
            item.fraction_remaining = 0.0
            item.status = ItemStatus.CONSUMED
            item.resolved_at = occurred
        else:
            item.status = ItemStatus.PARTIAL
            if item.opened_at is None:
                item.opened_at = occurred.date()

    db.commit()
    db.refresh(event)
    return event


@router.post("/items/{item_id}/open", response_model=InventoryItemOut)
def open_item(
    item_id: int, payload: OpenItemIn, db: DbSession, user: CurrentUser
) -> InventoryItemOut:
    """Mark an item opened. Opening usually shortens the real shelf life far more than the
    printed date suggests, so the expiry estimate is recomputed."""
    item = _load_item(db, user.id, item_id)
    if item.status in (ItemStatus.CONSUMED, ItemStatus.WASTED):
        raise HTTPException(status_code=409, detail="Item already resolved")

    item.opened_at = payload.opened_at or date.today()
    # Only carry the printed date forward if that's where the current estimate came from —
    # re-feeding an inferred date as if it were a label would launder a guess into a fact.
    label = item.expiry_date if item.expiry_source == ExpirySource.LABEL else None
    expiry, source = estimate_expiry(
        product=item.product,
        acquired_at=item.acquired_at,
        opened_at=item.opened_at,
        label_expiry=label,
    )
    if expiry is not None:
        item.expiry_date, item.expiry_source = expiry, source

    db.commit()
    db.refresh(item)
    return _to_out(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession, user: CurrentUser) -> None:
    """For mistyped entries only. Resolving an item is a *logged event*, not a delete —
    deleting a consumed/wasted item destroys a training label."""
    item = _load_item(db, user.id, item_id)
    db.delete(item)
    db.commit()


@router.get("/stats", response_model=InventoryStats)
def stats(db: DbSession, user: CurrentUser) -> InventoryStats:
    """Phase 0 counters. Non-food categories are excluded from the money figures —
    wasting dish soap is not food waste."""
    today = date.today()

    items = list(
        db.scalars(
            select(InventoryItem)
            .options(joinedload(InventoryItem.product).joinedload(Product.category))
            .where(InventoryItem.user_id == user.id)
        )
    )

    active = [i for i in items if i.status in (ItemStatus.ACTIVE, ItemStatus.PARTIAL)]
    expiring = [
        i for i in active if i.expiry_date is not None and 0 <= (i.expiry_date - today).days <= 3
    ]
    expired = [i for i in active if i.expiry_date is not None and i.expiry_date < today]

    consumed = [i for i in items if i.status == ItemStatus.CONSUMED]
    wasted = [i for i in items if i.status == ItemStatus.WASTED]

    def value(item: InventoryItem) -> Decimal:
        if item.unit_price_eur is None or not item.product.category.is_food:
            return Decimal("0")
        return (item.unit_price_eur * item.quantity).quantize(Decimal("0.01"))

    # Quantize the totals, not just the parts: an empty sum would otherwise serialize as
    # "0" while a non-empty one gives "0.00", which makes the field awkward to format.
    cents = Decimal("0.01")
    eur_wasted = sum((value(i) for i in wasted), Decimal("0")).quantize(cents)
    eur_consumed = sum((value(i) for i in consumed), Decimal("0")).quantize(cents)

    food_consumed = len([i for i in consumed if i.product.category.is_food])
    food_wasted = len([i for i in wasted if i.product.category.is_food])
    denom = food_consumed + food_wasted
    save_rate = (food_consumed / denom) if denom else None

    # What fraction of the resolved food actually carried a price. Without this the euro
    # totals read as complete when they're computed over a partial sample.
    resolved_food = [i for i in consumed + wasted if i.product.category.is_food]
    priced = len([i for i in resolved_food if i.unit_price_eur is not None])
    price_coverage = (priced / len(resolved_food)) if resolved_food else None

    return InventoryStats(
        active_items=len(active),
        expiring_within_3d=len(expiring),
        expired_still_active=len(expired),
        resolved_total=len(consumed) + len(wasted),
        consumed_total=len(consumed),
        wasted_total=len(wasted),
        eur_wasted=eur_wasted,
        eur_consumed=eur_consumed,
        save_rate=save_rate,
        price_coverage=price_coverage,
    )
