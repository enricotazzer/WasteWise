"""Derivation rules: what one item is worth, and how that value gets split.

These are the arithmetic tests. Everything downstream — the endpoints, the reconciler, the
dashboard — is aggregation over whatever this module decides, so a wrong number here is a
wrong number everywhere and nowhere obvious.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import factories
from sqlalchemy.orm import Session

from app.models.enums import (
    EntryBasis,
    EntryKind,
    EventSource,
    EventType,
    ItemStatus,
    SourceKind,
    Unit,
)
from app.services.savings.rules import derive

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _by_kind(drafts, kind: EntryKind) -> list:
    return [d for d in drafts if d.kind is kind]


def test_non_food_produces_no_rows_at_all(db: Session, seeded: dict) -> None:
    """R0. Not zero-amount rows — *no* rows.

    An unpriced food item does get a row, because its outcome is real and belongs in the
    coverage denominator. A detergent was never eligible for a food ledger, so putting it in
    the denominator would permanently depress a coverage figure it has nothing to do with.
    """
    item = factories.item(db, seeded["user"], seeded["detersivo"], unit_price_eur="3.00")
    factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    item.status = ItemStatus.WASTED

    assert derive(item, item.events) == []


def test_unpriced_food_lands_in_the_coverage_denominator(db: Session, seeded: dict) -> None:
    """R5. The outcome is known; only the money is missing. NULL, never 0.00 — a zero would
    sum silently and make thrown-away food look free."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur=None)
    event = factories.event(db, item, EventType.WASTED, occurred_at=NOW)
    item.status = ItemStatus.WASTED

    drafts = derive(item, [event])
    assert [d.kind for d in drafts] == [EntryKind.SPEND, EntryKind.WASTE]
    assert all(d.amount_eur is None for d in drafts)
    assert all(d.basis is EntryBasis.UNPRICED for d in drafts)


def test_partial_then_waste_charges_only_the_remainder(db: Session, seeded: dict) -> None:
    """R2, and the live bug this whole phase fixes.

    `/api/inventory/stats` charges the *full* €2.00 to eur_wasted here, because it reads the
    item's final status and knows nothing about the half that was eaten first. Half of a €2
    mozzarella was used and half was binned: €1.00 of waste, not €2.00.
    """
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="2.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    waste = factories.event(db, item, EventType.WASTED, occurred_at=NOW + timedelta(days=1))
    item.status = ItemStatus.WASTED

    drafts = derive(item, item.events)
    assert _by_kind(drafts, EntryKind.SPEND)[0].amount_eur == Decimal("2.00")
    assert _by_kind(drafts, EntryKind.USED)[0].amount_eur == Decimal("1.00")

    wasted = _by_kind(drafts, EntryKind.WASTE)
    assert len(wasted) == 1
    assert wasted[0].amount_eur == Decimal("1.00")
    assert wasted[0].source_kind is SourceKind.CONSUMPTION_EVENT
    assert wasted[0].source_id == waste.id


def test_the_walk_ignores_fraction_used_on_terminal_events(db: Session, seeded: dict) -> None:
    """`log_event` writes fraction_used=1.0 on a terminal event regardless of what was
    already partialled away, so the stored fractions here sum to 1.5. The walk is normative;
    the column is not. This is why no data fix was needed."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="2.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    terminal = factories.event(
        db, item, EventType.CONSUMED, fraction_used=1.0, occurred_at=NOW + timedelta(hours=1)
    )
    item.status = ItemStatus.CONSUMED

    assert sum(e.fraction_used for e in item.events) == 1.5  # the stored data really is wrong

    drafts = derive(item, item.events)
    partitions = [d for d in drafts if d.kind is not EntryKind.SPEND]
    assert sum(d.amount_eur for d in partitions) == Decimal("2.00")
    assert _by_kind(drafts, EntryKind.USED)[-1].source_id == terminal.id


def test_epsilon_remainder_is_folded_into_the_last_entry(db: Session, seeded: dict) -> None:
    """R2b. `log_event` auto-resolves an item below the epsilon *without writing an event*,
    so the walk ends with a sliver nothing accounts for. Left alone it would sit in
    unaccounted spend forever with no row to explain it."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="10.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    factories.event(
        db, item, EventType.PARTIAL, fraction_used=0.495, occurred_at=NOW + timedelta(hours=1)
    )
    item.status = ItemStatus.CONSUMED  # what log_event does at fraction_remaining <= 0.01
    item.fraction_remaining = 0.0

    drafts = derive(item, item.events)
    partitions = [d for d in drafts if d.kind is not EntryKind.SPEND]
    assert len(partitions) == 2
    # Without the fold the second entry would be €4.95 and €0.05 would be unaccounted for.
    assert partitions[-1].amount_eur == Decimal("5.00")
    assert sum(d.amount_eur for d in partitions) == Decimal("10.00")


