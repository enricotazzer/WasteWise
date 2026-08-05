from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import Unit


class PriceObservation(Base, TimestampMixin):
    """Append-only price history per (product, store). Feeds the anomaly detector and the
    waste ledger. Written whenever an item is added with a price — which is optional, so
    coverage is partial by design and every derived figure reports it."""

    __tablename__ = "price_observations"
    __table_args__ = (Index("ix_price_product_store_date", "product_id", "store", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    store: Mapped[str | None] = mapped_column(String(160), index=True)
    # Normalized to the product's default unit so €/kg is comparable across pack sizes.
    unit_price_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[Unit] = mapped_column(Enum(Unit, native_enum=False), nullable=False)
    observed_at: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL")
    )
