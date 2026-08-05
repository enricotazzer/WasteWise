"""The fridge graph, coverage ranking, and the unlock suggestion.

The distinction these tests protect: the graph **explains**, the ranking **decides**. If the
ranking ever started depending on graph structure, a suggestion could look well-connected
while rescuing nothing — which is the failure mode this module exists to avoid.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import InventoryItem, Product, Recipe
from app.models.enums import ItemStatus, Unit
from app.services.recipes import best_unlocks, build_graph, rank_recipes
from app.services.recipes.rank import urgency_weight


def _recipe(db: Session, title: str, product_ids: list[int], *, minutes: int = 30) -> Recipe:
    recipe = Recipe(
        source_url=f"seed://test/{title.lower().replace(' ', '-')}",
        source_site="seed",
        title_it=title,
        ingredients_raw=[f"ingrediente {i}" for i in product_ids],
        ingredient_product_ids=product_ids,
        n_ingredients=len(product_ids),
        servings=4,
        total_minutes=minutes,
        instructions="Cuoci.",
        scraped_at=datetime.now(UTC),
    )
    db.add(recipe)
    db.flush()
    return recipe


def _stock(db: Session, seeded: dict, product: Product, *, days: int | None) -> InventoryItem:
    expiry = date.today() + timedelta(days=days) if days is not None else None
    item = InventoryItem(
        user_id=seeded["user"].id,
        product_id=product.id,
        quantity=1,
        unit=Unit.PZ,
        acquired_at=date.today(),
        expiry_date=expiry,
        status=ItemStatus.ACTIVE,
    )
    db.add(item)
    db.flush()
    return item


@pytest.fixture
def pantry(db: Session, seeded: dict) -> dict:
    """Four products, three of them in the house."""
    olio = Product(
        name_it="Olio", name_en="oil", category_id=seeded["pantry"].id, default_unit=Unit.L
    )
    olio.is_staple = True
    db.add(olio)
    db.flush()

    _stock(db, seeded, seeded["mozzarella"], days=1)  # at risk
    _stock(db, seeded, seeded["pasta"], days=400)  # fine
    _stock(db, seeded, olio, days=None)  # staple, in the house
    db.commit()
    return {"olio": olio, **seeded}


# --- graph ---


def test_an_edge_appears_only_for_genuine_recipe_co_occurrence(db: Session, pantry: dict) -> None:
    _recipe(db, "Pasta e mozzarella", [pantry["pasta"].id, pantry["mozzarella"].id])
    _recipe(db, "Solo ricotta", [pantry["ricotta"].id])
    db.commit()

    graph = build_graph(db, pantry["user"].id)
    pairs = {(e.source, e.target) for e in graph.edges}
    assert pairs == {tuple(sorted((pantry["pasta"].id, pantry["mozzarella"].id)))}


def test_edge_weight_counts_shared_recipes(db: Session, pantry: dict) -> None:
    for i in range(3):
        _recipe(db, f"Ricetta {i}", [pantry["pasta"].id, pantry["mozzarella"].id])
    db.commit()

    graph = build_graph(db, pantry["user"].id)
    assert len(graph.edges) == 1
    assert graph.edges[0].weight == 3
    assert len(graph.edges[0].recipe_ids) == 3


def test_staples_are_excluded_by_default(db: Session, pantry: dict) -> None:
    """Olive oil is in most of the corpus. Including it makes the highest-degree node always
    olive oil — visually dominant, informationally empty."""
    _recipe(db, "Pasta all'olio", [pantry["pasta"].id, pantry["olio"].id])
    _recipe(db, "Mozzarella all'olio", [pantry["mozzarella"].id, pantry["olio"].id])
    db.commit()

    without = build_graph(db, pantry["user"].id)
    assert all(n.name != "Olio" for n in without.nodes)
    assert without.edges == []

    with_staples = build_graph(db, pantry["user"].id, include_staples=True)
    assert any(n.name == "Olio" for n in with_staples.nodes)
    assert len(with_staples.edges) == 2


def test_products_not_in_the_house_are_not_nodes(db: Session, pantry: dict) -> None:
    _recipe(db, "Ricotta e pasta", [pantry["ricotta"].id, pantry["pasta"].id])
    db.commit()
    graph = build_graph(db, pantry["user"].id)
    assert all(n.name != "Ricotta" for n in graph.nodes)


def test_duplicate_items_collapse_to_one_node_with_the_soonest_expiry(
    db: Session, pantry: dict
) -> None:
    """Two cartons of milk are one ingredient. The node must read as the *more* urgent of
    them, or a nearly-expired duplicate hides behind a fresh one."""
    _stock(db, pantry, pantry["mozzarella"], days=9)
    db.commit()

    graph = build_graph(db, pantry["user"].id)
    mozz = [n for n in graph.nodes if n.name == "Mozzarella"]
    assert len(mozz) == 1
    assert mozz[0].quantity == 2
    assert mozz[0].days_to_expiry == 1  # not 9


def test_degree_counts_distinct_partners_not_recipes(db: Session, pantry: dict) -> None:
    for i in range(4):
        _recipe(db, f"R{i}", [pantry["pasta"].id, pantry["mozzarella"].id])
    db.commit()
    graph = build_graph(db, pantry["user"].id)
    assert {n.degree for n in graph.nodes if n.name in ("Pasta", "Mozzarella")} == {1}
    assert {n.recipe_count for n in graph.nodes if n.name == "Pasta"} == {4}


def test_isolated_products_are_reported_not_hidden(db: Session, pantry: dict) -> None:
    """An isolated node almost always means a missing alias. Silently dropping it would make
    the corpus look better than it is."""
    _recipe(db, "Solo pasta", [pantry["pasta"].id])
    db.commit()
    graph = build_graph(db, pantry["user"].id)
    assert "Mozzarella" in graph.isolated


def test_empty_inventory_gives_an_empty_graph(db: Session, seeded: dict) -> None:
    graph = build_graph(db, seeded["user"].id)
    assert graph.nodes == [] and graph.edges == []


def test_graph_is_scoped_to_one_user(db: Session, pantry: dict) -> None:
    from app.models import User

    other = User(email="other@example.com", password_hash="x")
    db.add(other)
    db.commit()
    assert build_graph(db, other.id).nodes == []


# --- ranking ---


def test_a_recipe_rescuing_more_at_risk_food_ranks_higher(db: Session, pantry: dict) -> None:
    _stock(db, pantry, pantry["ricotta"], days=1)  # second at-risk item
    db.commit()

    _recipe(db, "Usa due a rischio", [pantry["mozzarella"].id, pantry["ricotta"].id])
    _recipe(db, "Usa uno a rischio", [pantry["mozzarella"].id, pantry["pasta"].id])
    db.commit()

    ranked = rank_recipes(db, pantry["user"].id)
    assert ranked[0].title == "Usa due a rischio"


def test_urgency_dominates_raw_ingredient_count(db: Session, pantry: dict) -> None:
    """Two long-dated ingredients must not outrank one that expires tomorrow — otherwise the
    list fills up with recipes that rescue nothing."""
    assert urgency_weight(_node(days=1)) > 2 * urgency_weight(_node(days=400))


def test_staples_are_never_listed_as_missing(db: Session, pantry: dict) -> None:
    """Nobody logs their salt. Counting it as missing makes every recipe look uncookable."""
    ricotta = pantry["ricotta"]
    _recipe(db, "Con olio", [pantry["mozzarella"].id, pantry["olio"].id, ricotta.id])
    db.commit()

    match = rank_recipes(db, pantry["user"].id)[0]
    assert "Olio" not in match.missing
    assert "Ricotta" in match.missing  # a real gap still shows
    assert match.cookable is False


def test_a_recipe_using_only_things_you_have_is_cookable(db: Session, pantry: dict) -> None:
    _recipe(db, "Tutto in casa", [pantry["mozzarella"].id, pantry["pasta"].id])
    db.commit()
    match = rank_recipes(db, pantry["user"].id)[0]
    assert match.cookable is True
    assert match.missing == []
    assert set(match.uses) == {"Mozzarella", "Pasta"}


def test_recipes_using_nothing_you_own_are_excluded(db: Session, pantry: dict) -> None:
    _recipe(db, "Niente in casa", [pantry["ricotta"].id])
    db.commit()
    assert rank_recipes(db, pantry["user"].id) == []


def test_rescues_lists_only_the_at_risk_items(db: Session, pantry: dict) -> None:
    _recipe(db, "Mista", [pantry["mozzarella"].id, pantry["pasta"].id])
    db.commit()
    match = rank_recipes(db, pantry["user"].id)[0]
    assert match.rescues == ["Mozzarella"]  # pasta is 400 days out


# --- unlock: the thing coverage counting cannot answer ---


def test_unlock_finds_the_single_purchase_that_completes_recipes(db: Session, pantry: dict) -> None:
    _recipe(db, "A", [pantry["mozzarella"].id, pantry["ricotta"].id])
    _recipe(db, "B", [pantry["mozzarella"].id, pantry["ricotta"].id, pantry["pasta"].id])
    db.commit()

    top = best_unlocks(db, pantry["user"].id)
    assert top[0].name == "Ricotta"
    assert top[0].unlocks == 2
    assert "Mozzarella" in top[0].rescues


def test_unlock_ignores_recipes_still_missing_two_things(db: Session, pantry: dict) -> None:
    """'Buy this and you're two ingredients away' is noise, not advice."""
    detersivo = pantry["detersivo"]
    _recipe(db, "Due buchi", [pantry["mozzarella"].id, pantry["ricotta"].id, detersivo.id])
    db.commit()
    assert best_unlocks(db, pantry["user"].id) == []


