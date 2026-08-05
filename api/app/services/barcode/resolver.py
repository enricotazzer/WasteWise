"""The four-tier barcode resolution, cheapest first.

    1. product_barcodes hit          -> instant, offline, free
    2. Open Food Facts + rules map   -> one network call, cached forever
    3. Open Food Facts + LLM tail    -> one free-tier call, only for unmapped categories
    4. manual                        -> the user names it once; bound forever after

Every tier degrades into the next, and tier 4 always works. Nothing here may raise on a
network or model failure: the scanner is a convenience over an add form that already
functions, so a failed lookup returns an unresolved scan and the UI asks for a name.

The tier that served a scan is recorded in `ProductBarcode.match_method`, which is where
the "how many items did you have to type" number comes from at the checkpoint.
"""

import logging
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Product, ProductAlias, ProductBarcode
from app.models.enums import AliasSource, MatchMethod, Unit
from app.services.barcode.category_map import (
    FALLBACK_CATEGORY,
    classify_with_llm,
    map_off_categories,
)
from app.services.barcode.off_client import (
    OffProduct,
    fetch_product,
    is_store_internal,
    normalize_barcode,
)
from app.services.normalize import normalize_text

log = logging.getLogger(__name__)

# OFF pack-size unit strings -> our Unit enum.
_UNIT_MAP = {"g": Unit.G, "kg": Unit.KG, "ml": Unit.ML, "l": Unit.L, "cl": Unit.ML}


class ResolvedScan(BaseModel):
    """What a scan produced. `product_id is None` means the caller must ask for a name."""

    barcode: str
    match_method: MatchMethod
    product_id: int | None = None
    product_name: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    # Suggested defaults for the confirm screen, from the OFF pack size where available.
    suggested_quantity: float | None = None
    suggested_unit: Unit | None = None
    # What OFF said, shown so the user can sanity-check a wrong-looking match.
    off_name: str | None = None
    off_brand: str | None = None
    off_quantity: str | None = None
    # True when this barcode has been scanned before — the UI says "già in catalogo".
    from_cache: bool = False
    lookup_count: int = 0
    needs_confirmation: bool = False


def _touch(record: ProductBarcode) -> None:
    # `or 0` is load-bearing: column defaults are applied by the INSERT, so a record built
    # in this request but not yet flushed still has lookup_count = None.
    record.lookup_count = (record.lookup_count or 0) + 1
    record.last_seen_at = datetime.now(UTC)


def _category_by_code(db: Session, code: str) -> Category | None:
    return db.scalar(select(Category).where(Category.code == code))


def _known_codes(db: Session) -> set[str]:
    return set(db.scalars(select(Category.code)))


def _suggested_size(off: OffProduct | None) -> tuple[float | None, Unit | None]:
    """Turn OFF's pack size into a quantity the confirm screen can pre-fill.

    Grams and millilitres are converted to kg/l because that's how the catalog stores them
    and how prices become comparable across pack sizes.
    """
    if off is None or off.quantity_value is None or not off.quantity_unit:
        return None, None
    unit = _UNIT_MAP.get(off.quantity_unit.lower())
    if unit is None:
        return None, None
    value = off.quantity_value
    if off.quantity_unit.lower() == "cl":
        value = value * 10  # cl -> ml
    if unit is Unit.G:
        return round(value / 1000, 3), Unit.KG
    if unit is Unit.ML:
        return round(value / 1000, 3), Unit.L
    return value, unit


def _find_product_by_name(db: Session, name: str) -> Product | None:
    """Reuse the text alias table so an OFF name that normalizes onto a known product
    (`"Mozzarella di bufala 125g"` -> `MOZZARELLA DI BUFALA`) doesn't create a duplicate."""
    norm = normalize_text(name)
    if not norm:
        return None
    alias = db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm))
    if alias is not None:
        alias.hit_count += 1
        return db.get(Product, alias.product_id)
    # Exact name match as a second chance — aliases strip pack sizes, names don't.
    return db.scalar(select(Product).where(func.lower(Product.name_it) == name.strip().lower()))


def _create_product(
    db: Session,
    *,
    name: str,
    category: Category,
    shelf_life_days: int | None,
    is_staple: bool = False,
    unit: Unit = Unit.PZ,
    alias_source: AliasSource = AliasSource.OFF,
) -> Product:
    product = Product(
        name_it=name.strip()[:160],
        category_id=category.id,
        default_unit=unit,
        shelf_life_days=shelf_life_days,
        is_perishable=category.is_perishable,
        is_staple=is_staple,
    )
    db.add(product)
    db.flush()

    norm = normalize_text(product.name_it)
    if norm and db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm)) is None:
        db.add(
            ProductAlias(
                raw_text_norm=norm,
                product_id=product.id,
                source=alias_source,
                confidence=1.0,
                confirmed_at=datetime.now(UTC) if alias_source is AliasSource.USER else None,
            )
        )
    return product


