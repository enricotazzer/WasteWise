"""v1 — a Cox proportional-hazards fit, a gradient-boosting comparison, and the evaluation.

Three properties matter more than the model itself, and each is enforced structurally rather
than by remembering to be careful:

**1. The split is by time, never at random.** `split_by_acquisition` is the only way into a
fit, and it partitions on `acquired_at`, so no row acquired after the cutoff can land in the
training set. A random split would let the model learn from consumption that had not happened
yet and would inflate every number below — quietly, and by a lot. The training rows are also
*administratively censored* at the cutoff: an item bought in the training period but eaten in
the test period contributes only "still here on the cutoff date", which is genuinely all the
model would have known.

**2. Below the gate, it refuses.** `risk_v1_min_resolved` / `risk_v1_min_waste_events` are the
counts at which a c-index stops being noise. Under them, `fit` returns a report with
`fitted=False` and the shortfall spelled out, rather than a plausible-looking 0.68 that
nobody can tell apart from a real one. `force_below_gate=True` overrides it for diagnostics
and stamps `-ungated` onto the version so the override cannot be lost downstream.

**3. Whatever the fit was built from travels with it.** If any synthetic row from
`seeds/demo.py` was in the fitting set, the model version carries `-demo` and every response
that reads the model echoes `trained_on_demo=True`. Nothing about that is manual: clear the
demo rows, re-run `make risk-fit`, and the suffix and the flag both disappear on their own.

Two metrics, on purpose
--------------------------------------------------------------------------------
*Concordance* is computed over each model's per-item **hazard**: does it rank the items that
got eaten sooner above the ones that took longer? Cox contributes its partial hazard, v0 its
`rate_per_day`, the GBM its consumption probability. All three are rank scores of the same
quantity, which is exactly what a c-index consumes.

*Precision/recall* is computed at `risk_at_risk_threshold` over each model's
`P(consumed before expiry)`, against the outcome that actually matters: was the item thrown
away? Both are evaluated **as at purchase** — every item scored with the information that
existed when it came through the door. That is the hardest version of the question and the
only leakage-free one; the live score for an item already sitting in the fridge is the
conditional form of the same number and gets sharper as the expiry date approaches.

The GBM cannot use censored rows at all — it sees only items with a known outcome — which is
the structural reason CoxPH is the primary model and the GBM is a comparison. That difference
is a finding in itself and is reported rather than smoothed over.
"""

import json
import math
import os
import pickle
import warnings
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.utils import concordance_index
from sklearn.ensemble import GradientBoostingClassifier
from sqlalchemy.orm import Session

from app.config import settings
from app.services.risk.baseline import BaselineScorer, stamp_version
from app.services.risk.features import (
    MIN_DURATION_DAYS,
    build_frame,
    design_defaults,
    design_matrix,
    drop_degenerate,
)

V1_BASE: Final = "v1-cox"
GBM_NAME: Final = "v1-gbm"

# Held-out share of the *rows*, taken from the end of the timeline. A third is enough to
# measure with and leaves the fit two-thirds of an already small dataset.
DEFAULT_TEST_FRACTION: Final = 0.30

# Ridge penalty on the Cox fit. Non-optional at this sample size: ten covariates against
# ~100 training events will otherwise produce separated coefficients and a fit that fails to
# converge rather than one that is merely wrong.
COX_PENALIZER: Final = 0.1

# Shallow and slow, because the training set is ~100 rows. Depth 3 on 100 rows memorises the
# rows. The seed is fixed so two runs of `make risk-fit` on the same data agree.
GBM_PARAMS: Final[dict[str, Any]] = {
    "n_estimators": 150,
    "learning_rate": 0.05,
    "max_depth": 2,
    "min_samples_leaf": 5,
    "subsample": 0.9,
    "random_state": 20260805,
}

# `/srv/data` is the volume mounted by docker-compose, so a fitted model outlives the
# container. The env override exists for tests, which must never write into the real one.
DEFAULT_MODEL_DIR: Final = "/srv/data/risk"
MODEL_FILE: Final = "v1_model.pkl"
MANIFEST_FILE: Final = "v1_model.json"


