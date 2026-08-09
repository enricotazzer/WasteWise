"""The reconciler.

Its whole job is to make the ledger agree with inventory + events no matter how the rows got
there — the demo seeder, an eight-week backfill and the repurchase job all write inventory
directly, and none of them go through the request path.
"""

from datetime import UTC, datetime, timedelta

import factories
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.savings import rebuild
from app.models import SavingsEntry
from app.models.enums import EntryKind, EventType, ItemStatus

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _rows(db: Session) -> list[tuple]:
    return sorted(
        (e.id, e.source_kind, e.source_id, e.kind, e.amount_eur)
        for e in db.scalars(select(SavingsEntry))
    )


def test_rebuild_backfills_rows_written_outside_the_request_path(db: Session, seeded: dict) -> None:
    """Nothing called `sync_item` for these items, which is exactly the state the database is
    in the first time the job runs after the migration."""
    user = seeded["user"]
    consumed = factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00")
    factories.event(db, consumed, EventType.CONSUMED, occurred_at=NOW)
    consumed.status = ItemStatus.CONSUMED
    factories.item(db, user, seeded["ricotta"], unit_price_eur="5.00")  # still active
    db.commit()

    summary = rebuild(db, user_id=user.id)
    assert summary.full, "an empty ledger has nothing to be incremental about"
    assert summary.items == 2
    assert summary.entries_after == 3  # two SPEND, one USED

    kinds = sorted(e.kind for e in db.scalars(select(SavingsEntry)))
    assert kinds == sorted([EntryKind.SPEND, EntryKind.SPEND, EntryKind.USED])


def test_rebuild_is_idempotent(db: Session, seeded: dict) -> None:
    """Run it N times, get the same table. Anything else and the nightly job would inflate
    the very figures it exists to keep correct."""
    user = seeded["user"]
    item = factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    factories.event(db, item, EventType.WASTED, occurred_at=NOW + timedelta(days=1))
    item.status = ItemStatus.WASTED
    db.commit()

    rebuild(db, user_id=user.id, since=None)
    first = _rows(db)

    rebuild(db, user_id=user.id, since=None)
    rebuild(db, user_id=user.id, since=None)
    assert _rows(db) == first
    assert len(first) == 3


def test_rebuild_prunes_rows_a_correction_made_stale(db: Session, seeded: dict) -> None:
    """Replacing the *set* is what makes a corrected outcome shrink the ledger. Appending
    would leave the original guess summing alongside the correction."""
    user = seeded["user"]
    item = factories.item(
        db,
        user,
        seeded["ricotta"],
        unit_price_eur="2.50",
        status=ItemStatus.CONSUMED,
        resolved_at=NOW,
    )
    db.commit()
    rebuild(db, user_id=user.id, since=None)
    assert len(_rows(db)) == 2

    item.status = ItemStatus.WASTED
    factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    db.commit()
    summary = rebuild(db, user_id=user.id, since=None)

    assert summary.entries_removed == 1
    entries = list(db.scalars(select(SavingsEntry)))
    assert len(entries) == 2
    assert {e.kind for e in entries} == {EntryKind.SPEND, EntryKind.WASTE}


def test_rebuild_skips_untouched_items_when_incremental(db: Session, seeded: dict) -> None:
    """The nightly window. `updated_at` moves whenever an event is logged, so an item nobody
    touched cannot have changed value."""
    user = seeded["user"]
    factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00")
    db.commit()
    rebuild(db, user_id=user.id, since=None)  # ledger is now non-empty

    factories.item(db, user, seeded["ricotta"], unit_price_eur="1.00")
    db.commit()

    summary = rebuild(db, user_id=user.id, since=datetime.now(UTC) + timedelta(days=1))
    assert summary.full is False
    assert summary.items == 0
    # The earlier item's rows are untouched, not deleted — the window limits what is
    # recomputed, never what is kept.
    assert len(_rows(db)) == 1


def test_rebuild_is_scoped_to_one_user(db: Session, seeded: dict) -> None:
    from app.models import User

    other = User(email="other@example.com", password_hash="x")
    db.add(other)
    db.flush()
    factories.item(db, other, seeded["mozzarella"], unit_price_eur="2.00")
    db.commit()

    summary = rebuild(db, user_id=seeded["user"].id, since=None)
    assert summary.items == 0
    assert _rows(db) == []
