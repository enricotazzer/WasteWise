"""v0: the heuristic that ships on day one.

The cold-start chain is the point of most of this file. v0 has to produce a usable number for
an unseen product in an empty kitchen, and has to *say* that is what it did — a score whose
provenance is invisible is a score nobody can argue with, which is worse than a wrong one.
"""

from datetime import UTC, date, datetime, timedelta

import factories
import pytest
from sqlalchemy.orm import Session

from app.models.enums import ItemStatus, RiskBand
from app.services.risk.baseline import (
    TIER_CATEGORY_KM,
    TIER_ITEM,
    TIER_PRODUCT,
    TIER_SHELF_LIFE_PRIOR,
    BaselineScorer,
    risk_band,
    stamp_version,
)
from app.services.risk.features import build_frame, empty_frame

TODAY = date(2026, 8, 5)


def _at(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=12)


def _row(frame, item_id: int) -> dict:
    return frame[frame["item_id"] == item_id].to_dict("records")[0]


# --- day one --------------------------------------------------------------------------


def test_scores_a_kitchen_with_no_history_whatsoever(db: Session, seeded: dict) -> None:
    """The actual day-one state: one item, no outcomes, nothing to learn from. v0 must
    answer, must land on the seeded prior, and must say so."""
    user = seeded["user"]
    fresh = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, fresh.id))

    assert score.tier == TIER_SHELF_LIFE_PRIOR
    assert score.tier_n == 0
    assert 0.0 <= score.p_consume_before_expiry <= 1.0
    assert 0.0 <= score.p_consume_3d <= score.p_consume_7d <= 1.0
    assert score.band in set(RiskBand)


def test_fits_on_an_entirely_empty_frame_without_raising(db: Session) -> None:
    """Before the first scan there is not even a row to score. `fit` must still return a
    scorer rather than an exception — the cold start is a normal path, not an error path."""
    scorer = BaselineScorer.fit(empty_frame())
    score = scorer.score(
        {
            "product_id": 1,
            "category_code": "ORTAGGI",
            "is_perishable": True,
            "shelf_life_days": 7.0,
            "days_to_expiry": 7.0,
            "days_in_house": 0.0,
            "fraction_remaining": 1.0,
        }
    )
    assert score.tier == TIER_SHELF_LIFE_PRIOR
    assert 0.0 < score.p_consume_before_expiry < 1.0


# --- the tier chain -------------------------------------------------------------------


def test_category_kaplan_meier_takes_over_from_the_seeded_prior(db: Session, seeded: dict) -> None:
    """Second-weakest tier: nothing known about *this* product, but its category has a
    history, and Kaplan-Meier is what turns censored rows into a usable curve."""
    user = seeded["user"]
    # Six resolved ricottas give FORMAGGIO_FRESCO a curve; the mozzarella below has none of
    # its own, so it has to borrow the category's.
    factories.resolved_history(
        db, user, [seeded["ricotta"]], consumed=5, wasted=1, end=TODAY, span_days=40
    )
    unseen = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, unseen.id))

    assert score.tier == TIER_CATEGORY_KM
    assert score.tier_n >= 6


def test_product_history_beats_the_category_curve(db: Session, seeded: dict) -> None:
    """Most specific evidence wins. Once this product has been eaten a few times, the
    household's own record of it is worth more than its category's average."""
    user = seeded["user"]
    factories.resolved_history(
        db, user, [seeded["mozzarella"]], consumed=6, wasted=2, end=TODAY, span_days=40
    )
    factories.resolved_history(
        db, user, [seeded["ricotta"]], consumed=5, wasted=1, end=TODAY, span_days=40
    )
    current = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, current.id))

    assert score.tier == TIER_PRODUCT
    assert score.tier_n == 8


def test_a_part_used_item_is_scored_from_its_own_consumption_speed(
    db: Session, seeded: dict
) -> None:
    """The strongest tier, and the only one that needs no history at all: the item has been
    opened and half eaten, so how fast this household gets through it is observed rather
    than inferred."""
    user = seeded["user"]
    factories.resolved_history(
        db, user, [seeded["mozzarella"]], consumed=6, wasted=2, end=TODAY, span_days=40
    )
    opened = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=2),
        expiry_date=TODAY + timedelta(days=3),
        status=ItemStatus.PARTIAL,
        fraction_remaining=0.5,
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, opened.id))

    assert score.tier == TIER_ITEM
    # Half of it went in two days, so the rest is expected to take about two more.
    assert score.rate_per_day == pytest.approx(0.5, rel=0.2)


