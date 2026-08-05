from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProductAlias
from app.services.normalize import normalize_text


def test_categories_are_listed_in_sort_order(auth_client: TestClient) -> None:
    cats = auth_client.get("/api/catalog/categories").json()
    assert len(cats) == 5
    assert [c["sort_order"] for c in cats] == sorted(c["sort_order"] for c in cats)


def test_product_search_matches_italian_and_english(auth_client: TestClient) -> None:
    assert len(auth_client.get("/api/catalog/products?q=mozz").json()) == 1
    # "cheese" is the category name, not the product's name_en ("mozzarella") — search
    # deliberately does not reach into the category.
    assert len(auth_client.get("/api/catalog/products?q=cheese").json()) == 0
    assert len(auth_client.get("/api/catalog/products?q=pasta").json()) == 1


def test_product_search_is_case_insensitive(auth_client: TestClient) -> None:
    lower = auth_client.get("/api/catalog/products?q=mozzarella").json()
    upper = auth_client.get("/api/catalog/products?q=MOZZARELLA").json()
    assert [p["id"] for p in lower] == [p["id"] for p in upper]


def test_products_can_be_filtered_by_category(auth_client: TestClient, seeded: dict) -> None:
    products = auth_client.get(f"/api/catalog/products?category_id={seeded['pantry'].id}").json()
    assert [p["name_it"] for p in products] == ["Pasta"]


def test_creating_a_product_also_registers_an_alias(
    auth_client: TestClient, db: Session, seeded: dict
) -> None:
    """The alias is what makes Phase 1's receipt parser match this product by exact text
    the very first time it appears on a receipt."""
    resp = auth_client.post(
        "/api/catalog/products",
        json={
            "name_it": "Stracchino",
            "name_en": "stracchino",
            "category_id": seeded["fridge"].id,
            "default_unit": "pz",
            "shelf_life_days": 6,
        },
    )
    assert resp.status_code == 201
    product = resp.json()
    assert product["shelf_life_days"] == 6

    alias = db.scalar(
        select(ProductAlias).where(ProductAlias.raw_text_norm == normalize_text("Stracchino"))
    )
    assert alias is not None
    assert alias.product_id == product["id"]
    assert alias.source.value == "user"


def test_new_product_inherits_category_perishability(auth_client: TestClient, seeded: dict) -> None:
    resp = auth_client.post(
        "/api/catalog/products",
        json={"name_it": "Ammorbidente", "category_id": seeded["nonfood"].id},
    )
    assert resp.json()["is_perishable"] is False


def test_product_in_unknown_category_is_404(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/catalog/products", json={"name_it": "Ghost", "category_id": 9999})
    assert resp.status_code == 404


def test_waste_reasons_are_available_for_the_kitchen_screen(auth_client: TestClient) -> None:
    reasons = auth_client.get("/api/catalog/waste-reasons").json()
    assert {"code", "label"} <= set(reasons[0])
    assert any(r["code"] == "dimenticato" for r in reasons)