def model_dir() -> Path:
    return Path(os.environ.get("RISK_MODEL_DIR", DEFAULT_MODEL_DIR))


# --- results ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateStatus:
    """Whether there is enough *observed* outcome to fit v1 on.

    Repurchase-inferred closures do not count: `features.py` censors them, because a guess
    about whether something was eaten is not an observation of it.
    """

    resolved: int
    waste_events: int
    min_resolved: int
    min_waste_events: int

    @property
    def met(self) -> bool:
        return self.resolved >= self.min_resolved and self.waste_events >= self.min_waste_events

    @property
    def summary(self) -> str:
        state = "gate met" if self.met else "gate not met"
        return (
            f"{state}: {self.resolved}/{self.min_resolved} resolved, "
            f"{self.waste_events}/{self.min_waste_events} waste events"
        )

    @property
    def resolved_short(self) -> int:
        return max(self.min_resolved - self.resolved, 0)

    @property
    def waste_short(self) -> int:
        return max(self.min_waste_events - self.waste_events, 0)


@dataclass(frozen=True, slots=True)
class ModelMetrics:
    """One model's scorecard on the held-out period.

    `precision`/`recall`/`f1` are None rather than 0.0 when the denominator is empty — a
    model that flagged nothing has an undefined precision, not a perfect or a zero one, and
    the difference decides whether the number belongs in a report at all.
    """

    name: str
    concordance: float | None
    threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float | None
    recall: float | None
    f1: float | None
    flagged: int
    positives: int
    evaluated: int
    # Rows the c-index was computed over — larger than `evaluated`, because concordance can
    # use censored items and a confusion matrix cannot.
    ranked: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Everything the API and `make risk-fit` need to say, in one object.

    Deliberately one shape for both producers — a gate refusal and a successful fit differ
    by which fields are populated, not by which type comes back, so no caller can handle one
    and forget the other.
    """

    generated_at: datetime
    gate: GateStatus
    fitted: bool
    # THE flag. See the fence note in `baseline.stamp_version`.
    trained_on_demo: bool
    fitted_below_gate: bool
    model_version: str | None
    threshold: float
    demo_rows: int
    total_rows: int
    split_on: date | None = None
    train_rows: int = 0
    test_rows: int = 0
    models: list[ModelMetrics] = field(default_factory=list)
    best_model: str | None = None
    v1_beats_v0: bool | None = None
    dropped_columns: list[str] = field(default_factory=list)
    design_columns: list[str] = field(default_factory=list)
    coefficients: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe, for the manifest a human reads next to the pickle."""
        payload = asdict(self)
        payload["generated_at"] = self.generated_at.isoformat()
        payload["split_on"] = self.split_on.isoformat() if self.split_on else None
        payload["gate"] = asdict(self.gate) | {"met": self.gate.met, "summary": self.gate.summary}
        return payload


@dataclass
class FittedModel:
    """What gets pickled into `/srv/data/risk`.

    Carries its own `defaults` and `design_columns`: filling a blank covariate differently at
    score time than at fit time would move the model out from under its own coefficients,
    silently.
    """

    version: str
    trained_on_demo: bool
    fitted_below_gate: bool
    fitted_at: datetime
    design_columns: list[str]
    defaults: dict[str, float]
    cox: CoxPHFitter
    gbm: GradientBoostingClassifier | None
    # Whether Cox actually beat v0 on the held-out concordance. The scoring job reads this:
    # shipping a learned model that lost to the heuristic would be worse than not shipping it.
    beats_v0: bool
    report: EvaluationReport


# --- the gate ---------------------------------------------------------------------------


def gate_status_from_frame(frame: pd.DataFrame) -> GateStatus:
    if len(frame) == 0:
        resolved = waste = 0
    else:
        waste = int(frame["event_wasted"].sum())
        resolved = int(frame["event_consumed"].sum()) + waste
    return GateStatus(
        resolved=resolved,
        waste_events=waste,
        min_resolved=settings.risk_v1_min_resolved,
        min_waste_events=settings.risk_v1_min_waste_events,
    )


