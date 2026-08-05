from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import AliasSource, Storage, Unit


class Category(Base, TimestampMixin):
    """Seeded taxonomy. `default_shelf_life_days` is the weakest expiry fallback and the
    cold-start prior for the survival model."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True, nullable=False)
    name_it: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    storage: Mapped[Storage] = mapped_column(Enum(Storage, native_enum=False), nullable=False)
    default_shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    # Non-food lines (detergents, toiletries) are excluded from every waste metric.
    is_food: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_perishable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base, TimestampMixin):
    """Canonical product vocabulary. `name_it` is what the UI shows and what Open Food Facts
    names normalize into; `name_en` is kept because the OFF category taxonomy is
    English-keyed (`en:mozzarella`) and it costs nothing to carry."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), index=True, nullable=False
    )
    name_it: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name_en: Mapped[str | None] = mapped_column(String(160), index=True)
    default_unit: Mapped[Unit] = mapped_column(Enum(Unit, native_enum=False), default=Unit.PZ)
    # Overrides the category default when set.
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    # Days remaining once opened — many items expire on opening, not on the printed date.
    shelf_life_days_opened: Mapped[int | None] = mapped_column(Integer)
    is_perishable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Pantry staples (olio, sale, farina...) are assumed to be in the house. Recipe ranking
    # never counts them as a missing ingredient, otherwise every recipe looks uncookable.
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    # Eager: the category carries is_food / shelf-life defaults that nearly every read of a
    # Product immediately needs.
    category: Mapped["Category"] = relationship(back_populates="products", lazy="joined")
    aliases: Mapped[list["ProductAlias"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def effective_shelf_life_days(self) -> int | None:
        return self.shelf_life_days or self.category.default_shelf_life_days


class ProductAlias(Base, TimestampMixin):
    """The text learning table. Open Food Facts product names, recipe ingredient strings and
    user corrections all normalize into this one vocabulary, so a name confirmed once is
    recognised everywhere afterwards without any retraining.

    Barcodes are the other half of the same idea and live in ProductBarcode — digits there,
    text here."""

    __tablename__ = "product_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Normalized product text: uppercased, accents/punctuation/pack sizes stripped.
    raw_text_norm: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    source: Mapped[AliasSource] = mapped_column(
        Enum(AliasSource, native_enum=False), default=AliasSource.LLM, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # Set when a human confirms it in the review screen. NULL = still a guess.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="aliases")
