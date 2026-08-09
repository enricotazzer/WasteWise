"""The reporting surface.

What is being protected here is not the arithmetic — that lives in test_savings_rules — but
the honesty of the presentation: every euro figure carrying the coverage of its own rows,
demo data staying out by default, and unknown never being rendered as zero.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import factories
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import SavingsEntry
from app.models.enums import (
    EntryBasis,
    EntryKind,
    EventType,
    ItemStatus,
    SavingsDomain,
    SourceKind,
)
from app.schemas.stats import MoneyFigure
from app.services.savings.writer import sync_item

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _resolve(db: Session, item, status: ItemStatus, *, at: datetime = NOW) -> None:
    factories.event(
        db,
        item,
        EventType.CONSUMED if status is ItemStatus.CONSUMED else EventType.WASTED,
        occurred_at=at,
    )
    item.status = status
    item.resolved_at = at
    item.fraction_remaining = 0.0
    sync_item(db, item)
    db.commit()


def test_overview_is_empty_before_any_data(auth_client: TestClient) -> None:
    body = auth_client.get("/api/stats/overview").json()
    assert body["save_rate"] is None
    # No denominator: unknown, not zero. A 0.00 here would read as "nothing was wasted".
    assert body["wasted"]["eur"] is None
    assert body["wasted"]["coverage"] is None
    assert body["demo_entries"] == 0
    assert body["demo_included"] is False


def test_every_figure_carries_the_coverage_of_its_own_rows(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The defect this table exists to fix: `eur_wasted` and `price_coverage` used to be
    computed from two independently-built Python lists, so the caveat printed beside a
    figure was not the caveat *for* that figure."""
    user = seeded["user"]
    _resolve(
        db,
        factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00"),
        ItemStatus.CONSUMED,
    )
    _resolve(
        db, factories.item(db, user, seeded["ricotta"], unit_price_eur="3.00"), ItemStatus.WASTED
    )
    _resolve(
        db, factories.item(db, user, seeded["ricotta"], unit_price_eur=None), ItemStatus.WASTED
    )

    body = auth_client.get("/api/stats/overview").json()

    assert body["wasted"] == {
        "eur": "3.00",
        "entries": 2,
        "priced_entries": 1,
        "coverage": 0.5,  # exactly the rows that produced the 3.00
        "basis": "measured",
    }
    assert body["used"]["eur"] == "2.00"
    assert body["used"]["coverage"] == 1.0

    # The headline is count-based, so it has no coverage caveat at all.
    assert body["save_rate"] == 1 / 3
    # The euro-weighted version answers a different question over a smaller sample.
    assert body["save_rate_eur"] == 0.4
    assert body["save_rate_eur_coverage"] == 2 / 3