def gate_status(
    db: Session,
    *,
    user_id: int | None = None,
    include_demo: bool = True,
    as_of: date | None = None,
) -> GateStatus:
    """Live gate check, straight off the database. Cheap enough for a request."""
    return gate_status_from_frame(
        build_frame(db, user_id=user_id, include_demo=include_demo, as_of=as_of)
    )


# --- the split --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Split:
    train: pd.DataFrame
    test: pd.DataFrame
    cutoff_ord: int

    @property
    def cutoff(self) -> date:
        return date.fromordinal(self.cutoff_ord)

    @property
    def usable(self) -> bool:
        return len(self.train) > 0 and len(self.test) > 0


def split_by_acquisition(
    frame: pd.DataFrame, *, test_fraction: float = DEFAULT_TEST_FRACTION
) -> Split:
    """Split on `acquired_at`. The only split function in this package, on purpose.

    Everything before the cutoff trains, everything on or after it tests, so
    `max(train.acquired_at) < min(test.acquired_at)` holds by construction rather than by
    convention. There is no seed and no shuffle to get wrong.

    Training rows are then censored at the cutoff: an item bought in week 2 and eaten in week
    7 enters the fit as "still unresolved after five weeks", which is what a model standing
    at the cutoff would actually have seen. Without this the split would still leak — the
    *label* would come from the future even though the row did not.
    """
    if len(frame) == 0:
        return Split(train=frame, test=frame, cutoff_ord=0)

    ordinals = np.sort(frame["acquired_ord"].unique())
    target_train = len(frame) * (1.0 - test_fraction)

    best_cutoff = int(ordinals[-1])
    best_gap = math.inf
    for candidate in ordinals[1:]:
        n_train = int((frame["acquired_ord"] < candidate).sum())
        gap = abs(n_train - target_train)
        if gap < best_gap:
            best_gap, best_cutoff = gap, int(candidate)

    train = frame[frame["acquired_ord"] < best_cutoff].copy()
    test = frame[frame["acquired_ord"] >= best_cutoff].copy()
    return Split(train=_censor_at(train, best_cutoff), test=test, cutoff_ord=best_cutoff)


def _censor_at(frame: pd.DataFrame, cutoff_ord: int) -> pd.DataFrame:
    """Administrative censoring: pretend it is `cutoff_ord` and nothing later is known yet."""
    if len(frame) == 0:
        return frame
    unknown = frame["resolved_ord"].isna() | (frame["resolved_ord"] >= cutoff_ord)
    capped = (cutoff_ord - frame["acquired_ord"]).clip(lower=MIN_DURATION_DAYS).astype(float)
    frame.loc[unknown, "duration_days"] = capped[unknown]
    frame.loc[unknown, "event_consumed"] = 0
    frame.loc[unknown, "event_wasted"] = 0
    frame.loc[unknown, "censored"] = 1
    return frame


# --- metrics ----------------------------------------------------------------------------


def confusion(flagged: np.ndarray, wasted: np.ndarray) -> tuple[int, int, int, int]:
    """(tp, fp, fn, tn) for the at-risk flag. Positive class = the item was thrown away."""
    flagged = np.asarray(flagged, dtype=bool)
    wasted = np.asarray(wasted, dtype=bool)
    tp = int(np.sum(flagged & wasted))
    fp = int(np.sum(flagged & ~wasted))
    fn = int(np.sum(~flagged & wasted))
    tn = int(np.sum(~flagged & ~wasted))
    return tp, fp, fn, tn


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _concordance(durations: np.ndarray, hazard: np.ndarray, events: np.ndarray) -> float | None:
    """c-index over the model's hazard ranking.

    `concordance_index` wants scores where *higher means longer survival*, so a hazard is
    negated on the way in. Getting this backwards produces 1 - c, which for a decent model
    looks like a spectacularly bad one and for a bad one looks fine.
    """
    finite = np.isfinite(hazard)
    if finite.sum() < 2 or events[finite].sum() < 1:
        return None
    try:
        return float(concordance_index(durations[finite], -hazard[finite], events[finite]))
    except ZeroDivisionError:
        # No admissible pairs: every observation censored at the same time, or one row.
        return None


