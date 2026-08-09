"""Writing the ledger: replacement semantics, parentage, and cascade.

The derivation being right is only half of it. The other half is that re-deriving replaces
what was there — a ledger that appended would double-count every correction, and the totals
would drift upward with no event to explain the growth.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import factories
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SavingsEntry
from app.models.enums import EntryKind, EventType, ItemStatus, SourceKind
from app.services.savings.writer import sync_item

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _entries(db: Session) -> list[SavingsEntry]:
    return list(db.scalars(select(SavingsEntry).order_by(SavingsEntry.id)))


def _fingerprint(entries: list[SavingsEntry]) -> list[tuple]:
    return sorted(
        (e.source_kind, e.source_id, e.kind, e.amount_eur, e.occurred_on) for e in entries
    )


def test_sync_is_idempotent(db: Session, seeded: dict) -> None:
    """Running it twice must converge, not accumulate. The same property is what makes the
    nightly reconciler safe to run on top of the synchronous writer."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="2.00")
    factories.event(db, item, EventType.CONSUMED, occurred_at=NOW)
    item.status = ItemStatus.CONSUMED

    sync_item(db, item)
    db.commit()
    first = _entries(db)

    sync_item(db, item)
    db.commit()
    second = _entries(db)

    assert len(first) == len(second) == 2
    # Same rows, not merely the same totals: the ids must be reused, or "idempotent" only
    # means the numbers happen to match while the table churns underneath.
    assert [e.id for e in first] == [e.id for e in second]


def test_a_corrected_outcome_shrinks_the_entry_set(db: Session, seeded: dict) -> None:
    """An item recorded as consumed and later logged as wasted must end with one WASTE row —
    not a USED row plus a WASTE row quietly double-counting the same €2.50."""
    item = factories.item(db, seeded["user"], seeded["ricotta"], unit_price_eur="2.50")
    item.status = ItemStatus.CONSUMED
    item.resolved_at = NOW
    sync_item(db, item)
    db.commit()

    outcomes = [e for e in _entries(db) if e.kind is not EntryKind.SPEND]
    assert [e.kind for e in outcomes] == [EntryKind.USED]

    # The correction: a real waste event replaces the status-derived guess.
    item.status = ItemStatus.WASTED
    factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    sync_item(db, item)
    db.commit()

    entries = _entries(db)
    assert len(entries) == 2
    outcomes = [e for e in entries if e.kind is not EntryKind.SPEND]
    assert [e.kind for e in outcomes] == [EntryKind.WASTE]
    assert outcomes[0].source_kind is SourceKind.CONSUMPTION_EVENT
    assert sum(e.amount_eur for e in outcomes) == Decimal("2.50")


def test_spend_is_written_before_its_partitions(db: Session, seeded: dict) -> None:
    """`unaccounted` is SPEND minus its partitions. A partition with a NULL parent breaks it
    silently — no error, just a figure that stops meaning anything."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="4.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    factories.event(db, item, EventType.WASTED, occurred_at=NOW + timedelta(days=1))
    item.status = ItemStatus.WASTED
    sync_item(db, item)
    db.commit()

    entries = _entries(db)
    spend = next(e for e in entries if e.kind is EntryKind.SPEND)
    partitions = [e for e in entries if e.kind is not EntryKind.SPEND]

    assert partitions, "expected the item to be partitioned"
    assert all(p.parent_entry_id == spend.id for p in partitions)
    assert spend.parent_entry_id is None


def test_deleting_an_item_cascades_its_entries(db: Session, seeded: dict) -> None:
    """Two delete paths already exist in the app. Orphaned money rows would inflate every
    total forever with nothing left to point at."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.00")
    factories.event(db, item, EventType.CONSUMED, occurred_at=NOW)
    item.status = ItemStatus.CONSUMED
    sync_item(db, item)
    db.commit()
    assert len(_entries(db)) == 2

    db.delete(item)
    db.commit()
    assert _entries(db) == []


def test_non_food_writes_nothing_at_all(db: Session, seeded: dict) -> None:
    item = factories.item(db, seeded["user"], seeded["detersivo"], unit_price_eur="3.00")
    factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    item.status = ItemStatus.WASTED
    sync_item(db, item)
    db.commit()

    assert _entries(db) == []


def test_unpriced_rows_are_written_with_a_null_amount(db: Session, seeded: dict) -> None:
    """The row exists so the outcome counts in the coverage denominator. NULL and not 0.00:
    a zero sums silently and makes an unpriced item look free."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur=None)
    factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    item.status = ItemStatus.WASTED
    sync_item(db, item)
    db.commit()

    entries = _entries(db)
    assert len(entries) == 2
    assert all(e.amount_eur is None for e in entries)


def test_re_deriving_an_unchanged_item_keeps_the_same_fingerprint(
    db: Session, seeded: dict
) -> None:
    """Guards against the upsert quietly changing a value it shouldn't — a shifted
    `occurred_on` would silently move euros into a different week."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="3.30")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.25, occurred_at=NOW)
    sync_item(db, item)
    db.commit()
    before = _fingerprint(_entries(db))

    sync_item(db, item)
    db.commit()
    assert _fingerprint(_entries(db)) == before
