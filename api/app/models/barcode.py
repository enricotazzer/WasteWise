from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.catalog import Product
from app.models.enums import MatchMethod


class ProductBarcode(Base, TimestampMixin):
    """Barcode -> canonical product. Cache and learning table in one.

    Every lookup that reaches Open Food Facts is written here once and never fetched again,
    so steady-state network traffic is zero and the app keeps working offline. Barcodes that
    OFF doesn't know (store-weighed produce, small local brands) get bound by hand exactly
    once and resolve instantly from then on — the same idea as ProductAlias, keyed on digits
    instead of text.

    `match_method` is the honest accounting of how the catalog got built: what fraction of
    scans resolved for free (OFF), needed an LLM, or needed typing. That ratio is the
    headline number for Module 1.
    """

    __tablename__ = "product_barcodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # EAN-13/EAN-8/UPC-A, digits only. Unique across users: a barcode identifies the same
    # physical product for everyone, so the cache is deliberately global.
    barcode: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)

    # --- Open Food Facts snapshot (NULL when OFF had no record) ---
    off_name: Mapped[str | None] = mapped_column(String(255))
    off_brand: Mapped[str | None] = mapped_column(String(160))
    # Display string as printed, e.g. "125 g". Parsed size lives in the next two columns.
    off_quantity: Mapped[str | None] = mapped_column(String(64))
    off_quantity_value: Mapped[float | None]
    off_quantity_unit: Mapped[str | None] = mapped_column(String(16))
    off_categories: Mapped[list | None] = mapped_column(JSONB)
    # Verbatim response. Kept because it is the only record of what OFF said at bind time,
    # and OFF is community-edited — the remote record can change under us.
    off_payload: Mapped[dict | None] = mapped_column(JSONB)

    match_method: Mapped[MatchMethod] = mapped_column(
        Enum(MatchMethod, native_enum=False), nullable=False, index=True
    )
    # True once a human accepted or corrected the mapping in the confirm screen.
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # How many times this barcode has been scanned. Distinguishes staples from one-offs, and
    # proves the cache is doing its job.
    lookup_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped["Product | None"] = relationship(lazy="joined")

    @property
    def is_resolved(self) -> bool:
        """False for a barcode we've seen but never managed to map to a product."""
        return self.product_id is not None