def score_metrics(
    name: str,
    *,
    frame: pd.DataFrame,
    hazard: np.ndarray,
    p_consume: np.ndarray,
    threshold: float,
) -> ModelMetrics:
    """One model's numbers over one test frame.

    The confusion matrix is restricted to rows with both a known outcome and an expiry date:
    an item still in the fridge has no answer yet, and an item with no expiry has no deadline
    to miss. Concordance uses every row, censored ones included — that is what it is for.
    """
    durations = frame["duration_days"].to_numpy(dtype=float)
    consumed = frame["event_consumed"].to_numpy(dtype=int)
    wasted = frame["event_wasted"].to_numpy(dtype=int)

    scored = (consumed + wasted) > 0
    dated = frame["has_expiry"].to_numpy(dtype=int) == 1
    mask = scored & dated & np.isfinite(p_consume)

    flagged = p_consume[mask] < threshold
    truth = wasted[mask] == 1
    tp, fp, fn, tn = confusion(flagged, truth)

    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )

    return ModelMetrics(
        name=name,
        concordance=_concordance(durations, hazard, consumed),
        threshold=threshold,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        flagged=int(flagged.sum()),
        positives=int(truth.sum()),
        evaluated=int(mask.sum()),
        ranked=int(len(frame)),
    )


# --- model application ------------------------------------------------------------------


def cox_probabilities(
    cox: CoxPHFitter, matrix: pd.DataFrame, *, start: np.ndarray, horizon: np.ndarray
) -> np.ndarray:
    """P(consumed within `horizon` days | still unresolved after `start` days), per row.

    The conditioning is what makes a live score improve as an item ages: an aubergine that
    has already survived six days is a different bet from the one that came home this
    morning, and the unconditional curve cannot tell them apart.
    """
    if len(matrix) == 0:
        return np.zeros(0)

    start = np.nan_to_num(np.asarray(start, dtype=float), nan=0.0).clip(min=0.0)
    horizon = np.nan_to_num(np.asarray(horizon, dtype=float), nan=0.0).clip(min=0.0)
    grid = np.unique(np.concatenate([[0.0], start, start + horizon]))

    surface = cox.predict_survival_function(matrix, times=grid).to_numpy()
    columns = np.arange(len(matrix))
    s_start = surface[np.searchsorted(grid, start), columns]
    s_end = surface[np.searchsorted(grid, start + horizon), columns]

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(s_start > 1e-12, s_end / np.maximum(s_start, 1e-12), 1.0)
    return np.clip(1.0 - ratio, 0.0, 1.0)


def cox_hazard(cox: CoxPHFitter, matrix: pd.DataFrame) -> np.ndarray:
    if len(matrix) == 0:
        return np.zeros(0)
    return np.asarray(cox.predict_partial_hazard(matrix), dtype=float).reshape(-1)


def _gbm_probabilities(gbm: GradientBoostingClassifier, matrix: pd.DataFrame) -> np.ndarray:
    if len(matrix) == 0:
        return np.zeros(0)
    return np.asarray(gbm.predict_proba(matrix)[:, 1], dtype=float).reshape(-1)


def _baseline_arrays(scorer: BaselineScorer, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """v0's (hazard, P(consumed before expiry)) over a frame, scored as at purchase."""
    scores = scorer.score_frame(frame, at_purchase=True)
    hazard = np.array([s.rate_per_day for s in scores], dtype=float)
    probability = np.array(
        [
            math.nan if s.p_consume_before_expiry is None else s.p_consume_before_expiry
            for s in scores
        ],
        dtype=float,
    )
    return hazard, probability


# --- persistence ------------------------------------------------------------------------


def save_model(model: FittedModel) -> Path | None:
    """Pickle the fit next to a readable manifest. Returns None if the volume is read-only.

    The pickle is local-only and produced by this application; it is never accepted from
    anywhere else, which is the condition under which unpickling it is safe.
    """
    directory = model_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MODEL_FILE
        with path.open("wb") as handle:
            pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        with (directory / MANIFEST_FILE).open("w", encoding="utf-8") as handle:
            json.dump(model.report.as_dict(), handle, indent=2, ensure_ascii=False)
    except OSError:
        return None
    return path


def load_model() -> FittedModel | None:
    """The last fit, or None. Never raises: a missing or unreadable model must fall back to
    v0 rather than take the nightly job down."""
    path = model_dir() / MODEL_FILE
    try:
        with path.open("rb") as handle:
            model = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, AttributeError, EOFError, ImportError):
        return None
    return model if isinstance(model, FittedModel) else None