def test_the_chain_degrades_one_step_at_a_time(db: Session, seeded: dict) -> None:
    """The whole point of a hierarchy: removing evidence moves the tier down, never off a
    cliff into an exception."""
    user = seeded["user"]
    tiers = []

    unseen = factories.item(
        db, user, seeded["pasta"], acquired_at=TODAY, expiry_date=TODAY + timedelta(days=30)
    )
    db.commit()
    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    tiers.append(BaselineScorer.fit(frame).score(_row(frame, unseen.id)).tier)

    factories.resolved_history(
        db, user, [seeded["mozzarella"], seeded["ricotta"]], consumed=8, wasted=2, end=TODAY
    )
    cheese = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
    )
    db.commit()
    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    tiers.append(BaselineScorer.fit(frame).score(_row(frame, cheese.id)).tier)

    assert tiers == [TIER_SHELF_LIFE_PRIOR, TIER_PRODUCT]


def test_kaplan_meier_is_extrapolated_past_the_window_it_was_fitted_on(
    db: Session, seeded: dict
) -> None:
    """A regression test for a real bug this file caught.

    KM is flat beyond its last observation, so eight weeks of logging says nothing about day
    500 — and reading that flatness as an answer put every long-dated pantry item into the
    HIGH band. A tin with sixteen months left is not about to be wasted.
    """
    user = seeded["user"]
    # A short window of fast-moving history, then an item whose horizon dwarfs it.
    factories.resolved_history(
        db, user, [seeded["ricotta"]], consumed=6, wasted=1, end=TODAY, span_days=30
    )
    tin = factories.item(
        db,
        user,
        seeded["mozzarella"],  # same category, no history of its own -> the KM tier
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=500),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, tin.id))

    assert score.tier == TIER_CATEGORY_KM
    assert score.p_consume_before_expiry > 0.5
    assert score.band is not RiskBand.HIGH


# --- output shape ---------------------------------------------------------------------


def test_an_item_with_no_expiry_has_no_probability_and_no_risk(db: Session, seeded: dict) -> None:
    """ "Will it be eaten in time?" needs a deadline. Without one there is nothing to be at
    risk of, and None is the honest answer — 0.0 would read as certain waste."""
    user = seeded["user"]
    salt = factories.item(db, user, seeded["pasta"], acquired_at=TODAY, expiry_date=None)
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, salt.id))

    assert score.p_consume_before_expiry is None
    assert score.band is RiskBand.LOW
    # The short-horizon answers still exist: they need no expiry date.
    assert 0.0 <= score.p_consume_3d <= 1.0


def test_an_already_expired_item_is_high_risk(db: Session, seeded: dict) -> None:
    user = seeded["user"]
    late = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=10),
        expiry_date=TODAY - timedelta(days=2),
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    score = BaselineScorer.fit(frame).score(_row(frame, late.id))

    assert score.p_consume_before_expiry == 0.0
    assert score.band is RiskBand.HIGH


def test_the_explanation_travels_with_the_score(db: Session, seeded: dict) -> None:
    """`RiskScore.features` is the only thing that makes a surprising number debuggable
    after the fact."""
    user = seeded["user"]
    row = factories.item(
        db, user, seeded["mozzarella"], acquired_at=TODAY, expiry_date=TODAY + timedelta(days=5)
    )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    features = BaselineScorer.fit(frame).score(_row(frame, row.id)).as_features()

    assert features["model"] == "v0"
    assert features["tier"] == TIER_SHELF_LIFE_PRIOR
    assert {"rate_per_day", "coverage", "horizon_days", "coverage_tier"} <= set(features)


# --- bands and the fence --------------------------------------------------------------


def test_bands_are_derived_from_the_operating_threshold(monkeypatch) -> None:
    """HIGH *is* the at-risk flag. If the band the user sees and the operating point the
    model is scored at could drift apart, the reported precision would describe a detector
    nobody is running."""
    from app.config import settings

    monkeypatch.setattr(settings, "risk_at_risk_threshold", 0.5)
    assert risk_band(0.49) is RiskBand.HIGH
    assert risk_band(0.50) is RiskBand.MEDIUM
    assert risk_band(0.74) is RiskBand.MEDIUM
    assert risk_band(0.75) is RiskBand.LOW
    assert risk_band(None) is RiskBand.LOW

    monkeypatch.setattr(settings, "risk_at_risk_threshold", 0.7)
    assert risk_band(0.69) is RiskBand.HIGH
    assert risk_band(0.71) is RiskBand.MEDIUM


def test_version_suffixes_say_what_the_number_was_built_from() -> None:
    """The first side of the demo fence: provenance welded to the version string, so it
    survives being read back out of `risk_scores` months later."""
    assert stamp_version("v0", trained_on_demo=False) == "v0"
    assert stamp_version("v0", trained_on_demo=True) == "v0-demo"
    assert stamp_version("v1-cox", trained_on_demo=False, below_gate=True) == "v1-cox-ungated"
    assert stamp_version("v1-cox", trained_on_demo=True, below_gate=True) == "v1-cox-demo-ungated"
    # Must fit `risk_scores.model_version`, which is String(32).
    assert len(stamp_version("v1-cox", trained_on_demo=True, below_gate=True)) <= 32
