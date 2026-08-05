from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MatchMethod, Unit


class ScanResult(BaseModel):
    """What a scanned barcode resolved to.

    `needs_confirmation` drives the UI: false means the barcode is already known and
    confirmed, so the item can be added with one tap; true means show the confirm sheet.
    `product_id is None` means we have nothing — ask for a name.
    """

    model_config = ConfigDict(from_attributes=True)

    barcode: str
    match_method: MatchMethod
    product_id: int | None
    product_name: str | None
    category_code: str | None
    category_name: str | None
    suggested_quantity: float | None
    suggested_unit: Unit | None
    off_name: str | None
    off_brand: str | None
    off_quantity: str | None
    from_cache: bool
    lookup_count: int
    needs_confirmation: bool


class BarcodeBindIn(BaseModel):
    """Bind a barcode to a product: either an existing one, or a new one described here."""

    product_id: int | None = None
    new_product_name: str | None = None
    category_id: int | None = None
    shelf_life_days: int | None = Field(default=None, gt=0)
    default_unit: Unit = Unit.PZ


class ScanAddIn(BaseModel):
    """Add an item straight from a scan. `barcode` is recorded on the item so the scan can
    be traced back, and the price — if given — also lands in price_observations."""

    barcode: str | None = None
    product_id: int
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: Unit | None = None
    unit_price_eur: Decimal | None = Field(default=None, ge=0)
    expiry_date: str | None = None
    store: str | None = None
    note: str | None = None


class ScanStats(BaseModel):
    """How the catalog got built — the measured quality of Module 1.

    `zero_typing_rate` is the headline: the share of scans that cost the user no typing.
    Naming is charged once per barcode, not once per scan — binding a code by hand is a
    one-off, and every later scan of it is free. That is what makes the number rise as the
    catalog fills in, which is the behaviour worth measuring.
    """

    total_barcodes: int
    by_method: dict[str, int]
    resolved: int
    unresolved: int
    total_scans: int
    zero_typing_rate: float | None