def clear_model() -> None:
    """Remove the persisted fit. Used by tests and by anyone who wants v0 back."""
    for name in (MODEL_FILE, MANIFEST_FILE):
        (model_dir() / name).unlink(missing_ok=True)


# --- fitting ----------------------------------------------------------------------------


def fit(
    db: Session,
    *,
    user_id: int | None = None,
    include_demo: bool = True,
    as_of: date | None = None,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    force_below_gate: bool = False,
    persist: bool = True,
) -> EvaluationReport:
    """Fit v1, evaluate it against v0 on a held-out later period, and persist the winner.

    `include_demo` defaults to True and that is the whole point of the fence: the fitting set
    is simply whatever the household has, and the version stamp reports what that was. Run it
    today and you get `v1-cox-demo`; run `make demo-clear`, log for real, run it again, and
    you get `v1-cox` — with no code change and no flag to remember.
    """
    frame = build_frame(db, user_id=user_id, include_demo=include_demo, as_of=as_of)
    gate = gate_status_from_frame(frame)
    demo_rows = int(frame["is_demo"].sum()) if len(frame) else 0
    threshold = settings.risk_at_risk_threshold
    notes: list[str] = []

    base = EvaluationReport(
        generated_at=datetime.now(UTC),
        gate=gate,
        fitted=False,
        trained_on_demo=False,
        fitted_below_gate=False,
        model_version=None,
        threshold=threshold,
        demo_rows=demo_rows,
        total_rows=int(len(frame)),
        notes=notes,
    )

    split = split_by_acquisition(frame, test_fraction=test_fraction)
    if not split.usable:
        notes.append("not enough history to hold out a later period — nothing to evaluate on")
        return base

    baseline = _baseline_report(base, split=split, threshold=threshold)

    if not gate.met and not force_below_gate:
        notes.append(gate.summary)
        notes.append(
            f"v1 not fitted: {gate.resolved_short} more resolved items and "
            f"{gate.waste_short} more waste events needed. A c-index from this much data "
            "would be noise wearing a number's clothes."
        )
        return baseline

    if not gate.met:
        notes.append(f"FORCED below the gate ({gate.summary}) — diagnostics, not evidence")

    fitted = _fit_v1(
        split=split,
        report=baseline,
        threshold=threshold,
        below_gate=not gate.met,
        notes=notes,
    )
    if fitted is None:
        return baseline

    model, report = fitted
    if persist:
        path = save_model(model)
        notes.append(f"model written to {path}" if path else "model NOT persisted (volume?)")
    return report


def _baseline_report(
    report: EvaluationReport, *, split: Split, threshold: float
) -> EvaluationReport:
    """v0's scorecard on the held-out period. Produced whether or not v1 is allowed to fit —
    the heuristic ships either way, so its numbers are always the ones in force.

    v0 is "trained" too: its tier tables are summarised from the training half. If synthetic
    rows are in there, the flag is set for the same reason it is set for Cox — this is about
    where the numbers came from, not about which estimator produced them.
    """
    trained_on_demo = bool(split.train["is_demo"].any())
    scorer = BaselineScorer.fit(split.train, threshold=threshold)
    hazard, probability = _baseline_arrays(scorer, split.test)
    metrics = score_metrics(
        stamp_version(settings.risk_model_version, trained_on_demo=trained_on_demo),
        frame=split.test,
        hazard=hazard,
        p_consume=probability,
        threshold=threshold,
    )
    return replace(
        report,
        trained_on_demo=trained_on_demo,
        # v0 is a model version too, and it is the one actually in force whenever the gate
        # refuses. Leaving this None here and letting the API substitute a bare "v0" would
        # print an unsuffixed version next to a metric row labelled `v0-demo` — the fence
        # holding everywhere except the field a reader looks at first.
        model_version=metrics.name,
        split_on=split.cutoff,
        train_rows=int(len(split.train)),
        test_rows=int(len(split.test)),
        models=[metrics],
        best_model=metrics.name,
    )


