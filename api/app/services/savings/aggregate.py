"""The read side of the ledger.

Everything here is SQLAlchemy Core against `savings_entries`. **Never `text()`**, and never a
hand-written string literal for an enum value: the enum columns are `native_enum=False`, so
Postgres holds the member *name*. `WHERE kind = 'waste'` matches zero rows and returns a
perfectly plausible-looking 0.00, which is the worst possible failure mode for a number
someone is about to believe. Going through the mapped column renders `kind = 'WASTE'`
automatically and correctly.

Division of labour: SQL does GROUP BY / SUM / COUNT / COUNT(...) FILTER, because those are
what an index can serve. Ratios, month-over-month deltas and zero-filling happen in Python,
because they are presentation decisions that have to distinguish "zero" from "unknown" —
something SQL aggregates express badly and review even worse.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import ColumnElement, Date, cast, func, select
from sqlalchemy.orm import Session

from app.models.enums import EntryKind, ItemStatus, SavingsDomain
from app.models.inventory import InventoryItem
from app.models.savings import SavingsEntry
from app.schemas.stats import (
    BasisRow,
    ByCategoryResponse,
    CategoryBucket,
    CategoryPoint,
    DomainTotal,
    MassFigure,
    MoneyFigure,
    StatsOverview,
    TimeseriesPoint,
    TimeseriesResponse,
)

Bucket = Literal["week", "month"]

# The aggregates every slice needs, defined once so the column layout cannot drift between
# the four queries below.
_MEASURES = (
    func.count().label("entries"),
    func.count(SavingsEntry.amount_eur).label("priced_entries"),
    func.sum(SavingsEntry.amount_eur).label("eur"),
    func.count(SavingsEntry.mass_kg).label("massed_entries"),
    func.sum(SavingsEntry.mass_kg).label("kg"),
)


def _filters(
    *,
    user_id: int,
    domain: SavingsDomain | None = None,
    include_demo: bool = False,
    include_inferred: bool = False,
) -> list[ColumnElement[bool]]:
    """The single place the exclusions are decided, so no endpoint can quietly forget one.

    Demo rows are out by default because synthetic outcomes must never reach a reported
    number. Inferred rows are out by default because a repurchase-inferred outcome is a
    job's guess, and a headline should be made of what a human actually observed — they stay
    in the table, and turning them on is a query parameter away.
    """
    clauses: list[ColumnElement[bool]] = [SavingsEntry.user_id == user_id]
    if domain is not None:
        clauses.append(SavingsEntry.domain == domain)
    if not include_demo:
        clauses.append(SavingsEntry.is_demo.is_(False))
    if not include_inferred:
        clauses.append(SavingsEntry.outcome_inferred.is_(False))
    return clauses


def _money(rows: Sequence[BasisRow]) -> MoneyFigure:
    return MoneyFigure.from_rows(rows) if rows else MoneyFigure.empty()


def bucket_start(day: date, bucket: Bucket) -> date:
    """Python's copy of `date_trunc`. Monday-anchored weeks, matching Postgres' ISO week: if
    these two disagreed, the zero-filled buckets would sit days away from the data they are
    supposed to be filling around."""
    if bucket == "month":
        return day.replace(day=1)
    return day - timedelta(days=day.weekday())


def _previous_bucket(start: date, bucket: Bucket) -> date:
    if bucket == "month":
        return (start - timedelta(days=1)).replace(day=1)
    return start - timedelta(days=7)


def _next_bucket(start: date, bucket: Bucket) -> date:
    if bucket == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start + timedelta(days=7)


def _bucket_range(*, end: date, periods: int, bucket: Bucket) -> list[date]:
    starts = [bucket_start(end, bucket)]
    while len(starts) < periods:
        starts.insert(0, _previous_bucket(starts[0], bucket))
    return starts


def demo_entry_count(db: Session, user_id: int) -> int:
    """Reported on every response whether or not demo rows were included — a caller has to be
    able to tell that synthetic data exists even when it was filtered out."""
    return (
        db.scalar(
            select(func.count())
            .select_from(SavingsEntry)
            .where(SavingsEntry.user_id == user_id, SavingsEntry.is_demo.is_(True))
        )
        or 0
    )


def _inventory_state(db: Session, *, user_id: int, include_demo: bool, today: date) -> dict:
    """Counts of *items*, not money. Still reads `inventory_items` on purpose: what is in the
    kitchen right now is a fact about the kitchen, and an item nobody has resolved yet has no
    ledger outcome to be counted from."""
    active = (InventoryItem.status.in_([ItemStatus.ACTIVE, ItemStatus.PARTIAL]),)
    clauses = [InventoryItem.user_id == user_id]
    if not include_demo:
        clauses.append(InventoryItem.is_demo.is_(False))

    row = db.execute(
        select(
            func.count().filter(*active).label("active_items"),
            func.count()
            .filter(
                *active,
                InventoryItem.expiry_date >= today,
                InventoryItem.expiry_date <= today + timedelta(days=3),
            )
            .label("expiring_within_3d"),
            func.count().filter(*active, InventoryItem.expiry_date < today).label("expired"),
            func.count().filter(InventoryItem.status == ItemStatus.CONSUMED).label("consumed"),
            func.count().filter(InventoryItem.status == ItemStatus.WASTED).label("wasted"),
        ).where(*clauses)
    ).one()

    return {
        "active_items": row.active_items,
        "expiring_within_3d": row.expiring_within_3d,
        "expired_still_active": row.expired,
        "consumed_total": row.consumed,
        "wasted_total": row.wasted,
        "resolved_total": row.consumed + row.wasted,
    }


def _unaccounted(spend: MoneyFigure, used: MoneyFigure, waste: MoneyFigure) -> MoneyFigure:
    """SPEND minus its partitions. Derived, so it borrows the SPEND base's counts and
    coverage — there is no row in the table representing "money never resolved"."""
    eur: Decimal | None = None
    if spend.eur is not None:
        eur = spend.eur - (used.eur or Decimal("0")) - (waste.eur or Decimal("0"))
    return MoneyFigure(
        eur=eur,
        entries=spend.entries,
        priced_entries=spend.priced_entries,
        coverage=spend.coverage,
        basis=spend.basis,
    )


def overview(
    db: Session,
    *,
    user_id: int,
    include_demo: bool = False,
    include_inferred: bool = False,
    today: date | None = None,
) -> StatsOverview:
    """Totals by domain and kind, plus the kitchen counters."""
    today = today or date.today()

    rows = db.execute(
        select(SavingsEntry.domain, SavingsEntry.kind, SavingsEntry.basis, *_MEASURES)
        .where(
            *_filters(user_id=user_id, include_demo=include_demo, include_inferred=include_inferred)
        )
        .group_by(SavingsEntry.domain, SavingsEntry.kind, SavingsEntry.basis)
    ).all()

    by_domain: dict[tuple[SavingsDomain, EntryKind], list[BasisRow]] = {}
    by_kind: dict[EntryKind, list[BasisRow]] = {}
    # (entries, entries carrying a mass, kg) per domain+kind, kept alongside the money rather
    # than derived from it: mass coverage is a different fraction of the same rows.
    mass: dict[tuple[SavingsDomain, EntryKind], list] = {}

    for row in rows:
        money: BasisRow = (row.basis, row.entries, row.priced_entries, row.eur)
        by_domain.setdefault((row.domain, row.kind), []).append(money)
        by_kind.setdefault(row.kind, []).append(money)
        bucket = mass.setdefault((row.domain, row.kind), [0, 0, Decimal("0")])
        bucket[0] += row.entries
        bucket[1] += row.massed_entries
        bucket[2] += row.kg or Decimal("0")

    def mass_figure(domain: SavingsDomain, kind: EntryKind) -> MassFigure:
        entries, massed, kg = mass.get((domain, kind), [0, 0, Decimal("0")])
        return MassFigure.build(entries=entries, measured_entries=massed, kg=kg)

    domains: list[DomainTotal] = []
    for domain in sorted({key[0] for key in by_domain}, key=lambda d: d.value):
        spend = _money(by_domain.get((domain, EntryKind.SPEND), []))
        used = _money(by_domain.get((domain, EntryKind.USED), []))
        waste = _money(by_domain.get((domain, EntryKind.WASTE), []))
        domains.append(
            DomainTotal(
                domain=domain,
                spend=spend,
                used=used,
                waste=waste,
                avoided=_money(by_domain.get((domain, EntryKind.AVOIDED), [])),
                unaccounted=_unaccounted(spend, used, waste),
                waste_kg=mass_figure(domain, EntryKind.WASTE),
            )
        )

    # The headline spans every domain. Only FOOD writes rows today; once Utilities lands its
    # USED/WASTE will mean something different enough that this needs splitting, and the
    # per-domain breakdown above is already the honest view.
    all_used = _money(by_kind.get(EntryKind.USED, []))
    all_waste = _money(by_kind.get(EntryKind.WASTE, []))
    all_spend = _money(by_kind.get(EntryKind.SPEND, []))

    waste_mass = [0, 0, Decimal("0")]
    for (_, kind), totals in mass.items():
        if kind is EntryKind.WASTE:
            waste_mass = [a + b for a, b in zip(waste_mass, totals, strict=True)]

    resolved_entries = all_used.entries + all_waste.entries
    priced_outcomes = all_used.priced_entries + all_waste.priced_entries
    outcome_eur = (all_used.eur or Decimal("0")) + (all_waste.eur or Decimal("0"))

    return StatsOverview(
        generated_at=datetime.now(UTC),
        demo_included=include_demo,
        demo_entries=demo_entry_count(db, user_id),
        **_inventory_state(db, user_id=user_id, include_demo=include_demo, today=today),
        save_rate=(all_used.entries / resolved_entries) if resolved_entries else None,
        save_rate_eur=(
            float((all_used.eur or Decimal("0")) / outcome_eur) if outcome_eur > 0 else None
        ),
        save_rate_eur_coverage=(priced_outcomes / resolved_entries) if resolved_entries else None,
        wasted=all_waste,
        used=all_used,
        spend=all_spend,
        wasted_kg=MassFigure.build(
            entries=waste_mass[0], measured_entries=waste_mass[1], kg=waste_mass[2]
        ),
        domains=domains,
    )


def _bucket_eur(row: Any | None) -> Decimal | None:
    """Three-way on purpose: a real total, None where the bucket holds outcomes that carried
    no price, and a filled 0.00 where the bucket is empty. `*_entries == 0` tells the last
    case from the first."""
    if row is None:
        return Decimal("0.00")
    return row.eur.quantize(Decimal("0.01")) if row.priced_entries else None


def _bucket_kg(row: Any | None) -> Decimal | None:
    if row is None:
        return Decimal("0.000")
    return row.kg.quantize(Decimal("0.001")) if row.massed_entries else None


def timeseries(
    db: Session,
    *,
    user_id: int,
    bucket: Bucket = "week",
    domain: SavingsDomain | None = None,
    periods: int = 12,
    include_demo: bool = False,
    include_inferred: bool = False,
    today: date | None = None,
) -> TimeseriesResponse:
    """Waste over time in € and kg, with a sparse per-category breakdown, plus the save-rate
    inputs per bucket.

    Buckets come back contiguous even where nothing was logged: a line chart interpolates
    across a missing point, drawing a straight line through the household's worst week.
    """
    today = today or date.today()
    starts = _bucket_range(end=today, periods=periods, bucket=bucket)
    window_start = starts[0]
    window_end = _next_bucket(starts[-1], bucket) - timedelta(days=1)

    trunc = cast(func.date_trunc(bucket, SavingsEntry.occurred_on), Date).label("bucket")
    base = [
        *_filters(
            user_id=user_id,
            domain=domain,
            include_demo=include_demo,
            include_inferred=include_inferred,
        ),
        SavingsEntry.occurred_on >= window_start,
    ]

    rows = db.execute(
        select(trunc, SavingsEntry.kind, *_MEASURES).where(*base).group_by(trunc, SavingsEntry.kind)
    ).all()
    indexed = {(row.bucket, row.kind): row for row in rows}

    points = [
        TimeseriesPoint(
            bucket_start=start,
            waste_eur=_bucket_eur(indexed.get((start, EntryKind.WASTE))),
            used_eur=_bucket_eur(indexed.get((start, EntryKind.USED))),
            spend_eur=_bucket_eur(indexed.get((start, EntryKind.SPEND))),
            waste_kg=_bucket_kg(indexed.get((start, EntryKind.WASTE))),
            waste_entries=_entries(indexed.get((start, EntryKind.WASTE))),
            used_entries=_entries(indexed.get((start, EntryKind.USED))),
            spend_entries=_entries(indexed.get((start, EntryKind.SPEND))),
            waste_priced_entries=_priced(indexed.get((start, EntryKind.WASTE))),
        )
        for start in starts
    ]

    category_rows = db.execute(
        select(trunc, SavingsEntry.category_code, *_MEASURES)
        .where(*base, SavingsEntry.kind == EntryKind.WASTE)
        .group_by(trunc, SavingsEntry.category_code)
        .order_by(trunc, SavingsEntry.category_code)
    ).all()

    entries = sum(row.entries for row in category_rows)
    priced = sum(row.priced_entries for row in category_rows)
    massed = sum(row.massed_entries for row in category_rows)

    return TimeseriesResponse(
        bucket=bucket,
        start=window_start,
        end=window_end,
        domain=domain,
        demo_included=include_demo,
        demo_entries=demo_entry_count(db, user_id),
        points=points,
        by_category=[
            CategoryPoint(
                bucket_start=row.bucket,
                category_code=row.category_code or "",
                waste_eur=_bucket_eur(row),
                waste_kg=_bucket_kg(row),
                waste_entries=row.entries,
            )
            for row in category_rows
        ],
        price_coverage=(priced / entries) if entries else None,
        # A different fraction of the same rows — pieces and litres have no mass at all.
        mass_coverage=(massed / entries) if entries else None,
    )


def _entries(row: Any | None) -> int:
    return row.entries if row is not None else 0


def _priced(row: Any | None) -> int:
    return row.priced_entries if row is not None else 0


def _delta(current: Decimal | None, previous: Decimal | None) -> float | None:
    """None whenever the comparison cannot be made — no prior bucket, or either side
    unpriced. A 0.0 there would claim the category was unchanged, which is a real finding
    and not one we have."""
    if current is None or previous is None or previous == 0:
        return None
    return float((current - previous) / previous)


def by_category(
    db: Session,
    *,
    user_id: int,
    bucket: Bucket = "month",
    domain: SavingsDomain | None = None,
    periods: int = 6,
    include_demo: bool = False,
    include_inferred: bool = False,
    today: date | None = None,
) -> ByCategoryResponse:
    """Spend and waste per category, bucket over bucket.

    Sparse: a category that saw nothing in a bucket gets no row. Zero-filling this one would
    be almost all zeros — there are dozens of categories and a household buys from a handful.
    """
    today = today or date.today()
    starts = _bucket_range(end=today, periods=periods, bucket=bucket)
    window_start = starts[0]
    window_end = _next_bucket(starts[-1], bucket) - timedelta(days=1)

    trunc = cast(func.date_trunc(bucket, SavingsEntry.occurred_on), Date).label("bucket")
    rows = db.execute(
        select(trunc, SavingsEntry.category_code, SavingsEntry.kind, SavingsEntry.basis, *_MEASURES)
        .where(
            *_filters(
                user_id=user_id,
                domain=domain,
                include_demo=include_demo,
                include_inferred=include_inferred,
            ),
            SavingsEntry.occurred_on >= window_start,
            SavingsEntry.kind.in_([EntryKind.SPEND, EntryKind.WASTE]),
        )
        .group_by(trunc, SavingsEntry.category_code, SavingsEntry.kind, SavingsEntry.basis)
    ).all()

    grouped: dict[tuple[str, date], dict[EntryKind, list[BasisRow]]] = {}
    for row in rows:
        key = (row.category_code or "", row.bucket)
        grouped.setdefault(key, {}).setdefault(row.kind, []).append(
            (row.basis, row.entries, row.priced_entries, row.eur)
        )

    figures = {
        key: (_money(kinds.get(EntryKind.SPEND, [])), _money(kinds.get(EntryKind.WASTE, [])))
        for key, kinds in grouped.items()
    }

    out: list[CategoryBucket] = []
    for (category, start), (spend, waste) in sorted(
        figures.items(), key=lambda kv: (kv[0][1], kv[0][0])
    ):
        # Only a bucket that is actually inside the window counts as a prior one; the first
        # bucket has no predecessor by construction and its deltas stay None.
        prior = (
            figures.get((category, _previous_bucket(start, bucket))) if start != starts[0] else None
        )
        out.append(
            CategoryBucket(
                category_code=category,
                bucket_start=start,
                spend=spend,
                waste=waste,
                waste_share=(
                    float(waste.eur / spend.eur)
                    if waste.eur is not None and spend.eur is not None and spend.eur > 0
                    else None
                ),
                spend_delta_pct=_delta(spend.eur, prior[0].eur if prior else None),
                waste_delta_pct=_delta(waste.eur, prior[1].eur if prior else None),
            )
        )

    return ByCategoryResponse(
        bucket=bucket,
        start=window_start,
        end=window_end,
        domain=domain,
        demo_included=include_demo,
        demo_entries=demo_entry_count(db, user_id),
        rows=out,
    )
