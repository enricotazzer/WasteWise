"""The fridge knowledge graph.

    node   = a product currently in the house
    edge   = "these two appear together in at least one recipe"
    weight = how many recipes

What the graph is *for* is worth being precise about, because it's easy to overclaim. For
**ranking** recipes, coverage counting (see `rank.py`) is already the right algorithm and the
graph adds nothing to it. What the graph adds instead:

  - **Explanation.** You see which of your items connect, so a suggestion stops being an
    opaque verdict.
  - **Clusters.** A dense group of expiring items is a set that genuinely cooks together.
  - **Leverage.** Degree answers "which of my ingredients is the most versatile", and its
    inverse (`unlock.py`) answers "what one thing should I buy".

Scoped to what is actually in the house — a graph over the whole catalog would be a hairball
that answers no question anyone asked.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import InventoryItem, Product
from app.models.enums import ItemStatus
from app.services.recipes.retrieval import overlapping

# Matches the UI's urgency bands. Kept here too so the API is usable without the frontend.
AT_RISK_DAYS = 3


@dataclass
class GraphNode:
    product_id: int
    name: str
    category_code: str
    quantity: float
    unit: str
    days_to_expiry: int | None
    is_staple: bool
    #: How many *other* in-house products this one shares a recipe with. Drives node size:
    #: a high-degree ingredient is one that combines with a lot of what you already have.
    degree: int = 0
    #: Number of corpus recipes this product appears in at all — its general versatility.
    recipe_count: int = 0

    @property
    def at_risk(self) -> bool:
        return self.days_to_expiry is not None and self.days_to_expiry <= AT_RISK_DAYS


@dataclass
class GraphEdge:
    source: int
    target: int
    weight: int
    recipe_ids: list[int] = field(default_factory=list)


@dataclass
class FridgeGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    #: Products in the house that no recipe in the corpus mentions. Reported rather than
    #: hidden — an isolated node usually means a missing alias, not an inert ingredient.
    isolated: list[str] = field(default_factory=list)


def active_products(db: Session, user_id: int) -> dict[int, GraphNode]:
    """One node per distinct product in the house, quantities summed across items.

    Two cartons of milk are one node with quantity 2, not two nodes — the graph is about
    ingredients, not about individual packages.
    """
    items = db.scalars(
        select(InventoryItem)
        .options(joinedload(InventoryItem.product).joinedload(Product.category))
        .where(
            InventoryItem.user_id == user_id,
            InventoryItem.status.in_([ItemStatus.ACTIVE, ItemStatus.PARTIAL]),
        )
    )

    nodes: dict[int, GraphNode] = {}
    today = date.today()
    for item in items:
        days = (item.expiry_date - today).days if item.expiry_date else None
        existing = nodes.get(item.product_id)
        if existing is None:
            nodes[item.product_id] = GraphNode(
                product_id=item.product_id,
                name=item.product.name_it,
                category_code=item.product.category.code,
                quantity=float(item.quantity),
                unit=item.unit.value,
                days_to_expiry=days,
                is_staple=item.product.is_staple,
            )
            continue

        existing.quantity += float(item.quantity)
        # The most urgent copy sets the node's urgency: if one of two mozzarellas expires
        # tomorrow, the node needs to read as "tomorrow".
        if days is not None and (existing.days_to_expiry is None or days < existing.days_to_expiry):
            existing.days_to_expiry = days
    return nodes


def build_graph(db: Session, user_id: int, *, include_staples: bool = False) -> FridgeGraph:
    """Build the fridge graph.

    Staples are excluded by default, and that matters more than it sounds. Olio, sale and
    aglio appear in most of the corpus, so including them produces a hub-and-spoke hairball
    where the highest-degree node is always olive oil — visually dominant and informationally
    empty. "Zucchine—olio" tells you nothing; "zucchine—uova" tells you frittata.
    """
    nodes = active_products(db, user_id)
    if not include_staples:
        nodes = {pid: n for pid, n in nodes.items() if not n.is_staple}
    if not nodes:
        return FridgeGraph(nodes=[], edges=[])

    in_house = set(nodes)
    # Pair -> (weight, recipe ids). Keyed low-id-first so an edge is stored once.
    pairs: dict[tuple[int, int], GraphEdge] = {}

    # Only recipes that mention something in the house can contribute a node count or an
    # edge, so the array-overlap index answers this instead of a full table scan. Uncapped:
    # `recipe_count` is an exhaustive count and a truncated pool would understate it.
    for recipe in overlapping(db, sorted(in_house), limit=None):
        ids = recipe.ingredient_product_ids or []
        present = sorted(in_house.intersection(ids))
        for pid in present:
            nodes[pid].recipe_count += 1
        # A recipe that uses only one thing you own says nothing about combinations.
        for i, a in enumerate(present):
            for b in present[i + 1 :]:
                edge = pairs.get((a, b))
                if edge is None:
                    edge = GraphEdge(source=a, target=b, weight=0)
                    pairs[(a, b)] = edge
                edge.weight += 1
                edge.recipe_ids.append(recipe.id)

    for a, b in pairs:
        nodes[a].degree += 1
        nodes[b].degree += 1

    isolated = [n.name for n in nodes.values() if n.recipe_count == 0]
    ordered = sorted(
        nodes.values(),
        # Urgent first, then the well-connected: the top of the list is what the UI should
        # draw attention to.
        key=lambda n: (n.days_to_expiry if n.days_to_expiry is not None else 9999, -n.degree),
    )
    return FridgeGraph(nodes=ordered, edges=list(pairs.values()), isolated=isolated)
