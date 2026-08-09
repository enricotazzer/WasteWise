"""The feature frame.

Most of these are about what the frame is *not allowed* to contain. The survival encoding
and the prior-only history features are the two places where a plausible-looking mistake
inflates every metric downstream without producing a single visible symptom.
"""

from datetime import UTC, date, datetime, timedelta

import factories
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.models.enums import EventSource, EventType, ItemStatus
from app.services.risk.features import (
    FRAME_COLUMNS,
    build_frame,
    design_defaults,
    design_matrix,
    drop_degenerate,
    empty_frame,
)

TODAY = date(2026, 8, 5)


def _at(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=12)


def test_empty_database_produces_a_frame_with_the_full_contract(db: Session) -> None:
    """Day one, before anything has been scanned. Every consumer below does arithmetic on
    these columns, so an empty frame has to have all of them and the right dtypes — not a
    zero-column DataFrame that fails three call sites later with a KeyError."""
    frame = build_frame(db, as_of=TODAY)

    assert len(frame) == 0
    assert tuple(frame.columns) == FRAME_COLUMNS
    assert tuple(empty_frame().columns) == FRAME_COLUMNS

    defaults = design_defaults(frame)
    matrix = design_matrix(frame, defaults)
    assert len(matrix) == 0
    assert drop_degenerate(matrix) == (matrix, [])


def test_survival_encoding_separates_the_event_the_competing_risk_and_the_censored(
    db: Session, seeded: dict
) -> None:
    """The three outcomes, and the whole reason this project needs survival analysis rather
    than a classifier: an item still in the fridge is not a negative example."""
    user = seeded["user"]
    eaten = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=10),
        status=ItemStatus.CONSUMED,
        resolved_at=_at(TODAY - timedelta(days=7)),
    )
    binned = factories.item(
        db,
        user,
        seeded["ricotta"],
        acquired_at=TODAY - timedelta(days=10),
        status=ItemStatus.WASTED,
        resolved_at=_at(TODAY - timedelta(days=4)),
    )
    still_here = factories.item(db, user, seeded["pasta"], acquired_at=TODAY - timedelta(days=10))
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY).set_index("item_id")

    outcome = ["event_consumed", "event_wasted", "censored"]
    assert frame.loc[eaten.id, outcome].tolist() == [1, 0, 0]
    assert frame.loc[binned.id, outcome].tolist() == [0, 1, 0]
    assert frame.loc[still_here.id, outcome].tolist() == [0, 0, 1]

    # T runs from acquisition to resolution, and to `as_of` for anything unresolved.
    assert frame.loc[eaten.id, "duration_days"] == 3.0
    assert frame.loc[binned.id, "duration_days"] == 6.0
    assert frame.loc[still_here.id, "duration_days"] == 10.0


def test_same_day_consumption_survives_as_half_a_day(db: Session, seeded: dict) -> None:
    """lifelines refuses T <= 0. Bread bought and finished on the same day is a real
    observation and the fastest-moving kind there is — dropping it would bias the fit toward
    everything slow."""
    user = seeded["user"]
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        status=ItemStatus.CONSUMED,
        resolved_at=_at(TODAY),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    assert frame["duration_days"].tolist() == [0.5]
    assert frame["event_consumed"].tolist() == [1]


def test_repurchase_inferred_outcomes_are_censored_rather_than_labelled(
    db: Session, seeded: dict
) -> None:
    """The job closed it because the product was bought again, which conflates "eaten" with
    "quietly binned" — the exact distinction being modelled. Keep the duration, drop the
    label."""
    user = seeded["user"]
    guessed = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=8),
        status=ItemStatus.CONSUMED,
        resolved_at=_at(TODAY - timedelta(days=2)),
    )
    factories.event(
        db,
        guessed,
        EventType.CONSUMED,
        occurred_at=_at(TODAY - timedelta(days=2)),
        source=EventSource.REPURCHASE_INFERRED,
    )
    db.commit()

    row = build_frame(db, user_id=user.id, as_of=TODAY).iloc[0]
    assert bool(row["outcome_inferred"])
    assert row["event_consumed"] == 0
    assert row["censored"] == 1
    # The time it survived is still real information, and is kept.
    assert row["duration_days"] == 6.0


