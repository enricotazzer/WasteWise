from datetime import date

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.deps import CurrentUser, DbSession
from app.models import InventoryItem, PriceObservation, Product, ProductBarcode
from app.models.enums import ItemStatus, MatchMethod
from app.schemas.inventory import InventoryItemOut
from app.schemas.scan import BarcodeBindIn, ScanAddIn, ScanResult, ScanStats
from app.services.barcode import bind_barcode, resolve_barcode
from app.services.shelf_life import estimate_expiry

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("/stats", response_model=ScanStats)
def scan_stats(db: DbSession, user: CurrentUser) -> ScanStats:
    """The Module 1 scorecard. Kept as an endpoint rather than a notebook cell so the number
    is visible while scanning a real shop, not only afterwards."""
    rows = list(
        db.execute(
            select(
                ProductBarcode.match_method,
                func.count(ProductBarcode.id),
                func.coalesce(func.sum(ProductBarcode.lookup_count), 0),
            ).group_by(ProductBarcode.match_method)
        )
    )

    by_method = {method.value: count for method, count, _ in rows}
    scans_by_method = {method.value: int(scans) for method, _, scans in rows}

    total = sum(by_method.values())
    unresolved = by_method.get(MatchMethod.UNRESOLVED.value, 0)
    total_scans = sum(scans_by_method.values())

    # Typing is charged **per naming event, not per scan**. Binding a barcode by hand costs
    # the user once; every later scan of it is free, which is the entire point of the
    # bind-once loop. Charging all of a bound barcode's scans would make the rate *fall* as
    # the cache warmed up — exactly backwards from what it's meant to show.
    #
    # An unresolved barcode is different: it prompted for a name and got nothing, so each of
    # those scans is a real miss and counts in full.
    typing_events = by_method.get(MatchMethod.MANUAL.value, 0) + scans_by_method.get(
        MatchMethod.UNRESOLVED.value, 0
    )
    free_scans = max(0, total_scans - typing_events)

    return ScanStats(
        total_barcodes=total,
        by_method=by_method,
        resolved=total - unresolved,
        unresolved=unresolved,
        total_scans=total_scans,
        zero_typing_rate=(free_scans / total_scans) if total_scans else None,
    )


@router.get("/{barcode}", response_model=ScanResult)
def scan(barcode: str, db: DbSession, user: CurrentUser) -> ScanResult:
    """Resolve a scanned barcode. Always answers 200: an unknown code is a normal outcome
    that routes the UI to manual naming, not an error."""
    result = resolve_barcode(db, barcode)
    if not result.barcode:
        raise HTTPException(status_code=422, detail="Barcode contains no digits")
    return ScanResult(**result.model_dump())


@router.post("/{barcode}/bind", response_model=ScanResult, status_code=status.HTTP_201_CREATED)
def bind(barcode: str, payload: BarcodeBindIn, db: DbSession, user: CurrentUser) -> ScanResult:
    """Tier 4: name an unknown barcode once. Permanent and authoritative — a later Open
    Food Facts change never overrides it."""
    try:
        result = bind_barcode(
            db,
            barcode,
            product_id=payload.product_id,
            new_product_name=payload.new_product_name,
            category_id=payload.category_id,
            shelf_life_days=payload.shelf_life_days,
            default_unit=payload.default_unit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScanResult(**result.model_dump())


@router.post("/add", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED)
def add_from_scan(payload: ScanAddIn, db: DbSession, user: CurrentUser) -> InventoryItemOut:
    """Commit a scanned item to inventory.

    Same table and same expiry chain as manual entry — the acquisition path is the only
    difference, and it's recorded in `barcode` so the two can be told apart later.
    """
    product = db.scalar(
        select(Product)
        .options(joinedload(Product.category))
        .where(Product.id == payload.product_id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    label_expiry: date | None = None
    if payload.expiry_date:
        try:
            label_expiry = date.fromisoformat(payload.expiry_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="expiry_date must be YYYY-MM-DD") from exc

    acquired = date.today()
    expiry, source = estimate_expiry(
        product=product, acquired_at=acquired, label_expiry=label_expiry
    )
    unit = payload.unit or product.default_unit

    item = InventoryItem(
        user_id=user.id,
        product_id=product.id,
        barcode=payload.barcode or None,
        quantity=payload.quantity,
        unit=unit,
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

    # A typed price is rare and valuable — it's the only price signal in the system now that
    # receipts are gone, so record it as an observation, not just on the item.
    if payload.unit_price_eur is not None:
        db.add(
            PriceObservation(
                user_id=user.id,
                product_id=product.id,
                store=payload.store,
                unit_price_eur=payload.unit_price_eur,
                unit=unit,
                observed_at=acquired,
                inventory_item_id=item.id,
            )
        )

    db.commit()
    db.refresh(item)
    return InventoryItemOut.from_item(item)
