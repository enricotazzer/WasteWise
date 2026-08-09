"""v1: the split, the gate, and the two things they exist to prevent.

A random train/test split and an ungated fit both produce a number that looks exactly like a
real one. Neither fails loudly, neither shows up in a code review of the model itself, and
both would make this project's central claim — that its figures are measured — untrue. So
they get the most direct tests in the suite: the split is asserted on, and the gate is
asserted to refuse.
"""

from datetime import UTC, date, datetime, timedelta

import factories
import numpy as np
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import EventSource, EventType, ItemStatus
from app.services.risk import survival
from app.services.risk.features import build_frame
from app.services.risk.survival import (
    GateStatus,
    confusion,
    evaluate,
    fit,
    gate_status,
    load_model,
    score_metrics,
    split_by_acquisition,
)

TODAY = date(2026, 8, 5)


@pytest.fixture
def small_gate(monkeypatch):
    """Fit on a fixture-sized household. The real thresholds get their own test — this one
    exists so every *other* v1 property can be checked without seeding 150 rows."""
    monkeypatch.setattr(settings, "risk_v1_min_resolved", 30)
    monkeypatch.setattr(settings, "risk_v1_min_waste_events", 8)


def _history(db: Session, seeded: dict, *, consumed=44, wasted=12, is_demo=False):
    return factories.resolved_history(
        db,
        seeded["user"],
        [seeded["mozzarella"], seeded["ricotta"], seeded["pasta"]],
        consumed=consumed,
        wasted=wasted,
        end=TODAY,
        span_days=120,
        is_demo=is_demo,
    )


# --- the split ------------------------------------------------------------------------


def test_the_split_never_puts_a_later_acquisition_in_the_training_half(
    db: Session, seeded: dict
) -> None:
    """THE property. A random split leaks future consumption into the past and inflates every
    metric downstream, silently. Asserted directly on the boundary rather than on a
    downstream number that would only look slightly too good."""
    _history(db, seeded)
    db.commit()

    frame = build_frame(db, user_id=seeded["user"].id, as_of=TODAY)
    split = split_by_acquisition(frame, test_fraction=0.3)

    assert split.usable
    assert split.train["acquired_ord"].max() < split.test["acquired_ord"].min()
    assert split.train["acquired_ord"].max() < split.cutoff_ord
    assert split.test["acquired_ord"].min() >= split.cutoff_ord
    # And no row went missing or got counted twice on the way through.
    assert len(split.train) + len(split.test) == len(frame)
    assert set(split.train["item_id"]).isdisjoint(set(split.test["item_id"]))


def test_the_split_censors_training_outcomes_that_happened_after_the_cutoff(
    db: Session, seeded: dict
) -> None:
    """Splitting rows by acquisition is not enough on its own: an item bought in week 2 and
    eaten in week 7 would still carry a label from the future. It enters the fit as "still
    unresolved on the cutoff date", which is genuinely all a model standing there would
    have known."""
    user = seeded["user"]
    early = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=60),
        expiry_date=TODAY - timedelta(days=50),
        status=ItemStatus.CONSUMED,
        resolved_at=datetime(2026, 8, 1, 12, tzinfo=UTC),  # long after the cutoff below
    )
    for offset in (58, 56, 54, 5, 3, 1):
        factories.item(
            db,
            user,
            seeded["ricotta"],
            acquired_at=TODAY - timedelta(days=offset),
            expiry_date=TODAY - timedelta(days=offset) + timedelta(days=5),
        )
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    split = split_by_acquisition(frame, test_fraction=0.4)
    row = split.train[split.train["item_id"] == early.id].iloc[0]

    assert row["event_consumed"] == 0, "the label came from after the cutoff and must be hidden"
    assert row["censored"] == 1
    assert row["duration_days"] == split.cutoff_ord - row["acquired_ord"]


def test_a_single_shopping_day_cannot_be_split(db: Session, seeded: dict) -> None:
    """No later period to hold out means no evaluation. Reported as unusable rather than
    silently training and testing on the same rows."""
    user = seeded["user"]
    for _ in range(4):
        factories.item(db, user, seeded["mozzarella"], acquired_at=TODAY)
    db.commit()

    frame = build_frame(db, user_id=user.id, as_of=TODAY)
    assert split_by_acquisition(frame).usable is False


# --- the gate -------------------------------------------------------------------------


def test_the_gate_refuses_and_says_exactly_how_far_short(db: Session, seeded: dict) -> None:
    """The real thresholds, against a household that has barely started. A c-index from this
    much data is noise wearing a number's clothes, so none is produced."""
    _history(db, seeded, consumed=20, wasted=5)
    db.commit()

    report = fit(db, user_id=seeded["user"].id, as_of=TODAY)

    assert report.fitted is False
    # v0 is the model in force when the gate refuses, and the report names it as such rather
    # than leaving the version blank for a client to guess at.
    assert report.model_version == "v0"
    assert report.gate.met is False
    assert report.gate.summary == "gate not met: 25/150 resolved, 5/40 waste events"
    assert report.gate.resolved_short == 125
    assert report.gate.waste_short == 35
    assert any("more waste events needed" in note for note in report.notes)
    # v0 is still evaluated: the heuristic ships regardless, so its numbers are the ones in
    # force and the report must never come back empty-handed.
    assert [m.name for m in report.models] == ["v0"]
    assert load_model() is None, "nothing may be persisted when the gate refuses"