def test_unaccounted_is_spend_minus_its_partitions(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """Value bought and never resolved. This is where the save rate's bias becomes visible:
    it is defined over resolved items only, and unresolved food is mostly forgotten food."""
    user = seeded["user"]
    _resolve(
        db,
        factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00"),
        ItemStatus.CONSUMED,
    )
    never_logged = factories.item(db, user, seeded["ricotta"], unit_price_eur="5.00")
    sync_item(db, never_logged)
    db.commit()

    food = next(
        d for d in auth_client.get("/api/stats/overview").json()["domains"] if d["domain"] == "food"
    )
    assert food["spend"]["eur"] == "7.00"
    assert food["unaccounted"]["eur"] == "5.00"
    # Derived from the SPEND base, so it reports the SPEND base's coverage.
    assert food["unaccounted"]["coverage"] == food["spend"]["coverage"] == 1.0
    # R6: the food domain never claims a saving for food that was simply eaten.
    assert food["avoided"]["eur"] is None
    assert food["avoided"]["entries"] == 0


def test_demo_rows_are_excluded_from_every_figure_by_default(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """`is_demo` exists because synthetic outcomes must never reach a reported number. Until
    now `/api/inventory/stats` did not filter on it at all."""
    user = seeded["user"]
    _resolve(
        db,
        factories.item(db, user, seeded["mozzarella"], unit_price_eur="2.00"),
        ItemStatus.CONSUMED,
    )
    fake = factories.item(db, user, seeded["ricotta"], unit_price_eur="99.00", is_demo=True)
    _resolve(db, fake, ItemStatus.WASTED)

    body = auth_client.get("/api/stats/overview").json()
    assert body["wasted"]["eur"] is None
    assert body["wasted"]["entries"] == 0
    assert body["spend"]["eur"] == "2.00"
    assert body["save_rate"] == 1.0
    assert body["active_items"] == 0
    # Excluded, but never hidden: the caller can always tell synthetic data is there.
    assert body["demo_entries"] == 2
    assert body["demo_included"] is False

    with_demo = auth_client.get("/api/stats/overview?include_demo=true").json()
    assert with_demo["wasted"]["eur"] == "99.00"
    assert with_demo["demo_included"] is True


def test_a_money_figure_never_blends_two_bases(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """A total that adds a price the user typed to one a model guessed is not a measurement
    of anything. The stronger basis wins and the weaker rows drop out of the sum — visibly,
    because they stay in `entries` and coverage falls."""
    user = seeded["user"]
    _resolve(
        db, factories.item(db, user, seeded["ricotta"], unit_price_eur="3.00"), ItemStatus.WASTED
    )
    # Stand-in for what a future domain will write: same slice, modelled money.
    db.add(
        SavingsEntry(
            user_id=user.id,
            domain=SavingsDomain.FOOD,
            kind=EntryKind.WASTE,
            basis=EntryBasis.MODELLED,
            amount_eur=Decimal("50.00"),
            occurred_on=date.today(),
            source_kind=SourceKind.SAVINGS_BASELINE,
            source_id=1,
            category_code="FORMAGGIO_FRESCO",
        )
    )
    db.commit()

    wasted = auth_client.get("/api/stats/overview").json()["wasted"]
    assert wasted["basis"] == "measured"
    assert wasted["eur"] == "3.00"  # the modelled 50.00 is not added in
    assert wasted["entries"] == 2 and wasted["priced_entries"] == 1
    assert wasted["coverage"] == 0.5


def test_money_figure_reports_unknown_rather_than_zero() -> None:
    """The primitive on its own. Entries with no price at all give None, not 0.00, and a
    coverage of 0.0 rather than None — there is a denominator, it is simply all misses."""
    figure = MoneyFigure.from_rows([(EntryBasis.UNPRICED, 4, 0, None)])
    assert figure.eur is None
    assert figure.coverage == 0.0
    assert MoneyFigure.empty().coverage is None


def test_timeseries_buckets_are_contiguous_and_zero_filled(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """A missing bucket makes a line chart interpolate across the gap, which reads as
    "nothing was wasted" when it means "nothing was logged"."""
    user = seeded["user"]
    long_ago = datetime.now(UTC) - timedelta(days=35)
    item = factories.item(
        db, user, seeded["ricotta"], unit_price_eur="4.00", acquired_at=long_ago.date()
    )
    _resolve(db, item, ItemStatus.WASTED, at=long_ago)

    body = auth_client.get("/api/stats/timeseries?weeks=8").json()
    points = body["points"]
    assert len(points) == 8

    starts = [date.fromisoformat(p["bucket_start"]) for p in points]
    assert all(b - a == timedelta(days=7) for a, b in zip(starts, starts[1:], strict=False))
    assert all(s.weekday() == 0 for s in starts), "weeks must be Monday-anchored like date_trunc"

    filled = [p for p in points if p["waste_entries"] == 0]
    assert filled and all(p["waste_eur"] == "0.00" for p in filled)
    assert sum(p["waste_entries"] for p in points) == 1
    assert body["price_coverage"] == 1.0
    # Ricotta is sold by the piece here, so there is no mass to report at all.
    assert body["mass_coverage"] == 0.0


def test_by_category_deltas_are_null_without_a_prior_bucket(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """None, never 0.0 — a zero would claim the category was unchanged, which is a real
    finding and not one we have."""
    user = seeded["user"]
    # Bought and binned inside the same bucket, so the share is over a spend that is there.
    item = factories.item(
        db, user, seeded["ricotta"], unit_price_eur="3.00", acquired_at=date.today()
    )
    _resolve(db, item, ItemStatus.WASTED, at=datetime.now(UTC))

    rows = auth_client.get("/api/stats/by-category?months=6").json()["rows"]
    assert rows, "expected at least one category row"
    first = rows[0]
    assert first["spend_delta_pct"] is None
    assert first["waste_delta_pct"] is None
    assert first["waste_share"] == 1.0  # everything bought in this category was binned


def test_legacy_inventory_stats_no_longer_overstates_waste(
    auth_client: TestClient, seeded: dict
) -> None:
    """The live bug, end to end. Half a €2.00 mozzarella eaten, the rest binned.

    The old endpoint charged the whole €2.00 to `eur_wasted` because it read the item's final
    status and knew nothing about the half that was used first.
    """
    created = auth_client.post(
        "/api/inventory/items",
        json={"product_id": seeded["mozzarella"].id, "quantity": 1, "unit_price_eur": 2.00},
    ).json()
    url = f"/api/inventory/items/{created['id']}/events"
    auth_client.post(url, json={"event_type": "partial", "fraction_used": 0.5})
    auth_client.post(url, json={"event_type": "wasted", "reason": "dimenticato"})

    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["eur_wasted"] == "1.00"
    assert stats["eur_consumed"] == "1.00"
    assert stats["demo_included"] is False


def test_legacy_inventory_stats_excludes_demo_by_default(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """These four figures were previously computed over ~8 weeks of synthetic outcomes on any
    machine that had run `make demo`, and rendered with no indication of it."""
    fake = factories.item(
        db, seeded["user"], seeded["ricotta"], unit_price_eur="99.00", is_demo=True
    )
    _resolve(db, fake, ItemStatus.WASTED)

    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["eur_wasted"] == "0.00"
    assert stats["save_rate"] is None

    with_demo = auth_client.get("/api/inventory/stats?include_demo=true").json()
    assert with_demo["eur_wasted"] == "99.00"
    assert with_demo["demo_included"] is True
