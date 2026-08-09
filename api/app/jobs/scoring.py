"""Recompute `risk_scores` for everything still in the kitchen.

Nightly, and the last link of `make jobs` — repurchase closes stale items, savings re-derives
the money, and this re-scores what is left. In that order, because a score computed before
the repurchase job runs is a score built on items the household finished a week ago.

Idempotent: re-running on the same day replaces that day's rows rather than adding to them.
The table stays append-per-day on purpose — see the note in `services/risk/scoring.py`, which
is also where the scoring itself lives. This module is the entry point and the summary, and
nothing else: the API serves the identical numbers by calling the same function.
"""

import sys
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.services.risk import scoring


@dataclass
class ScoringSummary:
    scored: int = 0
    written: int = 0
    demo_items: int = 0
    model_version: str = ""
    model_stage: str = ""
    trained_on_demo: bool = False
    fitted_below_gate: bool = False
    bands: Counter = field(default_factory=Counter)
    at_risk: int = 0
    notes: list[str] = field(default_factory=list)


def run(
    db: Session, *, user_id: int | None = None, include_demo: bool = True, persist: bool = True
) -> ScoringSummary:
    """Score, optionally persist, and report what the run consisted of."""
    result = scoring.score_active(db, user_id=user_id, include_demo=include_demo)
    written = scoring.persist(db, result) if persist else 0

    threshold = settings.risk_at_risk_threshold
    return ScoringSummary(
        scored=len(result.items),
        written=written,
        demo_items=result.demo_items,
        model_version=result.model_version,
        model_stage=result.model_stage,
        trained_on_demo=result.trained_on_demo,
        fitted_below_gate=result.fitted_below_gate,
        bands=Counter(item.band.value for item in result.items),
        at_risk=sum(
            1
            for item in result.items
            if item.p_consume_before_expiry is not None and item.p_consume_before_expiry < threshold
        ),
        notes=result.notes,
    )


def main() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.first_user_email))
        if user is None:
            print("no user found — run `make seed` first", file=sys.stderr)
            return 1

        summary = run(db, user_id=user.id)

        print(f"{summary.scored} active items scored with {summary.model_version}")
        if not summary.scored:
            print("  (nothing in the kitchen — the scorer has nothing to say yet)")
            for note in summary.notes:
                print(f"  note: {note}")
            return 0

        print(f"  {summary.written} rows written to risk_scores (today's run, replaced in place)")
        for band in ("high", "medium", "low"):
            print(f"  {band:<7} {summary.bands.get(band, 0):>4}")
        print(
            f"\n  {summary.at_risk} flagged at risk "
            f"(P(consumed before expiry) < {settings.risk_at_risk_threshold})"
        )

        for note in summary.notes:
            print(f"  note: {note}")

        # The fence, printed. A run over synthetic rows must say so on the terminal too, not
        # only in the database column nobody looks at.
        if summary.trained_on_demo:
            print(
                f"\n  ⚠️  {summary.demo_items} of these items are synthetic (`is_demo`), and the"
                "\n      history behind every score above includes them. These probabilities are"
                "\n      NOT measurements. `make demo-clear` removes them; the `-demo` suffix"
                "\n      disappears from the version on its own once it is gone."
            )
        if summary.fitted_below_gate:
            print("      The fitted model was forced below the v1 data gate (`-ungated`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