def test_the_gate_counts_observed_outcomes_only(db: Session, seeded: dict) -> None:
    """A repurchase-inferred closure is a guess, not an observation, and must not buy its way
    past the gate."""
    user = seeded["user"]
    items = _history(db, seeded, consumed=10, wasted=4)
    guessed = factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=9),
        status=ItemStatus.CONSUMED,
        resolved_at=datetime(2026, 8, 3, 12, tzinfo=UTC),
    )
    factories.event(
        db,
        guessed,
        EventType.CONSUMED,
        occurred_at=datetime(2026, 8, 3, 12, tzinfo=UTC),
        source=EventSource.REPURCHASE_INFERRED,
    )
    db.commit()

    status = gate_status(db, user_id=user.id, as_of=TODAY)
    assert status.resolved == len(items)
    assert status.waste_events == 4


def test_a_met_gate_fits_and_scores_both_models(db: Session, seeded: dict, small_gate) -> None:
    _history(db, seeded)
    db.commit()

    report = fit(db, user_id=seeded["user"].id, as_of=TODAY)

    assert report.gate.met is True
    assert report.fitted is True
    assert report.fitted_below_gate is False
    names = [m.name for m in report.models]
    assert names[0] == "v0"
    assert "v1-cox" in names
    assert report.split_on is not None
    assert report.train_rows and report.test_rows
    assert report.design_columns
    assert report.coefficients
    # Persisted, and readable back.
    stored = load_model()
    assert stored is not None
    assert stored.version == "v1-cox"
    assert stored.design_columns == report.design_columns


def test_forcing_below_the_gate_stamps_the_version_ungated(db: Session, seeded: dict) -> None:
    """The escape hatch, fenced. Diagnostics are sometimes worth having; a diagnostic that
    cannot be told apart from a measurement is not."""
    _history(db, seeded, consumed=30, wasted=10)
    db.commit()

    report = fit(db, user_id=seeded["user"].id, as_of=TODAY, force_below_gate=True)

    assert report.gate.met is False
    assert report.fitted is True
    assert report.fitted_below_gate is True
    assert report.model_version.endswith("-ungated")
    assert any("FORCED below the gate" in note for note in report.notes)


# --- the demo fence -------------------------------------------------------------------


def test_the_demo_suffix_appears_when_synthetic_rows_were_in_the_fitting_set(
    db: Session, seeded: dict, small_gate
) -> None:
    _history(db, seeded, is_demo=True)
    db.commit()

    report = fit(db, user_id=seeded["user"].id, as_of=TODAY)

    assert report.trained_on_demo is True
    assert report.model_version == "v1-cox-demo"
    assert [m.name for m in report.models][0] == "v0-demo"
    assert load_model().trained_on_demo is True


def test_and_disappears_on_its_own_once_the_demo_rows_are_gone(
    db: Session, seeded: dict, small_gate
) -> None:
    """`make risk-fit` on real data must produce an unsuffixed version with no code change —
    the same call, the same flags, a different database."""
    _history(db, seeded, is_demo=False)
    db.commit()

    report = fit(db, user_id=seeded["user"].id, as_of=TODAY)

    assert report.trained_on_demo is False
    assert report.model_version == "v1-cox"
    assert report.demo_rows == 0
    assert load_model().trained_on_demo is False


def test_demo_rows_in_the_test_half_alone_do_not_stamp_the_model(
    db: Session, seeded: dict, small_gate
) -> None:
    """The stamp means "a synthetic row shaped these coefficients". Rows the fit only scored
    against did not."""
    user = seeded["user"]
    factories.resolved_history(
        db,
        user,
        [seeded["mozzarella"], seeded["ricotta"], seeded["pasta"]],
        consumed=40,
        wasted=12,
        end=TODAY - timedelta(days=20),
        span_days=100,
    )
    factories.resolved_history(
        db,
        user,
        [seeded["mozzarella"]],
        consumed=8,
        wasted=2,
        end=TODAY,
        span_days=10,
        is_demo=True,
    )
    db.commit()

    report = fit(db, user_id=user.id, as_of=TODAY, test_fraction=0.2)

    assert report.demo_rows == 10, "the frame still carries them, and says so"
    assert report.trained_on_demo is False
    assert report.model_version == "v1-cox"


# --- metrics --------------------------------------------------------------------------


