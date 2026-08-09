"""Fit the v1 survival model, or explain why it refuses to.

    make risk-fit                    fit if the data gate is met, evaluate against v0, persist
    python -m app.jobs.fit --force   fit anyway, stamp `-ungated`, report as a diagnostic
    python -m app.jobs.fit --real    fit on real rows only, ignoring the demo household

The two flags have no Makefile target on purpose. `--force` produces a number the gate exists
to suppress, and `--real` answers a different question from the one `make risk-fit` asks;
neither should be one tab-completion away from the default path.

The interesting case today is the first one *failing*. Below `risk_v1_min_resolved` /
`risk_v1_min_waste_events` this prints how far short the household is and stops. That is the
job working, not the job broken: a concordance index computed from forty items is noise
wearing a number's clothes, and printing it would put a figure into someone's notes that
nothing later could dislodge.

Idempotent in the sense that matters for a fit: same data in, same model out. The GBM seed
and the time-based split are both fixed, so two runs an hour apart agree.
"""

import sys
from datetime import date

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.services.risk import survival
from app.services.risk.survival import EvaluationReport


def _fmt(value: float | None, spec: str = ".3f") -> str:
    return "—" if value is None else format(value, spec)


def _print_report(report: EvaluationReport) -> None:
    print(report.gate.summary)
    print(
        f"  {report.total_rows} items in the frame"
        f" ({report.demo_rows} synthetic) · threshold {report.threshold}"
    )
    if report.split_on:
        print(
            f"  time split on {report.split_on.isoformat()}:"
            f" {report.train_rows} train / {report.test_rows} test"
            "  (split by acquired_at — never at random)"
        )

    if report.models:
        header = f"{'model':<22} {'c-index':>8} {'prec':>7} {'recall':>7} {'F1':>7}"
        print(f"\n  {header} {'TP/FP/FN':>12}")
        for metrics in report.models:
            counts = f"{metrics.true_positives}/{metrics.false_positives}/{metrics.false_negatives}"
            print(
                f"  {metrics.name:<22} {_fmt(metrics.concordance):>8}"
                f" {_fmt(metrics.precision, '.2f'):>7} {_fmt(metrics.recall, '.2f'):>7}"
                f" {_fmt(metrics.f1, '.2f'):>7} {counts:>12}"
            )
        first = report.models[0]
        print(
            f"\n  scored over {first.evaluated} resolved test items"
            f" ({first.positives} actually wasted); c-index ranked {first.ranked} rows,"
            " censored ones included"
        )

    if report.coefficients:
        print("\n  Cox coefficients (log-hazard of consumption, positive = eaten sooner):")
        for name, value in sorted(report.coefficients.items(), key=lambda kv: -abs(kv[1])):
            print(f"    {name:<26} {value:+.3f}")

    if report.fitted:
        verdict = {
            True: "v1 beat v0 on concordance",
            False: "v1 did NOT beat v0 — reporting it, not hiding it",
            None: "not comparable (one of the two produced no c-index)",
        }[report.v1_beats_v0]
        print(f"\n  {verdict}. Best on this split: {report.best_model}")

    for note in report.notes:
        print(f"  note: {note}")

    if report.trained_on_demo:
        print(
            "\n  ⚠️  DEMO-DERIVED. Synthetic rows from `make demo` were in the fitting set, so"
            "\n      every figure above describes a generator, not a household. The version is"
            "\n      stamped `-demo` and the API echoes trained_on_demo=true until real logging"
            "\n      carries the fit. `make demo-clear`, then re-run — nothing else to change."
        )
    if report.fitted_below_gate:
        print(
            "\n  ⚠️  FORCED below the data gate (`-ungated`). Diagnostics only: the numbers"
            "\n      above have confidence intervals wide enough to contain their opposites."
        )


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    include_demo = "--real" not in args

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.first_user_email))
        if user is None:
            print("no user found — run `make seed` first", file=sys.stderr)
            return 1

        report = survival.fit(
            db,
            user_id=user.id,
            include_demo=include_demo,
            as_of=date.today(),
            force_below_gate=force,
        )
        _print_report(report)

        if not report.fitted and not force:
            print("\n  Nothing was persisted. `make risk-score` continues on the v0 heuristic,")
            print("  which is the model that ships and the baseline v1 has to beat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