def resolve_barcode(db: Session, raw_barcode: str, *, use_network: bool = True) -> ResolvedScan:
    """Resolve a scanned barcode. Never raises for a missing product or a failed lookup."""
    barcode = normalize_barcode(raw_barcode)
    if not barcode:
        return ResolvedScan(
            barcode="", match_method=MatchMethod.UNRESOLVED, needs_confirmation=True
        )

    # --- Tier 1: cache ---
    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == barcode))
    if record is not None and record.is_resolved:
        _touch(record)
        db.commit()
        product = record.product
        assert product is not None  # is_resolved guarantees it
        qty, unit = None, None
        if record.off_quantity_value and record.off_quantity_unit:
            qty, unit = _suggested_size(
                OffProduct(
                    barcode=barcode,
                    quantity_value=record.off_quantity_value,
                    quantity_unit=record.off_quantity_unit,
                )
            )
        return ResolvedScan(
            barcode=barcode,
            match_method=MatchMethod.CACHE,
            product_id=product.id,
            product_name=product.name_it,
            category_code=product.category.code,
            category_name=product.category.name_it,
            suggested_quantity=qty,
            suggested_unit=unit or product.default_unit,
            off_name=record.off_name,
            off_brand=record.off_brand,
            off_quantity=record.off_quantity,
            from_cache=True,
            lookup_count=record.lookup_count,
        )

    # --- Store-internal codes: no point asking OFF, they're price labels ---
    if is_store_internal(barcode):
        record = record or ProductBarcode(barcode=barcode, match_method=MatchMethod.UNRESOLVED)
        db.add(record)
        _touch(record)
        db.commit()
        return ResolvedScan(
            barcode=barcode,
            match_method=MatchMethod.UNRESOLVED,
            lookup_count=record.lookup_count,
            needs_confirmation=True,
        )

    # --- Tier 2/3: Open Food Facts ---
    off = fetch_product(barcode) if use_network else None
    if off is None or not off.name:
        record = record or ProductBarcode(barcode=barcode, match_method=MatchMethod.UNRESOLVED)
        record.off_payload = off.payload if off else None
        db.add(record)
        _touch(record)
        db.commit()
        return ResolvedScan(
            barcode=barcode,
            match_method=MatchMethod.UNRESOLVED,
            off_name=off.name if off else None,
            off_brand=off.brand if off else None,
            lookup_count=record.lookup_count,
            needs_confirmation=True,
        )

    known = _known_codes(db)
    method = MatchMethod.OFF
    shelf_life: int | None = None
    is_staple = False

    code = map_off_categories(off.categories, known)
    if code is None:
        tail = classify_with_llm(
            name=off.name, brand=off.brand, tags=off.categories, known_codes=known
        )
        if tail is not None:
            code, shelf_life, is_staple = tail
            method = MatchMethod.LLM
        else:
            code = FALLBACK_CATEGORY

    category = _category_by_code(db, code) or _category_by_code(db, FALLBACK_CATEGORY)
    if category is None:
        # Only possible against an unseeded database.
        raise RuntimeError("no categories seeded — run `make seed`")

    qty, unit = _suggested_size(off)
    product = _find_product_by_name(db, off.name)
    if product is None:
        product = _create_product(
            db,
            name=off.name,
            category=category,
            shelf_life_days=shelf_life,
            is_staple=is_staple,
            unit=unit or Unit.PZ,
        )

    record = record or ProductBarcode(barcode=barcode, match_method=method)
    record.product_id = product.id
    record.match_method = method
    record.off_name = off.name
    record.off_brand = off.brand
    record.off_quantity = off.quantity
    record.off_quantity_value = off.quantity_value
    record.off_quantity_unit = off.quantity_unit
    record.off_categories = off.categories
    record.off_payload = off.payload
    db.add(record)
    _touch(record)
    db.commit()

    return ResolvedScan(
        barcode=barcode,
        match_method=method,
        product_id=product.id,
        product_name=product.name_it,
        category_code=category.code,
        category_name=category.name_it,
        suggested_quantity=qty,
        suggested_unit=unit or product.default_unit,
        off_name=off.name,
        off_brand=off.brand,
        off_quantity=off.quantity,
        lookup_count=record.lookup_count,
        # A fresh OFF match hasn't been checked by a human yet, and the category may have
        # come from a model. Ask once; after that it's cached and silent.
        needs_confirmation=True,
    )


def bind_barcode(
    db: Session,
    barcode: str,
    *,
    product_id: int | None = None,
    new_product_name: str | None = None,
    category_id: int | None = None,
    shelf_life_days: int | None = None,
    default_unit: Unit = Unit.PZ,
) -> ResolvedScan:
    """Tier 4: a human says what this barcode is. Authoritative and permanent.

    Either binds to an existing product or creates one. The mapping is marked
    `confirmed_by_user`, so a later OFF change never silently overrides a human decision.
    """
    code = normalize_barcode(barcode)
    if not code:
        raise ValueError("empty barcode")

    if product_id is not None:
        product = db.get(Product, product_id)
        if product is None:
            raise LookupError(f"product {product_id} not found")
    else:
        if not new_product_name or category_id is None:
            raise ValueError("either product_id or (new_product_name + category_id) is required")
        category = db.get(Category, category_id)
        if category is None:
            raise LookupError(f"category {category_id} not found")
        product = _create_product(
            db,
            name=new_product_name,
            category=category,
            shelf_life_days=shelf_life_days,
            unit=default_unit,
            alias_source=AliasSource.USER,
        )

    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == code))
    if record is None:
        # Binding without a prior scan: the barcode still came off a physical product, so it
        # counts as one scan. Leaving it at 0 would drop hand-bound items out of the
        # zero-typing denominator and flatter the hit rate.
        record = ProductBarcode(barcode=code, match_method=MatchMethod.MANUAL, lookup_count=1)
        db.add(record)
    # An existing record was already counted by the scan that produced it — don't double up.
    record.product_id = product.id
    record.match_method = MatchMethod.MANUAL
    record.confirmed_by_user = True
    record.last_seen_at = datetime.now(UTC)
    db.commit()
    db.refresh(product)

    return ResolvedScan(
        barcode=code,
        match_method=MatchMethod.MANUAL,
        product_id=product.id,
        product_name=product.name_it,
        category_code=product.category.code,
        category_name=product.category.name_it,
        suggested_unit=product.default_unit,
        off_name=record.off_name,
        off_brand=record.off_brand,
        off_quantity=record.off_quantity,
        lookup_count=record.lookup_count,
    )
