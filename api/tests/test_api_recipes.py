"""Recipes API — the HTTP contract the Cosa cucino screen consumes."""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import InventoryItem, Recipe
from app.models.enums import ItemStatus, Unit


def _seed_kitchen(db: Session, seeded: dict) -> None:
    for product, days in ((seeded["mozzarella"], 1), (seeded["pasta"], 400)):
        db.add(
            InventoryItem(
                user_id=seeded["user"].id,
                product_id=product.id,
                quantity=1,
                unit=Unit.PZ,
                acquired_at=date.today(),
                expiry_date=date.today() + timedelta(days=days),
                status=ItemStatus.ACTIVE,
            )
        )
    db.add(
        Recipe(
            source_url="seed://test/pasta-e-mozzarella",
            source_site="seed",
            title_it="Pasta e mozzarella",
            ingredients_raw=["320 g di pasta", "250 g di mozzarella"],
            ingredient_product_ids=[seeded["pasta"].id, seeded["mozzarella"].id],
            n_ingredients=2,
            servings=4,
            total_minutes=20,
            instructions="Cuoci la pasta e condisci.",
            scraped_at=datetime.now(UTC),
        )
    )
    db.commit()


def test_all_recipe_routes_require_auth(client: TestClient) -> None:
    for path in ("/api/recipes/graph", "/api/recipes/suggestions", "/api/recipes/unlock"):
        assert client.get(path).status_code == 401


def test_graph_returns_nodes_and_edges(auth_client: TestClient, db: Session, seeded: dict) -> None:
    _seed_kitchen(db, seeded)
    body = auth_client.get("/api/recipes/graph").json()

    assert {n["name"] for n in body["nodes"]} == {"Mozzarella", "Pasta"}
    assert len(body["edges"]) == 1
    assert body["edges"][0]["weight"] == 1
    # at_risk is a computed property, not a column — easy to lose in serialization.
    mozz = next(n for n in body["nodes"] if n["name"] == "Mozzarella")
    assert mozz["at_risk"] is True
    assert next(n for n in body["nodes"] if n["name"] == "Pasta")["at_risk"] is False


def test_graph_include_staples_is_off_by_default(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    _seed_kitchen(db, seeded)
    default = auth_client.get("/api/recipes/graph").json()
    explicit = auth_client.get("/api/recipes/graph?include_staples=false").json()
    assert len(default["nodes"]) == len(explicit["nodes"])


def test_suggestions_report_what_is_rescued(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    _seed_kitchen(db, seeded)
    body = auth_client.get("/api/recipes/suggestions").json()

    assert len(body) == 1
    match = body[0]
    assert match["title"] == "Pasta e mozzarella"
    assert match["cookable"] is True
    assert match["rescues"] == ["Mozzarella"]  # pasta isn't at risk
    assert match["missing"] == []
    assert match["total_minutes"] == 20


def test_empty_kitchen_returns_empty_rather_than_erroring(auth_client: TestClient) -> None:
    assert auth_client.get("/api/recipes/graph").json()["nodes"] == []
    assert auth_client.get("/api/recipes/suggestions").json() == []
    assert auth_client.get("/api/recipes/unlock").json() == []


def test_suggestion_limit_is_capped(auth_client: TestClient) -> None:
    assert auth_client.get("/api/recipes/suggestions?limit=999").status_code == 422
