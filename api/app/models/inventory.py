from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.catalog import Product
from app.models.enums import EventSource, EventType, ExpirySource, ItemStatus, Unit

# Below this much of an item left, it counts as used up rather than leaving a 0.02 sliver in
# the kitchen forever. Lives next to the `fraction_remaining` column it governs because two
# unrelated places need the same number and must never disagree: `log_event` resolves an item
# on it *without writing an event*, and the savings ledger's fraction walk has to fold that
# same unrecorded sliver into the last entry or it shows as unaccounted spend forever.
RESOLVE_EPSILON = 0.01


class InventoryItem(Base, TimestampMixin):
    """THE unit of observation for the survival model.

    Survival framing:
      T          = resolved_at - acquired_at  (days)
      event      = CONSUMED
      competing  = WASTED
      censored   = ACTIVE / PARTIAL at analysis time

    `unit_price_eur` is a snapshot of what was paid, not a lookup — the waste ledger must
    stay correct even if catalog prices later change. It is optional: prices are typed by
    hand, so a blank one is normal and simply doesn't count toward any euro figure.
    """

    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_user_status", "user_id", "status"),
        Index("ix_inventory_user_expiry", "user_id", "expiry_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    # The scanned code, denormalized. NULL for quick-add and loose produce. Not a FK: it
    # records what was physically scanned, which must survive the barcode being re-bound to
    # a different product later.
    barcode: Mapped[str | None] = mapped_column(String(20), index=True)

    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=1, nullable=False)
    unit: Mapped[Unit] = mapped_column(Enum(Unit, native_enum=False), default=Unit.PZ)
    unit_price_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    acquired_at: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    expiry_source: Mapped[ExpirySource | None] = mapped_column(
        Enum(ExpirySource, native_enum=False)
    )
    opened_at: Mapped[date | None] = mapped_column(Date)

    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, native_enum=False), default=ItemStatus.ACTIVE, nullable=False, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Running total across PARTIAL events; 1.0 == fully used up.
    fraction_remaining: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    # Synthetic rows from `make demo`. The whole project rests on outcomes being *real* —
    # a fabricated consumed/wasted label would corrupt the survival model and the
    # euros-saved figure, which are the two things this system claims to measure. Demo rows
    # are visible in the UI so the app isn't empty, and MUST be excluded from Phase 3
    # training and from any reported number.
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )

    # Eager-loaded: every read path needs the product name, unit and category (the Kitchen
    # screen renders them, and stats filters on category.is_food). Joined loading here
    # avoids an N+1 across a full inventory listing.
    product: Mapped["Product"] = relationship(lazy="joined")

    events: Mapped[list["ConsumptionEvent"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ConsumptionEvent.occurred_at"
    )

    @property
    def days_to_expiry(self) -> int | None:
        if self.expiry_date is None:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None


class ConsumptionEvent(Base, TimestampMixin):
    """The label. Without these rows there is no survival model and no euros-saved figure —
    which is why the Kitchen screen ships before the risk model.

    `source` matters: REPURCHASE_INFERRED labels conflate consumption with waste, so they
    are down-weighted in training and excluded from the waste ledger entirely.
    """

    __tablename__ = "consumption_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, native_enum=False), nullable=False, index=True
    )
    # Share of the item this event accounts for: 1.0 for a clean consumed/wasted.
    fraction_used: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[EventSource] = mapped_column(
        Enum(EventSource, native_enum=False), default=EventSource.MANUAL, nullable=False, index=True
    )
    # Free-text "why" on waste events — the qualitative half of the analysis.
    reason: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)

    item: Mapped["InventoryItem"] = relationship(back_populates="events")
