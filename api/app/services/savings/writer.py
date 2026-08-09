"""Writing derived entries back, as a *set* rather than an append.

`sync_item` recomputes everything one item implies and makes the table match: upsert what
should exist, delete what shouldn't any more. That is the property that makes a corrected
outcome shrink the ledger instead of growing it — an item that was recorded as consumed and
is later logged as wasted must end up with one WASTE row, not a USED row plus a WASTE row
quietly double-counting the same €2.50.

It runs synchronously, inside the caller's transaction, from `create_item`, `add_from_scan`
and `log_event`. Not a background task, for two reasons: a failed ledger write must roll the
event back rather than leave the two out of step, and tapping "buttato" has to move the
number on the screen now, not after the next job runs.
"""

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.inventory import ConsumptionEvent, InventoryItem
from app.models.savings import SavingsEntry
from app.services.savings.rules import EntryDraft, EntryKey, derive

# Everything that a re-derivation can legitimately change. The conflict key itself
# (source_kind, source_id, kind) is excluded because it is what identified the row, and
# created_at because a corrected amount is not a new fact.
_UPDATABLE = (
    "domain",
    "basis",
    "amount_eur",
    "mass_kg",
    "occurred_on",
    "occurred_at",
    "category_code",
    "label",
    "parent_entry_id",
    "inventory_item_id",
    "is_demo",
    "outcome_inferred",
    "method",
    "user_id",
)


class SyncCounts(NamedTuple):
    """Rows written and rows dropped. The reconciler reports both: `deleted` is how many
    entries a correction made stale, which is the only visible sign that replacing the set —
    rather than appending to it — is doing anything."""

    written: int = 0
    deleted: int = 0

    def __add__(self, other: "SyncCounts") -> "SyncCounts":
        return SyncCounts(self.written + other.written, self.deleted + other.deleted)


def _row(draft: EntryDraft, *, user_id: int, parent_id: int | None) -> dict:
    return {
        "user_id": user_id,
        "domain": draft.domain,
        "kind": draft.kind,
        "basis": draft.basis,
        "amount_eur": draft.amount_eur,
        "mass_kg": draft.mass_kg,
        "occurred_on": draft.occurred_on,
        "occurred_at": draft.occurred_at,
        "source_kind": draft.source_kind,
        "source_id": draft.source_id,
        "category_code": draft.category_code,
        "label": draft.label,
        "parent_entry_id": parent_id,
        "inventory_item_id": draft.inventory_item_id,
        "is_demo": draft.is_demo,
        "outcome_inferred": draft.outcome_inferred,
        "method": draft.method,
    }


def _upsert(db: Session, values: dict) -> int:
    stmt = pg_insert(SavingsEntry).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_savings_source",
        set_={col: stmt.excluded[col] for col in _UPDATABLE} | {"updated_at": func.now()},
    ).returning(SavingsEntry.id)
    return db.execute(stmt).scalar_one()


def _apply(db: Session, *, item_id: int, user_id: int, drafts: Sequence[EntryDraft]) -> SyncCounts:
    """Make the rows for one item exactly `drafts`."""
    ids_by_key: dict[EntryKey, int] = {}

    for draft in drafts:
        parent_id: int | None = None
        if draft.parent_key is not None:
            parent_id = ids_by_key.get(draft.parent_key)
            if parent_id is None:
                # `derive` returns the SPEND first precisely so this cannot happen. If it
                # ever does, failing loudly beats writing a NULL parent: `unaccounted` is
                # SPEND minus its partitions, and an orphaned partition would make every
                # euro of it disappear without a trace.
                raise RuntimeError(
                    f"savings draft {draft.key} references parent {draft.parent_key} "
                    "which has not been written yet"
                )
        ids_by_key[draft.key] = _upsert(db, _row(draft, user_id=user_id, parent_id=parent_id))

    # Delete by id rather than by a NOT IN over the composite key: the key columns hold enum
    # *names* and building a tuple_() IN over three enum types is exactly the kind of query
    # that silently matches nothing.
    stale = [
        row.id
        for row in db.execute(
            select(
                SavingsEntry.id,
                SavingsEntry.source_kind,
                SavingsEntry.source_id,
                SavingsEntry.kind,
            ).where(SavingsEntry.inventory_item_id == item_id)
        )
        if (row.source_kind, row.source_id, row.kind) not in ids_by_key
    ]
    if stale:
        db.execute(
            delete(SavingsEntry)
            .where(SavingsEntry.id.in_(stale))
            .execution_options(synchronize_session=False)
        )

    return SyncCounts(written=len(drafts), deleted=len(stale))


def sync_item(db: Session, item: InventoryItem) -> SyncCounts:
    """Recompute this item's ledger rows. Call inside the caller's transaction, before commit.

    The flush is load-bearing: the session runs with autoflush off, so an event added by
    `log_event` a few lines earlier would otherwise be invisible to the SELECT below and the
    ledger would be built from the state *before* the tap that triggered it.
    """
    db.flush()
    events = list(
        db.scalars(
            select(ConsumptionEvent)
            .where(ConsumptionEvent.inventory_item_id == item.id)
            .order_by(ConsumptionEvent.occurred_at, ConsumptionEvent.id)
        )
    )
    return _apply(db, item_id=item.id, user_id=item.user_id, drafts=derive(item, events))


def sync_items(db: Session, items: Sequence[InventoryItem]) -> SyncCounts:
    """Batch form for the reconciler. Same derivation, one events query instead of N."""
    db.flush()
    if not items:
        return SyncCounts()

    by_item: dict[int, list[ConsumptionEvent]] = {item.id: [] for item in items}
    for event in db.scalars(
        select(ConsumptionEvent)
        .where(ConsumptionEvent.inventory_item_id.in_(list(by_item)))
        .order_by(ConsumptionEvent.occurred_at, ConsumptionEvent.id)
    ):
        by_item[event.inventory_item_id].append(event)

    counts = SyncCounts()
    for item in items:
        counts += _apply(
            db,
            item_id=item.id,
            user_id=item.user_id,
            drafts=derive(item, by_item[item.id]),
        )
    return counts
