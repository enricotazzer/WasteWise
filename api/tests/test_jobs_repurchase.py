"""Repurchase inference.

The rule is useful and the label is a guess, which is the whole tension: it recovers items
that would otherwise stay unresolved forever, and it cannot tell "eaten" from "quietly
binned". Both properties are asserted here — that it fires, and that it never overwrites a
human, and that everything it produces is flagged out of the headline figures.
"""

from datetime import date, timedelta

import factories
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.repurchase import infer_repurchases
from app.jobs.savings import rebuild
from app.models import ConsumptionEvent, SavingsEntry
from app.models.enums import EntryKind, EventSource, EventType, ItemStatus

TODAY = date.today()


def test_a_later_purchase_closes_the_older_item(db: Session, seeded: dict) -> None:
    user = seeded["user"]
    old = factories.item(
        db,
        user,
        seeded["mozzarella"],
        unit_price_eur="1.00",
        acquired_at=TODAY - timedelta(days=14),
    )
    factories.item(
        db, user, seeded["mozzarella"], unit_price_eur="1.00", acquired_at=TODAY - timedelta(days=3)
    )
    db.commit()

    summary = infer_repurchases(db, user_id=user.id)
    assert summary.closed == 1

    db.refresh(old)
    assert old.status is ItemStatus.CONSUMED
    # Resolved at the date of the repurchase, not today: that is when the evidence appeared.
    assert old.resolved_at.date() == TODAY - timedelta(days=3)

    event = db.scalar(select(ConsumptionEvent).where(ConsumptionEvent.inventory_item_id == old.id))
    assert event.source is EventSource.REPURCHASE_INFERRED


def test_it_never_touches_an_item_a_human_resolved(db: Session, seeded: dict) -> None:
    """A machine overwriting a human's label would corrupt the one thing this project claims
    to measure. Both a resolved item and a part-used one are off limits."""
    user = seeded["user"]
    wasted = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=14),
        status=ItemStatus.WASTED,
    )
    partial = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=13),
        status=ItemStatus.PARTIAL,
        fraction_remaining=0.5,
    )
    factories.event(db, partial, EventType.PARTIAL, fraction_used=0.5)
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY)
    db.commit()

    assert infer_repurchases(db, user_id=user.id).closed == 0
    db.refresh(wasted)
    db.refresh(partial)
    assert wasted.status is ItemStatus.WASTED
    assert partial.status is ItemStatus.PARTIAL


def test_same_day_duplicates_are_not_a_repurchase(db: Session, seeded: dict) -> None:
    """Two mozzarellas in one shop are two units, not evidence that the first is gone."""
    user = seeded["user"]
    for _ in range(2):
        factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=5))
    db.commit()

    assert infer_repurchases(db, user_id=user.id).closed == 0


def test_it_is_idempotent(db: Session, seeded: dict) -> None:
    """Closing an item gives it a status and an event, which disqualifies it from every later
    run. A second pass must be a no-op, not a second event."""
    user = seeded["user"]
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=14))
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=3))
    db.commit()

    assert infer_repurchases(db, user_id=user.id).closed == 1
    assert infer_repurchases(db, user_id=user.id).closed == 0
    assert db.query(ConsumptionEvent).count() == 1


def test_demo_items_are_left_alone(db: Session, seeded: dict) -> None:
    """Inferring on top of fabricated data produces a fabrication with a provenance flag. The
    demo also leaves ~8% of expired items unlogged on purpose — that missing-label case is
    what the unaccounted figure exists to show."""
    user = seeded["user"]
    factories.item(
        db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=14), is_demo=True
    )
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY, is_demo=True)
    db.commit()

    assert infer_repurchases(db, user_id=user.id).closed == 0


def test_inferred_outcomes_stay_out_of_the_headline_figures(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The row is written — dropping it would inflate unaccounted spend and hide that a job,
    not the user, closed the item — but it never reaches a reported number by default."""
    user = seeded["user"]
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        unit_price_eur="2.00",
        acquired_at=TODAY - timedelta(days=14),
    )
    factories.item(
        db, user, seeded["mozzarella"], unit_price_eur="2.00", acquired_at=TODAY - timedelta(days=3)
    )
    db.commit()
    # Backfill first, so the ledger has both SPENDs — otherwise the only rows in the table
    # would be the ones the inference itself wrote, and `unaccounted` would be measured
    # against half the spend.
    rebuild(db, user_id=user.id, since=None)
    infer_repurchases(db, user_id=user.id)

    inferred = [e for e in db.scalars(select(SavingsEntry)) if e.outcome_inferred]
    assert [e.kind for e in inferred] == [EntryKind.USED]

    body = auth_client.get("/api/stats/overview").json()
    assert body["used"]["entries"] == 0
    assert body["save_rate"] is None
    # And the euros it would have accounted for are visibly still unaccounted.
    food = next(d for d in body["domains"] if d["domain"] == "food")
    assert food["unaccounted"]["eur"] == "4.00"

    included = auth_client.get("/api/stats/overview?include_inferred=true").json()
    assert included["used"]["entries"] == 1
    assert included["used"]["eur"] == "2.00"
