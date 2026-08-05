from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Recipe(Base, TimestampMixin):
    """One Italian recipe, crawled from a site's schema.org/Recipe JSON-LD.

    Italian-native by choice: an English corpus forced a translation layer and produced
    recipes nobody here would actually cook. Populated in Phase 4; the table exists now so
    the schema stays stable.

    `ingredient_product_ids` is the join that matters — each `recipeIngredient` string is
    resolved through the same normalize_text + ProductAlias pipeline the barcode path uses,
    so retrieval is an integer-array containment check rather than string matching.
    Retrieval is hybrid: exact inverted-index match first (does this use my expiring
    zucchini?), `embedding` only to rerank near-misses.
    """

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(String(700), unique=True, index=True, nullable=False)
    source_site: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    title_it: Mapped[str] = mapped_column(String(300), nullable=False)

    # recipeIngredient verbatim: ["320 g di pasta", "500 g di passata", ...]
    ingredients_raw: Mapped[list | None] = mapped_column(JSONB)
    # Resolved canonical products. NULL entries are dropped, so this is shorter than
    # ingredients_raw whenever an ingredient didn't map.
    ingredient_product_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), index=True)
    n_ingredients: Mapped[int | None] = mapped_column(Integer, index=True)

    servings: Mapped[int | None] = mapped_column(Integer)
    total_minutes: Mapped[int | None] = mapped_column(Integer, index=True)
    instructions: Mapped[str | None] = mapped_column(Text)

    # all-MiniLM-L6-v2 -> 384 dims, over the ingredient set. NULL until embedded.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecipeSuggestion(Base, TimestampMixin):
    """A generated suggestion. `accepted` is the feedback signal for whether the
    recommendation engine actually changes behaviour — the thing Module 3 is judged on."""

    __tablename__ = "recipe_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Inventory items this suggestion was built to rescue.
    matched_item_ids: Mapped[list | None] = mapped_column(JSONB)
    retrieved_recipe_ids: Mapped[list | None] = mapped_column(JSONB)
    # Ingredients the recipe needs that aren't in inventory and aren't staples — shown to
    # the user, so a suggestion is never silently uncookable.
    missing_ingredients: Mapped[list | None] = mapped_column(JSONB)
    text_it: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    accepted: Mapped[bool | None] = mapped_column(Boolean)