def test_terminal_status_without_events_still_writes_one_outcome(db: Session, seeded: dict) -> None:
    """R2c. The demo seeder sets a status directly and so does the repurchase job's
    predecessor state. Sourced from the item, which cannot collide with its own SPEND
    because `kind` is part of the key."""
    resolved = NOW + timedelta(days=2)
    item = factories.item(
        db,
        seeded["user"],
        seeded["mozzarella"],
        unit_price_eur="1.50",
        status=ItemStatus.WASTED,
        resolved_at=resolved,
        fraction_remaining=0.0,
    )

    drafts = derive(item, [])
    assert [d.kind for d in drafts] == [EntryKind.SPEND, EntryKind.WASTE]
    outcome = drafts[1]
    assert outcome.source_kind is SourceKind.INVENTORY_ITEM
    assert outcome.source_id == item.id
    assert outcome.amount_eur == Decimal("1.50")
    assert outcome.occurred_on == resolved.date()
    assert outcome.method == "item_status"


def test_repurchase_inferred_outcomes_are_written_and_flagged(db: Session, seeded: dict) -> None:
    """R3. Dropping them would inflate unaccounted spend and hide that a job, not a human,
    decided this item was finished. The SPEND is not flagged: money left the household
    whoever later closed the item."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.00")
    event = factories.event(
        db, item, EventType.CONSUMED, occurred_at=NOW, source=EventSource.REPURCHASE_INFERRED
    )
    item.status = ItemStatus.CONSUMED

    drafts = derive(item, [event])
    assert drafts[0].kind is EntryKind.SPEND and drafts[0].outcome_inferred is False
    assert drafts[1].kind is EntryKind.USED and drafts[1].outcome_inferred is True


def test_food_never_writes_an_avoided_row(db: Session, seeded: dict) -> None:
    """R6, the honesty rule. Eating food you paid for is the ordinary case, not a saving.

    An AVOIDED row per consumed item would render as "€412 saved this month" when it means
    "€412 of groceries got eaten" — a headline that is enormous, meaningless, and would
    discredit the real waste figure sitting next to it.
    """
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.00")
    event = factories.event(db, item, EventType.CONSUMED, occurred_at=NOW)
    item.status = ItemStatus.CONSUMED

    drafts = derive(item, [event])
    assert EntryKind.AVOIDED not in {d.kind for d in drafts}
    assert [d.kind for d in drafts] == [EntryKind.SPEND, EntryKind.USED]


def test_mass_is_recorded_only_for_real_masses(db: Session, seeded: dict) -> None:
    """R7. A litre of milk is roughly a kilo and three peppers are not; guessing either puts
    fabricated kilos into the one figure comparable to national waste statistics."""
    kg = factories.item(db, seeded["user"], seeded["pasta"], quantity="0.5", unit=Unit.KG)
    grams = factories.item(db, seeded["user"], seeded["pasta"], quantity="250", unit=Unit.G)
    pieces = factories.item(db, seeded["user"], seeded["mozzarella"], quantity="3", unit=Unit.PZ)

    assert derive(kg, [])[0].mass_kg == Decimal("0.500")
    assert derive(grams, [])[0].mass_kg == Decimal("0.250")
    assert derive(pieces, [])[0].mass_kg is None


def test_partitions_sum_exactly_to_the_spend_they_came_from(db: Session, seeded: dict) -> None:
    """Rounding residue is absorbed by the closing entry. Splitting €1.00 three ways
    otherwise leaves a cent belonging to nobody, which surfaces as `unaccounted` on an item
    that was in fact completely accounted for."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.00")
    for index, fraction in enumerate((0.33, 0.33)):
        factories.event(
            db,
            item,
            EventType.PARTIAL,
            fraction_used=fraction,
            occurred_at=NOW + timedelta(hours=index),
        )
    factories.event(db, item, EventType.WASTED, occurred_at=NOW + timedelta(hours=3))
    item.status = ItemStatus.WASTED

    drafts = derive(item, item.events)
    spend, *partitions = drafts
    assert sum(d.amount_eur for d in partitions) == spend.amount_eur == Decimal("1.00")


def test_spend_comes_first_and_every_partition_points_at_it(db: Session, seeded: dict) -> None:
    """The writer resolves `parent_entry_id` from this ordering. A partition written before
    its SPEND would get a NULL parent, and `unaccounted` — SPEND minus its partitions —
    would lose its base without any error being raised."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.00")
    factories.event(db, item, EventType.PARTIAL, fraction_used=0.5, occurred_at=NOW)
    factories.event(db, item, EventType.CONSUMED, occurred_at=NOW + timedelta(hours=1))
    item.status = ItemStatus.CONSUMED

    spend, *partitions = derive(item, item.events)
    assert spend.kind is EntryKind.SPEND
    assert spend.parent_key is None
    assert all(d.parent_key == spend.key for d in partitions)


def test_an_active_item_leaves_its_value_unaccounted(db: Session, seeded: dict) -> None:
    """No outcome yet means no partition — the SPEND stands alone. That gap is the point:
    it is what the save rate, defined over resolved items only, silently excludes."""
    item = factories.item(db, seeded["user"], seeded["mozzarella"], unit_price_eur="1.29")
    drafts = derive(item, [])
    assert [d.kind for d in drafts] == [EntryKind.SPEND]