def test_history_features_never_see_an_outcome_from_the_future(db: Session, seeded: dict) -> None:
    """The leakage test. A product's consumption rate for the item bought on day N must use
    only outcomes already known on day N — otherwise every row can see itself and the c-index
    goes up for a reason that will never reproduce on live data."""
    user = seeded["user"]
    first = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=30),
        status=ItemStatus.CONSUMED,
        resolved_at=_at(TODAY - timedelta(days=25)),
    )
    second = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=20),
        status=ItemStatus.WASTED,
        resolved_at=_at(TODAY - timedelta(days=15)),
    )
    third = factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=5))
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY).set_index("item_id")

    # Nothing was known when the first one was bought: no prior rate at all, not a zero.
    assert frame.loc[first.id, "product_prior_n"] == 0
    assert pd.isna(frame.loc[first.id, "product_consume_rate"])
    # The second saw exactly one earlier outcome — and crucially not its own.
    assert frame.loc[second.id, "product_prior_n"] == 1
    assert frame.loc[third.id, "product_prior_n"] == 2


def test_same_category_active_counts_what_was_still_in_the_house(db: Session, seeded: dict) -> None:
    """The competition term. A fourth courgette in a fridge that already holds three is a
    worse bet than the first was, and an item resolved before this one arrived is not
    competition."""
    user = seeded["user"]
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=30),
        status=ItemStatus.CONSUMED,
        resolved_at=_at(TODAY - timedelta(days=25)),
    )
    factories.item(db, user, seeded["ricotta"], acquired_at=TODAY - timedelta(days=20))
    latest = factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=10))
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY).set_index("item_id")
    # Same category (FORMAGGIO_FRESCO): one finished long ago, one still open.
    assert frame.loc[latest.id, "same_category_active"] == 1


def test_quantity_is_measured_against_earlier_purchases_of_the_same_product(
    db: Session, seeded: dict
) -> None:
    user = seeded["user"]
    factories.item(db, user, seeded["pasta"], quantity="1", acquired_at=TODAY - timedelta(days=20))
    bulk = factories.item(
        db, user, seeded["pasta"], quantity="3", acquired_at=TODAY - timedelta(days=10)
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY).set_index("item_id")
    assert frame.loc[bulk.id, "qty_ratio"] == pytest.approx(3.0)


def test_non_food_stays_out(db: Session, seeded: dict) -> None:
    """Same exclusion the savings ledger applies at R0. Detergent has no expiry and is never
    thrown out for going off; it would only dilute every rate in the frame."""
    user = seeded["user"]
    factories.item(db, user, seeded["detersivo"], acquired_at=TODAY - timedelta(days=3))
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY - timedelta(days=3))
    db.commit()

    assert len(build_frame(db, user_id=user.id, as_of=TODAY)) == 1
    assert len(build_frame(db, user_id=user.id, as_of=TODAY, include_nonfood=True)) == 2


def test_demo_rows_are_visible_and_flagged_rather_than_dropped(db: Session, seeded: dict) -> None:
    """The frame carries synthetic rows so callers can decide, and so `is_demo` can be read
    back off the fitting set. Silently filtering here would make the fence invisible."""
    user = seeded["user"]
    factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY, is_demo=True)
    factories.item(db, user, seeded["ricotta"], acquired_at=TODAY)
    db.commit()

    assert build_frame(db, user_id=user.id, as_of=TODAY)["is_demo"].sum() == 1
    assert len(build_frame(db, user_id=user.id, as_of=TODAY, include_demo=False)) == 1


def test_drop_degenerate_removes_columns_a_fit_cannot_use(db: Session, seeded: dict) -> None:
    """A household that never buys a staple gives `is_staple` zero variance. lifelines raises
    on those rather than ignoring them, so a perfectly reasonable dataset would fail to fit
    for a reason no error message explains."""
    user = seeded["user"]
    factories.resolved_history(
        db, user, [seeded["mozzarella"]], consumed=6, wasted=2, end=TODAY, span_days=40
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    matrix = design_matrix(frame, design_defaults(frame))
    kept, dropped = drop_degenerate(matrix)

    assert "is_staple" in dropped
    assert "is_staple" not in kept.columns
    assert all(kept[column].nunique() > 1 for column in kept.columns)
