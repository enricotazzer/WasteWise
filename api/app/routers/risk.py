"""Waste-risk scores and the v0-vs-v1 evaluation.

Thin, like `stats.py`: every number comes from `services/risk`, which is also what the
nightly jobs call. `/scores` recomputes live rather than reading `risk_scores` back, because
the table is *history* — the score an item had last night, kept so the detector can be
evaluated against what happened afterwards. Serving it as the current answer would mean the
Kitchen screen went stale between job runs, and would be empty entirely before the first one.
Each row still reports `last_persisted_at`, so a client can tell whether history exists.

Both responses carry `trained_on_demo` at the top level and a ready-to-render `warning`. The
synthetic household exists so the app isn't empty; a probability derived from it is not a
measurement, and this is where that stops being an internal detail.
"""

from fastapi import APIRouter, Query

from app.config import settings
from app.deps import CurrentUser, DbSession
from app.models.enums import RiskBand
from app.schemas.risk import RiskEvaluationResponse, RiskScoresResponse
from app.services.risk import scoring, survival

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/scores", response_model=RiskScoresResponse)
def scores(
    db: DbSession,
    user: CurrentUser,
    include_demo: bool = Query(default=False),
    band: RiskBand | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> RiskScoresResponse:
    """P(consumed before expiry) for everything still in the kitchen, highest risk first.

    Sorted by risk then by expiry date, which is the Kitchen screen's order — decided in the
    service so the list, the nightly job and any second client cannot disagree about it.

    `include_demo` defaults to False to match `/api/stats`. The Kitchen screen wants the demo
    household visible and should pass `include_demo=true`; whatever it passes, `provenance`
    reports what the answer was built from.
    """
    run = scoring.score_active(db, user_id=user.id, include_demo=include_demo)
    response = RiskScoresResponse.from_run(run, threshold=settings.risk_at_risk_threshold)

    # Filtering and truncation happen after the counts are computed, so `at_risk_count` and
    # `band_counts` keep describing the whole kitchen rather than the current page of it.
    if band is not None:
        response.items = [item for item in response.items if item.risk_band is band]
    response.items = response.items[:limit]
    return response


@router.get("/evaluation", response_model=RiskEvaluationResponse)
def evaluation(
    db: DbSession,
    user: CurrentUser,
    include_demo: bool = Query(default=False),
) -> RiskEvaluationResponse:
    """How well the at-risk flag actually works: v0 vs v1, on a held-out later period.

    Precision/recall are measured at `risk_at_risk_threshold` against realised waste, over
    items scored **as at purchase** — the hardest and only leakage-free version of the
    question. The c-index is a separate measurement over each model's hazard ranking and uses
    censored items too, which is why its row count is larger.

    A persisted fit is reported exactly as it was measured; re-splitting it here would put its
    own training rows into the test set. Only `gate` is live, so "fitted below the gate, gate
    now met" shows up as the prompt to re-fit that it is.
    """
    report = survival.evaluate(db, user_id=user.id, include_demo=include_demo)
    return RiskEvaluationResponse.from_report(report, include_demo=include_demo)
