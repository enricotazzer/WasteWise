"""Reconcile `savings_entries` against inventory and events.

This is the backfill *and* the nightly repair, and it is deliberately the same code path as
the synchronous writer on the request path — `services/savings/rules.derive` decides what an
item is worth in both. That is also why the Alembic revision that creates the table carries
no backfill: re-expressing the fraction walk in SQL would guarantee that the migration and
the application disagreed about some item, in some week, and nobody would find out until the
two numbers were compared.

Idempotent by construction: each item's rows are replaced as a set, so running this N times
converges on the same table rather than accumulating.
"""

import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import InventoryItem, SavingsEntry, User
from app.services.savings.writer import sync_items

# How far back the nightly run looks. Items are only re-derived if something touched them,
# and `updated_at` moves whenever an event is logged against one.
RECENT_WINDOW_DAYS = 7

# Distinguishes "the caller said nothing" from an explicit `since=None`. A plain None default
# cannot express both, and the difference is the difference between the nightly job and the
# one-off backfill.
_UNSET: Final[Any] = object()

_CHUNK = 500


@dataclass
class RebuildSummary:
    items: int = 0
    entries_written: int = 0
    # Rows a correction made stale. The only visible sign that the ledger is replacing an
    # item's rows as a set rather than appending to them.
    entries_removed: int = 0
    entries_before: int = 0
    entries_after: int = 0
    full: bool = False
    # (domain, kind, basis) -> (rows, priced rows, euros)
    breakdown: dict[tuple[str, str, str], tuple[int, int, Decimal]] = field(default_factory=dict)


def _scope(user_id: int | None) -> list:
    clauses = []
    if user_id is not None:
        clauses.append(InventoryItem.user_id == user_id)
    return clauses


def _count_entries(db: Session, user_id: int | None) -> int:
    stmt = select(func.count()).select_from(SavingsEntry)
    if user_id is not None:
        stmt = stmt.where(SavingsEntry.user_id == user_id)
    return db.scalar(stmt) or 0


def rebuild(
    db: Session,
    user_id: int | None = None,
    since: datetime | None = _UNSET,
) -> RebuildSummary:
    """Recompute, upsert and prune ledger rows for every item in scope.

    `since` defaults to the last `RECENT_WINDOW_DAYS` of `updated_at`; passing `None`
    explicitly rebuilds everything, which is the backfill. An empty ledger escalates the
    default to a full rebuild on its own — there is nothing to be incremental about, and a
    first run that silently did almost nothing would look like the job was working.
    """
    summary = RebuildSummary(entries_before=_count_entries(db, user_id))

    if since is _UNSET:
        since = datetime.now(UTC) - timedelta(days=RECENT_WINDOW_DAYS)
        if summary.entries_before == 0:
            since = None
    summary.full = since is None

    stmt = select(InventoryItem).where(*_scope(user_id))
    if since is not None:
        stmt = stmt.where(InventoryItem.updated_at >= since)
    items = list(db.scalars(stmt.order_by(InventoryItem.id)).unique())

    for start in range(0, len(items), _CHUNK):
        chunk = items[start : start + _CHUNK]
        counts = sync_items(db, chunk)
        summary.entries_written += counts.written
        summary.entries_removed += counts.deleted
        summary.items += len(chunk)
        # Commit per chunk: a rebuild interrupted halfway leaves a consistent prefix rather
        # than an hour of work on the floor. Safe because each item's rows are independent.
        db.commit()

    summary.entries_after = _count_entries(db, user_id)
    summary.breakdown = _breakdown(db, user_id)
    return summary


def _breakdown(
    db: Session, user_id: int | None
) -> dict[tuple[str, str, str], tuple[int, int, Decimal]]:
    """What is in the table now, demo rows included — this is an operational report on the
    ledger, not a reported figure about the household."""
    stmt = select(
        SavingsEntry.domain,
        SavingsEntry.kind,
        SavingsEntry.basis,
        func.count().label("rows"),
        func.count(SavingsEntry.amount_eur).label("priced"),
        func.coalesce(func.sum(SavingsEntry.amount_eur), 0).label("eur"),
    ).group_by(SavingsEntry.domain, SavingsEntry.kind, SavingsEntry.basis)
    if user_id is not None:
        stmt = stmt.where(SavingsEntry.user_id == user_id)

    return {
        (row.domain.value, row.kind.value, row.basis.value): (row.rows, row.priced, row.eur)
        for row in db.execute(stmt)
    }


def main() -> int:
    args = sys.argv[1:]
    since: datetime | None = _UNSET
    if "--full" in args:
        since = None
    elif "--since" in args:
        since = datetime.now(UTC) - timedelta(days=int(args[args.index("--since") + 1]))

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.first_user_email))
        if user is None:
            print("no user found — run `make seed` first", file=sys.stderr)
            return 1

        summary = rebuild(db, user_id=user.id, since=since)

        mode = "full rebuild" if summary.full else f"incremental ({RECENT_WINDOW_DAYS}d)"
        print(f"{mode}: {summary.items} items -> {summary.entries_written} entries written")
        print(f"  ledger {summary.entries_before} -> {summary.entries_after} rows", end="")
        print(f" ({summary.entries_removed} stale removed)" if summary.entries_removed else "")

        if not summary.breakdown:
            print("  (empty — nothing has been logged yet)")
            return 0

        print(f"\n  {'domain':<8} {'kind':<9} {'basis':<9} {'rows':>6} {'priced':>7} {'euros':>10}")
        for key in sorted(summary.breakdown):
            rows, priced, eur = summary.breakdown[key]
            domain, kind, basis = key
            print(f"  {domain:<8} {kind:<9} {basis:<9} {rows:>6} {priced:>7} {float(eur):>10.2f}")

        # The number nobody asks for and everybody needs: how much of the money has an
        # outcome attached at all.
        spend = sum(v[2] for k, v in summary.breakdown.items() if k[1] == "spend")
        used = sum(v[2] for k, v in summary.breakdown.items() if k[1] == "used")
        waste = sum(v[2] for k, v in summary.breakdown.items() if k[1] == "waste")
        print(f"\n  unaccounted: {float(spend - used - waste):.2f} EUR of spend never resolved")
        print("  Run `make demo-clear` first if these numbers should exclude synthetic rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
