"""Close items that a later purchase implies are gone.

Buying the same product again while an older one is still sitting in the inventory as
`active` means, almost always, that the older one was finished and never logged. Left alone
those items stay unresolved forever: they are censored observations the survival model can
learn nothing from, and their value sits in `unaccounted` making the euro figures look
incomplete — which they are.

Two hard rules:

  * it never touches an item a human already resolved, or partly resolved. A candidate must
    be `ACTIVE` with *no events at all*. A machine overwriting a human's label would corrupt
    the one thing this project claims to measure.
  * the label it writes is marked `REPURCHASE_INFERRED`, which flows through to
    `outcome_inferred` on every ledger row derived from it and keeps them out of the
    headline figures by default. The inference conflates "eaten" with "quietly binned", so
    it is a useful default and a bad measurement.

Idempotent: closing an item gives it a status and an event, which disqualifies it from every
later run.
"""

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import ConsumptionEvent, InventoryItem, User
from app.models.enums import EventSource, EventType, ItemStatus
from app.services.savings.writer import sync_item


@dataclass
class RepurchaseSummary:
    closed: int = 0
    candidates: int = 0


def _resolved_at(day: date) -> datetime:
    """Midday rather than midnight: the date is a guess to within a day anyway, and a
    midnight UTC timestamp renders as the previous day in every timezone west of London,
    which makes a weekly bucket look like it lost an item."""
    return datetime.combine(day, time(hour=12), tzinfo=UTC)


def infer_repurchases(
    db: Session, *, user_id: int | None = None, include_demo: bool = False
) -> RepurchaseSummary:
    """Resolve each stale item at the date its product was next bought."""
    summary = RepurchaseSummary()

    has_events = exists().where(ConsumptionEvent.inventory_item_id == InventoryItem.id)
    clauses = [InventoryItem.status == ItemStatus.ACTIVE, ~has_events]
    if user_id is not None:
        clauses.append(InventoryItem.user_id == user_id)
    if not include_demo:
        # Inference on top of fabrication produces a fabrication with a provenance flag on
        # it. The demo also leaves ~8% of expired items deliberately unlogged, because that
        # missing-label case is exactly what the dashboard needs to show.
        clauses.append(InventoryItem.is_demo.is_(False))

    candidates = list(db.scalars(select(InventoryItem).where(*clauses)).unique())
    summary.candidates = len(candidates)
    if not candidates:
        return summary

    keys = {(item.user_id, item.product_id) for item in candidates}
    purchases: dict[tuple[int, int], list[date]] = {key: [] for key in keys}
    for row in db.execute(
        select(InventoryItem.user_id, InventoryItem.product_id, InventoryItem.acquired_at).where(
            InventoryItem.user_id.in_({key[0] for key in keys}),
            InventoryItem.product_id.in_({key[1] for key in keys}),
        )
    ):
        key = (row.user_id, row.product_id)
        if key in purchases:
            purchases[key].append(row.acquired_at)

    for item in candidates:
        # Strictly later: two of the same product bought on one shop are two units, not a
        # repurchase, and closing one of them would invent an outcome that never happened.
        later = [
            day for day in purchases[(item.user_id, item.product_id)] if day > item.acquired_at
        ]
        if not later:
            continue

        occurred = _resolved_at(min(later))
        db.add(
            ConsumptionEvent(
                inventory_item_id=item.id,
                event_type=EventType.CONSUMED,
                fraction_used=1.0,
                occurred_at=occurred,
                source=EventSource.REPURCHASE_INFERRED,
                note="closed by repurchase inference",
            )
        )
        item.status = ItemStatus.CONSUMED
        item.resolved_at = occurred
        item.fraction_remaining = 0.0
        # Same transaction as the event, same derivation as the request path.
        sync_item(db, item)
        summary.closed += 1

    db.commit()
    return summary


def main() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.first_user_email))
        if user is None:
            print("no user found — run `make seed` first", file=sys.stderr)
            return 1

        summary = infer_repurchases(db, user_id=user.id)
        print(f"{summary.candidates} unresolved items examined")
        print(f"  {summary.closed} closed as consumed (source=repurchase_inferred)")
        if summary.closed:
            print("  These are guesses: excluded from every headline figure by default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
