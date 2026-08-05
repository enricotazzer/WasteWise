"""Load the starter recipe corpus and resolve its ingredients to canonical products.

Ingredient strings are written the way a recipe site writes them, then matched through the
**same `normalize_text` + ProductAlias pipeline the barcode path uses**. That is deliberate:
it means the match rate printed here is a genuine measurement of that matcher, and every
alias confirmed elsewhere in the app improves recipe matching for free.

Unmatched ingredients are **reported, never silently dropped** — an unmatched string is a
missing alias, and a graph built on quietly-lost ingredients would be wrong in a way nobody
would notice.

Idempotent: recipes are upserted by `source_url`, so re-running after editing the corpus
updates in place.
"""

import re
import sys
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Product, ProductAlias, Recipe
from app.models.enums import AliasSource
from app.seeds.recipes import RECIPES
from app.services.normalize import normalize_text

SOURCE_SITE = "seed"

# Measure words that precede the ingredient itself. Stripped so "400 g di pomodori" and
# "2 pomodori" both reduce to "pomodori".
_MEASURES = r"(?:g|gr|kg|ml|cl|l|lt|fette|fetta|spicchi|spicchio|cucchiai|cucchiaio|foglie)"
_LEADING_QTY = re.compile(
    rf"^\s*\d+[.,]?\d*\s*(?:{_MEASURES}\b)?\s*(?:di\s+|d')?", flags=re.IGNORECASE
)

# Italian recipes name ingredients in whatever number reads naturally; the catalog picks one.
# These are real aliases, so they go into product_aliases and help every other matcher too.
SYNONYMS: dict[str, str] = {
    "cipolla": "Cipolle",
    "carota": "Carote",
    "limone": "Limoni",
    "mela": "Mele",
    "banana": "Banane",
    "arancia": "Arance",
    "patata": "Patate",
    "zucchina": "Zucchine",
    "melanzana": "Melanzane",
    "peperone": "Peperoni",
    "uovo": "Uova",
    "pomodoro": "Pomodori",
    "pomodori pelati": "Pelati",
    "pomodorini": "Pomodori",
    "olio": "Olio extravergine",
    "olio evo": "Olio extravergine",
    "pepe": "Pepe nero",
    "parmigiano reggiano": "Parmigiano",
    "latte": "Latte intero",
    "yogurt": "Yogurt bianco",
    "tonno": "Tonno in scatola",
    "pane raffermo": "Pane",
    "fagiolini": "Fagioli",
}


def parse_ingredient(raw: str) -> str:
    """'400 g di passata di pomodoro' -> 'passata di pomodoro'."""
    return _LEADING_QTY.sub("", raw).strip()


def seed_synonyms(db: Session) -> int:
    """Register the plural/singular and colloquial forms as real aliases."""
    created = 0
    for phrase, product_name in SYNONYMS.items():
        product = db.scalar(select(Product).where(Product.name_it == product_name))
        if product is None:
            print(f"  ! synonym target missing: {product_name}", file=sys.stderr)
            continue
        norm = normalize_text(phrase)
        if not norm:
            continue
        if db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm)) is None:
            db.add(
                ProductAlias(
                    raw_text_norm=norm,
                    product_id=product.id,
                    source=AliasSource.SEED,
                    confidence=1.0,
                )
            )
            created += 1
    db.flush()
    return created


def resolve_ingredient(db: Session, raw: str) -> int | None:
    """Ingredient string -> product id, or None. Same two tiers as the barcode resolver."""
    text = parse_ingredient(raw)
    norm = normalize_text(text)
    if not norm:
        return None

    alias = db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm))
    if alias is not None:
        return alias.product_id

    # Exact canonical name as a second chance — aliases strip pack sizes, names don't.
    product = db.scalar(select(Product).where(Product.name_it.ilike(text.strip())))
    return product.id if product else None


def main() -> int:
    with SessionLocal() as db:
        if db.scalar(select(Product).limit(1)) is None:
            print("no products found — run `make seed` first", file=sys.stderr)
            return 1

        new_aliases = seed_synonyms(db)
        print(f"registered {new_aliases} new ingredient aliases")

        matched = 0
        total = 0
        unmatched: Counter[str] = Counter()
        created = 0

        for title, servings, minutes, ingredients, instructions in RECIPES:
            slug = normalize_text(title).lower().replace(" ", "-")
            url = f"seed://{SOURCE_SITE}/{slug}"

            product_ids: list[int] = []
            for raw in ingredients:
                total += 1
                pid = resolve_ingredient(db, raw)
                if pid is None:
                    unmatched[parse_ingredient(raw)] += 1
                    continue
                matched += 1
                if pid not in product_ids:
                    product_ids.append(pid)

            recipe = db.scalar(select(Recipe).where(Recipe.source_url == url))
            if recipe is None:
                recipe = Recipe(source_url=url)
                db.add(recipe)
                created += 1
            recipe.source_site = SOURCE_SITE
            recipe.title_it = title
            recipe.ingredients_raw = ingredients
            recipe.ingredient_product_ids = product_ids
            recipe.n_ingredients = len(ingredients)
            recipe.servings = servings
            recipe.total_minutes = minutes
            recipe.instructions = instructions
            recipe.scraped_at = datetime.now(UTC)

        db.commit()

        rate = matched / total if total else 0
        print(f"loaded {len(RECIPES)} recipes ({created} new)")
        print(f"ingredient match rate: {matched}/{total} = {rate:.0%}")
        if unmatched:
            print("\nunmatched ingredients (each one is a missing alias, not a lost row):")
            for text, count in unmatched.most_common(15):
                print(f"  {count:>3}x  {text}")
        print("\n  source_site='seed' — the phase 4 crawler replaces these by provenance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
