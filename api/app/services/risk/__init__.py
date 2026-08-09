"""The waste-risk model.

Three layers, in the order a reader should meet them:

    features.py   one row per inventory item, in survival form
    baseline.py   v0 — the shipped heuristic, and the yardstick v1 has to beat
    survival.py   v1 — CoxPH + a GBM comparison, gated on having enough real outcomes
    scoring.py    what the nightly job persists and what the API returns, from one code path

Every number that leaves this package carries `trained_on_demo`. The synthetic household in
`seeds/demo.py` exists so the app isn't empty on day one; it is not evidence, and a score
derived from it must say so all the way to the screen. See the fence in `baseline.py`.
"""

from app.services.risk.baseline import BaselineScore, BaselineScorer, risk_band, stamp_version
from app.services.risk.features import DESIGN_COLUMNS, build_frame, design_defaults, design_matrix
from app.services.risk.scoring import ScoredItem, ScoringRun, persist, score_active
from app.services.risk.survival import (
    EvaluationReport,
    GateStatus,
    ModelMetrics,
    evaluate,
    fit,
    gate_status,
    load_model,
)

__all__ = [
    "DESIGN_COLUMNS",
    "BaselineScore",
    "BaselineScorer",
    "EvaluationReport",
    "GateStatus",
    "ModelMetrics",
    "ScoredItem",
    "ScoringRun",
    "build_frame",
    "design_defaults",
    "design_matrix",
    "evaluate",
    "fit",
    "gate_status",
    "load_model",
    "persist",
    "risk_band",
    "score_active",
    "stamp_version",
]
