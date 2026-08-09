"""Recipe ranking by coverage of what's about to go off.

This — not the graph — is what actually answers "what should I cook tonight". Strict
"recipe contains only things I have" returns an empty screen almost every day, so the score
is coverage-based:

    score = Σ urgency(item used)  −  0.5 × (missing non-staple ingredients)

Urgency weighting is the whole point: a recipe that rescues something expiring tomorrow beats
one that uses two things with a fortnight left. Staples (olio, sale, aglio…) are assumed to
be in the house and never count as missing — otherwise every recipe looks uncookable because
nobody logs their salt.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.services.recipes.graph import GraphNode, active_products
from app.services.recipes.retrieval import DEFAULT_POOL, candidate_recipes


@dataclass
class RecipeMatch:
    recipe_id: int
    title: str
    servings: int | None
    total_minutes: int | None
    source_url: str
    instructions: str | None
    #: Products you have that this recipe uses, most urgent first.
    uses: list[str] = field(default_factory=list)
    use_ids: list[int] = field(default_factory=list)
    #: Of those, the ones at risk — the reason this recipe is being suggested at all.
    rescues: list[str] = field(default_factory=list)
    #: Non-staple ingredients you'd have to buy. Shown, never hidden.
    missing: list[str] = field(default_factory=list)
    #: The recipe's ingredient lines as the site published them. Grounding material for the
    #: LLM phrasing in `generate.py`, and deliberately not part of `RecipeMatchOut` — the
    #: suggestions endpoint returns a decision, not a copy of someone else's recipe.
    ingredients_raw: list[str] = field(default_factory=list)
    score: float = 0.0
    #: True when nothing is missing — the UI can badge it as cookable right now.
    cookable: bool = False


def urgency_weight(node: GraphNode) -> float:
    """Expiring tomorrow is worth far more than expiring in a fortnight.

    Staples are held near zero: rescuing the garlic is not a goal, and letting garlic score
    would flood the list with recipes that merely use garlic.
    """
    if node.is_staple:
        return 0.1
    d = node.days_to_expiry
    if d is None:
        return 0.5
    if d < 0:
        return 4.0
    if d == 0:
        return 3.5
    if d <= 3:
        return 3.0
    if d <= 7:
        return 1.5
    return 0.7


def rank_recipes(
    db: Session, user_id: int, *, limit: int = 12, pool: int = DEFAULT_POOL
) -> list[RecipeMatch]:
    """Score the retrieved candidates and return the best.

    Retrieval decides *what is considered*, this decides *what wins*, and the two are kept
    apart on purpose. `candidate_recipes` returns exactly the recipes that use something in
    the kitchen — which is the set this loop already skipped everything outside of, so the
    answer is unchanged and only the number of rows read went down.
    """
    nodes = active_products(db, user_id)
    if not nodes:
        return []

    # Names for every product a recipe might reference, so "missing" can be spelled out
    # rather than shown as an id.
    products = {p.id: p for p in db.scalars(select(Product))}

    matches: list[RecipeMatch] = []
    for recipe in candidate_recipes(db, list(nodes), pool=pool):
        ids = recipe.ingredient_product_ids or []
        if not ids:
            continue

        present = [pid for pid in ids if pid in nodes]
        if not present:
            continue

        missing_ids = [pid for pid in ids if pid not in nodes]
        # Assumed to be in the cupboard whether or not they were ever logged.
        missing_named = [
            products[pid].name_it
            for pid in missing_ids
            if pid in products and not products[pid].is_staple
        ]

        used = sorted(present, key=lambda pid: urgency_weight(nodes[pid]), reverse=True)
        score = sum(urgency_weight(nodes[pid]) for pid in used) - 0.5 * len(missing_named)

        matches.append(
            RecipeMatch(
                recipe_id=recipe.id,
                title=recipe.title_it,
                servings=recipe.servings,
                total_minutes=recipe.total_minutes,
                source_url=recipe.source_url,
                instructions=recipe.instructions,
                uses=[nodes[pid].name for pid in used],
                use_ids=used,
                rescues=[nodes[pid].name for pid in used if nodes[pid].at_risk],
                missing=missing_named,
                ingredients_raw=[
                    line for line in (recipe.ingredients_raw or []) if isinstance(line, str)
                ],
                score=round(score, 3),
                cookable=not missing_named,
            )
        )

    matches.sort(key=lambda m: (-m.score, m.total_minutes or 999))
    return matches[:limit]