def test_unlock_ignores_recipes_that_rescue_nothing(db: Session, pantry: dict) -> None:
    """Otherwise the top suggestion is whatever appears in most of the corpus — true and
    completely useless."""
    _recipe(db, "Solo dispensa", [pantry["pasta"].id, pantry["ricotta"].id])
    db.commit()
    assert best_unlocks(db, pantry["user"].id) == []


def test_unlock_never_suggests_something_already_in_the_house(db: Session, pantry: dict) -> None:
    _recipe(db, "A", [pantry["mozzarella"].id, pantry["ricotta"].id])
    db.commit()
    names = {u.name for u in best_unlocks(db, pantry["user"].id)}
    assert "Mozzarella" not in names and "Pasta" not in names


def test_unlock_never_suggests_a_staple(db: Session, pantry: dict) -> None:
    """Staples are assumed present, so "buy olive oil" is never the answer."""
    _stock(db, pantry, pantry["ricotta"], days=1)
    db.commit()
    _recipe(db, "Con olio", [pantry["mozzarella"].id, pantry["ricotta"].id, pantry["olio"].id])
    db.commit()
    assert all(u.name != "Olio" for u in best_unlocks(db, pantry["user"].id))


def test_no_unlock_when_nothing_is_at_risk(db: Session, seeded: dict) -> None:
    _stock(db, seeded, seeded["pasta"], days=400)
    db.commit()
    _recipe(db, "A", [seeded["pasta"].id, seeded["ricotta"].id])
    db.commit()
    assert best_unlocks(db, seeded["user"].id) == []


def _node(*, days: int):
    from app.services.recipes.graph import GraphNode

    return GraphNode(
        product_id=1,
        name="x",
        category_code="X",
        quantity=1,
        unit="pz",
        days_to_expiry=days,
        is_staple=False,
    )
