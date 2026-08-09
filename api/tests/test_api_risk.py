"""`/api/risk`.

The endpoint tests are mostly contract tests for the demo fence. The frontend renders a
persistent warning band off `trained_on_demo` and `provenance.warning`, so those two fields
are as load-bearing as the probabilities next to them: a score derived from the synthetic
household that arrives at the browser looking like a measurement is the one failure this
whole phase is designed to prevent.
"""

from datetime import date, timedelta

import factories
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.services.risk import survival

TODAY = date.today()


def _kitchen(db: Session, seeded: dict, *, is_demo: bool = False) -> None:
    user = seeded["user"]
    factories.resolved_history(
        db,
        user,
        [seeded["mozzarella"], seeded["ricotta"]],
        consumed=12,
        wasted=4,
        end=TODAY,
        is_demo=is_demo,
    )
    factories.item(
        db,
        user,
        seeded["mozzarella"],
        acquired_at=TODAY - timedelta(days=4),
        expiry_date=TODAY + timedelta(days=1),
        is_demo=is_demo,
    )
    factories.item(
        db,
        user,
        seeded["pasta"],
        acquired_at=TODAY - timedelta(days=2),
        expiry_date=TODAY + timedelta(days=300),
        is_demo=is_demo,
    )
    db.commit()


def test_both_endpoints_require_a_token(client: TestClient) -> None:
    assert client.get("/api/risk/scores").status_code == 401
    assert client.get("/api/risk/evaluation").status_code == 401


def test_scores_returns_a_renderable_list_without_a_second_call(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    _kitchen(db, seeded)

    body = auth_client.get("/api/risk/scores").json()

    assert body["total_items"] == 2
    assert body["threshold"] == settings.risk_at_risk_threshold
    assert sum(body["band_counts"].values()) == 2

    row = body["items"][0]
    # Everything a Kitchen row needs: name, category, unit, dates, and the score itself.
    assert {"product_name", "category_code", "unit", "expiry_date", "days_to_expiry"} <= set(row)
    assert row["risk_band"] in {"low", "medium", "high"}
    assert row["at_risk"] is (row["risk_band"] == "high")
    assert row["explain"]["tier"]
    # Highest risk first — the short-dated mozzarella above the 300-day pasta.
    assert (
        body["items"][0]["p_consume_before_expiry"] <= body["items"][1]["p_consume_before_expiry"]
    )


def test_scores_can_be_filtered_to_one_band_without_distorting_the_counts(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The counts describe the kitchen; `items` describes the current page of it. A band
    filter that also moved the totals would make the summary line disagree with itself."""
    _kitchen(db, seeded)

    body = auth_client.get("/api/risk/scores", params={"band": "low"}).json()

    assert body["total_items"] == 2
    assert all(item["risk_band"] == "low" for item in body["items"])
    assert len(body["items"]) == body["band_counts"]["low"]


def test_synthetic_history_is_flagged_at_the_top_level_and_carries_banner_copy(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The second and third sides of the fence. `trained_on_demo` sits at the top level so a
    client cannot miss it, and `warning` is pre-written so the sentence cannot drift from the
    flag that triggered it."""
    _kitchen(db, seeded, is_demo=True)

    body = auth_client.get("/api/risk/scores", params={"include_demo": "true"}).json()

    assert body["trained_on_demo"] is True
    assert body["provenance"]["trained_on_demo"] is True
    assert body["provenance"]["model_version"] == "v0-demo"
    assert body["provenance"]["demo_items"] == 2
    # Italian: this string is rendered verbatim into an Italian UI, like `name_it` and the
    # waste-reason labels. Log lines and docstrings stay English; user-facing copy does not.
    assert "non da consumi reali" in body["provenance"]["warning"]


def test_a_real_household_gets_no_warning_and_no_suffix(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """Same endpoint, same defaults, a database with nothing synthetic in it."""
    _kitchen(db, seeded)

    body = auth_client.get("/api/risk/scores").json()

    assert body["trained_on_demo"] is False
    assert body["provenance"]["model_version"] == "v0"
    assert body["provenance"]["warning"] is None


def test_demo_rows_are_excluded_by_default_like_every_other_endpoint(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    _kitchen(db, seeded, is_demo=True)

    default = auth_client.get("/api/risk/scores").json()
    included = auth_client.get("/api/risk/scores", params={"include_demo": "true"}).json()

    assert default["total_items"] == 0
    assert default["trained_on_demo"] is False
    assert included["total_items"] == 2


def test_evaluation_reports_the_gate_rather_than_a_number_it_cannot_support(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The state this project is actually in: v0 is live and measured, v1 is not fitted
    because there is nowhere near enough real data yet, and the response says both."""
    _kitchen(db, seeded)

    body = auth_client.get("/api/risk/evaluation").json()

    assert body["fitted"] is False
    assert body["gate"]["met"] is False
    assert body["gate"]["summary"] == "gate not met: 16/150 resolved, 4/40 waste events"
    assert body["gate"]["resolved_short"] == 134
    assert body["gate"]["waste_short"] == 36
    assert [m["name"] for m in body["models"]] == ["v0"]
    assert body["split_on"] is not None, "the split is by date, and the date is reported"
    assert body["v1_beats_v0"] is None


def test_evaluation_reports_a_fitted_model_with_both_comparisons(
    auth_client: TestClient, db: Session, seeded: dict, monkeypatch
) -> None:
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
    db.commit()
    survival.fit(db, user_id=seeded["user"].id)

    body = auth_client.get("/api/risk/evaluation").json()

    assert body["fitted"] is True
    assert body["gate"]["met"] is True
    names = [m["name"] for m in body["models"]]
    assert names[0] == "v0"
    assert "v1-cox" in names
    assert body["v1_beats_v0"] in (True, False)
    assert body["design_columns"]
    assert body["coefficients"]
    for metrics in body["models"]:
        assert metrics["threshold"] == settings.risk_at_risk_threshold
        assert metrics["true_positives"] + metrics["false_negatives"] == metrics["positives"]


def test_both_endpoints_answer_on_an_empty_kitchen(auth_client: TestClient) -> None:
    """No items, no history, no fitted model. Charts render on empty data or they don't
    render at all."""
    scores = auth_client.get("/api/risk/scores")
    assert scores.status_code == 200
    assert scores.json()["items"] == []
    assert scores.json()["at_risk_count"] == 0

    evaluation = auth_client.get("/api/risk/evaluation")
    assert evaluation.status_code == 200
    assert evaluation.json()["models"] == []
    assert evaluation.json()["gate"]["met"] is False