def _fit_v1(
    *,
    split: Split,
    report: EvaluationReport,
    threshold: float,
    below_gate: bool,
    notes: list[str],
) -> tuple[FittedModel, EvaluationReport] | None:
    """Fit CoxPH and the GBM comparison on the training half, score both on the test half."""
    defaults = design_defaults(split.train)
    train_matrix, dropped = drop_degenerate(design_matrix(split.train, defaults))
    if train_matrix.shape[1] == 0:
        notes.append("every covariate was constant in the training period — nothing to fit")
        return None
    if dropped:
        notes.append(f"constant in the training period, dropped: {', '.join(dropped)}")

    columns = list(train_matrix.columns)
    test_matrix = design_matrix(split.test, defaults)[columns].reset_index(drop=True)

    cox_frame = train_matrix.reset_index(drop=True)
    cox_frame["duration_days"] = split.train["duration_days"].to_numpy(dtype=float)
    cox_frame["event_consumed"] = split.train["event_consumed"].to_numpy(dtype=int)

    cox = CoxPHFitter(penalizer=COX_PENALIZER)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cox.fit(cox_frame, duration_col="duration_days", event_col="event_consumed")
        notes.extend(sorted({f"lifelines: {w.message}" for w in caught})[:3])
    except Exception as exc:  # lifelines raises several unrelated types on a bad design
        notes.append(f"CoxPH did not converge ({exc.__class__.__name__}) — staying on v0")
        return None

    # --- trained on what? ---------------------------------------------------------------
    # The fitting set is the training half, so this is literally "did a synthetic row shape
    # these coefficients". Test-only demo rows would not, and are not counted.
    trained_on_demo = bool(split.train["is_demo"].any())
    version = stamp_version(V1_BASE, trained_on_demo=trained_on_demo, below_gate=below_gate)

    metrics = list(report.models)
    metrics.append(
        score_metrics(
            version,
            frame=split.test,
            hazard=cox_hazard(cox, test_matrix),
            p_consume=cox_probabilities(
                cox,
                test_matrix,
                start=np.zeros(len(split.test)),
                horizon=split.test["shelf_life_days"].to_numpy(dtype=float),
            ),
            threshold=threshold,
        )
    )

    gbm = _fit_gbm(train_matrix.reset_index(drop=True), split.train, notes=notes)
    if gbm is not None:
        probability = _gbm_probabilities(gbm, test_matrix)
        gbm_metrics = score_metrics(
            stamp_version(GBM_NAME, trained_on_demo=trained_on_demo, below_gate=below_gate),
            frame=split.test,
            # A classifier has no time axis, so its consumption probability doubles as its
            # rank score. Documented rather than disguised: it answers *whether*, and a
            # c-index asks *when*.
            hazard=probability,
            p_consume=probability,
            threshold=threshold,
        )
        metrics.append(gbm_metrics)
        if gbm_metrics.concordance is not None and gbm_metrics.concordance < 0.5:
            notes.append(
                f"the GBM's c-index ({gbm_metrics.concordance:.3f}) is below 0.5 because its "
                "score is a classifier probability, not a hazard: a tin of chickpeas is very "
                "likely to be eaten and very slow to be eaten, so ranking by P(consumed) "
                "inverts the time ordering. Read its precision/recall, which is its own "
                "metric; the c-index row is measuring the mismatch, not the model."
            )

    ranked = [m for m in metrics if m.concordance is not None]
    best = max(ranked, key=lambda m: m.concordance or 0.0).name if ranked else None
    # `report.models[0]` is v0's row by construction — `_baseline_report` always runs first
    # and always writes exactly one. Matching on the name would break the moment the `-demo`
    # suffix is stamped onto it.
    v0_name = report.models[0].name
    v0_c = report.models[0].concordance
    cox_c = next((m.concordance for m in metrics if m.name == version), None)
    beats_v0 = bool(cox_c is not None and v0_c is not None and cox_c > v0_c)

    if v0_c is not None and cox_c is not None and not beats_v0:
        notes.append(
            f"v1 did NOT beat v0 on concordance ({cox_c:.3f} vs {v0_c:.3f}). "
            "Scoring stays on the v0 heuristic — reported, not hidden."
        )

    final = replace(
        report,
        fitted=True,
        trained_on_demo=trained_on_demo,
        fitted_below_gate=below_gate,
        model_version=version,
        models=metrics,
        best_model=best,
        v1_beats_v0=None if (v0_c is None or not ranked) else best != v0_name,
        dropped_columns=dropped,
        design_columns=columns,
        coefficients={k: float(v) for k, v in cox.params_.items()},
        notes=notes,
    )
    model = FittedModel(
        version=version,
        trained_on_demo=trained_on_demo,
        fitted_below_gate=below_gate,
        fitted_at=final.generated_at,
        design_columns=columns,
        defaults=defaults,
        cox=cox,
        gbm=gbm,
        beats_v0=beats_v0,
        report=final,
    )
    return model, final


