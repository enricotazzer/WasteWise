from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import RiskBand


class RiskScore(Base, TimestampMixin):
    """One row per (item, scoring run). Append-only rather than an in-place update, so
    Phase 3's evaluation can replay what the model predicted *at the time* against what
    actually happened — scoring history is the whole basis of the precision/recall figure.

    `model_version` distinguishes the v0 heuristic from v1 Cox/GBM, which is what makes
    the "did the learned model beat the baseline" comparison possible.
    """

    __tablename__ = "risk_scores"
    __table_args__ = (Index("ix_risk_item_computed", "inventory_item_id", "computed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    p_consume_3d: Mapped[float | None] = mapped_column(Float)
    p_consume_7d: Mapped[float | None] = mapped_column(Float)
    # P(consumed before its own expiry date) — the number the UI actually acts on.
    p_consume_before_expiry: Mapped[float | None] = mapped_column(Float)
    risk_band: Mapped[RiskBand] = mapped_column(
        Enum(RiskBand, native_enum=False), nullable=False, index=True
    )
    # Feature snapshot, so a prediction can be explained and debugged after the fact.
    features: Mapped[dict | None] = mapped_column(JSONB)
