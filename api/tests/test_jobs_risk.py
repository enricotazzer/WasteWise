"""The nightly scoring job.

`risk_scores` is history rather than state: the whole precision/recall figure depends on
being able to replay what the model predicted *at the time* against what actually happened.
That makes idempotency subtler than "don't write twice" — the table has to keep growing over
days while a job re-run within a day has to converge, not accumulate.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import factories
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.jobs import scoring as job
from app.models import RiskScore
from app.models.enums import ItemStatus, RiskBand
from app.services.risk import scoring, survival

TODAY = date(2026, 8, 5)


def _rows(db: Session) -> list[RiskScore]:
    return list(db.scalars(select(RiskScore).order_by(RiskScore.id)))


def _kitchen(db: Session, seeded: dict) -> None:
    user = seeded["user"]
    factories.resolved_history(
        db, user, [seeded["mozzarella"], seeded["ricotta"]], consumed=12, wasted=4, end=TODAY
    )
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=1),
        expiry_date=TODAY + timedelta(days=4),
    )
    factories.item(
        db,
        user,
        seeded["ricotta"],
        acquired_at=TODAY - timedelta(days=6),
        expiry_date=TODAY - timedelta(days=1),
    )
    factories.item(
        db,
        user,
        seeded["pasta"],
        acquired_at=TODAY - timedelta(days=2),
        expiry_date=TODAY + timedelta(days=400),
        status=ItemStatus.PARTIAL,
        fraction_remaining=0.5,
    )
    db.commit()


def test_scores_everything_still_in_the_kitchen_and_nothing_else(db: Session, seeded: dict) -> None:
    _kitchen(db, seeded)

    summary = job.run(db, user_id=seeded["user"].id)

    assert summary.scored == 3, "the 16 resolved items are history, not inventory"
    assert summary.written == 3
    assert sum(summary.bands.values()) == 3
    assert len(_rows(db)) == 3


def test_running_twice_in_a_day_converges_instead_of_accumulating(
    db: Session, seeded: dict
) -> None:
    """The idempotency property. Anything else and the table the evaluation reads would grow
    a duplicate every time the job was retried."""
    _kitchen(db, seeded)

    job.run(db, user_id=seeded["user"].id)
    job.run(db, user_id=seeded["user"].id)
    job.run(db, user_id=seeded["user"].id)

    rows = _rows(db)
    assert len(rows) == 3
    assert len({row.inventory_item_id for row in rows}) == 3


def test_yesterdays_scores_are_kept_because_history_is_the_point(db: Session, seeded: dict) -> None:
    """A detector can only be evaluated against later outcomes if the score it gave *before*
    those outcomes survives. Replace within a day, append across days."""
    _kitchen(db, seeded)
    run = scoring.score_active(db, user_id=seeded["user"].id)

    yesterday = replace(run, computed_at=datetime.now(UTC) - timedelta(days=1))
    scoring.persist(db, yesterday)
    scoring.persist(db, run)
    scoring.persist(db, run)

    rows = _rows(db)
    assert len(rows) == 6, "two days of history for three items"

    latest = scoring.latest_scores(db, item_ids=[item.item_id for item in run.items])
    assert len(latest) == 3
    for item_id, row in latest.items():
        assert row.computed_at == run.computed_at, f"item {item_id} read a stale score"


def test_every_score_carries_its_explanation(db: Session, seeded: dict) -> None:
    _kitchen(db, seeded)
    job.run(db, user_id=seeded["user"].id)

    for row in _rows(db):
        assert row.model_version == settings.risk_model_version
        assert row.risk_band in set(RiskBand)
        assert row.features["model"] == "v0"
        assert row.features["tier"]


def test_an_empty_kitchen_raises_nothing_and_writes_nothing(db: Session) -> None:
    summary = job.run(db)

    assert summary.scored == 0
    assert summary.written == 0
    assert _rows(db) == []
    assert summary.notes == ["nothing in the kitchen to score"]


def test_the_run_is_stamped_demo_when_the_history_behind_it_is(db: Session, seeded: dict) -> None:
    """v0 has no coefficients, but its tier tables are summarised from the household's
    history. A v0 score computed over a synthetic kitchen is exactly as synthetic as a v1
    one, and the second side of the fence has to say so."""
    user = seeded["user"]
    factories.resolved_history(
        db, user, [seeded["mozzarella"]], consumed=8, wasted=2, end=TODAY, is_demo=True
    )
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
        is_demo=True,
    )
    db.commit()

    with_demo = job.run(db, user_id=user.id, include_demo=True)
    assert with_demo.trained_on_demo is True
    assert with_demo.model_version == "v0-demo"
    assert with_demo.demo_items == 1

    # Same code, same flags, a database with the synthetic rows filtered out.
    real_only = job.run(db, user_id=user.id, include_demo=False, persist=False)
    assert real_only.trained_on_demo is False
    assert real_only.model_version == "v0"


def test_scoring_stays_on_v0_when_the_fitted_model_lost_to_it(
    db: Session, seeded: dict, monkeypatch
) -> None:
    """Shipping a learned model that lost to the heuristic it was meant to improve on would
    be worse than not shipping one. The decision is made at fit time on held-out data and
    read back here."""
    monkeypatch.setattr(settings, "risk_v1_min_resolved", 30)
    monkeypatch.setattr(settings, "risk_v1_min_waste_events", 8)
    factories.resolved_history(
        db,
        seeded["user"],
        [seeded["mozzarella"], seeded["ricotta"], seeded["pasta"]],
        consumed=44,
        wasted=12,
        end=TODAY,
        span_days=120,
    )
    factories.item(
        db,
        seeded["user"],
        seeded["mozzarella"],
        acquired_at=TODAY,
        expiry_date=TODAY + timedelta(days=5),
    )
    db.commit()
    survival.fit(db, user_id=seeded["user"].id, as_of=TODAY)

    model = survival.load_model()
    assert model is not None

    run = scoring.score_active(db, user_id=seeded["user"].id)
    if model.beats_v0:
        assert run.model_stage == "v1"
        assert run.model_version.startswith("v1-cox")
    else:
        assert run.model_stage == "v0"
        assert any("did not beat v0" in note for note in run.notes)


def test_a_corrupt_model_file_falls_back_rather_than_taking_the_job_down(
    db: Session, seeded: dict
) -> None:
    """The nightly chain must survive a half-written pickle. v0 needs no file at all, which
    is the whole reason it stays in the codebase after v1 lands."""
    _kitchen(db, seeded)
    path = survival.model_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / survival.MODEL_FILE).write_bytes(b"not a pickle")

    assert survival.load_model() is None
    summary = job.run(db, user_id=seeded["user"].id)
    assert summary.scored == 3
    assert summary.model_stage == "v0"