def _fit_gbm(
    matrix: pd.DataFrame, train: pd.DataFrame, *, notes: list[str]
) -> GradientBoostingClassifier | None:
    """The sklearn comparison, on the rows it is capable of using.

    A classifier cannot represent censoring, so every item still sitting in the kitchen is
    simply dropped. That is not a tuning detail — it is the reason the Cox model is primary,
    and dropping ~30% of a small dataset is exactly the kind of thing a c-index comparison
    should be able to show.
    """
    observed = (train["event_consumed"] + train["event_wasted"]).to_numpy() > 0
    target = train["event_consumed"].to_numpy(dtype=int)[observed]
    if observed.sum() < 20 or len(np.unique(target)) < 2:
        notes.append("GBM skipped: too few observed outcomes, or only one class among them")
        return None
    notes.append(
        f"GBM trained on {int(observed.sum())} of {len(train)} training rows "
        f"({len(train) - int(observed.sum())} censored rows are unusable by a classifier)"
    )
    gbm = GradientBoostingClassifier(**GBM_PARAMS)
    gbm.fit(matrix[observed], target)
    return gbm


# --- reading the evaluation back --------------------------------------------------------


def evaluate(
    db: Session,
    *,
    user_id: int | None = None,
    include_demo: bool = True,
    as_of: date | None = None,
    test_fraction: float = DEFAULT_TEST_FRACTION,
) -> EvaluationReport:
    """What `/api/risk/evaluation` returns.

    A stored fit is reported as it was measured — re-splitting live would put its own
    training rows into the test set and turn the report into fiction. Only the gate is
    recomputed, because that is a live fact about the database rather than about the fit, and
    "fitted below the gate, gate now met" is precisely the state that should prompt a re-fit.

    With no stored fit, v0 is evaluated live. It needs no fitting beyond summarising the
    training half, so a fresh time split is leakage-free.
    """
    frame = build_frame(db, user_id=user_id, include_demo=include_demo, as_of=as_of)
    live_gate = gate_status_from_frame(frame)
    model = load_model()
    if model is not None:
        return replace(model.report, gate=live_gate)

    threshold = settings.risk_at_risk_threshold
    report = EvaluationReport(
        generated_at=datetime.now(UTC),
        gate=live_gate,
        fitted=False,
        trained_on_demo=False,
        fitted_below_gate=False,
        model_version=None,
        threshold=threshold,
        demo_rows=int(frame["is_demo"].sum()) if len(frame) else 0,
        total_rows=int(len(frame)),
        notes=["no fitted v1 model on disk — run `make risk-fit`", live_gate.summary],
    )
    split = split_by_acquisition(frame, test_fraction=test_fraction)
    if not split.usable:
        report.notes.append("not enough history to hold out a later period")
        return report
    return _baseline_report(report, split=split, threshold=threshold)
