"""How one inventory item becomes money facts. Pure functions: no Session, no I/O.

Everything that turns an item into euros lives here, and both writers use it — the
synchronous one on the request path (`writer.sync_item`) and the batch reconciler
(`jobs/savings.py`). Two implementations of this arithmetic would agree the day they were
written and disagree within months, at which point the dashboard and the backfill would
report different totals for the same week and neither could be trusted.

The rules, in the order they apply:

  R0  Only food is eligible. Non-food produces *zero rows*, not zero-amount rows.
  R1  Every eligible item writes one SPEND at its acquisition date.
  R2  Events partition that SPEND by the fraction of the item they account for.
  R2b A sub-epsilon remainder on a resolved item is folded into the last entry.
  R2c A terminal status with no events at all still writes one outcome entry.
  R3  Machine-inferred outcomes are written, and flagged.
  R4  PARTIAL always means *used*.
  R5  No price -> the row is still written, with amount_eur NULL.
  R6  The food domain never writes AVOIDED. See below.
  R7  mass_kg only where the unit is a real mass.

R6, the honesty rule, is the single most important constraint in this project
--------------------------------------------------------------------------------
Eating food you paid for is the ordinary case, not a saving. If every consumed item wrote
an AVOIDED row, the dashboard would print "€412 saved this month" when what actually
happened was "€412 of groceries got eaten" — a number that is both enormous and completely
meaningless, and one that would quietly discredit the real measurement sitting next to it.

So food measures **waste**: euros that bought nothing. Plus a count-based save rate. That
is a smaller, duller number, and it is the one that is true.

AVOIDED exists in the enum because the Utilities domain will genuinely measure a
counterfactual later — a bill against a modelled baseline is a real avoided cost, with a
stated method and a MODELLED basis. Food has no such baseline and must never fake one.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.models.enums import (
    EntryBasis,
    EntryKind,
    EventSource,
    EventType,
    ItemStatus,
    SavingsDomain,
    SourceKind,
    Unit,
)
from app.models.inventory import RESOLVE_EPSILON, ConsumptionEvent, InventoryItem

CENTS = Decimal("0.01")
GRAMS = Decimal("0.001")

# Same threshold `log_event` resolves an item on, as a Decimal. Imported, never re-declared:
# if the two ever differed, every item auto-resolved by the write path would leave a sliver
# of permanently unaccounted spend that no query could explain.
EPSILON = Decimal(str(RESOLVE_EPSILON))

TERMINAL_STATUSES = (ItemStatus.CONSUMED, ItemStatus.WASTED)

# (source_kind, source_id, kind) — the unique constraint, and how a draft addresses the row
# it will become. Parents are referenced by key because a draft has no id until it is
# written, and the writer is the only thing that knows ids.
EntryKey = tuple[SourceKind, int, EntryKind]


@dataclass(frozen=True, slots=True)
class EntryDraft:
    """One row-to-be. Deliberately not a SavingsEntry: derivation runs without a Session,
    and the writer decides whether this becomes an INSERT or an UPDATE."""

    domain: SavingsDomain
    kind: EntryKind
    basis: EntryBasis
    source_kind: SourceKind
    source_id: int
    occurred_on: date
    occurred_at: datetime | None
    amount_eur: Decimal | None
    mass_kg: Decimal | None
    category_code: str | None
    label: str | None
    is_demo: bool
    outcome_inferred: bool
    method: str
    inventory_item_id: int | None
    parent_key: EntryKey | None = None

    @property
    def key(self) -> EntryKey:
        return (self.source_kind, self.source_id, self.kind)


@dataclass(frozen=True, slots=True)
class _Charge:
    """An intermediate step of the fraction walk: what share of the item this outcome
    accounts for, before it is priced."""

    kind: EntryKind
    share: Decimal
    event: ConsumptionEvent | None


def item_value(item: InventoryItem) -> Decimal | None:
    """What the item cost. None when no price was typed — which is the common case, since
    prices are optional and hand-entered."""
    if item.unit_price_eur is None:
        return None
    return (item.unit_price_eur * item.quantity).quantize(CENTS, rounding=ROUND_HALF_UP)


def item_mass_kg(item: InventoryItem) -> Decimal | None:
    """R7. Only kg and g are a mass. A litre of milk is roughly a kilo and three peppers are
    not, and guessing either would put fabricated kilos into the one figure that is supposed
    to be comparable to national food-waste statistics."""
    if item.unit is Unit.KG:
        return item.quantity.quantize(GRAMS)
    if item.unit is Unit.G:
        return (item.quantity / 1000).quantize(GRAMS)
    return None


def _event_order(event: ConsumptionEvent) -> tuple[datetime, int]:
    # `or 0` covers an event added to the session but not yet flushed; the writer flushes
    # first, so in practice this only matters to callers testing `derive` in isolation.
    return (event.occurred_at, event.id or 0)


def _walk(item: InventoryItem, events: Sequence[ConsumptionEvent]) -> tuple[list[_Charge], Decimal]:
    """R2: partition the item by walking its events in time order, carrying a remainder.

    The walk is normative and the stored `fraction_used` on a terminal event is ignored:
    `log_event` writes 1.0 there regardless of what was already partialled away, so the
    stored fractions of a half-used-then-binned item sum to 1.5. Deriving the share from the
    remainder instead of trusting the column means no data fix is needed and none of the
    historical rows are wrong — they just aren't the source of truth for money.

    This is also where a live bug gets fixed. `/api/inventory/stats` charges the *whole*
    item value to `eur_wasted` whenever the item ends WASTED, even if half of it was eaten
    first. Invisible in demo data, because the seeder emits no partials; unavoidable as soon
    as anyone taps "Usato a metà" and later "Buttato".
    """
    charges: list[_Charge] = []
    remaining = Decimal("1")

    for event in sorted(events, key=_event_order):
        if remaining <= 0:
            break

        if event.event_type is EventType.PARTIAL:
            # R4: PARTIAL is *used*, always. The button says "Usato a metà" and posts
            # fraction_used: 0.5. If a "half thrown away" affordance is ever added it needs
            # its own EventType — re-reading this one from context (say, because the item
            # later ends WASTED) would silently rewrite history for every past partial.
            #
            # Decimal(str(...)) is mandatory: fraction_used is a Float column and
            # Decimal * float raises TypeError.
            share = min(Decimal(str(event.fraction_used)), remaining)
            kind, terminal = EntryKind.USED, False
        elif event.event_type is EventType.CONSUMED:
            share, kind, terminal = remaining, EntryKind.USED, True
        else:  # WASTED
            share, kind, terminal = remaining, EntryKind.WASTE, True

        if share > 0:
            charges.append(_Charge(kind=kind, share=share, event=event))
            remaining -= share
        if terminal:
            break

    return charges, remaining


def derive(item: InventoryItem, events: Sequence[ConsumptionEvent]) -> list[EntryDraft]:
    """Every money fact this item implies, ordered so that the SPEND comes first.

    Order matters to the writer: a partition's `parent_entry_id` points at the SPEND, so the
    SPEND has to be written and flushed before them or the `unaccounted` figure — SPEND
    minus its partitions — silently loses its base.
    """
    category = item.product.category
    # R0. A detergent was never eligible for the food ledger, so it produces nothing at all.
    # Contrast R5, where an *unpriced food* item does get a row: that item belongs in the
    # coverage denominator, because the outcome is real and only the price is missing. A
    # zero-amount non-food row would instead sit in the denominator forever, permanently
    # depressing a coverage figure it has no business appearing in.
    if not category.is_food:
        return []

    value = item_value(item)
    mass = item_mass_kg(item)
    # R5. The outcome is known either way; only the money is missing.
    basis = EntryBasis.MEASURED if value is not None else EntryBasis.UNPRICED
    label = item.product.name_it[:160]

    spend = EntryDraft(
        domain=SavingsDomain.FOOD,
        kind=EntryKind.SPEND,
        basis=basis,
        source_kind=SourceKind.INVENTORY_ITEM,
        source_id=item.id,
        occurred_on=item.acquired_at,
        occurred_at=None,  # an acquisition has a date and no meaningful time of day
        amount_eur=value,
        mass_kg=mass,
        category_code=category.code,
        label=label,
        is_demo=item.is_demo,
        outcome_inferred=False,  # money left the household whoever later closed the item
        method="acquisition",
        inventory_item_id=item.id,
    )
    drafts = [spend]

    charges, remaining = _walk(item, events)

    # R2b. `log_event` resolves an item once the remainder drops below epsilon and writes no
    # event for that last sliver. Folding it into the last entry is what keeps a fully
    # resolved item's partitions summing to its SPEND; leaving it would park a few cents in
    # `unaccounted` for every partially-logged item, forever, with nothing to point at.
    if charges and Decimal(0) < remaining <= EPSILON and item.status in TERMINAL_STATUSES:
        charges[-1] = replace(charges[-1], share=charges[-1].share + remaining)
        remaining = Decimal(0)

    # R2c. Terminal status, no events: the demo seeder does this, and so does any path that
    # sets a status directly. One entry for the whole item, sourced from the item itself. No
    # key collision with the SPEND above because `kind` differs — which is exactly why `kind`
    # is in the unique constraint.
    if not charges and item.status in TERMINAL_STATUSES:
        kind = EntryKind.USED if item.status is ItemStatus.CONSUMED else EntryKind.WASTE
        charges.append(_Charge(kind=kind, share=remaining, event=None))
        remaining = Decimal(0)

    fully_partitioned = remaining == 0
    allocated_eur = Decimal("0")
    allocated_kg = Decimal("0")

    for index, charge in enumerate(charges):
        # The last charge of a fully resolved item absorbs the rounding residue instead of
        # being quantized independently. Splitting €1.00 three ways otherwise leaves a cent
        # that never belonged to anyone and shows up as `unaccounted` on an item that was
        # completely accounted for.
        is_closing = fully_partitioned and index == len(charges) - 1

        amount: Decimal | None = None
        if value is not None:
            amount = (
                value - allocated_eur
                if is_closing
                else (value * charge.share).quantize(CENTS, rounding=ROUND_HALF_UP)
            )
            allocated_eur += amount

        charge_mass: Decimal | None = None
        if mass is not None:
            charge_mass = (
                mass - allocated_kg
                if is_closing
                else (mass * charge.share).quantize(GRAMS, rounding=ROUND_HALF_UP)
            )
            allocated_kg += charge_mass

        event = charge.event
        if event is not None:
            source_kind, source_id = SourceKind.CONSUMPTION_EVENT, event.id
            occurred_at = event.occurred_at
            occurred_on = event.occurred_at.date()
            # R3. Inference is recorded, not hidden. These rows are excluded from every
            # headline by default; deleting them instead would inflate unaccounted spend and
            # erase the fact that a job, not the user, decided this item was finished.
            inferred = event.source is EventSource.REPURCHASE_INFERRED
            method = "fraction_walk"
        else:
            source_kind, source_id = SourceKind.INVENTORY_ITEM, item.id
            occurred_at = item.resolved_at
            occurred_on = item.resolved_at.date() if item.resolved_at else item.acquired_at
            inferred = False
            method = "item_status"

        drafts.append(
            EntryDraft(
                domain=SavingsDomain.FOOD,
                kind=charge.kind,
                basis=basis,
                source_kind=source_kind,
                source_id=source_id,
                occurred_on=occurred_on,
                occurred_at=occurred_at,
                amount_eur=amount,
                mass_kg=charge_mass,
                category_code=category.code,
                label=label,
                is_demo=item.is_demo,
                outcome_inferred=inferred,
                method=method,
                inventory_item_id=item.id,
                parent_key=spend.key,
            )
        )

    # R6 is enforced by omission, and this is the enforcement point: nothing above can emit
    # EntryKind.AVOIDED for SavingsDomain.FOOD.
    return drafts
