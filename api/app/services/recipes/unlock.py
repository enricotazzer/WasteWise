"""What one ingredient would unlock the most?

This is the question coverage ranking genuinely cannot answer, and the reason the graph earns
its place: rank.py scores the recipes you can *nearly* make, but it can't tell you the single
purchase that would turn several of them cookable at once.

Deliberately conservative about what counts as "unlocked": a recipe only counts if buying
this one product leaves **nothing else** missing. "Buy tomatoes and you're two ingredients
away from four recipes" is not advice, it's noise.

It also only counts recipes that rescue something at risk — otherwise the top suggestion is
always a pantry staple that appears in half the corpus, which is true and useless.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Recipe
from app.services.recipes.graph import active_products


@dataclass
class UnlockSuggestion:
    product_id: int
    name: str
    category_code: str
    #: Recipes that become fully cookable if this one thing is bought.
    unlocks: int
    #: Items already in the house that those recipes would rescue.
    rescues: list[str] = field(default_factory=list)
    recipe_titles: list[str] = field(default_factory=list)


def best_unlocks(db: Session, user_id: int, *, limit: int = 3) -> list[UnlockSuggestion]:
    nodes = active_products(db, user_id)
    if not nodes:
        return []

    products = {p.id: p for p in db.scalars(select(Product))}
    at_risk = {pid for pid, n in nodes.items() if n.at_risk}
    if not at_risk:
        return []

    # candidate product -> (recipes it would complete, at-risk items those rescue)
    tally: dict[int, tuple[list[str], set[str]]] = {}

    for recipe in db.scalars(select(Recipe).where(Recipe.ingredient_product_ids.isnot(None))):
        ids = recipe.ingredient_product_ids or []
        if not ids:
            continue

        # Everything absent that isn't assumed to be in the cupboard.
        gaps = [
            pid
            for pid in ids
            if pid not in nodes and pid in products and not products[pid].is_staple
        ]
        # Exactly one gap — anything more isn't a single purchase.
        if len(gaps) != 1:
            continue

        rescued = {nodes[pid].name for pid in ids if pid in at_risk}
        if not rescued:
            continue

        titles, items = tally.setdefault(gaps[0], ([], set()))
        titles.append(recipe.title_it)
        items.update(rescued)

    suggestions = [
        UnlockSuggestion(
            product_id=pid,
            name=products[pid].name_it,
            category_code=products[pid].category.code,
            unlocks=len(titles),
            rescues=sorted(items),
            recipe_titles=titles[:5],
        )
        for pid, (titles, items) in tally.items()
    ]
    suggestions.sort(key=lambda s: (-s.unlocks, -len(s.rescues), s.name))
    return suggestions[:limit]