def test_confusion_matrix_is_counted_the_way_a_hand_count_would():
    """Positive class = the item was thrown away. Flagged = the model said it would be."""
    flagged = np.array([True, True, True, False, False, False])
    wasted = np.array([True, True, False, True, False, False])

    assert confusion(flagged, wasted) == (2, 1, 1, 2)


def test_precision_recall_and_f1_against_a_hand_built_confusion_matrix():
    """Six items: three flagged, of which two really were wasted; one wasted item missed.

    tp=2  fp=1  fn=1  tn=2
    precision = 2/3, recall = 2/3, F1 = 2/3
    """
    frame = pd.DataFrame(
        {
            "duration_days": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            "event_consumed": [0, 0, 1, 0, 1, 1],
            "event_wasted": [1, 1, 0, 1, 0, 0],
            "has_expiry": [1, 1, 1, 1, 1, 1],
        }
    )
    # Below the threshold == flagged at risk.
    p_consume = np.array([0.1, 0.2, 0.3, 0.9, 0.8, 0.7])
    metrics = score_metrics(
        "hand-built",
        frame=frame,
        hazard=np.array([1.0, 0.9, 0.8, 0.2, 0.3, 0.4]),
        p_consume=p_consume,
        threshold=0.5,
    )

    assert (metrics.true_positives, metrics.false_positives) == (2, 1)
    assert (metrics.false_negatives, metrics.true_negatives) == (1, 2)
    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 / 3)
    assert metrics.evaluated == 6
    assert metrics.positives == 3
    assert metrics.flagged == 3


def test_a_model_that_flags_nothing_has_no_precision_rather_than_a_bad_one():
    """None, never 0.0. "Undefined" and "always wrong" are different findings and a dashboard
    that renders them the same way is lying about one of them."""
    frame = pd.DataFrame(
        {
            "duration_days": [3.0, 4.0],
            "event_consumed": [0, 1],
            "event_wasted": [1, 0],
            "has_expiry": [1, 1],
        }
    )
    metrics = score_metrics(
        "silent",
        frame=frame,
        hazard=np.array([0.5, 0.5]),
        p_consume=np.array([0.9, 0.9]),
        threshold=0.5,
    )

    assert metrics.flagged == 0
    assert metrics.precision is None
    assert metrics.f1 is None
    assert metrics.recall == 0.0  # it *did* have a waste event to find, and missed it


def test_items_without_an_expiry_date_are_left_out_of_the_confusion_matrix():
    """No deadline, nothing to be at risk of. They still count toward the c-index, which
    ranks time-to-consumption and needs no expiry at all."""
    frame = pd.DataFrame(
        {
            "duration_days": [3.0, 4.0, 5.0],
            "event_consumed": [1, 0, 1],
            "event_wasted": [0, 1, 0],
            "has_expiry": [1, 1, 0],
        }
    )
    metrics = score_metrics(
        "mixed",
        frame=frame,
        hazard=np.array([0.9, 0.2, 0.5]),
        p_consume=np.array([0.8, 0.1, np.nan]),
        threshold=0.5,
    )

    assert metrics.evaluated == 2
    assert metrics.ranked == 3


def test_gate_status_summary_reads_as_a_sentence():
    status = GateStatus(resolved=152, waste_events=25, min_resolved=150, min_waste_events=40)
    assert status.met is False
    assert status.summary == "gate not met: 152/150 resolved, 25/40 waste events"
    assert GateStatus(resolved=200, waste_events=50, min_resolved=150, min_waste_events=40).met


# --- empty database -------------------------------------------------------------------


def test_nothing_in_this_module_raises_on_an_empty_database(db: Session) -> None:
    """Every entry point, against a database with no user, no products and no items."""
    assert gate_status(db, as_of=TODAY).met is False
    assert split_by_acquisition(build_frame(db, as_of=TODAY)).usable is False

    for report in (fit(db, as_of=TODAY), evaluate(db, as_of=TODAY)):
        assert report.fitted is False
        assert report.models == []
        assert report.total_rows == 0
        assert report.trained_on_demo is False

    assert load_model() is None


def test_evaluate_reports_the_stored_fit_with_a_live_gate(
    db: Session, seeded: dict, small_gate
) -> None:
    """The stored report is a fact about a past fit and is returned as measured; the gate is a
    fact about the database now. "Fitted below the gate, gate now met" is exactly the state
    that should prompt a re-fit, so it has to be visible."""
    _history(db, seeded)
    db.commit()
    fit(db, user_id=seeded["user"].id, as_of=TODAY)

    report = evaluate(db, user_id=seeded["user"].id, as_of=TODAY)
    assert report.fitted is True
    assert report.gate.min_resolved == settings.risk_v1_min_resolved

    survival.clear_model()
    fallback = evaluate(db, user_id=seeded["user"].id, as_of=TODAY)
    assert fallback.fitted is False
    assert [m.name for m in fallback.models] == ["v0"]
    assert any("run `make risk-fit`" in note for note in fallback.notes)
