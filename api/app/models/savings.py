from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import EntryBasis, EntryKind, SavingsDomain, SourceKind


class SavingsEntry(Base, TimestampMixin):
    """One money fact, in one place, for every domain the household spends in.

    This table exists because the two numbers this project claims to measure — euros wasted
    and euros saved — were previously computed by walking Python lists in the stats endpoint.
    Two lists built independently drift: `eur_wasted` summed one collection and
    `price_coverage` counted another, so the caveat printed next to a euro figure was not
    actually the caveat *for* that figure. Here, a figure and its coverage are
    `sum(amount_eur)` and `count(amount_eur) / count(*)` over the very same rows.

    Three decisions carry the design:

    `amount_eur` is NULL when no price was typed, and the row is written anyway. Prices are
    optional and hand-entered, so an unpriced outcome is a normal event that must land in the
    coverage denominator. A zero would sum silently and make wasted food look free.

    Row identity is `(source_kind, source_id, kind)`, not a foreign key. `inventory_item_id`
    below is housekeeping only. That is what lets a Bills module write rows here without
    inventing an inventory item to hang them off.

    Every enum column is `native_enum=False` with an explicit `length=32`. Without the
    length, SQLAlchemy sizes the VARCHAR to the longest member that exists *today*, so adding
    `'subscriptions'` later would need an ALTER on a table holding real money history. The
    length is the reason a new domain is a code change and not a migration.

    NOTE for anyone writing a query against this table: `native_enum=False` persists the
    member *name*, so the stored value is `'WASTE'`, not `'waste'`. Always compare against
    the enum member through the ORM/Core column, never a hand-written string literal.
    """

    __tablename__ = "savings_entries"
    __table_args__ = (
        # The upsert target. `kind` is part of the key because one source legitimately
        # produces two rows: an item is both a SPEND and (with no events) its own outcome.
        UniqueConstraint("source_kind", "source_id", "kind", name="uq_savings_source"),
        # The dashboard's shape: everything is "this user, optionally this domain, this kind,
        # bucketed by day".
        Index("ix_savings_user_domain_kind_day", "user_id", "domain", "kind", "occurred_on"),
        Index("ix_savings_user_day", "user_id", "occurred_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    domain: Mapped[SavingsDomain] = mapped_column(
        Enum(SavingsDomain, native_enum=False, length=32), nullable=False
    )
    kind: Mapped[EntryKind] = mapped_column(
        Enum(EntryKind, native_enum=False, length=32), nullable=False
    )
    basis: Mapped[EntryBasis] = mapped_column(
        Enum(EntryBasis, native_enum=False, length=32), nullable=False
    )

    # NULL means "this outcome carried no price", never "this outcome was free".
    amount_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Only set where the unit makes it a real mass (kg/g). Pieces and litres are NULL, and
    # mass coverage is reported separately from price coverage — they are different fractions
    # of the same rows and conflating them would overstate one of the two.
    mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    # The bucketing key for every chart. A date, not a timestamp: an item acquired on a date
    # has no meaningful time, and `date_trunc` over a mix of the two invites timezone drift
    # into a weekly total.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Kept when the source actually had one, for ordering within a day. Never bucketed on.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_kind: Mapped[SourceKind] = mapped_column(
        Enum(SourceKind, native_enum=False, length=32), nullable=False
    )
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Denormalized from the product's category and name. Copied rather than joined so that
    # one set of aggregation queries serves food, utilities and subscriptions — a domain
    # without a category table still groups by `category_code`.
    category_code: Mapped[str | None] = mapped_column(String(48), index=True)
    label: Mapped[str | None] = mapped_column(String(160))

    # Points a partition at the SPEND it came out of. `unaccounted` — value bought and never
    # resolved — is SPEND minus its partitions, so a NULL parent here silently breaks it.
    parent_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("savings_entries.id", ondelete="CASCADE")
    )

    # HOUSEKEEPING ONLY — this column exists so the database cascades, and must never appear
    # in a dashboard query (that is what `source_kind`/`source_id` are for). Two delete paths
    # already exist, `DELETE /api/inventory/items/{id}` and `demo.clear()`; without this FK
    # both would leave orphaned money rows quietly inflating every total. Deliberately *not*
    # backed by a `relationship()` on InventoryItem: an ORM-side cascade would fight the
    # database-side one and the winner would depend on whether the item happened to be loaded.
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )

    # Copied from the source row rather than joined, so that excluding synthetic data is a
    # WHERE clause on this table alone. Demo outcomes are fabricated and must never reach a
    # reported number — see `InventoryItem.is_demo`.
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )
    # True when a machine, not a human, decided this outcome (repurchase inference). Excluded
    # from every headline by default, but present in the table: dropping these rows would
    # inflate unaccounted spend and hide that the label was guessed.
    outcome_inferred: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false", index=True
    )
    # Free text naming the derivation that produced this row ("fraction_walk",
    # "item_status", ...). Debugging aid: it is how you tell, months later, why one item's
    # euros were split the way they were.
    method: Mapped[str | None] = mapped_column(String(64))
